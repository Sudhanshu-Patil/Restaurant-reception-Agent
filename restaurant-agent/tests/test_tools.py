"""Tool layer: dispatch safety, individual tool behaviour, ownership. No LLM."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from agent.tools.catalog import registry
from agent.tools.context import ToolContext
from agent.tools.result import ErrorCode


def _dispatch(name: str, args: Any, ctx: ToolContext) -> dict[str, Any]:
    return registry.dispatch(
        name, args if isinstance(args, str) else json.dumps(args), ctx
    ).model_dump()


# -- dispatch defends against model misbehaviour --------------------------------
def test_unknown_tool_name(ctx):
    out = _dispatch("teleport_customer", {}, ctx)
    assert out["ok"] is False
    assert out["error_code"] == ErrorCode.UNKNOWN_TOOL.value


def test_malformed_json_arguments(ctx):
    out = _dispatch("search_menu", '{"category": "star', ctx)
    assert out["ok"] is False
    assert out["error_code"] == ErrorCode.BAD_JSON.value


def test_non_object_arguments(ctx):
    out = _dispatch("search_menu", "[1, 2, 3]", ctx)
    assert out["ok"] is False
    assert out["error_code"] == ErrorCode.BAD_ARGUMENTS.value


def test_missing_required_argument(ctx):
    out = _dispatch("check_availability", {"party_size": 2}, ctx)
    assert out["ok"] is False
    assert out["error_code"] == ErrorCode.BAD_ARGUMENTS.value
    assert "when" in out["message"]


def test_wrong_type_argument(ctx):
    out = _dispatch("check_availability", {"when": "tomorrow 7pm", "party_size": "lots"}, ctx)
    assert out["ok"] is False
    assert out["error_code"] == ErrorCode.BAD_ARGUMENTS.value


def test_unparseable_datetime(ctx):
    out = _dispatch("check_availability", {"when": "someday-ish", "party_size": 2}, ctx)
    assert out["ok"] is False
    assert out["error_code"] == ErrorCode.BAD_DATETIME.value


# -- read-only tools -----------------------------------------------------------
def test_search_menu_filters(call):
    out = call("search_menu", category="starter", max_price=320)
    assert out["ok"] is True
    assert {i["name"] for i in out["data"]["items"]} == {"Hara Bhara Kabab", "Samosa"}


def test_search_menu_excludes_unavailable(call):
    out = call("search_menu", category="dessert")
    assert {i["name"] for i in out["data"]["items"]} == {"Gulab Jamun"}


# -- smart table assignment --------------------------------------------------
def test_book_table_picks_smallest_fitting_table(call, ctx):
    out = call("book_table", when="tomorrow 7:00pm", party_size=3)
    assert out["ok"] is True
    assert out["data"]["assigned_table"]["table_number"] == 7  # smallest outdoor fitting 3
    assert out["data"]["seating"] == "outdoor"
    assert ctx.session.active_reservation_id == out["data"]["reservation"]["id"]


def test_book_table_honours_explicit_location(call):
    out = call("book_table", when="tomorrow 7:00pm", party_size=2, location="indoor")
    assert out["data"]["assigned_table"]["location"] == "indoor"


def test_book_table_no_table_available(call, backend):
    # fill every table that fits 2 at the slot
    for table in backend.tables:
        backend.create_reservation(
            1,
            table["id"],
            datetime.now().replace(hour=19, minute=0, second=0, microsecond=0) + timedelta(days=1),
            2,
        )
    out = call("book_table", when="tomorrow 7:00pm", party_size=2)
    assert out["ok"] is False
    assert out["error_code"] == ErrorCode.NO_TABLE_AVAILABLE.value


# -- change_reservation ----------------------------------------------------
def test_change_reservation_moves_and_cancels_old(call, ctx, backend):
    old = call("book_table", when="tomorrow 7:00pm", party_size=2, location="indoor")
    old_id = old["data"]["reservation"]["id"]

    out = call("change_reservation", location="outdoor")
    assert out["ok"] is True
    assert out["data"]["changed_from"] == old_id
    assert out["data"]["assigned_table"]["location"] == "outdoor"
    assert backend.get_reservation(old_id)["status"] == "cancelled"
    assert ctx.session.active_reservation_id == out["data"]["reservation"]["id"]


def test_change_reservation_blocked_by_cutoff_leaves_original(call, ctx, backend):
    soon = backend.create_reservation(1, 2, datetime.now() + timedelta(minutes=45), 2)
    ctx.session.active_reservation_id = soon["id"]
    out = call("change_reservation", location="outdoor")
    assert out["ok"] is False
    assert "2 hours" in out["message"]
    assert backend.get_reservation(soon["id"])["status"] == "confirmed"


# -- ownership ------------------------------------------------------------
def test_cannot_cancel_another_customers_reservation(call, backend):
    other = backend.create_customer("Someone Else", phone="+91-1111111111")
    theirs = backend.create_reservation(other["id"], 2, datetime.now() + timedelta(days=2), 2)
    out = call("cancel_reservation", reservation_id=theirs["id"])
    assert out["ok"] is False
    assert out["error_code"] == ErrorCode.OWNERSHIP_DENIED.value
    assert backend.get_reservation(theirs["id"])["status"] == "confirmed"


def test_cannot_add_items_to_another_customers_reservation(call, backend):
    other = backend.create_customer("Someone Else", email="x@y.z")
    theirs = backend.create_reservation(other["id"], 2, datetime.now() + timedelta(days=2), 2)
    out = call("add_items_to_reservation", reservation_id=theirs["id"], items=[{"name": "samosa"}])
    assert out["ok"] is False
    assert out["error_code"] == ErrorCode.OWNERSHIP_DENIED.value


def test_cancel_without_active_reservation(call):
    out = call("cancel_reservation")
    assert out["ok"] is False
    assert out["error_code"] == ErrorCode.NO_ACTIVE_RESERVATION.value


# -- orders: name matching + guards -------------------------------------
def test_add_items_matches_names_and_skips_guarded_items(call):
    rid = call("book_table", when="tomorrow 8:00pm", party_size=2)["data"]["reservation"]["id"]
    out = call(
        "add_items_to_reservation",
        reservation_id=rid,
        avoid_tags=["contains-nuts"],
        items=[
            {"name": "paneer tikka", "quantity": 2},
            {"name": "kulfi", "quantity": 1},  # unavailable
            {"name": "butter chicken", "quantity": 1},  # nuts (avoided)
            {"name": "flying pizza", "quantity": 1},  # not on menu
        ],
    )
    assert out["ok"] is True
    added = {a["item"] for a in out["data"]["added"]}
    skipped = {s["item"]: s["reason"] for s in out["data"]["skipped"]}
    assert added == {"Paneer Tikka"}
    assert "unavailable" in skipped["Kulfi"]
    assert "contains-nuts" in skipped["Butter Chicken"]
    assert skipped["flying pizza"] == "not on the menu"


def test_remove_order_item_verifies_it_belongs_to_the_reservation(call, backend):
    rid = call("book_table", when="tomorrow 8:00pm", party_size=2)["data"]["reservation"]["id"]
    out = call("remove_order_item", order_id=999, reservation_id=rid)
    assert out["ok"] is False
    assert out["error_code"] == ErrorCode.OWNERSHIP_DENIED.value


def test_remove_order_item_happy_path(call, backend):
    rid = call("book_table", when="tomorrow 8:00pm", party_size=2)["data"]["reservation"]["id"]
    added = call("add_items_to_reservation", reservation_id=rid, items=[{"name": "samosa"}])
    order_id = added["data"]["added"][0]["order_id"]
    out = call("remove_order_item", order_id=order_id, reservation_id=rid)
    assert out["ok"] is True
    assert backend.list_order_items(rid) == []


# -- the remaining read-only + explicit tools --------------------------------
def test_get_current_datetime(call):
    out = call("get_current_datetime")
    assert out["ok"] is True
    assert "now" in out["data"] and "weekday" in out["data"]


def test_create_reservation_explicit_table(call, ctx):
    out = call("create_reservation", table_id=5, when="tomorrow 7:00pm", party_size=3)
    assert out["ok"] is True
    assert out["data"]["reservation"]["table_id"] == 5
    assert ctx.session.active_reservation_id == out["data"]["reservation"]["id"]


def test_list_my_reservations_and_history(call):
    out = call("list_my_reservations")
    assert out["ok"] is True
    assert isinstance(out["data"]["reservations"], list)

    hist = call("list_order_history")
    assert hist["ok"] is True
    assert {h["item"] for h in hist["data"]["history"]} == {"Paneer Tikka", "Dal Makhani"}


def test_add_items_needs_an_active_reservation(call):
    out = call("add_items_to_reservation", items=[{"name": "samosa"}])
    assert out["ok"] is False
    assert out["error_code"] == ErrorCode.NO_ACTIVE_RESERVATION.value


def test_change_reservation_needs_a_target(call):
    out = call("change_reservation", when="tomorrow 9pm")
    assert out["ok"] is False
    assert out["error_code"] == ErrorCode.NO_ACTIVE_RESERVATION.value


def test_change_reservation_restores_original_when_no_table_fits(call, ctx, backend):
    call("book_table", when="tomorrow 7:00pm", party_size=2, location="outdoor")
    # ask to grow the party past every table's capacity
    out = call("change_reservation", party_size=99)
    assert out["ok"] is False
    assert out["error_code"] == ErrorCode.NO_TABLE_AVAILABLE.value
    # the original was cancelled then re-created; a confirmed booking still exists
    confirmed = backend.list_customer_reservations(1, status="confirmed")
    assert len(confirmed) == 1
    assert ctx.session.active_reservation_id == confirmed[0]["id"]

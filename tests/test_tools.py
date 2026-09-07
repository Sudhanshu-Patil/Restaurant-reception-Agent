"""Tool layer: dispatch safety + individual tool behaviour. No LLM involved."""
from __future__ import annotations

import json

from agent.tools.catalog import registry


def _call(name, args, ctx):
    return json.loads(registry.dispatch(name, json.dumps(args), ctx))


# -- dispatch defends against LLM misbehaviour ------------------------------
def test_unknown_tool_name_returns_error(ctx):
    out = json.loads(registry.dispatch("teleport_customer", "{}", ctx))
    assert "error" in out and "Unknown tool" in out["error"]


def test_malformed_json_arguments_return_error(ctx):
    out = json.loads(registry.dispatch("search_menu", '{"category": "star', ctx))
    assert "error" in out and "not valid JSON" in out["error"]


def test_non_object_arguments_return_error(ctx):
    out = json.loads(registry.dispatch("search_menu", "[1, 2, 3]", ctx))
    assert "error" in out and "must be a JSON object" in out["error"]


def test_missing_required_argument_returns_readable_error(ctx):
    out = json.loads(registry.dispatch("check_availability", '{"party_size": 2}', ctx))
    assert "error" in out and "when" in out["error"]


def test_wrong_type_argument_returns_error(ctx):
    out = _call("check_availability", {"when": "tomorrow 7pm", "party_size": "lots"}, ctx)
    assert "error" in out


# -- read-only tools ---------------------------------------------------------
def test_search_menu_filters(ctx):
    out = _call("search_menu", {"category": "starter", "max_price": 320}, ctx)
    names = {i["name"] for i in out["items"]}
    assert names == {"Hara Bhara Kabab", "Samosa"}


def test_search_menu_excludes_unavailable(ctx):
    out = _call("search_menu", {"category": "dessert"}, ctx)
    assert {i["name"] for i in out["items"]} == {"Gulab Jamun"}  # Kulfi is unavailable


# -- smart table assignment -----------------------------------------------
def test_book_table_picks_smallest_fitting_table(ctx):
    out = _call("book_table", {"when": "tomorrow 7:00pm", "party_size": 3}, ctx)
    # Priya prefers outdoor; smallest outdoor table fitting 3 is table_number 7 (cap 4).
    assert out["assigned_table"]["table_number"] == 7
    assert out["seating"] == "outdoor"
    assert out["reservation"]["status"] == "confirmed"
    assert ctx.session.active_reservation_id == out["reservation"]["id"]


def test_book_table_honours_explicit_location_over_preference(ctx):
    out = _call(
        "book_table",
        {"when": "tomorrow 7:00pm", "party_size": 2, "location": "indoor"},
        ctx,
    )
    assert out["assigned_table"]["location"] == "indoor"


# -- orders: name matching, allergy + availability guards ------------------
def test_add_items_matches_names_and_skips_guarded_items(ctx):
    booking = _call("book_table", {"when": "tomorrow 8:00pm", "party_size": 2}, ctx)
    rid = booking["reservation"]["id"]
    out = _call(
        "add_items_to_reservation",
        {
            "reservation_id": rid,
            "avoid_tags": ["contains-nuts"],
            "items": [
                {"name": "paneer tikka", "quantity": 2},
                {"name": "kulfi", "quantity": 1},          # unavailable
                {"name": "butter chicken", "quantity": 1},  # nuts (avoided)
                {"name": "flying pizza", "quantity": 1},     # not on menu
            ],
        },
        ctx,
    )
    added = {a["item"] for a in out["added"]}
    skipped = {s["item"]: s["reason"] for s in out["skipped"]}
    assert added == {"Paneer Tikka"}
    assert "unavailable" in skipped["Kulfi"]
    assert "contains-nuts" in skipped["Butter Chicken"]
    assert skipped["flying pizza"] == "not on the menu"

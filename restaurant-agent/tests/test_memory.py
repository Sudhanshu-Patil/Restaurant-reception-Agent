"""Durable customer memory: load, derive, persist - and how it reaches tools."""

from __future__ import annotations

import json

from agent.conversation import Session
from agent.memory.customer_memory import CustomerMemory
from agent.tools.catalog import registry
from agent.tools.context import ToolContext
from agent.tools.result import ErrorCode

from tests.fakes import FakeBackend


def test_load_reads_stored_preferences(memory):
    assert memory.preferences["seating"] == "outdoor"
    assert memory.preferences["dietary"] == ["vegetarian"]


def test_order_history_is_aggregated_with_names(memory):
    hist = {h["item"]: h["total_quantity"] for h in memory.order_history()}
    assert hist == {"Paneer Tikka": 1, "Dal Makhani": 2}


def test_summary_mentions_seating_and_history(memory):
    summary = memory.summary()
    assert "outdoor" in summary
    assert "Paneer Tikka" in summary


def test_remember_shallow_merges_and_persists(backend: FakeBackend):
    mem = CustomerMemory(backend, customer_id=1)
    mem.load()
    mem.remember("allergies", "nuts")
    assert mem.preferences["allergies"] == ["nuts"]
    assert mem.preferences["seating"] == "outdoor"  # untouched by the merge
    assert backend.get_customer(1)["preferences"]["allergies"] == ["nuts"]


def test_remember_preference_tool_updates_memory(backend: FakeBackend):
    mem = CustomerMemory(backend, customer_id=1)
    mem.load()
    ctx = ToolContext(
        backend=backend, session=Session(customer_id=1, name="Priya Sharma"), memory=mem
    )
    result = registry.dispatch("remember_preference", '{"key": "seating", "value": "indoor"}', ctx)
    assert result.ok
    assert result.data["preferences"]["seating"] == "indoor"
    assert mem.preferences["seating"] == "indoor"


def test_stored_allergy_blocks_ordering(backend: FakeBackend):
    backend.update_preferences(1, {"allergies": ["nuts"]})
    mem = CustomerMemory(backend, customer_id=1)
    mem.load()
    ctx = ToolContext(
        backend=backend, session=Session(customer_id=1, name="Priya Sharma"), memory=mem
    )
    booking = registry.dispatch("book_table", '{"when": "tomorrow 7:00pm", "party_size": 2}', ctx)
    rid = booking.data["reservation"]["id"]
    out = registry.dispatch(
        "add_items_to_reservation",
        json.dumps({"reservation_id": rid, "items": [{"name": "butter chicken"}]}),
        ctx,
    )
    assert out.ok
    assert out.data["added"] == []
    assert "contains-nuts" in out.data["skipped"][0]["reason"]


def test_add_items_folds_in_stored_allergy_without_avoid_tags(backend: FakeBackend):
    """The model need not pass avoid_tags - allergies are always enforced."""
    backend.update_preferences(1, {"allergies": ["nuts"]})
    mem = CustomerMemory(backend, customer_id=1)
    mem.load()
    assert mem.allergy_tags == {"contains-nuts"}


def test_new_customer_summary_is_empty(backend: FakeBackend):
    new = backend.create_customer("Fresh Face", phone="+91-2222222222")
    mem = CustomerMemory(backend, customer_id=new["id"])
    mem.load()
    assert "Nothing on file" in mem.summary()
    assert mem.order_history() == []


def test_unknown_error_code_is_never_leaked_as_raw_dict(ctx):
    """Every dispatch outcome is a ToolResult, even a crash inside a tool."""
    out = registry.dispatch("get_current_datetime", "not json at all", ctx)
    assert out.error_code == ErrorCode.BAD_JSON

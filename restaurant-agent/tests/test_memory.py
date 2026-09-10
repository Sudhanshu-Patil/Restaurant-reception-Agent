"""Durable customer memory: load, derive, persist."""

from __future__ import annotations

import json

from agent.conversation import Session
from agent.memory.customer_memory import CustomerMemory
from agent.tools.catalog import registry
from agent.tools.context import ToolContext


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


def test_remember_shallow_merges_and_refreshes(backend):
    mem = CustomerMemory(backend, customer_id=1)
    mem.load()
    mem.remember("allergies", "nuts")
    assert mem.preferences["allergies"] == ["nuts"]
    # seating preference is untouched by the shallow merge
    assert mem.preferences["seating"] == "outdoor"
    # and it is actually persisted in the backend
    assert backend.get_customer(1)["preferences"]["allergies"] == ["nuts"]


def test_remember_preference_tool_updates_memory(backend):
    mem = CustomerMemory(backend, customer_id=1)
    mem.load()
    ctx = ToolContext(
        backend=backend,
        session=Session(customer_id=1, name="Priya Sharma"),
        memory=mem,
    )
    out = json.loads(
        registry.dispatch("remember_preference", '{"key": "seating", "value": "indoor"}', ctx)
    )
    assert out["preferences"]["seating"] == "indoor"
    assert mem.preferences["seating"] == "indoor"


def test_stored_allergy_blocks_ordering(backend):
    backend.update_preferences(1, {"allergies": ["nuts"]})
    mem = CustomerMemory(backend, customer_id=1)
    mem.load()
    session = Session(customer_id=1, name="Priya Sharma")
    ctx = ToolContext(backend=backend, session=session, memory=mem)

    booking = json.loads(
        registry.dispatch("book_table", '{"when": "tomorrow 7:00pm", "party_size": 2}', ctx)
    )
    rid = booking["reservation"]["id"]
    out = json.loads(
        registry.dispatch(
            "add_items_to_reservation",
            json.dumps({"reservation_id": rid, "items": [{"name": "butter chicken"}]}),
            ctx,
        )
    )
    assert out["added"] == []
    assert "contains-nuts" in out["skipped"][0]["reason"]

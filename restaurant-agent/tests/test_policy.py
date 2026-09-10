"""The deterministic guardrails - pure, no LLM."""

from __future__ import annotations

import json

from agent.conversation import Conversation
from agent.llm.base import ToolCall
from agent.policy import evaluate, resolved_seating
from agent.tools.result import ErrorCode


def _convo(*user_messages: str) -> Conversation:
    c = Conversation()
    for m in user_messages:
        c.add_user(m)
    return c


def _tc(name: str, **args: object) -> ToolCall:
    return ToolCall(id="1", name=name, arguments=json.dumps(args))


# -- confirmation gate ------------------------------------------------------
def test_cancel_without_explicit_intent_is_blocked(memory):
    convo = _convo("I'd like to change something about my evening")
    decision = evaluate(_tc("cancel_reservation", reservation_id=4), convo, memory)
    assert decision.allowed is False
    assert decision.blocked_result.error_code == ErrorCode.NEEDS_CONFIRMATION


def test_cancel_passes_when_user_said_cancel(memory):
    convo = _convo("actually cancel that reservation")
    assert evaluate(_tc("cancel_reservation"), convo, memory).allowed is True


def test_cancel_passes_after_a_yes(memory):
    convo = _convo("can you get rid of my booking?", "yes please")
    assert evaluate(_tc("cancel_reservation"), convo, memory).allowed is True


def test_remove_item_needs_confirmation_too(memory):
    convo = _convo("hmm the order looks off")
    d = evaluate(_tc("remove_order_item", order_id=3), convo, memory)
    assert d.allowed is False
    assert d.blocked_result.error_code == ErrorCode.NEEDS_CONFIRMATION


def test_non_destructive_tools_are_never_gated_for_confirmation(memory):
    convo = _convo("show me the menu")
    assert evaluate(_tc("search_menu"), convo, memory).allowed is True


# -- booking seating gate -------------------------------------------------
def test_book_table_blocked_when_seating_unresolved(backend):
    from agent.memory.customer_memory import CustomerMemory

    new = backend.create_customer("New Guest", phone="+91-9000000000")
    mem = CustomerMemory(backend, new["id"])
    mem.load()
    convo = _convo("book a table for 2 tomorrow at 8")
    d = evaluate(_tc("book_table", when="tomorrow 8pm", party_size=2), convo, mem)
    assert d.allowed is False
    assert d.blocked_result.error_code == ErrorCode.NEEDS_CLARIFICATION


def test_book_table_allowed_with_stored_seating_preference(memory):
    # Priya has seating=outdoor stored
    convo = _convo("book a table for 2 tomorrow at 8")
    assert evaluate(_tc("book_table", when="tomorrow 8pm", party_size=2), convo, memory).allowed


def test_book_table_allowed_when_user_states_seating(backend):
    from agent.memory.customer_memory import CustomerMemory

    new = backend.create_customer("New Guest", email="n@e.w")
    mem = CustomerMemory(backend, new["id"])
    mem.load()
    convo = _convo("table for 2 tomorrow at 8, outside please")
    assert evaluate(_tc("book_table", when="tomorrow 8pm", party_size=2), convo, mem).allowed


def test_resolved_seating_recognises_either(backend):
    from agent.memory.customer_memory import CustomerMemory

    new = backend.create_customer("New Guest", email="n2@e.w")
    mem = CustomerMemory(backend, new["id"])
    mem.load()
    assert resolved_seating(_convo("either is fine"), mem) == "either"
    assert resolved_seating(_convo("we'd like to sit inside"), mem) == "indoor"
    assert resolved_seating(_convo("nothing about seating"), mem) is None

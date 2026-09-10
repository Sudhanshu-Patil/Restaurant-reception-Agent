"""End-to-end agent loop driven by a scripted LLM - no network."""

from __future__ import annotations

import json
from dataclasses import replace

from agent.agent import ReceptionAgent
from agent.conversation import Conversation
from agent.llm.base import LLMMessage, MalformedToolCall, ToolCall
from agent.llm.stub import ScriptedLLM
from agent.tools.catalog import registry


def _tc(call_id: str, name: str, **args: object) -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=json.dumps(args))


def _agent(llm: ScriptedLLM, backend, settings) -> ReceptionAgent:
    return ReceptionAgent(llm, registry, backend, settings)


def test_booking_then_ordering_flow(backend, priya_session, memory, settings):
    llm = ScriptedLLM(
        [
            LLMMessage(tool_calls=[_tc("1", "book_table", when="tomorrow 8:00pm", party_size=3)]),
            LLMMessage(
                tool_calls=[
                    _tc(
                        "2",
                        "add_items_to_reservation",
                        items=[
                            {"name": "paneer tikka", "quantity": 1},
                            {"name": "dal makhani", "quantity": 2},
                        ],
                    )
                ]
            ),
            LLMMessage(content="Booked table 7 outside and added your usuals. See you at 8!"),
        ]
    )
    result = _agent(llm, backend, settings).run_turn(
        "Book a table for 3 tomorrow at 8 and add one paneer tikka and two dal makhani",
        Conversation(),
        priya_session,
        memory,
    )

    assert "8" in result.reply
    assert result.trace.llm_rounds == 3
    assert result.trace.stopped_reason == "completed"
    assert [s.name for s in result.trace.steps] == ["book_table", "add_items_to_reservation"]
    assert all(s.ok for s in result.trace.steps)
    assert result.trace.mutations == 2

    reservations = backend.list_customer_reservations(1, status="confirmed")
    assert len(reservations) == 1
    assert sum(o["quantity"] for o in backend.list_order_items(reservations[0]["id"])) == 3


def test_system_prompt_carries_memory(backend, priya_session, memory, settings):
    llm = ScriptedLLM([LLMMessage(content="hi")])
    _agent(llm, backend, settings).run_turn("hello", Conversation(), priya_session, memory)
    system_msg = llm.calls[0]["messages"][0]["content"]
    assert "outdoor" in system_msg
    assert "Paneer Tikka" in system_msg


def test_step_budget_caps_a_runaway_loop(backend, priya_session, memory, settings):
    llm = ScriptedLLM(
        [LLMMessage(tool_calls=[_tc(str(i), "get_current_datetime")]) for i in range(50)]
    )
    result = _agent(llm, backend, settings).run_turn(
        "what time is it", Conversation(), priya_session, memory
    )
    assert result.trace.llm_rounds == settings.max_agent_steps
    assert result.trace.stopped_reason == "step_budget"


def test_mutation_budget_stops_further_changes(backend, priya_session, memory, settings):
    tight = replace(settings, max_mutations=1, max_tool_calls=20, max_agent_steps=20)
    llm = ScriptedLLM(
        [
            LLMMessage(tool_calls=[_tc(str(i), "remember_preference", key="note", value=f"n{i}")])
            for i in range(10)
        ]
    )
    result = _agent(llm, backend, tight).run_turn(
        "remember lots", Conversation(), priya_session, memory
    )
    assert result.trace.stopped_reason == "mutation_budget"
    assert result.trace.mutations == 1


def test_provider_rejected_tool_call_is_recovered(backend, priya_session, memory, settings):
    llm = ScriptedLLM(
        [
            MalformedToolCall("parameters for add_items_to_reservation did not match schema"),
            LLMMessage(content="Sorry about that - could you confirm the dishes again?"),
        ]
    )
    result = _agent(llm, backend, settings).run_turn(
        "add food", Conversation(), priya_session, memory
    )
    assert result.trace.llm_rounds == 2
    assert result.trace.steps[0].ok is False
    assert result.reply.startswith("Sorry about that")


def test_unknown_tool_from_llm_is_surfaced_not_crashed(backend, priya_session, memory, settings):
    llm = ScriptedLLM(
        [
            LLMMessage(tool_calls=[_tc("1", "make_reservation_now")]),
            LLMMessage(content="Let me try that a different way."),
        ]
    )
    result = _agent(llm, backend, settings).run_turn(
        "book me in", Conversation(), priya_session, memory
    )
    assert result.trace.steps[0].name == "make_reservation_now"
    assert result.trace.steps[0].ok is False
    assert result.reply == "Let me try that a different way."


def test_stream_turn_emits_ordered_events(backend, priya_session, memory, settings):
    llm = ScriptedLLM(
        [
            LLMMessage(tool_calls=[_tc("1", "search_menu", category="dessert")]),
            LLMMessage(content="We have Gulab Jamun."),
        ]
    )
    events = list(
        _agent(llm, backend, settings).stream_turn(
            "what desserts", Conversation(), priya_session, memory
        )
    )
    types = [e.type for e in events]
    assert types == ["tool_call", "tool_result", "message", "done"]
    assert events[0].data["name"] == "search_menu"
    assert events[1].data["ok"] is True
    assert events[-1].data["stopped_reason"] == "completed"


def test_tuple_unpacking_backcompat(backend, priya_session, memory, settings):
    llm = ScriptedLLM([LLMMessage(content="hello")])
    reply, trace = _agent(llm, backend, settings).run_turn(
        "hi", Conversation(), priya_session, memory
    )
    assert reply == "hello"
    assert trace.llm_rounds == 1

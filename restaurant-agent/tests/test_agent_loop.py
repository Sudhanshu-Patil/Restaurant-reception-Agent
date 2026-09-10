"""End-to-end agent loop driven by a scripted LLM — no network."""

from __future__ import annotations

import json

from agent.agent import ReceptionAgent
from agent.config import Settings
from agent.conversation import Conversation
from agent.llm.base import LLMMessage, MalformedToolCall, ToolCall
from agent.llm.stub import ScriptedLLM
from agent.tools.catalog import registry


def _settings() -> Settings:
    return Settings(groq_api_key="x", model="m", restaurant_api_url="http://x")


def _tc(call_id, name, **args):
    return ToolCall(id=call_id, name=name, arguments=json.dumps(args))


def _make_agent(llm, backend):
    return ReceptionAgent(llm, registry, backend, _settings())


def test_booking_then_ordering_flow(backend, priya_session, memory):
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
    agent = _make_agent(llm, backend)
    convo = Conversation()

    reply, trace = agent.run_turn(
        "Book a table for 3 tomorrow at 8 and add one paneer tikka and two dal makhani",
        convo,
        priya_session,
        memory,
    )

    assert "8" in reply
    assert trace.llm_rounds == 3
    assert [s.name for s in trace.steps] == ["book_table", "add_items_to_reservation"]
    assert all(s.ok for s in trace.steps)

    # the booking really happened in the backend
    reservations = backend.list_customer_reservations(1, status="confirmed")
    assert len(reservations) == 1
    orders = backend.list_order_items(reservations[0]["id"])
    assert sum(o["quantity"] for o in orders) == 3


def test_system_prompt_carries_memory(backend, priya_session, memory):
    llm = ScriptedLLM([LLMMessage(content="hi")])
    agent = _make_agent(llm, backend)
    agent.run_turn("hello", Conversation(), priya_session, memory)

    system_msg = llm.calls[0]["messages"][0]["content"]
    assert "outdoor" in system_msg
    assert "Paneer Tikka" in system_msg


def test_runaway_tool_loop_is_capped(backend, priya_session, memory):
    # LLM that always asks for another tool call — must not loop forever.
    llm = ScriptedLLM(
        [LLMMessage(tool_calls=[_tc(str(i), "get_current_datetime")]) for i in range(50)]
    )
    agent = _make_agent(llm, backend)
    reply, trace = agent.run_turn("what time is it", Conversation(), priya_session, memory)
    assert trace.llm_rounds == _settings().max_agent_steps
    assert "couldn't finish" in reply.lower()


def test_provider_rejected_tool_call_is_recovered(backend, priya_session, memory):
    llm = ScriptedLLM(
        [
            MalformedToolCall("parameters for add_items_to_reservation did not match schema"),
            LLMMessage(content="Sorry about that — could you confirm the dishes again?"),
        ]
    )
    agent = _make_agent(llm, backend)
    reply, trace = agent.run_turn("add food", Conversation(), priya_session, memory)
    assert trace.llm_rounds == 2
    assert trace.steps[0].ok is False
    assert reply.startswith("Sorry about that")


def test_unknown_tool_from_llm_is_surfaced_not_crashed(backend, priya_session, memory):
    llm = ScriptedLLM(
        [
            LLMMessage(tool_calls=[_tc("1", "make_reservation_now")]),  # wrong name
            LLMMessage(content="Sorry, let me try that a different way."),
        ]
    )
    agent = _make_agent(llm, backend)
    reply, trace = agent.run_turn("book me in", Conversation(), priya_session, memory)
    assert trace.steps[0].name == "make_reservation_now"
    assert trace.steps[0].ok is False
    assert reply == "Sorry, let me try that a different way."

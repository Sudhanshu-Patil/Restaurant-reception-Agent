"""Backend business-rule errors must surface as tool results and reach the customer."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from agent.agent import ReceptionAgent
from agent.config import Settings
from agent.conversation import Conversation
from agent.llm.base import LLMMessage, ToolCall
from agent.llm.stub import ScriptedLLM
from agent.tools.catalog import registry


def _settings():
    return Settings(groq_api_key="x", model="m", restaurant_api_url="http://x")


def _tc(cid, name, **args):
    return ToolCall(id=cid, name=name, arguments=json.dumps(args))


def test_double_booking_returns_409_as_tool_error(ctx, backend):
    backend.create_reservation(
        customer_id=1, table_id=5,
        slot_datetime=datetime.now().replace(hour=19, minute=0, second=0, microsecond=0)
        + timedelta(days=1),
        party_size=4,
    )
    out = json.loads(
        registry.dispatch(
            "create_reservation",
            json.dumps({"table_id": 5, "when": "tomorrow 7:00pm", "party_size": 4}),
            ctx,
        )
    )
    assert out["status_code"] == 409
    assert "already booked" in out["error"]


def test_cancellation_within_cutoff_surfaces_to_customer(ctx, backend):
    soon = backend.create_reservation(
        customer_id=1, table_id=5,
        slot_datetime=datetime.now() + timedelta(minutes=30),
        party_size=2,
    )
    out = json.loads(
        registry.dispatch(
            "cancel_reservation", json.dumps({"reservation_id": soon["id"]}), ctx
        )
    )
    assert out["status_code"] == 409
    assert "2 hours" in out["error"]


def test_agent_relays_backend_error_instead_of_crashing(backend, priya_session, memory):
    soon = backend.create_reservation(
        customer_id=1, table_id=5,
        slot_datetime=datetime.now() + timedelta(minutes=30),
        party_size=2,
    )
    llm = ScriptedLLM(
        [
            LLMMessage(tool_calls=[_tc("1", "cancel_reservation", reservation_id=soon["id"])]),
            LLMMessage(
                content="I can't cancel that — it's within 2 hours of the booking. "
                "Please call the restaurant directly."
            ),
        ]
    )
    agent = ReceptionAgent(llm, registry, backend, _settings())
    reply, trace = agent.run_turn("cancel my booking", Conversation(), priya_session, memory)

    assert trace.steps[0].ok is False
    assert "within 2 hours" in reply
    # reservation still confirmed — nothing was destroyed
    assert backend.get_reservation(soon["id"])["status"] == "confirmed"

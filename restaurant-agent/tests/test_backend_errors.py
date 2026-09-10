"""Backend business-rule errors surface as typed tool results and reach the customer."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from agent.agent import ReceptionAgent
from agent.conversation import Conversation
from agent.llm.base import LLMMessage, ToolCall
from agent.llm.stub import ScriptedLLM
from agent.tools.catalog import registry
from agent.tools.result import ErrorCode


def _tc(cid: str, name: str, **args: object) -> ToolCall:
    return ToolCall(id=cid, name=name, arguments=json.dumps(args))


def test_double_booking_returns_409(call, backend):
    slot = datetime.now().replace(hour=19, minute=0, second=0, microsecond=0) + timedelta(days=1)
    backend.create_reservation(1, 5, slot, 4)
    out = call("create_reservation", table_id=5, when="tomorrow 7:00pm", party_size=4)
    assert out["ok"] is False
    assert out["http_status"] == 409
    assert "already booked" in out["message"]


def test_cancellation_within_cutoff_is_typed(call, ctx, backend):
    soon = backend.create_reservation(1, 5, datetime.now() + timedelta(minutes=30), 2)
    ctx.session.active_reservation_id = soon["id"]
    out = call("cancel_reservation", reservation_id=soon["id"])
    assert out["ok"] is False
    assert out["error_code"] == ErrorCode.NOT_MODIFIABLE.value
    assert out["http_status"] == 409
    assert "2 hours" in out["message"]


def test_agent_relays_backend_error_without_crashing_or_data_loss(
    backend, priya_session, memory, settings
):
    soon = backend.create_reservation(1, 5, datetime.now() + timedelta(minutes=30), 2)
    priya_session.active_reservation_id = soon["id"]
    llm = ScriptedLLM(
        [
            LLMMessage(tool_calls=[_tc("1", "cancel_reservation", reservation_id=soon["id"])]),
            LLMMessage(content="I can't - it's within 2 hours. Please call the restaurant."),
        ]
    )
    agent = ReceptionAgent(llm, registry, backend, settings)
    result = agent.run_turn("cancel my booking", Conversation(), priya_session, memory)

    assert result.trace.steps[0].ok is False
    assert "within 2 hours" in result.reply
    assert backend.get_reservation(soon["id"])["status"] == "confirmed"


def test_upstream_unavailable_is_classified(call, ctx, monkeypatch):
    from agent.backend.errors import BackendError

    def boom(*_a: object, **_k: object) -> object:
        raise BackendError(503, "Cannot reach the restaurant API")

    monkeypatch.setattr(ctx.backend, "list_menu", boom)
    out = call("search_menu")
    assert out["ok"] is False
    assert out["error_code"] == ErrorCode.UPSTREAM_UNAVAILABLE.value

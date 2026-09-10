"""AgentService - identity, persistence, idempotency, memory refresh, streaming."""

from __future__ import annotations

import json

import pytest
from agent.llm.base import LLMMessage, ToolCall
from agent.llm.stub import ScriptedLLM
from agent.service import IdempotencyConflict, SessionNotFound


def _tc(name: str, **args: object) -> ToolCall:
    return ToolCall(id="1", name=name, arguments=json.dumps(args))


def test_start_session_creates_and_summarises(make_service):
    svc = make_service(ScriptedLLM([]))
    out = svc.start_session(phone="+91-9876543210", email=None, name=None)
    assert out["customer"]["name"] == "Priya Sharma"
    assert "outdoor" in out["memory"]
    assert svc.store.load(out["session_id"]) is not None


def test_run_message_persists_the_transcript(make_service):
    llm = ScriptedLLM([LLMMessage(content="Hi Priya!")])
    svc = make_service(llm)
    sid = svc.start_session(phone="+91-9876543210", email=None, name=None)["session_id"]

    out = svc.run_message(sid, "hello")
    assert out["reply"] == "Hi Priya!"

    _, conversation = svc.store.load(sid)
    roles = [m["role"] for m in conversation.messages]
    assert roles == ["user", "assistant"]


def test_idempotent_replay_returns_stored_response_without_rerunning(make_service):
    llm = ScriptedLLM([LLMMessage(content="first answer")])  # only ONE response scripted
    svc = make_service(llm)
    sid = svc.start_session(phone="+91-9876543210", email=None, name=None)["session_id"]

    first = svc.run_message(sid, "book me a table", client_message_id="abc")
    replay = svc.run_message(sid, "book me a table", client_message_id="abc")
    assert first == replay
    assert len(llm.calls) == 1  # the model was not consulted again


def test_idempotency_conflict_on_reused_id_with_new_body(make_service):
    svc = make_service(ScriptedLLM([LLMMessage(content="ok")]))
    sid = svc.start_session(phone="+91-9876543210", email=None, name=None)["session_id"]
    svc.run_message(sid, "message one", client_message_id="dup")
    with pytest.raises(IdempotencyConflict):
        svc.run_message(sid, "a different message", client_message_id="dup")


def test_unknown_session(make_service):
    svc = make_service(ScriptedLLM([]))
    with pytest.raises(SessionNotFound):
        svc.run_message("ghost", "hi")


def test_memory_refreshes_after_a_preference_is_saved(make_service, backend):
    llm = ScriptedLLM(
        [
            LLMMessage(tool_calls=[_tc("remember_preference", key="seating", value="indoor")]),
            LLMMessage(content="Saved - you're down for indoor from now on."),
        ]
    )
    svc = make_service(llm)
    sid = svc.start_session(phone="+91-9876543210", email=None, name=None)["session_id"]
    svc.run_message(sid, "always seat me indoors please")
    assert svc._memory(1).preferences["seating"] == "indoor"


def test_stream_message_emits_events_and_persists(make_service):
    llm = ScriptedLLM(
        [
            LLMMessage(tool_calls=[_tc("search_menu", category="dessert")]),
            LLMMessage(content="Gulab Jamun."),
        ]
    )
    svc = make_service(llm)
    sid = svc.start_session(phone="+91-9876543210", email=None, name=None)["session_id"]
    events = list(svc.stream_message(sid, "desserts?"))
    assert [e.type for e in events] == ["tool_call", "tool_result", "message", "done"]
    _, conversation = svc.store.load(sid)
    assert conversation.messages[-1]["content"] == "Gulab Jamun."

"""HTTP surface via FastAPI's TestClient, with a stubbed service."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from agent.api import app, get_service
from agent.llm.base import LLMMessage, ToolCall
from agent.llm.stub import ScriptedLLM
from fastapi.testclient import TestClient


@pytest.fixture
def client(make_service) -> Iterator[TestClient]:
    def _scripts() -> ScriptedLLM:
        return ScriptedLLM(
            [
                LLMMessage(
                    tool_calls=[ToolCall("1", "search_menu", json.dumps({"category": "dessert"}))]
                ),
                LLMMessage(content="We have Gulab Jamun."),
            ]
        )

    service = make_service(_scripts())
    app.dependency_overrides[get_service] = lambda: service
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_security_headers_are_present(client):
    r = client.get("/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy" in r.headers
    assert r.headers["x-request-id"]


def test_ready(client):
    body = client.get("/ready").json()
    assert body["ready"] is True
    assert body["checks"] == {"database": True, "backend": True}


def test_start_session_requires_contact(client):
    assert client.post("/sessions", json={}).status_code == 400


def test_full_conversation_over_http(client):
    start = client.post("/sessions", json={"phone": "+91-9876543210"}).json()
    sid = start["session_id"]
    assert "outdoor" in start["memory"]

    r = client.post(f"/sessions/{sid}/messages", json={"content": "any desserts?", "verbose": True})
    body = r.json()
    assert "Gulab Jamun" in body["reply"]
    assert body["tool_calls"] == ["search_menu"]
    assert "reasoning trace" in body["trace"]


def test_message_to_unknown_session_is_404(client):
    r = client.post("/sessions/nope/messages", json={"content": "hi"})
    assert r.status_code == 404


def test_sse_stream(client):
    sid = client.post("/sessions", json={"phone": "+91-9876543210"}).json()["session_id"]
    with client.stream(
        "POST", f"/sessions/{sid}/messages/stream", json={"content": "desserts?"}
    ) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        raw = "".join(r.iter_text())
    assert "event: tool_call" in raw
    assert "event: message" in raw
    assert "event: done" in raw

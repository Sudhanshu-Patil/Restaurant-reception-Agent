"""GroqClient - response parsing, retries, and error classification.

Uses httpx's MockTransport, so no network and no real key.
"""

from __future__ import annotations

import httpx
import pytest
from agent.llm.base import MalformedToolCall
from agent.llm.groq import GroqClient, GroqError


def _client(handler) -> GroqClient:
    transport = httpx.MockTransport(handler)
    return GroqClient("test-key", "test-model", client=httpx.Client(transport=transport))


def test_requires_an_api_key():
    with pytest.raises(GroqError):
        GroqClient("", "m")


def test_parses_a_plain_message():
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi there"}}]})

    msg = _client(handler).complete([{"role": "user", "content": "hi"}])
    assert msg.content == "hi there"
    assert msg.tool_calls == []


def test_parses_tool_calls():
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_x",
                                    "function": {
                                        "name": "search_menu",
                                        "arguments": '{"category":"main"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    msg = _client(handler).complete([], tools=[{"type": "function"}])
    assert msg.tool_calls[0].name == "search_menu"
    assert msg.tool_calls[0].arguments == '{"category":"main"}'


def test_retries_then_succeeds_on_429(monkeypatch):
    monkeypatch.setattr("agent.llm.groq.time.sleep", lambda _s: None)
    calls = {"n": 0}

    def handler(_req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, text="rate limited, try again in 0.1s")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    assert _client(handler).complete([]).content == "ok"
    assert calls["n"] == 2


def test_tool_use_failed_becomes_malformed_tool_call():
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"error": {"code": "tool_use_failed", "message": "bad tool args"}}
        )

    with pytest.raises(MalformedToolCall) as exc:
        _client(handler).complete([])
    assert "bad tool args" in exc.value.detail


def test_hard_error_raises_groq_error():
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    with pytest.raises(GroqError):
        _client(handler).complete([])

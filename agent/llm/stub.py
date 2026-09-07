"""A deterministic LLM double for tests — no network, fully scripted."""
from __future__ import annotations

from typing import Any

from agent.llm.base import LLMMessage


class ScriptedLLM:
    """Returns a pre-built queue of :class:`LLMMessage` objects, one per ``complete`` call.

    Every call is recorded in :attr:`calls` so tests can assert on what the agent
    sent (system prompt contents, tool specs, transcript growth).
    """

    def __init__(self, responses: list[LLMMessage | Exception]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMMessage:
        self.calls.append({"messages": messages, "tools": tools})
        if not self._responses:
            raise AssertionError("ScriptedLLM ran out of scripted responses")
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

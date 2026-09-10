"""Provider-agnostic LLM contract.

The rest of the agent depends only on :class:`LLMClient` and the small dataclasses
here — never on Groq, OpenAI, or httpx directly. Swapping providers or dropping in
a test double is a one-file change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class LLMError(RuntimeError):
    """Transport-level failure talking to the model provider."""


class MalformedToolCall(LLMError):
    """The provider rejected the model's own tool call as schema-invalid.

    Recoverable — the agent loop can feed the reason back and let the model retry.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(f"model emitted an invalid tool call: {detail}")
        self.detail = detail


@dataclass
class ToolCall:
    """A single tool call requested by the model."""

    id: str
    name: str
    arguments: str  # raw JSON string exactly as the model emitted it


@dataclass
class LLMMessage:
    """Normalised assistant turn returned by :meth:`LLMClient.complete`."""

    role: str = "assistant"
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMClient(Protocol):
    """Anything that can continue a chat given the transcript and available tools."""

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMMessage: ...

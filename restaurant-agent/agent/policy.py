"""Deterministic guardrails between the model's tool call and its execution.

Two independent checks, applied to every tool call before dispatch:

* **Confirmation** - a destructive tool (cancel / remove) is blocked unless the
  latest customer message either *asked* for the destructive action or *confirms*
  a previous request. The block is fed back to the model as a tool result, so the
  model naturally produces a "are you sure?" turn; the customer's "yes" on the
  next turn clears the check.
* **Booking precondition** - ``book_table`` is blocked until seating is resolved
  (an explicit indoor/outdoor/either in the conversation, or a stored preference).
  New guests are asked once; returning guests with a stored preference sail through.

The checks are pure functions of (tool call, conversation, memory) and are unit
tested without an LLM.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from agent.conversation import Conversation
from agent.llm.base import ToolCall
from agent.memory.customer_memory import CustomerMemory
from agent.tools.registry import DESTRUCTIVE_TOOLS
from agent.tools.result import ErrorCode, ToolResult

_AFFIRMATIVE = {
    "yes",
    "y",
    "yeah",
    "yep",
    "yup",
    "confirm",
    "confirmed",
    "ok",
    "okay",
    "sure",
    "do it",
    "go ahead",
    "please do",
    "proceed",
    "that's right",
    "correct",
}
_DESTRUCTIVE_INTENT = {
    "cancel",
    "cancelled",
    "cancelling",
    "remove",
    "delete",
    "drop",
    "call off",
    "scrap",
}
_GATED_BOOKING_TOOLS = {"book_table"}
_ANY_SEATING = {
    "either",
    "any",
    "anywhere",
    "no preference",
    "doesn't matter",
    "does not matter",
    "whatever",
}
_INDOOR = {"indoor", "inside"}
_OUTDOOR = {"outdoor", "outside", "patio", "al fresco"}

SEATING_QUESTION = "Would you like indoor or outdoor seating (or is either fine)?"


@dataclass(frozen=True)
class Decision:
    """The outcome of policy evaluation for one tool call."""

    allowed: bool
    blocked_result: ToolResult | None = None

    @classmethod
    def allow(cls) -> Decision:
        return cls(allowed=True)

    @classmethod
    def block(cls, result: ToolResult) -> Decision:
        return cls(allowed=False, blocked_result=result)


def _latest_user_text(conversation: Conversation) -> str:
    for message in reversed(conversation.messages):
        content = message.get("content")
        if message.get("role") == "user" and isinstance(content, str):
            return content.lower()
    return ""


def _all_user_text(conversation: Conversation) -> str:
    return " ".join(
        content.lower()
        for message in conversation.messages
        if message.get("role") == "user" and isinstance(content := message.get("content"), str)
    )


def _is_affirmative(text: str) -> bool:
    stripped = text.strip().rstrip("!.").strip()
    return stripped in _AFFIRMATIVE or any(
        stripped.startswith(f"{word} ") or stripped == word for word in _AFFIRMATIVE
    )


def _mentions_destructive_intent(text: str) -> bool:
    return any(word in text for word in _DESTRUCTIVE_INTENT)


def resolved_seating(conversation: Conversation, memory: CustomerMemory) -> str | None:
    """'indoor' / 'outdoor' / 'either' if the customer has settled it, else None."""
    text = _all_user_text(conversation)
    if any(phrase in text for phrase in _ANY_SEATING):
        return "either"
    if any(word in text for word in _OUTDOOR):
        return "outdoor"
    if any(word in text for word in _INDOOR):
        return "indoor"
    stored = memory.preferences.get("seating")
    if isinstance(stored, str) and stored in {"indoor", "outdoor"}:
        return stored
    return None


def _tool_args(call: ToolCall) -> dict[str, object]:
    try:
        parsed = json.loads(call.arguments or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def evaluate(call: ToolCall, conversation: Conversation, memory: CustomerMemory) -> Decision:
    """Return whether ``call`` may run now, or a blocking tool result if not."""

    # 1. Destructive actions need explicit intent or a confirmation.
    if call.name in DESTRUCTIVE_TOOLS:
        latest = _latest_user_text(conversation)
        if not (_mentions_destructive_intent(latest) or _is_affirmative(latest)):
            return Decision.block(
                ToolResult.failure(
                    ErrorCode.NEEDS_CONFIRMATION,
                    f"Do not call {call.name} yet. Ask the customer to confirm this "
                    "cancellation/removal in plain language first, then call it again "
                    "once they say yes.",
                )
            )

    # 2. Booking needs seating resolved (explicit choice or stored preference).
    if call.name in _GATED_BOOKING_TOOLS:
        args = _tool_args(call)
        if not args.get("location") and resolved_seating(conversation, memory) is None:
            return Decision.block(
                ToolResult.failure(
                    ErrorCode.NEEDS_CLARIFICATION,
                    f"Do not call {call.name} yet. Ask the customer: {SEATING_QUESTION} "
                    "Then call it again with their answer.",
                )
            )

    return Decision.allow()

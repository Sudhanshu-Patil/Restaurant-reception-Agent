"""In-conversation state — the ephemeral transcript for one chat session.

Deliberately separate from :mod:`agent.memory`, which holds durable, cross-session
knowledge about the customer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Session:
    """Who the agent is talking to, plus light per-session bookkeeping."""

    customer_id: int
    name: str
    phone: str | None = None
    email: str | None = None
    # Reservation created or last referenced this session, so the customer can say
    # "cancel that" or "add X to my booking" without repeating an id.
    active_reservation_id: int | None = None


@dataclass
class Conversation:
    """The message list replayed to the LLM every turn (OpenAI wire format)."""

    messages: list[dict[str, Any]] = field(default_factory=list)

    def add_user(self, content: str) -> None:
        self.messages.append({"role": "user", "content": content})

    def add_raw(self, message: dict[str, Any]) -> None:
        self.messages.append(message)

"""Turn events - the streamable unit of agent progress.

``ReceptionAgent.stream_turn`` yields these; ``run_turn`` drains them into a
:class:`~agent.trace.TurnResult`; the SSE endpoint forwards them to the browser.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

EventType = Literal["tool_call", "tool_result", "notice", "message", "done"]


@dataclass
class TurnEvent:
    type: EventType
    data: dict[str, Any] = field(default_factory=dict)

    def to_sse(self) -> str:
        return f"event: {self.type}\ndata: {json.dumps(self.data)}\n\n"

    # -- constructors ---------------------------------------------------
    @staticmethod
    def tool_call(name: str, arguments: dict[str, Any]) -> TurnEvent:
        return TurnEvent("tool_call", {"name": name, "arguments": arguments})

    @staticmethod
    def tool_result(name: str, *, ok: bool, message: str, error_code: str | None) -> TurnEvent:
        return TurnEvent(
            "tool_result",
            {"name": name, "ok": ok, "message": message, "error_code": error_code},
        )

    @staticmethod
    def notice(text: str) -> TurnEvent:
        return TurnEvent("notice", {"text": text})

    @staticmethod
    def message(content: str) -> TurnEvent:
        return TurnEvent("message", {"content": content})

    @staticmethod
    def done(stopped_reason: str, trace: str) -> TurnEvent:
        return TurnEvent("done", {"stopped_reason": stopped_reason, "trace": trace})

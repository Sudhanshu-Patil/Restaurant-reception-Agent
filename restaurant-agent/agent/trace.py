"""Reasoning trace - an auditable record of what the agent did in one turn."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field


@dataclass
class ToolInvocation:
    name: str
    arguments: str
    result: str
    ok: bool


@dataclass
class TurnTrace:
    """What the agent considered, called, observed and concluded for one turn."""

    user_message: str
    llm_rounds: int = 0
    tool_calls: int = 0
    mutations: int = 0
    steps: list[ToolInvocation] = field(default_factory=list)
    final_response: str = ""
    stopped_reason: str = "completed"  # completed | step_budget | tool_budget | mutation_budget

    def render(self) -> str:
        lines = [
            "-- reasoning trace --",
            f"user said     : {self.user_message}",
            f"llm rounds    : {self.llm_rounds}",
            f"tool calls    : {self.tool_calls} ({self.mutations} mutating)",
            f"stopped       : {self.stopped_reason}",
        ]
        if not self.steps:
            lines.append("tools called  : none (answered directly)")
        for i, step in enumerate(self.steps, 1):
            status = "ok " if step.ok else "ERR"
            lines.append(f"  [{status}] {i}. {step.name}({_compact(step.arguments)})")
            lines.append(f"         -> {_compact(step.result)}")
        lines.append(f"final reply   : {self.final_response}")
        return "\n".join(lines)


@dataclass
class TurnResult:
    """The outcome of one call to :meth:`ReceptionAgent.run_turn`."""

    reply: str
    trace: TurnTrace

    @property
    def stopped_reason(self) -> str:
        return self.trace.stopped_reason

    def __iter__(self) -> Iterator[object]:
        # Back-compat: ``reply, trace = agent.run_turn(...)`` keeps working.
        yield self.reply
        yield self.trace


def _compact(raw: str, limit: int = 300) -> str:
    try:
        raw = json.dumps(json.loads(raw), separators=(",", ":"))
    except (ValueError, TypeError):
        pass
    return raw if len(raw) <= limit else raw[:limit] + "..."

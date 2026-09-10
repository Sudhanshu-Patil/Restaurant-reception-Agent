"""Reasoning trace — an auditable record of what the agent did in one turn."""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class ToolInvocation:
    name: str
    arguments: str
    result: str
    ok: bool


@dataclass
class TurnTrace:
    """What the agent considered, called, observed and concluded for a single turn."""

    user_message: str
    llm_rounds: int = 0
    steps: list[ToolInvocation] = field(default_factory=list)
    final_response: str = ""

    def render(self) -> str:
        lines = [
            "── reasoning trace ──",
            f"user said     : {self.user_message}",
            f"llm rounds    : {self.llm_rounds}",
        ]
        if not self.steps:
            lines.append("tools called  : none (answered directly)")
        for i, step in enumerate(self.steps, 1):
            status = "ok " if step.ok else "ERR"
            lines.append(f"  [{status}] {i}. {step.name}({_compact(step.arguments)})")
            lines.append(f"         -> {_compact(step.result)}")
        lines.append(f"final reply   : {self.final_response}")
        return "\n".join(lines)


def _compact(raw: str, limit: int = 300) -> str:
    try:
        raw = json.dumps(json.loads(raw), separators=(",", ":"))
    except (ValueError, TypeError):
        pass
    return raw if len(raw) <= limit else raw[:limit] + "…"

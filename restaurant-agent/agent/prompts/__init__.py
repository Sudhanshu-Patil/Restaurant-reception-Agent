"""Versioned prompt text, loaded from ``.md`` files next to this module."""

from __future__ import annotations

from functools import cache
from pathlib import Path

_PROMPT_DIR = Path(__file__).parent


@cache
def _read(name: str) -> str:
    return (_PROMPT_DIR / name).read_text(encoding="utf-8").strip()


def render_system_prompt(*, name: str, customer_id: int, now: str, memory: str) -> str:
    """Fill the system prompt template with per-turn context."""
    return _read("system.md").format(name=name, customer_id=customer_id, now=now, memory=memory)

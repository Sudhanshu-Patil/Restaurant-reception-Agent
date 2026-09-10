"""Runtime configuration, loaded once from the environment / .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    """Immutable settings bundle passed explicitly to the components that need it."""

    groq_api_key: str
    model: str
    restaurant_api_url: str
    groq_base_url: str = "https://api.groq.com/openai/v1"
    request_timeout: float = 60.0

    # agent-loop budgets
    max_agent_steps: int = 8
    max_tool_calls: int = 12
    max_mutations: int = 6

    # session persistence
    db_path: Path = field(default_factory=lambda: Path("data/agent.db"))
    session_ttl_days: int = 30

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            groq_api_key=os.environ.get("GROQ_API_KEY", ""),
            model=os.environ.get("MODEL", "openai/gpt-oss-20b"),
            restaurant_api_url=os.environ.get("RESTAURANT_API_URL", "http://localhost:8000"),
            request_timeout=float(os.environ.get("REQUEST_TIMEOUT", "60")),
            max_agent_steps=_int("MAX_AGENT_STEPS", 8),
            max_tool_calls=_int("MAX_TOOL_CALLS", 12),
            max_mutations=_int("MAX_MUTATIONS", 6),
            db_path=Path(os.environ.get("AGENT_DB_PATH", "data/agent.db")),
            session_ttl_days=_int("SESSION_TTL_DAYS", 30),
        )

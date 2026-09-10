"""Runtime configuration, loaded once from the environment / .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    """Immutable settings bundle passed explicitly to the components that need it."""

    groq_api_key: str
    model: str
    restaurant_api_url: str
    groq_base_url: str = "https://api.groq.com/openai/v1"
    request_timeout: float = 60.0
    max_agent_steps: int = 8

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            groq_api_key=os.environ.get("GROQ_API_KEY", ""),
            model=os.environ.get("MODEL", "openai/gpt-oss-20b"),
            restaurant_api_url=os.environ.get("RESTAURANT_API_URL", "http://localhost:8000"),
        )

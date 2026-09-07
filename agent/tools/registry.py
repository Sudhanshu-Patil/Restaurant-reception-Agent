"""Tool registration, LLM-facing schema generation, and safe dispatch.

``dispatch`` is the trust boundary between the LLM and the backend: it never raises,
and turns every failure mode (unknown tool, bad JSON, bad arguments, backend error,
unexpected exception) into a JSON string with an ``error`` field the model can read
and react to.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Type

from pydantic import BaseModel, ValidationError

from agent.backend.errors import BackendError
from agent.tools.context import ToolContext

ToolFn = Callable[[BaseModel, ToolContext], dict]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args_model: Type[BaseModel]
    fn: ToolFn

    def openai_spec(self) -> dict[str, Any]:
        schema = self.args_model.model_json_schema()
        schema.pop("title", None)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": schema,
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(
        self, name: str, description: str, args_model: Type[BaseModel]
    ) -> Callable[[ToolFn], ToolFn]:
        """Decorator: attach a function to the registry under ``name``."""

        def decorator(fn: ToolFn) -> ToolFn:
            if name in self._tools:
                raise ValueError(f"tool {name!r} is already registered")
            self._tools[name] = Tool(name, description, args_model, fn)
            return fn

        return decorator

    # -- introspection ------------------------------------------------
    def specs(self) -> list[dict[str, Any]]:
        return [t.openai_spec() for t in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    # -- execution --------------------------------------------------
    def dispatch(self, name: str, raw_arguments: str, ctx: ToolContext) -> str:
        """Run tool ``name``. Always returns a JSON string; never raises."""
        tool = self._tools.get(name)
        if tool is None:
            return _error(
                f"Unknown tool {name!r}. Available: {', '.join(self._tools) or '(none)'}"
            )

        try:
            raw = json.loads(raw_arguments or "{}")
        except json.JSONDecodeError as exc:
            return _error(f"Arguments for {name!r} were not valid JSON ({exc}).")
        if not isinstance(raw, dict):
            return _error(f"Arguments for {name!r} must be a JSON object.")

        try:
            args = tool.args_model.model_validate(raw)
        except ValidationError as exc:
            return _error(f"Invalid arguments for {name!r}: {_format_validation(exc)}")

        try:
            result = tool.fn(args, ctx)
        except BackendError as exc:
            return json.dumps({"error": exc.detail, "status_code": exc.status_code})
        except Exception as exc:  # noqa: BLE001 — must not break the agent loop
            return _error(f"{type(exc).__name__}: {exc}")

        return json.dumps(result, default=str)


def _error(message: str) -> str:
    return json.dumps({"error": message})


def _format_validation(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
        for err in exc.errors()
    )

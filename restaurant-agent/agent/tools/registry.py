"""Tool registration, LLM-facing schema generation, and safe dispatch.

:meth:`ToolRegistry.dispatch` is the trust boundary between the model and the
backend. It never raises. Every failure mode - unknown tool, malformed JSON,
schema-invalid arguments, an upstream error, or an unexpected exception in the
tool - comes back as a :class:`~agent.tools.result.ToolResult` with ``ok=False``
and a typed ``error_code`` the model (and the tests) can branch on.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from agent.backend.errors import BackendError
from agent.tools.context import ToolContext
from agent.tools.result import ErrorCode, ToolResult

M = TypeVar("M", bound=BaseModel)

ToolFn = Callable[[Any, ToolContext], ToolResult]

#: tools that change state upstream - the agent loop budgets these separately.
MUTATING_TOOLS: frozenset[str] = frozenset(
    {
        "create_reservation",
        "book_table",
        "change_reservation",
        "cancel_reservation",
        "add_items_to_reservation",
        "remove_order_item",
        "remember_preference",
    }
)

#: destructive tools - need explicit user intent or a confirmation before running.
DESTRUCTIVE_TOOLS: frozenset[str] = frozenset({"cancel_reservation", "remove_order_item"})


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
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
        self, name: str, description: str, args_model: type[M]
    ) -> Callable[[Callable[[M, ToolContext], ToolResult]], Callable[[M, ToolContext], ToolResult]]:
        """Decorator: attach a function to the registry under ``name``."""

        def decorator(
            fn: Callable[[M, ToolContext], ToolResult],
        ) -> Callable[[M, ToolContext], ToolResult]:
            if name in self._tools:
                raise ValueError(f"tool {name!r} is already registered")
            self._tools[name] = Tool(name, description, args_model, fn)
            return fn

        return decorator

    # -- introspection -----------------------------------------------------
    def specs(self) -> list[dict[str, Any]]:
        return [t.openai_spec() for t in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    # -- execution -------------------------------------------------------
    def dispatch(self, name: str, raw_arguments: str, ctx: ToolContext) -> ToolResult:
        """Run tool ``name``. Always returns a :class:`ToolResult`; never raises."""
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult.failure(
                ErrorCode.UNKNOWN_TOOL,
                f"Unknown tool {name!r}. Available: {', '.join(self._tools) or '(none)'}",
            )

        try:
            raw = json.loads(raw_arguments or "{}")
        except json.JSONDecodeError as exc:
            return ToolResult.failure(
                ErrorCode.BAD_JSON, f"Arguments for {name!r} were not valid JSON ({exc})."
            )
        if not isinstance(raw, dict):
            return ToolResult.failure(
                ErrorCode.BAD_ARGUMENTS, f"Arguments for {name!r} must be a JSON object."
            )

        try:
            args = tool.args_model.model_validate(raw)
        except ValidationError as exc:
            return ToolResult.failure(
                ErrorCode.BAD_ARGUMENTS,
                f"Invalid arguments for {name!r}: {_format_validation(exc)}",
            )

        try:
            return tool.fn(args, ctx)
        except BackendError as exc:
            code = (
                ErrorCode.UPSTREAM_UNAVAILABLE
                if exc.status_code >= 502
                else ErrorCode.UPSTREAM_ERROR
            )
            return ToolResult.failure(code, exc.detail, http_status=exc.status_code)
        except Exception as exc:
            return ToolResult.failure(ErrorCode.INTERNAL_ERROR, f"{type(exc).__name__}: {exc}")


def _format_validation(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}" for err in exc.errors()
    )

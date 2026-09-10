"""The agent loop: turn a customer message into a reply by calling tools.

Kept out of here on purpose: HTTP (``backend``), model transport (``llm``),
argument validation and error shaping (``registry``), durable customer state
(``memory``), and the confirmation / booking guardrails (``policy``). This module
only orchestrates - and it does so as a stream of :class:`~agent.events.TurnEvent`
so the same loop drives both the blocking API and the SSE endpoint.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Any

from agent.backend.protocol import BackendClient
from agent.config import Settings
from agent.conversation import Conversation, Session
from agent.events import TurnEvent
from agent.llm.base import LLMClient, LLMMessage, MalformedToolCall, ToolCall
from agent.memory.customer_memory import CustomerMemory
from agent.policy import Decision
from agent.policy import evaluate as default_policy
from agent.prompts import render_system_prompt
from agent.tools.context import ToolContext
from agent.tools.registry import MUTATING_TOOLS, ToolRegistry
from agent.trace import ToolInvocation, TurnResult, TurnTrace

Policy = Callable[[ToolCall, Conversation, CustomerMemory], Decision]

_STEP_BUDGET_MSG = (
    "Sorry - I couldn't finish that within a safe number of steps. "
    "Could you rephrase or break it into parts?"
)
_TOOL_BUDGET_MSG = (
    "I stopped before making more calls - this request went past the safe action limit."
)
_MUTATION_BUDGET_MSG = (
    "I stopped before changing anything else - this request went past the safe change limit."
)


class ReceptionAgent:
    def __init__(
        self,
        llm: LLMClient,
        registry: ToolRegistry,
        backend: BackendClient,
        settings: Settings,
        *,
        policy: Policy = default_policy,
    ) -> None:
        self._llm = llm
        self._registry = registry
        self._backend = backend
        self._settings = settings
        self._policy = policy

    # -- public API -------------------------------------------------------
    def turn(
        self,
        user_message: str,
        conversation: Conversation,
        session: Session,
        memory: CustomerMemory,
    ) -> TurnRunner:
        """A per-turn runner. Call ``.events()`` (stream) or hand it to ``run_turn``."""
        return TurnRunner(self, user_message, conversation, session, memory)

    def run_turn(
        self,
        user_message: str,
        conversation: Conversation,
        session: Session,
        memory: CustomerMemory,
    ) -> TurnResult:
        """Process one message, blocking until done."""
        runner = self.turn(user_message, conversation, session, memory)
        for _ in runner.events():
            pass
        return TurnResult(runner.reply, runner.trace)

    def stream_turn(
        self,
        user_message: str,
        conversation: Conversation,
        session: Session,
        memory: CustomerMemory,
    ) -> Iterator[TurnEvent]:
        yield from self.turn(user_message, conversation, session, memory).events()

    # -- internals -----------------------------------------------------
    def _system_prompt(self, session: Session, memory: CustomerMemory) -> str:
        return render_system_prompt(
            name=session.name,
            customer_id=session.customer_id,
            now=datetime.now().strftime("%A %Y-%m-%d %H:%M"),
            memory=memory.summary(),
        )


class TurnRunner:
    """Runs a single turn and exposes it as a stream of events plus a final trace."""

    def __init__(
        self,
        agent: ReceptionAgent,
        user_message: str,
        conversation: Conversation,
        session: Session,
        memory: CustomerMemory,
    ) -> None:
        self._agent = agent
        self._user_message = user_message
        self._conversation = conversation
        self._session = session
        self._memory = memory
        self.trace = TurnTrace(user_message=user_message)
        self.reply = ""

    def events(self) -> Iterator[TurnEvent]:
        agent = self._agent
        settings = agent._settings
        trace = self.trace
        ctx = ToolContext(backend=agent._backend, session=self._session, memory=self._memory)

        self._conversation.add_user(self._user_message)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": agent._system_prompt(self._session, self._memory)},
            *self._conversation.messages,
        ]
        specs = agent._registry.specs()

        while trace.llm_rounds < settings.max_agent_steps:
            trace.llm_rounds += 1
            try:
                reply = agent._llm.complete(messages, specs)
            except MalformedToolCall as exc:
                yield self._recover_from_malformed_call(messages, exc)
                continue

            assistant_msg = _assistant_to_dict(reply)
            messages.append(assistant_msg)
            self._conversation.add_raw(assistant_msg)

            if not reply.tool_calls:
                yield from self._finish(reply.content or "", "completed")
                return

            for call in reply.tool_calls:
                budget_stop = self._budget_check(call)
                if budget_stop is not None:
                    reason, text = budget_stop
                    yield from self._finish(text, reason)
                    return
                yield from self._run_tool_call(call, ctx, messages)

        yield from self._finish(_STEP_BUDGET_MSG, "step_budget")

    # -- steps ------------------------------------------------------
    def _budget_check(self, call: ToolCall) -> tuple[str, str] | None:
        trace = self.trace
        settings = self._agent._settings
        if trace.tool_calls >= settings.max_tool_calls:
            return "tool_budget", _TOOL_BUDGET_MSG
        if call.name in MUTATING_TOOLS and trace.mutations >= settings.max_mutations:
            return "mutation_budget", _MUTATION_BUDGET_MSG
        return None

    def _run_tool_call(
        self, call: ToolCall, ctx: ToolContext, messages: list[dict[str, Any]]
    ) -> Iterator[TurnEvent]:
        yield TurnEvent.tool_call(call.name, _safe_json(call.arguments))

        decision = self._agent._policy(call, self._conversation, self._memory)
        if not decision.allowed and decision.blocked_result is not None:
            result = decision.blocked_result
        else:
            self.trace.tool_calls += 1
            if call.name in MUTATING_TOOLS:
                self.trace.mutations += 1
            result = self._agent._registry.dispatch(call.name, call.arguments, ctx)

        payload = result.to_json()
        tool_msg = {"role": "tool", "tool_call_id": call.id, "content": payload}
        messages.append(tool_msg)
        self._conversation.add_raw(tool_msg)
        self.trace.steps.append(ToolInvocation(call.name, call.arguments, payload, ok=result.ok))
        code = result.error_code if isinstance(result.error_code, str) else None
        yield TurnEvent.tool_result(
            call.name, ok=result.ok, message=result.message, error_code=code
        )

    def _recover_from_malformed_call(
        self, messages: list[dict[str, Any]], exc: MalformedToolCall
    ) -> TurnEvent:
        correction = {
            "role": "system",
            "content": (
                "Your last tool call was rejected as schema-invalid: "
                f"{exc.detail}. Re-issue it using exactly the documented argument names."
            ),
        }
        messages.append(correction)
        self._conversation.add_raw(correction)
        self.trace.steps.append(ToolInvocation("(rejected tool call)", "", exc.detail, ok=False))
        return TurnEvent.notice(
            f"Provider rejected a tool call ({exc.detail}); asking the model to retry."
        )

    def _finish(self, text: str, reason: str) -> Iterator[TurnEvent]:
        self.reply = text
        self.trace.final_response = text
        self.trace.stopped_reason = reason
        yield TurnEvent.message(text)
        yield TurnEvent.done(reason, self.trace.render())


def _assistant_to_dict(msg: LLMMessage) -> dict[str, Any]:
    out: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
    if msg.tool_calls:
        out["tool_calls"] = [
            {
                "id": c.id,
                "type": "function",
                "function": {"name": c.name, "arguments": c.arguments},
            }
            for c in msg.tool_calls
        ]
    return out


def _safe_json(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}

"""The agent loop: turn a customer message into a reply by calling tools.

Responsibilities kept out of here on purpose: HTTP (``backend``), model transport
(``llm``), argument validation and error shaping (``registry``), durable state
(``memory``). This module only orchestrates.
"""
from __future__ import annotations

import json
from datetime import datetime

from agent.config import Settings
from agent.conversation import Conversation, Session
from agent.llm.base import LLMClient, LLMMessage, MalformedToolCall
from agent.memory.customer_memory import CustomerMemory
from agent.tools.context import ToolContext
from agent.tools.registry import ToolRegistry
from agent.trace import ToolInvocation, TurnTrace

SYSTEM_PROMPT = """\
You are the reception agent for a restaurant. You help customers book tables, manage \
their orders, and answer menu questions, by calling the provided tools.

You are already speaking with {name} (customer #{customer_id}). They are identified \
and logged in. NEVER ask them for a phone number or email, and never say you can't \
find their record — every booking and order tool already acts on their account.

Rules:
- Reservation slots are 30-minute increments from 12:00 to 22:30 inclusive.
- Never invent table ids, reservation ids, menu items, prices, or availability. Get \
them from a tool.
- One request may need several tool calls in sequence (check availability, then book, \
then add items).
- Do exactly what was asked: if the customer asks you to *check* availability, report \
what you found and wait — don't book until they say to.
- To move or modify an existing booking (time, party size, seating), use \
change_reservation. Never make a second reservation to "switch" something. If a change \
is refused (e.g. within 2 hours of the slot), tell the customer their original \
booking still stands and offer alternatives.
- Interpret times yourself; do not interrogate the customer for an exact slot. \
"tonight"/"this evening" means today; "around 8" in an evening context means 20:00. \
Pass the time straight to the tools — they accept phrases like "today 8pm" or ISO and \
snap to the nearest valid slot. Call get_current_datetime if you need today's date. \
Only ask for the time if the customer gave none at all.
- Only ask for genuinely missing details (e.g. party size if never stated).
- When a tool result contains an "error" field, explain the problem to the customer \
plainly and suggest a fix. Do not silently retry.
- Always respect the customer's stored allergies and dietary preferences.
- Keep replies warm and concise.

Right now it is {now}.

What we remember about {name}:
{memory}
"""


class ReceptionAgent:
    def __init__(
        self,
        llm: LLMClient,
        registry: ToolRegistry,
        backend,
        settings: Settings,
    ) -> None:
        self._llm = llm
        self._registry = registry
        self._backend = backend
        self._settings = settings

    def run_turn(
        self,
        user_message: str,
        conversation: Conversation,
        session: Session,
        memory: CustomerMemory,
    ) -> tuple[str, TurnTrace]:
        """Process one customer message; return (reply, trace)."""
        trace = TurnTrace(user_message=user_message)
        ctx = ToolContext(backend=self._backend, session=session, memory=memory)

        conversation.add_user(user_message)
        messages = [
            {"role": "system", "content": _system_prompt(session, memory)},
            *conversation.messages,
        ]
        specs = self._registry.specs()

        for _ in range(self._settings.max_agent_steps):
            trace.llm_rounds += 1
            try:
                reply = self._llm.complete(messages, specs)
            except MalformedToolCall as exc:
                correction = {
                    "role": "system",
                    "content": (
                        "Your last tool call was rejected as invalid: "
                        f"{exc.detail}. Re-issue it using exactly the documented "
                        "argument names and types."
                    ),
                }
                messages.append(correction)
                conversation.add_raw(correction)
                trace.steps.append(
                    ToolInvocation(
                        name="(rejected tool call)",
                        arguments="",
                        result=exc.detail,
                        ok=False,
                    )
                )
                continue

            assistant_msg = _assistant_to_dict(reply)
            messages.append(assistant_msg)
            conversation.add_raw(assistant_msg)

            if not reply.tool_calls:
                trace.final_response = reply.content or ""
                return trace.final_response, trace

            for call in reply.tool_calls:
                result = self._registry.dispatch(call.name, call.arguments, ctx)
                tool_msg = {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": result,
                }
                messages.append(tool_msg)
                conversation.add_raw(tool_msg)
                trace.steps.append(
                    ToolInvocation(
                        name=call.name,
                        arguments=call.arguments,
                        result=result,
                        ok=not _is_error(result),
                    )
                )

        fallback = (
            "Sorry — I couldn't finish that. Could you rephrase or break it into steps?"
        )
        trace.final_response = fallback
        return fallback, trace


def _system_prompt(session: Session, memory: CustomerMemory) -> str:
    return SYSTEM_PROMPT.format(
        now=datetime.now().strftime("%A %Y-%m-%d %H:%M"),
        name=session.name,
        customer_id=session.customer_id,
        memory=memory.summary(),
    )


def _assistant_to_dict(msg: LLMMessage) -> dict:
    out: dict = {"role": "assistant", "content": msg.content or ""}
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


def _is_error(tool_result: str) -> bool:
    try:
        return isinstance(json.loads(tool_result), dict) and "error" in json.loads(
            tool_result
        )
    except (ValueError, TypeError):
        return False

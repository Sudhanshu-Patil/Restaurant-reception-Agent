"""Interactive CLI. Identify once, then converse freely.

    python -m agent.cli --phone +91-9876543210
    python -m agent.cli --email someone@example.com --verbose
"""
from __future__ import annotations

import argparse
import sys

from agent.agent import ReceptionAgent
from agent.backend.client import RestaurantClient
from agent.config import Settings
from agent.conversation import Conversation
from agent.identity import NewCustomerNeedsName, resolve_customer
from agent.llm.groq import GroqClient
from agent.memory.customer_memory import CustomerMemory
from agent.tools.catalog import registry

_QUIT = {"quit", "exit", "bye", ":q"}


def _prompt(text: str) -> str:
    try:
        return input(text).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Restaurant reception agent (CLI)")
    parser.add_argument("--phone")
    parser.add_argument("--email")
    parser.add_argument("--name", help="Only needed when creating a new customer")
    parser.add_argument(
        "--verbose", action="store_true", help="Print the reasoning trace each turn"
    )
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    backend = RestaurantClient(settings.restaurant_api_url, timeout=settings.request_timeout)

    phone, email, name = args.phone, args.email, args.name
    if not phone and not email:
        print("Welcome! Identify yourself so I can pull up your details.")
        phone = _prompt("Phone (Enter to skip): ") or None
        if not phone:
            email = _prompt("Email: ") or None
    if not phone and not email:
        print("I need a phone or email to continue.")
        return

    try:
        session = resolve_customer(backend, phone=phone, email=email, name=name)
    except NewCustomerNeedsName:
        name = _prompt("Looks like you're new — what's your name? ")
        session = resolve_customer(backend, phone=phone, email=email, name=name)

    memory = CustomerMemory(backend, session.customer_id)
    memory.load()

    llm = GroqClient(
        settings.groq_api_key,
        settings.model,
        settings.groq_base_url,
        settings.request_timeout,
    )
    agent = ReceptionAgent(llm, registry, backend, settings)
    conversation = Conversation()

    print(f"\nHi {session.name}! How can I help you today? (type 'quit' to leave)\n")
    while True:
        user = _prompt("you   > ")
        if not user:
            continue
        if user.lower() in _QUIT:
            print("agent > Thanks for visiting — see you soon!")
            break
        reply, trace = agent.run_turn(user, conversation, session, memory)
        print(f"agent > {reply}\n")
        if args.verbose:
            print(trace.render())
            print()

    backend.close()


if __name__ == "__main__":
    main()

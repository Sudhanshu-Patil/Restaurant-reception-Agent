"""Resolve a customer (phone/email) to a :class:`Session`, creating them if new."""
from __future__ import annotations

from agent.backend.client import RestaurantClient
from agent.backend.errors import BackendError
from agent.conversation import Session


class NewCustomerNeedsName(Exception):
    """Raised when a lookup misses and we have no name to create the customer with."""


def resolve_customer(
    backend: RestaurantClient,
    *,
    phone: str | None = None,
    email: str | None = None,
    name: str | None = None,
) -> Session:
    if not phone and not email:
        raise ValueError("A phone or email is required to identify the customer.")

    try:
        customer = backend.lookup_customer(phone=phone, email=email)
    except BackendError as exc:
        if exc.status_code != 404:
            raise
        if not name:
            raise NewCustomerNeedsName from exc
        customer = backend.create_customer(name=name, phone=phone, email=email)

    return Session(
        customer_id=customer["id"],
        name=customer["name"],
        phone=customer.get("phone"),
        email=customer.get("email"),
    )

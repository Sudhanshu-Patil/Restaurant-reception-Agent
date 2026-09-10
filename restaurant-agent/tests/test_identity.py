"""Customer resolution / creation."""

from __future__ import annotations

import pytest
from agent.backend.errors import BackendError
from agent.identity import NewCustomerNeedsName, resolve_customer


def test_resolves_an_existing_customer(backend):
    session = resolve_customer(backend, phone="+91-9876543210")
    assert session.customer_id == 1
    assert session.name == "Priya Sharma"


def test_creates_a_new_customer_when_a_name_is_given(backend):
    session = resolve_customer(backend, email="new@guest.io", name="New Guest")
    assert session.name == "New Guest"
    assert backend.get_customer(session.customer_id)["email"] == "new@guest.io"


def test_new_customer_without_a_name_raises(backend):
    with pytest.raises(NewCustomerNeedsName):
        resolve_customer(backend, phone="+91-0000000000")


def test_requires_a_contact_detail(backend):
    with pytest.raises(ValueError):
        resolve_customer(backend)


def test_non_404_backend_errors_propagate(backend):
    def boom(**_kw: object) -> object:
        raise BackendError(500, "backend on fire")

    backend.lookup_customer = boom  # type: ignore[method-assign]
    with pytest.raises(BackendError):
        resolve_customer(backend, phone="+91-9876543210")

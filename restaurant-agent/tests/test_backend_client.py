"""RestaurantClient - request shaping and error mapping, via httpx MockTransport."""

from __future__ import annotations

from datetime import datetime

import httpx
import pytest
from agent.backend.client import RestaurantClient
from agent.backend.errors import BackendError


def _client(handler) -> RestaurantClient:
    transport = httpx.MockTransport(handler)
    return RestaurantClient(
        "http://backend", client=httpx.Client(base_url="http://backend", transport=transport)
    )


def test_get_menu_forwards_filters_as_query_params():
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(req.url.params)
        return httpx.Response(200, json=[{"id": 1, "name": "Roti"}])

    items = _client(handler).list_menu(category="main", max_price=200, available_only=True)
    assert items[0]["name"] == "Roti"
    assert seen["category"] == "main"
    assert seen["max_price"] == "200"


def test_check_availability_sends_iso_datetime():
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(req.url.params)
        return httpx.Response(200, json={"available_tables": []})

    _client(handler).check_availability(datetime(2026, 9, 8, 19, 0), 2, location="outdoor")
    assert seen["slot_datetime"] == "2026-09-08T19:00:00"
    assert seen["party_size"] == "2"


def test_204_returns_none():
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    assert _client(handler).remove_order_item(5) is None


def test_error_response_becomes_backend_error_with_detail():
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "Table 7 is already booked"})

    with pytest.raises(BackendError) as exc:
        _client(handler).cancel_reservation(3)
    assert exc.value.status_code == 409
    assert "already booked" in exc.value.detail


def test_validation_error_list_is_flattened():
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422, json={"detail": [{"loc": ["body", "party_size"], "msg": "must be > 0"}]}
        )

    with pytest.raises(BackendError) as exc:
        _client(handler).create_reservation(1, 1, datetime(2026, 9, 8, 19, 0), 0)
    assert "party_size" in exc.value.detail


def test_network_failure_becomes_503():
    def handler(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    with pytest.raises(BackendError) as exc:
        _client(handler).list_tables()
    assert exc.value.status_code == 503

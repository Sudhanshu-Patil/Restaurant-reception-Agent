"""Typed client for the restaurant-api backend.

One method per endpoint. Every non-2xx response becomes a :class:`BackendError`.
Nothing above this layer knows about HTTP, URLs, or status codes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from agent.backend.errors import BackendError

JSON = Any


def _drop_none(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


def _extract_detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text or resp.reason_phrase
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, list):  # FastAPI validation errors
        return "; ".join(
            f"{'.'.join(str(p) for p in e.get('loc', []))}: {e.get('msg', '')}" for e in detail
        )
    return str(detail) if detail is not None else resp.text


class RestaurantClient:
    """Thin, synchronous wrapper around the restaurant API."""

    def __init__(
        self,
        base_url: str,
        timeout: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(base_url=self._base_url, timeout=timeout)

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> RestaurantClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- core ------------------------------------------------------------
    def _request(self, method: str, path: str, **kwargs: Any) -> JSON:
        try:
            resp = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise BackendError(503, f"Cannot reach the restaurant API: {exc}") from exc
        if resp.status_code >= 400:
            raise BackendError(resp.status_code, _extract_detail(resp))
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    # -- tables --------------------------------------------------------
    def list_tables(self, location: str | None = None, min_capacity: int | None = None) -> JSON:
        return self._request(
            "GET",
            "/tables",
            params=_drop_none({"location": location, "min_capacity": min_capacity}),
        )

    # -- menu --------------------------------------------------------
    def list_menu(
        self,
        category: str | None = None,
        tags_any: list[str] | None = None,
        tags_all: list[str] | None = None,
        exclude_tags: list[str] | None = None,
        max_price: float | None = None,
        available_only: bool = True,
    ) -> JSON:
        return self._request(
            "GET",
            "/menu",
            params=_drop_none(
                {
                    "category": category,
                    "tags_any": tags_any,
                    "tags_all": tags_all,
                    "exclude_tags": exclude_tags,
                    "max_price": max_price,
                    "available_only": available_only,
                }
            ),
        )

    # -- availability -------------------------------------------------
    def check_availability(
        self, slot_datetime: datetime, party_size: int, location: str | None = None
    ) -> JSON:
        return self._request(
            "GET",
            "/availability",
            params=_drop_none(
                {
                    "slot_datetime": slot_datetime.isoformat(),
                    "party_size": party_size,
                    "location": location,
                }
            ),
        )

    # -- customers --------------------------------------------------
    def lookup_customer(self, phone: str | None = None, email: str | None = None) -> JSON:
        return self._request(
            "GET",
            "/customers/lookup",
            params=_drop_none({"phone": phone, "email": email}),
        )

    def create_customer(
        self,
        name: str,
        phone: str | None = None,
        email: str | None = None,
        preferences: dict | None = None,
    ) -> JSON:
        body = _drop_none({"name": name, "phone": phone, "email": email})
        body["preferences"] = preferences or {}
        return self._request("POST", "/customers", json=body)

    def get_customer(self, customer_id: int) -> JSON:
        return self._request("GET", f"/customers/{customer_id}")

    def update_preferences(self, customer_id: int, preferences: dict) -> JSON:
        return self._request(
            "PATCH",
            f"/customers/{customer_id}/preferences",
            json={"preferences": preferences},
        )

    def list_customer_reservations(self, customer_id: int, status: str | None = None) -> JSON:
        return self._request(
            "GET",
            f"/customers/{customer_id}/reservations",
            params=_drop_none({"status": status}),
        )

    def list_customer_orders(self, customer_id: int) -> JSON:
        return self._request("GET", f"/customers/{customer_id}/orders")

    # -- reservations ----------------------------------------------
    def create_reservation(
        self,
        customer_id: int,
        table_id: int,
        slot_datetime: datetime,
        party_size: int,
        special_requests: str | None = None,
    ) -> JSON:
        return self._request(
            "POST",
            "/reservations",
            json=_drop_none(
                {
                    "customer_id": customer_id,
                    "table_id": table_id,
                    "slot_datetime": slot_datetime.isoformat(),
                    "party_size": party_size,
                    "special_requests": special_requests,
                }
            ),
        )

    def get_reservation(self, reservation_id: int) -> JSON:
        return self._request("GET", f"/reservations/{reservation_id}")

    def cancel_reservation(self, reservation_id: int) -> JSON:
        return self._request("DELETE", f"/reservations/{reservation_id}")

    # -- orders ---------------------------------------------------
    def add_order_item(self, reservation_id: int, menu_item_id: int, quantity: int = 1) -> JSON:
        return self._request(
            "POST",
            f"/reservations/{reservation_id}/orders",
            json={"menu_item_id": menu_item_id, "quantity": quantity},
        )

    def list_order_items(self, reservation_id: int) -> JSON:
        return self._request("GET", f"/reservations/{reservation_id}/orders")

    def remove_order_item(self, order_id: int) -> None:
        self._request("DELETE", f"/orders/{order_id}")

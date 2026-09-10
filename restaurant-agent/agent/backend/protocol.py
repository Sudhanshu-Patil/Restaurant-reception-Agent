"""The backend surface the agent depends on.

The agent, tools and memory type-check against this Protocol, not against the
concrete :class:`~agent.backend.client.RestaurantClient`. The in-memory test
double satisfies it structurally, so tests need no HTTP and no Docker.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

Json = dict[str, Any]


class BackendClient(Protocol):
    # menu / tables
    def list_menu(
        self,
        category: str | None = ...,
        tags_any: list[str] | None = ...,
        tags_all: list[str] | None = ...,
        exclude_tags: list[str] | None = ...,
        max_price: float | None = ...,
        available_only: bool = ...,
    ) -> list[Json]: ...

    def list_tables(
        self, location: str | None = ..., min_capacity: int | None = ...
    ) -> list[Json]: ...

    # availability
    def check_availability(
        self, slot_datetime: datetime, party_size: int, location: str | None = ...
    ) -> Json: ...

    # customers
    def lookup_customer(self, phone: str | None = ..., email: str | None = ...) -> Json: ...

    def create_customer(
        self,
        name: str,
        phone: str | None = ...,
        email: str | None = ...,
        preferences: Json | None = ...,
    ) -> Json: ...

    def get_customer(self, customer_id: int) -> Json: ...

    def update_preferences(self, customer_id: int, preferences: Json) -> Json: ...

    def list_customer_reservations(
        self, customer_id: int, status: str | None = ...
    ) -> list[Json]: ...

    def list_customer_orders(self, customer_id: int) -> list[Json]: ...

    # reservations
    def create_reservation(
        self,
        customer_id: int,
        table_id: int,
        slot_datetime: datetime,
        party_size: int,
        special_requests: str | None = ...,
    ) -> Json: ...

    def get_reservation(self, reservation_id: int) -> Json: ...

    def cancel_reservation(self, reservation_id: int) -> Json: ...

    # orders
    def add_order_item(
        self, reservation_id: int, menu_item_id: int, quantity: int = ...
    ) -> Json: ...

    def list_order_items(self, reservation_id: int) -> list[Json]: ...

    def remove_order_item(self, order_id: int) -> None: ...

"""In-memory stand-in for the restaurant API, with just enough rule enforcement
to exercise the agent without Docker or a network."""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta

from agent.backend.errors import BackendError

_MENU = [
    {
        "id": 1,
        "name": "Paneer Tikka",
        "description": "",
        "price": 350,
        "tags": ["vegetarian", "contains-dairy"],
        "category": "starter",
        "available": True,
    },
    {
        "id": 2,
        "name": "Hara Bhara Kabab",
        "description": "",
        "price": 300,
        "tags": ["vegetarian", "vegan"],
        "category": "starter",
        "available": True,
    },
    {
        "id": 3,
        "name": "Samosa",
        "description": "",
        "price": 150,
        "tags": ["vegetarian", "vegan"],
        "category": "starter",
        "available": True,
    },
    {
        "id": 4,
        "name": "Dal Makhani",
        "description": "",
        "price": 400,
        "tags": ["vegetarian", "contains-dairy"],
        "category": "main",
        "available": True,
    },
    {
        "id": 5,
        "name": "Butter Chicken",
        "description": "",
        "price": 550,
        "tags": ["contains-nuts", "contains-dairy"],
        "category": "main",
        "available": True,
    },
    {
        "id": 6,
        "name": "Kulfi",
        "description": "",
        "price": 250,
        "tags": ["vegetarian", "contains-nuts", "contains-dairy"],
        "category": "dessert",
        "available": False,
    },
    {
        "id": 7,
        "name": "Gulab Jamun",
        "description": "",
        "price": 200,
        "tags": ["vegetarian", "contains-dairy"],
        "category": "dessert",
        "available": True,
    },
]

_TABLES = [
    {"id": 1, "table_number": 1, "capacity": 2, "location": "indoor"},
    {"id": 2, "table_number": 3, "capacity": 4, "location": "indoor"},
    {"id": 3, "table_number": 5, "capacity": 6, "location": "indoor"},
    {"id": 4, "table_number": 6, "capacity": 2, "location": "outdoor"},
    {"id": 5, "table_number": 7, "capacity": 4, "location": "outdoor"},
    {"id": 6, "table_number": 8, "capacity": 8, "location": "outdoor"},
]


class FakeBackend:
    """Mirrors the subset of :class:`RestaurantClient` the agent uses."""

    def __init__(self) -> None:
        self.menu = [dict(m) for m in _MENU]
        self.tables = [dict(t) for t in _TABLES]
        self.customers: dict[int, dict] = {
            1: {
                "id": 1,
                "name": "Priya Sharma",
                "phone": "+91-9876543210",
                "email": "priya.sharma@example.com",
                "preferences": {"seating": "outdoor", "dietary": ["vegetarian"]},
                "created_at": "2025-01-01T00:00:00",
            }
        }
        self.reservations: dict[int, dict] = {}
        self.orders: dict[int, dict] = {}
        self._cust_ids = itertools.count(2)
        self._res_ids = itertools.count(1)
        self._ord_ids = itertools.count(1)
        # a completed historical reservation + orders for Priya
        past = next(self._res_ids)
        self.reservations[past] = {
            "id": past,
            "customer_id": 1,
            "table_id": 5,
            "slot_datetime": (datetime.now() - timedelta(days=14))
            .replace(hour=20, minute=0, second=0, microsecond=0)
            .isoformat(),
            "party_size": 3,
            "special_requests": None,
            "status": "completed",
            "created_at": "2025-01-01T00:00:00",
        }
        for mid, qty in [(1, 1), (4, 2)]:
            oid = next(self._ord_ids)
            self.orders[oid] = {
                "id": oid,
                "reservation_id": past,
                "menu_item_id": mid,
                "quantity": qty,
                "created_at": "2025-01-01T00:00:00",
            }

    # -- menu / tables ------------------------------------------------
    def list_menu(
        self,
        category=None,
        tags_any=None,
        tags_all=None,
        exclude_tags=None,
        max_price=None,
        available_only=True,
    ):
        out = []
        for m in self.menu:
            if available_only and not m["available"]:
                continue
            if category and m["category"] != category:
                continue
            if max_price is not None and m["price"] > max_price:
                continue
            tags = set(m["tags"])
            if tags_all and not set(tags_all).issubset(tags):
                continue
            if tags_any and not (tags & set(tags_any)):
                continue
            if exclude_tags and (tags & set(exclude_tags)):
                continue
            out.append(dict(m))
        return out

    def list_tables(self, location=None, min_capacity=None):
        return [
            dict(t)
            for t in self.tables
            if (location is None or t["location"] == location)
            and (min_capacity is None or t["capacity"] >= min_capacity)
        ]

    # -- availability -----------------------------------------------
    def check_availability(self, slot_datetime: datetime, party_size: int, location=None):
        iso = slot_datetime.isoformat()
        booked = {
            r["table_id"]
            for r in self.reservations.values()
            if r["slot_datetime"] == iso and r["status"] == "confirmed"
        }
        available = [
            {
                "table_id": t["id"],
                "table_number": t["table_number"],
                "capacity": t["capacity"],
                "location": t["location"],
            }
            for t in sorted(self.tables, key=lambda x: (x["capacity"], x["table_number"]))
            if t["capacity"] >= party_size
            and t["id"] not in booked
            and (location is None or t["location"] == location)
        ]
        return {"slot_datetime": iso, "party_size": party_size, "available_tables": available}

    # -- customers -------------------------------------------------
    def lookup_customer(self, phone=None, email=None):
        for c in self.customers.values():
            if (phone and c.get("phone") == phone) or (email and c.get("email") == email):
                return dict(c)
        raise BackendError(404, "Customer not found")

    def create_customer(self, name, phone=None, email=None, preferences=None):
        cid = next(self._cust_ids)
        c = {
            "id": cid,
            "name": name,
            "phone": phone,
            "email": email,
            "preferences": preferences or {},
            "created_at": datetime.now().isoformat(),
        }
        self.customers[cid] = c
        return dict(c)

    def get_customer(self, customer_id: int):
        try:
            return dict(self.customers[customer_id])
        except KeyError:
            raise BackendError(404, "Customer not found")

    def update_preferences(self, customer_id: int, preferences: dict):
        c = self.customers[customer_id]
        c["preferences"] = {**c.get("preferences", {}), **preferences}
        return dict(c)

    def list_customer_reservations(self, customer_id: int, status=None):
        rows = [
            dict(r)
            for r in self.reservations.values()
            if r["customer_id"] == customer_id and (status is None or r["status"] == status)
        ]
        return sorted(rows, key=lambda r: r["slot_datetime"], reverse=True)

    def list_customer_orders(self, customer_id: int):
        res_ids = {r["id"] for r in self.reservations.values() if r["customer_id"] == customer_id}
        return [dict(o) for o in self.orders.values() if o["reservation_id"] in res_ids]

    # -- reservations --------------------------------------------
    def create_reservation(
        self, customer_id, table_id, slot_datetime: datetime, party_size, special_requests=None
    ):
        iso = slot_datetime.isoformat()
        table = next((t for t in self.tables if t["id"] == table_id), None)
        if table is None:
            raise BackendError(404, "Table not found")
        if party_size > table["capacity"]:
            raise BackendError(
                422, f"Party of {party_size} exceeds table capacity {table['capacity']}"
            )
        clash = any(
            r["table_id"] == table_id and r["slot_datetime"] == iso and r["status"] == "confirmed"
            for r in self.reservations.values()
        )
        if clash:
            raise BackendError(409, f"Table {table['table_number']} is already booked")
        rid = next(self._res_ids)
        row = {
            "id": rid,
            "customer_id": customer_id,
            "table_id": table_id,
            "slot_datetime": iso,
            "party_size": party_size,
            "special_requests": special_requests,
            "status": "confirmed",
            "created_at": datetime.now().isoformat(),
        }
        self.reservations[rid] = row
        return dict(row)

    def get_reservation(self, reservation_id: int):
        try:
            return dict(self.reservations[reservation_id])
        except KeyError:
            raise BackendError(404, "Reservation not found")

    def cancel_reservation(self, reservation_id: int):
        r = self.reservations.get(reservation_id)
        if r is None:
            raise BackendError(404, "Reservation not found")
        if r["status"] == "cancelled":
            raise BackendError(409, "Reservation already cancelled")
        slot = datetime.fromisoformat(r["slot_datetime"])
        if datetime.now() > slot - timedelta(hours=2):
            raise BackendError(409, "Cannot cancel within 2 hours of the reservation")
        r["status"] = "cancelled"
        return dict(r)

    # -- orders -------------------------------------------------
    def add_order_item(self, reservation_id: int, menu_item_id: int, quantity: int = 1):
        r = self.reservations.get(reservation_id)
        if r is None:
            raise BackendError(404, "Reservation not found")
        if r["status"] == "cancelled":
            raise BackendError(409, "Cannot order on a cancelled reservation")
        item = next((m for m in self.menu if m["id"] == menu_item_id), None)
        if item is None:
            raise BackendError(404, "Menu item not found")
        if not item["available"]:
            raise BackendError(409, f"'{item['name']}' is currently unavailable")
        oid = next(self._ord_ids)
        row = {
            "id": oid,
            "reservation_id": reservation_id,
            "menu_item_id": menu_item_id,
            "quantity": quantity,
            "created_at": datetime.now().isoformat(),
        }
        self.orders[oid] = row
        return dict(row)

    def list_order_items(self, reservation_id: int):
        return [dict(o) for o in self.orders.values() if o["reservation_id"] == reservation_id]

    def remove_order_item(self, order_id: int):
        if order_id not in self.orders:
            raise BackendError(404, "Order item not found")
        del self.orders[order_id]

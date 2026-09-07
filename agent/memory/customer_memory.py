"""Cross-session knowledge about one customer.

Storage: the backend's free-form ``customers.preferences`` JSON blob, whose schema
we own. We use:

    {
      "seating":   "outdoor" | "indoor",
      "dietary":   ["vegetarian", ...],
      "allergies": ["nuts", ...],
      "notes":     ["free text", ...]
    }

Order history is derived on load from ``/customers/{id}/orders`` joined with the menu.
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Any

from agent.backend.client import RestaurantClient

_LIST_KEYS = ("dietary", "allergies")


class CustomerMemory:
    def __init__(self, backend: RestaurantClient, customer_id: int) -> None:
        self._backend = backend
        self._customer_id = customer_id
        self.preferences: dict[str, Any] = {}
        self._orders: list[dict] = []
        self._reservations: list[dict] = []
        self._menu_by_id: dict[int, dict] = {}

    # -- loading ---------------------------------------------------------
    def load(self) -> None:
        customer = self._backend.get_customer(self._customer_id)
        self.preferences = dict(customer.get("preferences") or {})
        self._reservations = self._backend.list_customer_reservations(self._customer_id)
        self._orders = self._backend.list_customer_orders(self._customer_id)
        self._menu_by_id = {
            m["id"]: m for m in self._backend.list_menu(available_only=False)
        }

    # -- writing -------------------------------------------------------
    def remember(self, key: str, value: str) -> None:
        """Persist a durable preference. Backend shallow-merges the patch."""
        if key in _LIST_KEYS:
            current = list(self.preferences.get(key, []))
            if value not in current:
                current.append(value)
            patch = {key: current}
        elif key == "note":
            notes = list(self.preferences.get("notes", []))
            notes.append(value)
            patch = {"notes": notes}
        else:  # "seating" or any other scalar key
            patch = {key: value}

        updated = self._backend.update_preferences(self._customer_id, patch)
        self.preferences = dict(updated.get("preferences") or {})

    # -- derived views ----------------------------------------------
    @property
    def allergy_tags(self) -> set[str]:
        """Allergies expressed as menu tags, e.g. 'nuts' -> 'contains-nuts'."""
        tags: set[str] = set()
        for a in self.preferences.get("allergies", []):
            a = a.lower()
            tags.add(a if a.startswith("contains-") else f"contains-{a}")
        return tags

    def order_history(self) -> list[dict]:
        agg: "OrderedDict[int, dict]" = OrderedDict()
        for o in self._orders:
            item = self._menu_by_id.get(o["menu_item_id"])
            name = item["name"] if item else f"item #{o['menu_item_id']}"
            entry = agg.setdefault(
                o["menu_item_id"],
                {"item": name, "times_ordered": 0, "total_quantity": 0},
            )
            entry["times_ordered"] += 1
            entry["total_quantity"] += o["quantity"]
        return list(agg.values())

    def summary(self) -> str:
        """Compact block injected into the system prompt each turn."""
        p = self.preferences
        lines: list[str] = []
        if p.get("seating"):
            lines.append(f"- Usual seating preference: {p['seating']}")
        if p.get("dietary"):
            lines.append(f"- Dietary preferences: {', '.join(p['dietary'])}")
        if p.get("allergies"):
            lines.append(
                f"- ALLERGIES (never let them order these): {', '.join(p['allergies'])}"
            )
        if p.get("notes"):
            lines.append(f"- Notes: {'; '.join(p['notes'])}")

        history = self.order_history()
        if history:
            top = ", ".join(
                f"{h['item']} (x{h['total_quantity']})" for h in history[:5]
            )
            lines.append(f"- Has previously ordered: {top}")

        upcoming = [r for r in self._reservations if r.get("status") == "confirmed"]
        if upcoming:
            r = upcoming[0]
            lines.append(
                f"- Upcoming reservation: id {r['id']} at {r['slot_datetime']} "
                f"for {r['party_size']}"
            )
        return "\n".join(lines) if lines else "- Nothing on file yet (new or infrequent guest)."

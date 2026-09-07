"""Every tool the reception agent can call.

To add a capability: write one Pydantic args model + one function decorated with
``@registry.register(...)`` here. Nothing else in the codebase changes.

Conventions:
- ``customer_id`` is always taken from ``ctx.session`` — never a tool argument — so
  the agent cannot act on behalf of a different customer.
- Times are accepted as loose natural language ("tomorrow 7:30pm") or ISO, then
  snapped to a valid 30-minute slot. The backend still has the final say.
- Tools return plain dicts. A dict containing ``"error"`` tells the agent something
  went wrong without raising.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

from agent.tools.context import ToolContext
from agent.tools.registry import ToolRegistry
from agent.util.datetime_utils import nearest_slot, parse_when

registry = ToolRegistry()


class NoArgs(BaseModel):
    """No arguments."""


# --------------------------------------------------------------------------
# Read-only helpers
# --------------------------------------------------------------------------
@registry.register(
    "get_current_datetime",
    "Get the current local date and time. Call this to resolve relative phrases "
    "like 'tonight', 'tomorrow', or 'next Friday' before booking.",
    NoArgs,
)
def get_current_datetime(_args: NoArgs, _ctx: ToolContext) -> dict:
    now = datetime.now()
    return {"now": now.isoformat(timespec="minutes"), "weekday": now.strftime("%A")}


class SearchMenuArgs(BaseModel):
    category: Optional[Literal["starter", "main", "dessert"]] = None
    max_price: Optional[float] = Field(None, description="Only items at or below this price")
    include_tags: Optional[list[str]] = Field(
        None, description="Item must carry ALL of these tags, e.g. ['vegetarian']"
    )
    exclude_tags: Optional[list[str]] = Field(
        None, description="Drop items carrying ANY of these tags, e.g. ['contains-nuts']"
    )


@registry.register(
    "search_menu",
    "Search the menu with optional filters. Returns matching dishes with price, "
    "tags, category and availability.",
    SearchMenuArgs,
)
def search_menu(args: SearchMenuArgs, ctx: ToolContext) -> dict:
    items = ctx.backend.list_menu(
        category=args.category,
        tags_all=args.include_tags,
        exclude_tags=args.exclude_tags,
        max_price=args.max_price,
        available_only=True,
    )
    return {"count": len(items), "items": items}


class CheckAvailabilityArgs(BaseModel):
    when: str = Field(description="Date & time, natural language or ISO 8601")
    party_size: int = Field(gt=0)
    location: Optional[Literal["indoor", "outdoor"]] = None


@registry.register(
    "check_availability",
    "List tables that are free for a given time and party size. Use before "
    "create_reservation if the customer wants to pick a table themselves.",
    CheckAvailabilityArgs,
)
def check_availability(args: CheckAvailabilityArgs, ctx: ToolContext) -> dict:
    slot = nearest_slot(parse_when(args.when))
    data = ctx.backend.check_availability(slot, args.party_size, location=args.location)
    return {
        "slot_datetime": slot.isoformat(),
        "party_size": args.party_size,
        "available_tables": data["available_tables"],
    }


# --------------------------------------------------------------------------
# Reservations
# --------------------------------------------------------------------------
class BookTableArgs(BaseModel):
    when: str = Field(description="Date & time, natural language or ISO 8601")
    party_size: int = Field(gt=0)
    location: Optional[Literal["indoor", "outdoor"]] = Field(
        None,
        description="Leave empty to use the customer's stored seating preference",
    )
    special_requests: Optional[str] = None


@registry.register(
    "book_table",
    "Find and book the best table for the party in one step. Picks the smallest "
    "available table that fits, honouring the customer's stored seating preference "
    "when they didn't ask for a specific area. Use this unless the customer wants a "
    "particular table number.",
    BookTableArgs,
)
def book_table(args: BookTableArgs, ctx: ToolContext) -> dict:
    slot = nearest_slot(parse_when(args.when))
    preferred = args.location or ctx.memory.preferences.get("seating")

    data = ctx.backend.check_availability(slot, args.party_size, location=preferred)
    tables = data["available_tables"]
    used_location = preferred
    if not tables and preferred:  # fall back to any area
        data = ctx.backend.check_availability(slot, args.party_size, location=None)
        tables = data["available_tables"]
        used_location = None

    if not tables:
        return {
            "error": f"No tables available for {args.party_size} at "
            f"{slot.isoformat()}. Suggest a different time."
        }

    best = min(tables, key=lambda t: (t["capacity"], t["table_number"]))
    reservation = ctx.backend.create_reservation(
        customer_id=ctx.session.customer_id,
        table_id=best["table_id"],
        slot_datetime=slot,
        party_size=args.party_size,
        special_requests=args.special_requests,
    )
    ctx.session.active_reservation_id = reservation["id"]
    return {
        "reservation": reservation,
        "assigned_table": best,
        "seating": used_location or best["location"],
        "note": (
            "Preferred area was full; booked another area instead."
            if preferred and used_location is None
            else None
        ),
    }


class CreateReservationArgs(BaseModel):
    table_id: int = Field(description="A table id from check_availability")
    when: str
    party_size: int = Field(gt=0)
    special_requests: Optional[str] = None


@registry.register(
    "create_reservation",
    "Book a specific table by id. Prefer book_table unless the customer asked for "
    "a particular table.",
    CreateReservationArgs,
)
def create_reservation(args: CreateReservationArgs, ctx: ToolContext) -> dict:
    slot = nearest_slot(parse_when(args.when))
    reservation = ctx.backend.create_reservation(
        customer_id=ctx.session.customer_id,
        table_id=args.table_id,
        slot_datetime=slot,
        party_size=args.party_size,
        special_requests=args.special_requests,
    )
    ctx.session.active_reservation_id = reservation["id"]
    return {"reservation": reservation}


class ListReservationsArgs(BaseModel):
    status: Optional[Literal["confirmed", "cancelled", "completed"]] = None


@registry.register(
    "list_my_reservations",
    "List this customer's reservations (most recent first), optionally filtered by status.",
    ListReservationsArgs,
)
def list_my_reservations(args: ListReservationsArgs, ctx: ToolContext) -> dict:
    reservations = ctx.backend.list_customer_reservations(
        ctx.session.customer_id, status=args.status
    )
    return {"reservations": reservations}


class CancelReservationArgs(BaseModel):
    reservation_id: Optional[int] = Field(
        None,
        description="Omit to cancel the reservation created earlier this conversation",
    )


@registry.register(
    "cancel_reservation",
    "Cancel a reservation. The backend refuses cancellations within 2 hours of the slot.",
    CancelReservationArgs,
)
def cancel_reservation(args: CancelReservationArgs, ctx: ToolContext) -> dict:
    rid = args.reservation_id or ctx.session.active_reservation_id
    if rid is None:
        return {
            "error": "No reservation id given and none is active this conversation. "
            "Ask the customer which reservation to cancel."
        }
    result = ctx.backend.cancel_reservation(rid)
    if ctx.session.active_reservation_id == rid:
        ctx.session.active_reservation_id = None
    return {"cancelled": result}


# --------------------------------------------------------------------------
# Orders
# --------------------------------------------------------------------------
class OrderLine(BaseModel):
    name: str = Field(description="Dish name as the customer said it")
    quantity: int = Field(1, gt=0)


class AddItemsArgs(BaseModel):
    items: list[OrderLine] = Field(min_length=1)
    reservation_id: Optional[int] = Field(
        None, description="Omit to use this conversation's active reservation"
    )
    avoid_tags: Optional[list[str]] = Field(
        None,
        description="Extra dietary tags to refuse for this order, e.g. ['contains-nuts']",
    )


@registry.register(
    "add_items_to_reservation",
    "Add menu items to a reservation's order by dish name. Matches names to the "
    "menu, refuses unavailable items and anything matching an avoided tag or a "
    "stored allergy, and reports exactly what was added and what was skipped.",
    AddItemsArgs,
)
def add_items_to_reservation(args: AddItemsArgs, ctx: ToolContext) -> dict:
    rid = args.reservation_id or ctx.session.active_reservation_id
    if rid is None:
        return {
            "error": "No reservation to add items to. Book a table first or ask the "
            "customer for the reservation id."
        }

    menu = ctx.backend.list_menu(available_only=False)
    avoid = {t.lower() for t in (args.avoid_tags or [])} | ctx.memory.allergy_tags

    added: list[dict] = []
    skipped: list[dict] = []
    for line in args.items:
        match = _match_menu_item(line.name, menu)
        if match is None:
            skipped.append({"item": line.name, "reason": "not on the menu"})
            continue
        if not match["available"]:
            skipped.append({"item": match["name"], "reason": "currently unavailable"})
            continue
        clash = {t.lower() for t in match["tags"]} & avoid
        if clash:
            skipped.append(
                {"item": match["name"], "reason": f"contains {', '.join(sorted(clash))}"}
            )
            continue
        order = ctx.backend.add_order_item(rid, match["id"], line.quantity)
        added.append(
            {"item": match["name"], "quantity": line.quantity, "order_id": order["id"]}
        )

    return {"reservation_id": rid, "added": added, "skipped": skipped}


class RemoveOrderArgs(BaseModel):
    order_id: int


@registry.register(
    "remove_order_item",
    "Remove one item from a reservation's order by its order id (from "
    "add_items_to_reservation or list_order_history).",
    RemoveOrderArgs,
)
def remove_order_item(args: RemoveOrderArgs, ctx: ToolContext) -> dict:
    ctx.backend.remove_order_item(args.order_id)
    return {"removed_order_id": args.order_id}


@registry.register(
    "list_order_history",
    "List what this customer has ordered across past reservations, aggregated by dish.",
    NoArgs,
)
def list_order_history(_args: NoArgs, ctx: ToolContext) -> dict:
    return {"history": ctx.memory.order_history()}


# --------------------------------------------------------------------------
# Memory
# --------------------------------------------------------------------------
class RememberArgs(BaseModel):
    key: Literal["seating", "dietary", "allergies", "note"]
    value: str = Field(
        description="seating -> 'indoor'|'outdoor'; dietary/allergies -> one tag "
        "like 'vegetarian' or 'nuts'; note -> free text"
    )


@registry.register(
    "remember_preference",
    "Persist a lasting preference for this returning customer (seating, dietary "
    "need, allergy, or a note). Only call this for durable preferences the customer "
    "states, not one-off requests for the current booking.",
    RememberArgs,
)
def remember_preference(args: RememberArgs, ctx: ToolContext) -> dict:
    ctx.memory.remember(args.key, args.value)
    return {"saved": {args.key: args.value}, "preferences": ctx.memory.preferences}


# --------------------------------------------------------------------------
# Internal helpers
# --------------------------------------------------------------------------
def _match_menu_item(query: str, menu: list[dict]) -> Optional[dict]:
    """Resolve a customer's dish name to a menu row. Exact, then substring, then
    token-overlap — good enough for 12 items and defensive against typos."""
    q = query.strip().lower()
    by_name = {m["name"].lower(): m for m in menu}
    if q in by_name:
        return by_name[q]
    for name, item in by_name.items():
        if q in name or name in q:
            return item
    q_tokens = set(q.split())
    best, best_overlap = None, 0
    for name, item in by_name.items():
        overlap = len(q_tokens & set(name.split()))
        if overlap > best_overlap:
            best, best_overlap = item, overlap
    return best

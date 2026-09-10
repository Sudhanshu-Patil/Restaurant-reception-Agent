"""Every tool the reception agent can call.

To add a capability: write one Pydantic args model and one function decorated with
``@registry.register(...)``. Nothing else in the codebase changes - the JSON schema
sent to the model, argument validation, error shaping, and the mutation/destructive
classifications all follow from the registration.

Conventions
-----------
* ``customer_id`` is always taken from ``ctx.session`` - never a tool argument - so
  the model cannot act on behalf of another customer.
* Any tool that takes a ``reservation_id``/``order_id`` re-verifies ownership against
  the backend before it reads or mutates.
* Times are accepted as loose natural language ("tomorrow 7:30pm") or ISO and snapped
  to a valid 30-minute slot. The backend still has the final say.
* Every tool returns a :class:`~agent.tools.result.ToolResult`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from agent.backend.errors import BackendError
from agent.tools.context import ToolContext
from agent.tools.registry import ToolRegistry
from agent.tools.result import ErrorCode, ToolResult
from agent.util.datetime_utils import nearest_slot, parse_when

registry = ToolRegistry()

Seating = Literal["indoor", "outdoor"]


class NoArgs(BaseModel):
    """No arguments."""


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _resolve_slot(when: str) -> datetime | ToolResult:
    try:
        return nearest_slot(parse_when(when))
    except ValueError as exc:
        return ToolResult.failure(ErrorCode.BAD_DATETIME, str(exc))


def _owned_reservation(ctx: ToolContext, reservation_id: int) -> dict[str, Any] | None:
    """Return the reservation iff it belongs to the conversation's customer."""
    for reservation in ctx.backend.list_customer_reservations(ctx.session.customer_id):
        if reservation["id"] == reservation_id:
            return reservation
    return None


def _active_reservation_id(ctx: ToolContext, given: int | None) -> int | None:
    return given if given is not None else ctx.session.active_reservation_id


def _match_menu_item(query: str, menu: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Resolve a customer's dish name to a menu row: exact, then substring, then
    token-overlap. Good enough for a dozen items and forgiving of typos."""
    q = query.strip().lower()
    by_name = {m["name"].lower(): m for m in menu}
    if q in by_name:
        return by_name[q]
    for name, item in by_name.items():
        if q in name or name in q:
            return item
    q_tokens = set(q.split())
    best: dict[str, Any] | None = None
    best_overlap = 0
    for name, item in by_name.items():
        overlap = len(q_tokens & set(name.split()))
        if overlap > best_overlap:
            best, best_overlap = item, overlap
    return best


def _pick_smallest(tables: list[dict[str, Any]]) -> dict[str, Any]:
    return min(tables, key=lambda t: (t["capacity"], t["table_number"]))


# ---------------------------------------------------------------------------
# Read-only tools
# ---------------------------------------------------------------------------
@registry.register(
    "get_current_datetime",
    "Get the current local date and time. Call this to resolve relative phrases "
    "like 'tonight', 'tomorrow', or 'next Friday' before booking.",
    NoArgs,
)
def get_current_datetime(_args: NoArgs, _ctx: ToolContext) -> ToolResult:
    now = datetime.now()
    return ToolResult.success(
        f"It is {now:%A %d %b %Y, %H:%M}.",
        now=now.isoformat(timespec="minutes"),
        weekday=now.strftime("%A"),
    )


class SearchMenuArgs(BaseModel):
    category: Literal["starter", "main", "dessert"] | None = None
    max_price: float | None = Field(None, description="Only items at or below this price")
    include_tags: list[str] | None = Field(
        None, description="Item must carry ALL of these tags, e.g. ['vegetarian']"
    )
    exclude_tags: list[str] | None = Field(
        None, description="Drop items carrying ANY of these tags, e.g. ['contains-nuts']"
    )


@registry.register(
    "search_menu",
    "Search the menu with optional filters. Returns matching dishes with price, "
    "tags, category and availability.",
    SearchMenuArgs,
)
def search_menu(args: SearchMenuArgs, ctx: ToolContext) -> ToolResult:
    items = ctx.backend.list_menu(
        category=args.category,
        tags_all=args.include_tags,
        exclude_tags=args.exclude_tags,
        max_price=args.max_price,
        available_only=True,
    )
    return ToolResult.success(f"Found {len(items)} matching menu items.", items=items)


class CheckAvailabilityArgs(BaseModel):
    when: str = Field(description="Date & time, natural language or ISO 8601")
    party_size: int = Field(gt=0)
    location: Seating | None = None


@registry.register(
    "check_availability",
    "List tables that are free for a given time and party size. Read-only - it "
    "makes no booking. Use before create_reservation when the customer wants to "
    "pick a table themselves.",
    CheckAvailabilityArgs,
)
def check_availability(args: CheckAvailabilityArgs, ctx: ToolContext) -> ToolResult:
    slot = _resolve_slot(args.when)
    if isinstance(slot, ToolResult):
        return slot
    data = ctx.backend.check_availability(slot, args.party_size, location=args.location)
    tables = sorted(data["available_tables"], key=lambda t: (t["capacity"], t["table_number"]))
    return ToolResult.success(
        f"{len(tables)} tables free at {slot:%d %b %H:%M} for {args.party_size}.",
        slot_datetime=slot.isoformat(),
        party_size=args.party_size,
        available_tables=tables,
    )


class ListReservationsArgs(BaseModel):
    status: Literal["confirmed", "cancelled", "completed"] | None = None


@registry.register(
    "list_my_reservations",
    "List the current customer's reservations (most recent first), optionally "
    "filtered by status. Use it to find a reservation id.",
    ListReservationsArgs,
)
def list_my_reservations(args: ListReservationsArgs, ctx: ToolContext) -> ToolResult:
    reservations = ctx.backend.list_customer_reservations(
        ctx.session.customer_id, status=args.status
    )
    return ToolResult.success(f"{len(reservations)} reservations.", reservations=reservations)


# ---------------------------------------------------------------------------
# Reservations
# ---------------------------------------------------------------------------
class BookTableArgs(BaseModel):
    when: str = Field(description="Date & time, natural language or ISO 8601")
    party_size: int = Field(gt=0)
    location: Seating | None = Field(
        None, description="Leave empty to use the customer's stored seating preference"
    )
    special_requests: str | None = None


@registry.register(
    "book_table",
    "Find and book the best table for the party in one step. Picks the smallest "
    "available table that fits, honouring the customer's stored seating preference "
    "when they didn't ask for a specific area. Use this unless the customer wants a "
    "particular table number.",
    BookTableArgs,
)
def book_table(args: BookTableArgs, ctx: ToolContext) -> ToolResult:
    slot = _resolve_slot(args.when)
    if isinstance(slot, ToolResult):
        return slot

    preferred = args.location or ctx.memory.preferences.get("seating")
    data = ctx.backend.check_availability(slot, args.party_size, location=preferred)
    tables = data["available_tables"]
    used_location = preferred
    fell_back = False
    if not tables and preferred:
        data = ctx.backend.check_availability(slot, args.party_size, location=None)
        tables = data["available_tables"]
        used_location, fell_back = None, True

    if not tables:
        return ToolResult.failure(
            ErrorCode.NO_TABLE_AVAILABLE,
            f"No tables for {args.party_size} at {slot:%d %b %H:%M}. Suggest another time.",
        )

    best = _pick_smallest(tables)
    reservation = ctx.backend.create_reservation(
        customer_id=ctx.session.customer_id,
        table_id=best["table_id"],
        slot_datetime=slot,
        party_size=args.party_size,
        special_requests=args.special_requests,
    )
    ctx.session.active_reservation_id = reservation["id"]
    note = "Preferred area was full; booked another area." if fell_back else ""
    return ToolResult.success(
        (
            f"Booked table {best['table_number']} ({best['location']}) at "
            f"{slot:%d %b %H:%M} for {args.party_size}. {note}"
        ).strip(),
        reservation=reservation,
        assigned_table=best,
        seating=used_location or best["location"],
    )


class CreateReservationArgs(BaseModel):
    table_id: int = Field(description="A table id from check_availability")
    when: str
    party_size: int = Field(gt=0)
    special_requests: str | None = None


@registry.register(
    "create_reservation",
    "Book a specific table by id. Prefer book_table unless the customer asked for "
    "a particular table.",
    CreateReservationArgs,
)
def create_reservation(args: CreateReservationArgs, ctx: ToolContext) -> ToolResult:
    slot = _resolve_slot(args.when)
    if isinstance(slot, ToolResult):
        return slot
    reservation = ctx.backend.create_reservation(
        customer_id=ctx.session.customer_id,
        table_id=args.table_id,
        slot_datetime=slot,
        party_size=args.party_size,
        special_requests=args.special_requests,
    )
    ctx.session.active_reservation_id = reservation["id"]
    return ToolResult.success(
        f"Reservation {reservation['id']} confirmed.", reservation=reservation
    )


class CancelReservationArgs(BaseModel):
    reservation_id: int | None = Field(
        None, description="Omit to cancel the reservation created earlier this conversation"
    )


@registry.register(
    "cancel_reservation",
    "Cancel a reservation owned by the current customer. The backend refuses "
    "cancellations within 2 hours of the slot. Destructive - the agent asks the "
    "customer to confirm unless they explicitly asked to cancel.",
    CancelReservationArgs,
)
def cancel_reservation(args: CancelReservationArgs, ctx: ToolContext) -> ToolResult:
    rid = _active_reservation_id(ctx, args.reservation_id)
    if rid is None:
        return ToolResult.failure(
            ErrorCode.NO_ACTIVE_RESERVATION,
            "No reservation id given and none is active. Ask the customer which one.",
        )
    if _owned_reservation(ctx, rid) is None:
        return ToolResult.failure(
            ErrorCode.OWNERSHIP_DENIED,
            f"Reservation {rid} does not belong to this customer.",
        )
    try:
        result = ctx.backend.cancel_reservation(rid)
    except BackendError as exc:
        return ToolResult.failure(ErrorCode.NOT_MODIFIABLE, exc.detail, http_status=exc.status_code)
    if ctx.session.active_reservation_id == rid:
        ctx.session.active_reservation_id = None
    return ToolResult.success(f"Reservation {rid} cancelled.", cancelled=result)


class ChangeReservationArgs(BaseModel):
    reservation_id: int | None = Field(
        None, description="Omit to change this conversation's active reservation"
    )
    when: str | None = Field(None, description="New date/time; omit to keep it")
    party_size: int | None = Field(None, gt=0, description="New party size; omit to keep it")
    location: Seating | None = Field(
        None, description="New seating area; omit to keep current / use stored preference"
    )
    special_requests: str | None = None


@registry.register(
    "change_reservation",
    "Move or modify an existing reservation (different time, party size, or seating "
    "area). Cancels the old booking and re-books in one safe step - use this instead "
    "of making a second reservation. If the change can't be made (2-hour cutoff, or "
    "no table fits), the original booking is left untouched.",
    ChangeReservationArgs,
)
def change_reservation(args: ChangeReservationArgs, ctx: ToolContext) -> ToolResult:
    rid = _active_reservation_id(ctx, args.reservation_id)
    if rid is None:
        return ToolResult.failure(
            ErrorCode.NO_ACTIVE_RESERVATION,
            "No reservation specified and none is active. Ask which one.",
        )
    current = _owned_reservation(ctx, rid)
    if current is None:
        return ToolResult.failure(
            ErrorCode.OWNERSHIP_DENIED,
            f"Reservation {rid} does not belong to this customer.",
        )
    if current["status"] != "confirmed":
        return ToolResult.failure(
            ErrorCode.NOT_MODIFIABLE,
            f"Reservation {rid} is {current['status']} and cannot be changed.",
        )

    if args.when:
        slot_or_err = _resolve_slot(args.when)
        if isinstance(slot_or_err, ToolResult):
            return slot_or_err
        slot = slot_or_err
    else:
        slot = datetime.fromisoformat(current["slot_datetime"])
    party = args.party_size or current["party_size"]
    location = args.location or ctx.memory.preferences.get("seating")
    special = (
        args.special_requests
        if args.special_requests is not None
        else current.get("special_requests")
    )

    # 1. release the old booking first - this is what the 2-hour rule blocks.
    try:
        ctx.backend.cancel_reservation(rid)
    except BackendError as exc:
        return ToolResult.failure(
            ErrorCode.NOT_MODIFIABLE,
            f"Couldn't change reservation {rid}: {exc.detail}",
            http_status=exc.status_code,
            unchanged_reservation=current,
        )

    # 2. find a table for the new requirements.
    data = ctx.backend.check_availability(slot, party, location=location)
    tables = data["available_tables"]
    used_location = location
    if not tables and location:
        data = ctx.backend.check_availability(slot, party, location=None)
        tables = data["available_tables"]
        used_location = None

    if not tables:
        restored = _recreate(ctx, current)
        ctx.session.active_reservation_id = restored["id"]
        return ToolResult.failure(
            ErrorCode.NO_TABLE_AVAILABLE,
            f"No table for {party} at {slot:%d %b %H:%M}; kept your original booking.",
            reservation=restored,
        )

    best = _pick_smallest(tables)
    new_reservation = ctx.backend.create_reservation(
        customer_id=ctx.session.customer_id,
        table_id=best["table_id"],
        slot_datetime=slot,
        party_size=party,
        special_requests=special,
    )
    ctx.session.active_reservation_id = new_reservation["id"]
    return ToolResult.success(
        (
            f"Moved reservation {rid} to table {best['table_number']} "
            f"({best['location']}) at {slot:%d %b %H:%M}."
        ),
        changed_from=rid,
        reservation=new_reservation,
        assigned_table=best,
        seating=used_location or best["location"],
    )


def _recreate(ctx: ToolContext, reservation: dict[str, Any]) -> dict[str, Any]:
    return ctx.backend.create_reservation(
        customer_id=ctx.session.customer_id,
        table_id=reservation["table_id"],
        slot_datetime=datetime.fromisoformat(reservation["slot_datetime"]),
        party_size=reservation["party_size"],
        special_requests=reservation.get("special_requests"),
    )


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------
class OrderLine(BaseModel):
    name: str = Field(description="Dish name as the customer said it")
    quantity: int = Field(1, gt=0)


class AddItemsArgs(BaseModel):
    items: list[OrderLine] = Field(min_length=1)
    reservation_id: int | None = Field(
        None, description="Omit to use this conversation's active reservation"
    )
    avoid_tags: list[str] | None = Field(
        None,
        description="Extra dietary tags to refuse for this order, e.g. ['contains-nuts']",
    )


@registry.register(
    "add_items_to_reservation",
    "Add menu items to a reservation's order by dish name. Matches names to the "
    "menu, refuses unavailable items and anything matching an avoided tag or a "
    "stored allergy, and reports exactly what was added and skipped.",
    AddItemsArgs,
)
def add_items_to_reservation(args: AddItemsArgs, ctx: ToolContext) -> ToolResult:
    rid = _active_reservation_id(ctx, args.reservation_id)
    if rid is None:
        return ToolResult.failure(
            ErrorCode.NO_ACTIVE_RESERVATION,
            "No reservation to add items to. Book a table or give a reservation id first.",
        )
    if _owned_reservation(ctx, rid) is None:
        return ToolResult.failure(
            ErrorCode.OWNERSHIP_DENIED,
            f"Reservation {rid} does not belong to this customer.",
        )

    menu = ctx.backend.list_menu(available_only=False)
    avoid = {t.lower() for t in (args.avoid_tags or [])} | ctx.memory.allergy_tags

    added: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
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
        added.append({"item": match["name"], "quantity": line.quantity, "order_id": order["id"]})

    verb = "Added" if added else "Added nothing"
    tail = f"; skipped {len(skipped)}" if skipped else ""
    return ToolResult.success(
        f"{verb} {len(added)} item(s) to reservation {rid}{tail}.",
        reservation_id=rid,
        added=added,
        skipped=skipped,
    )


class RemoveOrderArgs(BaseModel):
    order_id: int
    reservation_id: int | None = Field(
        None, description="Omit to use this conversation's active reservation"
    )


@registry.register(
    "remove_order_item",
    "Remove one item from a reservation's order by its order id. Destructive - the "
    "agent asks the customer to confirm unless they explicitly asked to remove it.",
    RemoveOrderArgs,
)
def remove_order_item(args: RemoveOrderArgs, ctx: ToolContext) -> ToolResult:
    rid = _active_reservation_id(ctx, args.reservation_id)
    if rid is None:
        return ToolResult.failure(
            ErrorCode.NO_ACTIVE_RESERVATION,
            "No reservation given and none is active. Ask which reservation.",
        )
    if _owned_reservation(ctx, rid) is None:
        return ToolResult.failure(
            ErrorCode.OWNERSHIP_DENIED,
            f"Reservation {rid} does not belong to this customer.",
        )
    if not any(o["id"] == args.order_id for o in ctx.backend.list_order_items(rid)):
        return ToolResult.failure(
            ErrorCode.OWNERSHIP_DENIED,
            f"Order {args.order_id} is not on reservation {rid}.",
        )
    ctx.backend.remove_order_item(args.order_id)
    return ToolResult.success(
        f"Removed order item {args.order_id}.", removed_order_id=args.order_id
    )


@registry.register(
    "list_order_history",
    "List what the current customer has ordered across past reservations, aggregated by dish.",
    NoArgs,
)
def list_order_history(_args: NoArgs, ctx: ToolContext) -> ToolResult:
    history = ctx.memory.order_history()
    return ToolResult.success(f"{len(history)} distinct dishes ordered.", history=history)


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------
class RememberArgs(BaseModel):
    key: Literal["seating", "dietary", "allergies", "note"]
    value: str = Field(
        description="seating -> 'indoor'|'outdoor'; dietary/allergies -> one tag like "
        "'vegetarian' or 'nuts'; note -> free text"
    )


@registry.register(
    "remember_preference",
    "Persist a lasting preference for this returning customer (seating, dietary "
    "need, allergy, or a note). Only for durable preferences the customer states, "
    "not one-off requests for the current booking.",
    RememberArgs,
)
def remember_preference(args: RememberArgs, ctx: ToolContext) -> ToolResult:
    ctx.memory.remember(args.key, args.value)
    return ToolResult.success(
        f"Saved {args.key}: {args.value}.",
        saved={args.key: args.value},
        preferences=ctx.memory.preferences,
    )

# Copyright (c) Microsoft. All rights reserved.

"""Bike-rental MCP server.

Exposes the CyclePro rental fleet as a set of Model Context Protocol tools so
that any MCP-compatible client (LLM agent, IDE plugin, voice agent, etc.) can
search the catalogue, get a price quote, place a reservation hold, and confirm
the final booking.

The conversational state that used to live in the bike-renting voice agent
(`src/agents/bike-renting/main.py`) is split here into two layers:

* **Stateless tools** for browsing and pricing -- the LLM is expected to carry
  the user's intent (chosen `bike_id`, requested `rental_days`) across calls.
* **Server-side ledger** for reservations and bookings -- each one gets a
  durable code (e.g. ``RES-MB-001-A1B2``) that the caller passes back on the
  follow-up tool calls.

Transports
----------
Run with the Streamable HTTP transport by default (what Foundry / Agent
Framework MCP clients expect)::

    python server.py                # http://localhost:8000/mcp
    python server.py --transport stdio

Tools
-----
* ``list_categories``       -- browse available bike categories.
* ``search_bikes``          -- keyword + category search over the fleet.
* ``get_bike``              -- full detail for one bike, optionally priced
                               for N rental days.
* ``reserve_bike``          -- place a 30-minute hold and get a reservation
                               code.
* ``confirm_booking``       -- convert a reservation into a confirmed booking.
* ``cancel_reservation``    -- release a reservation hold.
* ``get_reservation``       -- inspect a reservation by code.
* ``get_booking``           -- inspect a confirmed booking by code.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import threading
import time
import uuid
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from data.bikes import RENTAL_BIKES

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RESERVATION_HOLD_MINUTES = 30
MAX_RENTAL_DAYS = 30

VALID_CATEGORIES = sorted({b["category"] for b in RENTAL_BIKES})

_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "city": ["city", "commute", "commuter", "urban", "town"],
    "ebike": ["e-bike", "ebike", "electric", "battery", "motor", "assist"],
    "mountain": ["mountain", "mtb", "trail", "off-road", "offroad", "downhill", "enduro"],
    "gravel": ["gravel", "tour", "touring", "adventure", "long ride"],
    "children": ["child", "children", "kid", "kids", "junior", "teen", "boy", "girl"],
}

# ---------------------------------------------------------------------------
# In-memory ledger (replace with Redis / Cosmos DB / SQL in production)
# ---------------------------------------------------------------------------

_ledger_lock = threading.Lock()

# reservation_code -> reservation dict
_reservations: dict[str, dict[str, Any]] = {}
# confirmation_code -> booking dict
_bookings: dict[str, dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_bike(bike_id: str) -> dict[str, Any] | None:
    bike_id = bike_id.strip().upper()
    return next((b for b in RENTAL_BIKES if b["id"].upper() == bike_id), None)


def _public_bike(bike: dict[str, Any]) -> dict[str, Any]:
    """Strip internal fields and return a stable shape for clients."""
    return {
        "id": bike["id"],
        "name": bike["name"],
        "brand": bike["brand"],
        "category": bike["category"],
        "rental_price_per_day_eur": bike["rental_price_per_day_eur"],
        "deposit_eur": bike["deposit_eur"],
        "available_units": bike["available_units"],
        "frame": bike["frame"],
        "gears": bike["gears"],
        "wheel_size_inch": bike["wheel_size_inch"],
        "features": list(bike["features"]),
        "suitable_for": bike["suitable_for"],
        "rider_height_cm": bike["rider_height_cm"],
    }


def _quote(bike: dict[str, Any], rental_days: int) -> dict[str, Any]:
    total = round(bike["rental_price_per_day_eur"] * rental_days, 2)
    return {
        "bike_id": bike["id"],
        "rental_days": rental_days,
        "price_per_day_eur": bike["rental_price_per_day_eur"],
        "subtotal_eur": total,
        "deposit_eur": bike["deposit_eur"],
        "total_payable_at_pickup_eur": round(total + bike["deposit_eur"], 2),
        "currency": "EUR",
    }


def _validate_rental_days(rental_days: int) -> None:
    if not isinstance(rental_days, int) or rental_days < 1 or rental_days > MAX_RENTAL_DAYS:
        raise ValueError(
            f"rental_days must be an integer between 1 and {MAX_RENTAL_DAYS} (got {rental_days!r})."
        )


def _expire_old_reservations() -> None:
    """Drop reservations that are past their hold window."""
    now = time.time()
    with _ledger_lock:
        expired = [
            code for code, r in _reservations.items()
            if r["status"] == "held" and r["expires_at_epoch"] < now
        ]
        for code in expired:
            _reservations[code]["status"] = "expired"


def _new_code(prefix: str, bike_id: str) -> str:
    suffix = uuid.uuid4().hex[:4].upper()
    return f"{prefix}-{bike_id.upper()}-{suffix}"


def _slugify_query(q: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", q.lower()) if t]


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "bike-rental",
    instructions=(
        "Tools for the CyclePro bike-rental shop. Use `search_bikes` to find "
        "candidates, `get_bike` for full details and a price quote, "
        "`reserve_bike` to place a 30-minute hold, and `confirm_booking` "
        "to finalise. Reservation and confirmation codes are returned to the "
        "caller and must be passed back on follow-up calls."
    ),
)


# ----- discovery / search ---------------------------------------------------

@mcp.tool()
def list_categories() -> dict[str, Any]:
    """List the bike categories currently available for rental.

    Returns the category names along with a short description and the number
    of distinct bike models in each.
    """
    descriptions = {
        "city": "Comfortable upright bikes for commuting and sightseeing.",
        "ebike": "Pedal-assist electric bikes for hills and longer rides.",
        "mountain": "Suspension bikes for trails and off-road riding.",
        "gravel": "Drop-bar bikes for mixed-surface day tours.",
        "children": "Smaller bikes sized for kids and pre-teens.",
    }
    counts: dict[str, int] = {}
    for b in RENTAL_BIKES:
        counts[b["category"]] = counts.get(b["category"], 0) + 1

    return {
        "categories": [
            {
                "name": cat,
                "description": descriptions.get(cat, ""),
                "models_available": counts.get(cat, 0),
            }
            for cat in VALID_CATEGORIES
        ],
    }


@mcp.tool()
def search_bikes(
    query: str = "",
    category: Literal["city", "ebike", "mountain", "gravel", "children", ""] = "",
    max_price_per_day_eur: float | None = None,
    include_unavailable: bool = False,
) -> dict[str, Any]:
    """Search the rental fleet.

    Combine any of:
    * ``query``   -- free-text keywords matched against names, brands,
                     features, and category aliases (e.g. "electric commuter",
                     "kids 24 inch", "full suspension").
    * ``category`` -- exact category filter (see ``list_categories``).
    * ``max_price_per_day_eur`` -- upper bound on daily rental price.
    * ``include_unavailable`` -- include sold-out models (default: False).

    With no filters, returns every currently available bike so the customer
    can browse the whole fleet.
    """
    results: list[dict[str, Any]] = list(RENTAL_BIKES)

    if not include_unavailable:
        results = [b for b in results if b["available_units"] > 0]

    if category:
        results = [b for b in results if b["category"] == category]

    if max_price_per_day_eur is not None:
        results = [
            b for b in results
            if b["rental_price_per_day_eur"] <= max_price_per_day_eur
        ]

    if query:
        tokens = _slugify_query(query)
        # Expand tokens with category aliases.
        matched_categories = {
            cat for cat, kws in _CATEGORY_KEYWORDS.items()
            if any(any(kw_part in t or t in kw_part for kw_part in kw.split())
                   for kw in kws for t in tokens)
        }

        def _bike_matches(b: dict[str, Any]) -> bool:
            if b["category"] in matched_categories:
                return True
            haystack = " ".join([
                b["name"], b["brand"], b["category"],
                b["suitable_for"],
                " ".join(b["features"]),
            ]).lower()
            return all(t in haystack for t in tokens)

        narrowed = [b for b in results if _bike_matches(b)]
        # Only narrow if we actually matched something; otherwise let the
        # caller see the broader (category/price-filtered) list.
        if narrowed:
            results = narrowed

    return {
        "query": query,
        "category": category or None,
        "max_price_per_day_eur": max_price_per_day_eur,
        "result_count": len(results),
        "bikes": [_public_bike(b) for b in results],
    }


@mcp.tool()
def get_bike(bike_id: str, rental_days: int = 1) -> dict[str, Any]:
    """Get full details for one bike, with an optional price quote.

    Args:
        bike_id: Identifier returned by ``search_bikes`` (e.g. ``"MB-001"``).
        rental_days: Number of rental days for the quote (1-30, default 1).
    """
    bike = _find_bike(bike_id)
    if not bike:
        raise ValueError(f"No bike found with id {bike_id!r}.")
    _validate_rental_days(rental_days)

    return {
        "bike": _public_bike(bike),
        "quote": _quote(bike, rental_days),
    }


# ----- reservation / booking -----------------------------------------------

@mcp.tool()
def reserve_bike(
    bike_id: str,
    rental_days: int,
    customer_name: str = "",
) -> dict[str, Any]:
    """Place a 30-minute hold on a bike.

    The hold blocks one unit from being booked elsewhere while the customer
    decides. Use ``confirm_booking`` (with the returned ``reservation_code``)
    to finalise, or ``cancel_reservation`` to release the hold early.

    Args:
        bike_id: Bike identifier from ``search_bikes`` / ``get_bike``.
        rental_days: Number of rental days (1-30).
        customer_name: Optional customer name attached to the reservation.
    """
    bike = _find_bike(bike_id)
    if not bike:
        raise ValueError(f"No bike found with id {bike_id!r}.")
    _validate_rental_days(rental_days)

    _expire_old_reservations()

    with _ledger_lock:
        held_for_this_bike = sum(
            1 for r in _reservations.values()
            if r["bike_id"] == bike["id"] and r["status"] == "held"
        )
        booked_for_this_bike = sum(
            1 for bk in _bookings.values()
            if bk["bike_id"] == bike["id"] and bk["status"] == "confirmed"
        )
        committed = held_for_this_bike + booked_for_this_bike
        if committed >= bike["available_units"]:
            raise ValueError(
                f"No units of {bike['name']} ({bike['id']}) are currently "
                f"available. All {bike['available_units']} are already held "
                f"or booked."
            )

        now = time.time()
        expires_at = now + RESERVATION_HOLD_MINUTES * 60
        code = _new_code("RES", bike["id"])
        reservation = {
            "reservation_code": code,
            "bike_id": bike["id"],
            "bike_name": bike["name"],
            "rental_days": rental_days,
            "customer_name": customer_name.strip() or None,
            "status": "held",
            "created_at_epoch": now,
            "expires_at_epoch": expires_at,
            "hold_minutes": RESERVATION_HOLD_MINUTES,
            "quote": _quote(bike, rental_days),
        }
        _reservations[code] = reservation

    logger.info("Reservation %s created for %s (%s days)", code, bike["id"], rental_days)
    return reservation


@mcp.tool()
def confirm_booking(
    reservation_code: str,
    customer_name: str = "",
    customer_contact: str = "",
) -> dict[str, Any]:
    """Confirm a previously reserved bike and create a final booking.

    Args:
        reservation_code: Code returned by ``reserve_bike``.
        customer_name: Customer name (required if not provided at reservation).
        customer_contact: Optional phone or email for pickup logistics.
    """
    _expire_old_reservations()

    with _ledger_lock:
        reservation = _reservations.get(reservation_code.strip().upper())
        if not reservation:
            raise ValueError(f"No reservation found with code {reservation_code!r}.")
        if reservation["status"] == "expired":
            raise ValueError(
                f"Reservation {reservation_code} has expired. Please reserve again."
            )
        if reservation["status"] == "cancelled":
            raise ValueError(
                f"Reservation {reservation_code} was cancelled and cannot be confirmed."
            )
        if reservation["status"] == "confirmed":
            # Idempotent: return the existing booking.
            existing = next(
                (bk for bk in _bookings.values()
                 if bk.get("reservation_code") == reservation["reservation_code"]),
                None,
            )
            if existing:
                return existing

        name = (customer_name or reservation.get("customer_name") or "").strip()
        if not name:
            raise ValueError(
                "customer_name is required to confirm the booking."
            )

        bike = _find_bike(reservation["bike_id"])
        assert bike is not None  # reservation could only exist if bike does

        confirmation_code = _new_code("BK", bike["id"])
        booking = {
            "confirmation_code": confirmation_code,
            "reservation_code": reservation["reservation_code"],
            "bike_id": bike["id"],
            "bike_name": bike["name"],
            "rental_days": reservation["rental_days"],
            "customer_name": name,
            "customer_contact": customer_contact.strip() or None,
            "status": "confirmed",
            "created_at_epoch": time.time(),
            "quote": _quote(bike, reservation["rental_days"]),
            "pickup_instructions": (
                "Please bring photo ID and the deposit amount when picking "
                "up the bike at the CyclePro shop."
            ),
        }
        _bookings[confirmation_code] = booking
        reservation["status"] = "confirmed"
        reservation["confirmation_code"] = confirmation_code

    logger.info("Booking %s confirmed from reservation %s", confirmation_code, reservation_code)
    return booking


@mcp.tool()
def cancel_reservation(reservation_code: str) -> dict[str, Any]:
    """Cancel an active reservation hold.

    Bookings that have already been confirmed cannot be cancelled with this
    tool -- use a separate refund flow for that.
    """
    with _ledger_lock:
        reservation = _reservations.get(reservation_code.strip().upper())
        if not reservation:
            raise ValueError(f"No reservation found with code {reservation_code!r}.")
        if reservation["status"] == "confirmed":
            raise ValueError(
                f"Reservation {reservation_code} has already been confirmed "
                f"as booking {reservation.get('confirmation_code')}. Use the "
                f"refund flow to cancel a confirmed booking."
            )
        reservation["status"] = "cancelled"

    return {
        "reservation_code": reservation_code,
        "status": "cancelled",
        "bike_id": reservation["bike_id"],
    }


@mcp.tool()
def get_reservation(reservation_code: str) -> dict[str, Any]:
    """Look up a reservation by code."""
    _expire_old_reservations()
    reservation = _reservations.get(reservation_code.strip().upper())
    if not reservation:
        raise ValueError(f"No reservation found with code {reservation_code!r}.")
    return reservation


@mcp.tool()
def get_booking(confirmation_code: str) -> dict[str, Any]:
    """Look up a confirmed booking by confirmation code."""
    booking = _bookings.get(confirmation_code.strip().upper())
    if not booking:
        raise ValueError(f"No booking found with code {confirmation_code!r}.")
    return booking


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bike-rental MCP server.")
    parser.add_argument(
        "--transport",
        choices=["streamable-http", "stdio", "sse"],
        default=os.environ.get("MCP_TRANSPORT", "streamable-http"),
        help="MCP transport to expose (default: streamable-http).",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("MCP_HOST", "0.0.0.0"),
        help="Host to bind the HTTP transport to (default: 0.0.0.0).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("MCP_PORT", "8000")),
        help="Port to bind the HTTP transport to (default: 8000).",
    )
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _parse_args()

    if args.transport in ("streamable-http", "sse"):
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        logger.info(
            "Starting bike-rental MCP server on %s://%s:%d/mcp",
            args.transport, args.host, args.port,
        )

    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()

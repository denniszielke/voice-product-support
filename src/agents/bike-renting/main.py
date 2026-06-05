# Copyright (c) Microsoft. All rights reserved.

"""Bike-renting agent using azure-ai-agentserver-invocations.

A voice + click demo agent that helps customers browse the rental fleet,
reserve a bike, and confirm a rental booking.

Voice Live sends two kinds of input:
    1. {"type": "input_audio.transcription", "input": "..."}  -- speech
    2. Arbitrary JSON from response.create invoke_input        -- click/UI events

The agent returns SSE streams with:
    - output_audio_transcription.delta -- text to be spoken by TTS
    - output_audio_transcription.done  -- marks speech complete
    - Custom typed events              -- passed through to the client
                                          (UI cards, reservations, etc.)
    - done                             -- marks the invocation complete

Conversation states per session:
    idle -> results_shown -> bike_selected -> reserved -> booked
"""

import asyncio
import json
import logging
import re
from collections import defaultdict

from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

from azure.ai.agentserver.invocations import InvocationAgentServerHost

from data.bikes import RENTAL_BIKES

logger = logging.getLogger(__name__)

app = InvocationAgentServerHost()

# ---------------------------------------------------------------------------
# In-memory session store (replace with Redis/Cosmos DB for production)
# ---------------------------------------------------------------------------

_sessions: dict[str, dict] = defaultdict(lambda: {
    "state": "idle",          # idle | results_shown | bike_selected | reserved | booked
    "search_query": None,
    "results": [],
    "selected_bike": None,
    "rental_days": 1,
    "reservation": None,
    "booking": None,
})

# ---------------------------------------------------------------------------
# Search helpers
# ---------------------------------------------------------------------------

_CATEGORY_KEYWORDS = {
    "city": ["city", "commute", "commuter", "urban", "town"],
    "ebike": ["e-bike", "ebike", "electric", "battery", "motor", "assist"],
    "mountain": ["mountain", "mtb", "trail", "off-road", "offroad", "downhill", "enduro"],
    "gravel": ["gravel", "tour", "touring", "adventure", "long ride"],
    "children": ["child", "children", "kid", "kids", "junior", "teen", "boy", "girl"],
}


def _search_bikes(query: str) -> list[dict]:
    """Keyword search over the rental fleet.

    Matches by category keywords first, then by bike name; otherwise returns
    every available bike so the customer can browse.
    """
    q = query.lower()

    # Category filter
    matched_categories = {
        cat for cat, keywords in _CATEGORY_KEYWORDS.items()
        if any(kw in q for kw in keywords)
    }
    if matched_categories:
        results = [
            b for b in RENTAL_BIKES
            if b["category"] in matched_categories and b["available_units"] > 0
        ]
        if results:
            return results

    # Name match
    name_matches = [
        b for b in RENTAL_BIKES
        if b["available_units"] > 0
        and any(part in q for part in b["name"].lower().split())
    ]
    if name_matches:
        return name_matches

    # Default: show all currently available bikes
    return [b for b in RENTAL_BIKES if b["available_units"] > 0]


def _format_bike_list(bikes: list[dict]) -> str:
    """Speakable summary of a list of bikes."""
    lines = []
    for i, b in enumerate(bikes, 1):
        lines.append(
            f"Option {i}: {b['name']} by {b['brand']}, "
            f"{b['rental_price_per_day_eur']:.0f} euros per day, "
            f"{b['available_units']} available."
        )
    return " ".join(lines)


def _extract_rental_days(text: str) -> int | None:
    """Pull a day count out of a phrase like 'for three days' or '5 days'."""
    m = re.search(r"(\d+)\s*(?:day|days|d\b)", text.lower())
    if m:
        try:
            n = int(m.group(1))
            if 1 <= n <= 30:
                return n
        except ValueError:
            pass

    words = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    }
    for word, n in words.items():
        if re.search(rf"\b{word}\s*(?:day|days)\b", text.lower()):
            return n
    return None


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------

async def _stream_speech_and_events(speech_text: str, events: list[dict]):
    """Yield SSE: word-by-word speech deltas, then custom events, then done."""
    words = speech_text.split()
    for word in words:
        evt = {"type": "output_audio_transcription.delta", "delta": word + " "}
        yield f"data: {json.dumps(evt)}\n\n"
        await asyncio.sleep(0.03)

    yield f'data: {json.dumps({"type": "output_audio_transcription.done", "text": speech_text})}\n\n'

    for evt in events:
        yield f"data: {json.dumps(evt)}\n\n"

    yield 'data: {"type": "done"}\n\n'


def _sse_response(speech_text: str, events: list[dict] | None = None) -> StreamingResponse:
    return StreamingResponse(
        _stream_speech_and_events(speech_text, events or []),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Intent detection (simple keyword-based; swap for LLM in production)
# ---------------------------------------------------------------------------

_BROWSE_PATTERN = re.compile(
    r"rent|hire|rental|available|looking for|need.*bike|want.*bike|show.*bike|find.*bike",
    re.IGNORECASE,
)
_SELECT_PATTERN = re.compile(r"(option|number|the)\s*(\d)", re.IGNORECASE)
_RESERVE_PATTERN = re.compile(r"reserve|hold|put.*aside|set.*aside", re.IGNORECASE)
_CONFIRM_PATTERN = re.compile(r"yes|confirm|go ahead|book it|sounds good|that works", re.IGNORECASE)
_CANCEL_PATTERN = re.compile(r"cancel|never\s?mind|start over|forget it", re.IGNORECASE)
_CHANGE_PATTERN = re.compile(r"change|different|another|longer|shorter|extend", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------

@app.invoke_handler
async def handle_invoke(request: Request) -> Response:
    data = await request.json()
    session_id = request.state.session_id
    session = _sessions[session_id]

    input_type = data.get("type", "")

    # --- Path A: speech input ---
    if input_type == "input_audio.transcription":
        user_text = data.get("input", "").strip()
        logger.info("Session %s | speech: %s", session_id, user_text)
        return _handle_speech(session, user_text)

    # --- Path B: click / UI input ---
    action = data.get("action", "")
    logger.info("Session %s | click: type=%s action=%s", session_id, input_type, action)
    return _handle_click(session, data)


# ---------------------------------------------------------------------------
# Speech handler
# ---------------------------------------------------------------------------

def _handle_speech(session: dict, text: str) -> StreamingResponse:
    state = session["state"]

    # Cancel / reset
    if _CANCEL_PATTERN.search(text):
        session.update(
            state="idle", search_query=None, results=[], selected_bike=None,
            rental_days=1, reservation=None, booking=None,
        )
        return _sse_response(
            "No problem, I've cleared everything. Would you like to look at "
            "available bikes again?"
        )

    # Browse / search
    if state in ("idle", "booked") and _BROWSE_PATTERN.search(text):
        results = _search_bikes(text)
        session["results"] = results
        session["search_query"] = text
        session["state"] = "results_shown"
        session["selected_bike"] = None
        session["reservation"] = None

        speech = (
            f"I found {len(results)} bikes you can rent. "
            + _format_bike_list(results)
            + " Which one would you like?"
        )
        ui_events = [
            {
                "type": "ui.bike_cards",
                "bikes": [
                    {
                        "id": b["id"],
                        "name": b["name"],
                        "brand": b["brand"],
                        "category": b["category"],
                        "rental_price_per_day_eur": b["rental_price_per_day_eur"],
                        "available_units": b["available_units"],
                        "features": b["features"],
                    }
                    for b in results
                ],
            },
            {
                "type": "ui.action_buttons",
                "actions": [
                    {"label": f"Select {b['name']}", "action": "select_bike", "bike_id": b["id"]}
                    for b in results
                ],
            },
        ]
        return _sse_response(speech, ui_events)

    # Voice-select from results: "option 2", "the second one"
    if state == "results_shown":
        m = _SELECT_PATTERN.search(text)
        if m:
            idx = int(m.group(2)) - 1
            if 0 <= idx < len(session["results"]):
                return _select_bike(session, session["results"][idx])
            return _sse_response(
                f"Sorry, I only have {len(session['results'])} options. Which one?"
            )

        # Bike name match
        for b in session["results"]:
            if b["name"].lower().split()[0] in text.lower():
                return _select_bike(session, b)

        return _sse_response(
            "Which bike would you like? You can say the option number or tap to select."
        )

    # After selecting a bike: handle rental length, reserve, confirm
    if state in ("bike_selected", "reserved"):
        days = _extract_rental_days(text)
        if days:
            session["rental_days"] = days
            bike = session["selected_bike"]
            total = bike["rental_price_per_day_eur"] * days
            speech = (
                f"Got it — {days} day{'s' if days != 1 else ''} on the {bike['name']}. "
                f"That's {total:.0f} euros plus a {bike['deposit_eur']:.0f} euro refundable "
                f"deposit. Shall I reserve it for you?"
            )
            return _sse_response(speech, [
                {
                    "type": "ui.booking_update",
                    "change": "rental_days",
                    "rental_days": days,
                    "total_eur": total,
                },
                {
                    "type": "ui.action_buttons",
                    "actions": [
                        {"label": "Reserve", "action": "reserve_bike"},
                        {"label": "Confirm Booking", "action": "confirm_booking"},
                        {"label": "Cancel", "action": "cancel"},
                    ],
                },
            ])

        if _RESERVE_PATTERN.search(text):
            return _reserve_bike(session)

        if _CONFIRM_PATTERN.search(text):
            return _confirm_booking(session)

        if _CHANGE_PATTERN.search(text):
            return _sse_response(
                "Sure, would you like a different bike or a different rental length? "
                "Say 'show me bikes' to browse again, or tell me how many days you'd like."
            )

    # Post-booking
    if state == "booked":
        return _sse_response(
            "Your rental is confirmed. Is there anything else I can help with? "
            "You can say 'rent another bike' or 'start over' anytime."
        )

    # Default / greeting
    return _sse_response(
        "Welcome to CyclePro Rentals! I can help you find and book a rental bike. "
        "Just say something like 'show me available city bikes' or 'I want to rent "
        "a mountain bike' to get started."
    )


# ---------------------------------------------------------------------------
# Click handler
# ---------------------------------------------------------------------------

def _handle_click(session: dict, data: dict) -> StreamingResponse:
    action = data.get("action", "")

    if action == "browse_bikes":
        return _handle_speech(session, "show me available bikes")

    if action == "select_bike":
        bike_id = data.get("bike_id", "")
        # Look up first in results, fall back to the whole fleet
        bike = next(
            (b for b in session.get("results", []) if b["id"] == bike_id),
            None,
        ) or next((b for b in RENTAL_BIKES if b["id"] == bike_id), None)
        if bike:
            return _select_bike(session, bike)
        return _sse_response("I couldn't find that bike. Could you try again?")

    if action == "set_rental_days":
        days = data.get("days")
        if isinstance(days, int) and 1 <= days <= 30 and session.get("selected_bike"):
            session["rental_days"] = days
            bike = session["selected_bike"]
            total = bike["rental_price_per_day_eur"] * days
            return _sse_response(
                f"Updated to {days} day{'s' if days != 1 else ''} on the {bike['name']}, "
                f"for a total of {total:.0f} euros plus deposit.",
                [{
                    "type": "ui.booking_update",
                    "change": "rental_days",
                    "rental_days": days,
                    "total_eur": total,
                }],
            )
        return _sse_response("Please choose a rental length between 1 and 30 days.")

    if action == "reserve_bike":
        if session["state"] in ("bike_selected", "reserved"):
            return _reserve_bike(session)
        return _sse_response("Please select a bike first, then I can reserve it for you.")

    if action == "confirm_booking":
        if session["state"] in ("bike_selected", "reserved"):
            return _confirm_booking(session)
        return _sse_response("There's no bike selected yet. Would you like to browse our rentals?")

    if action == "cancel":
        session.update(
            state="idle", search_query=None, results=[], selected_bike=None,
            rental_days=1, reservation=None, booking=None,
        )
        return _sse_response("Reservation cancelled. How else can I help?")

    return _sse_response("I'm not sure what that action means. Could you try again?")


# ---------------------------------------------------------------------------
# Shared business logic
# ---------------------------------------------------------------------------

def _select_bike(session: dict, bike: dict) -> StreamingResponse:
    session["selected_bike"] = bike
    session["state"] = "bike_selected"
    if not session.get("rental_days"):
        session["rental_days"] = 1
    days = session["rental_days"]
    total = bike["rental_price_per_day_eur"] * days

    speech = (
        f"Great choice! The {bike['name']} by {bike['brand']} rents for "
        f"{bike['rental_price_per_day_eur']:.0f} euros per day. "
        f"For {days} day{'s' if days != 1 else ''} that's {total:.0f} euros "
        f"plus a {bike['deposit_eur']:.0f} euro refundable deposit. "
        f"How many days would you like, or shall I reserve it now?"
    )
    ui_events = [
        {
            "type": "ui.bike_detail",
            "bike": {
                "id": bike["id"],
                "name": bike["name"],
                "brand": bike["brand"],
                "category": bike["category"],
                "rental_price_per_day_eur": bike["rental_price_per_day_eur"],
                "deposit_eur": bike["deposit_eur"],
                "rental_days": days,
                "total_eur": total,
                "features": bike["features"],
                "suitable_for": bike["suitable_for"],
                "rider_height_cm": bike["rider_height_cm"],
            },
        },
        {
            "type": "ui.action_buttons",
            "actions": [
                {"label": "Reserve (hold for 30 min)", "action": "reserve_bike"},
                {"label": "Confirm Booking", "action": "confirm_booking"},
                {"label": "Cancel", "action": "cancel"},
            ],
        },
    ]
    return _sse_response(speech, ui_events)


def _reserve_bike(session: dict) -> StreamingResponse:
    bike = session.get("selected_bike")
    if not bike:
        return _sse_response("No bike selected. Let's browse the rentals first.")

    days = session.get("rental_days", 1)
    reservation_code = f"RES-{bike['id']}-{abs(hash(bike['id'] + str(days))) % 10000:04d}"
    session["reservation"] = {
        "reservation_code": reservation_code,
        "bike_id": bike["id"],
        "rental_days": days,
        "hold_minutes": 30,
    }
    session["state"] = "reserved"

    speech = (
        f"I've reserved the {bike['name']} for you under code "
        f"{reservation_code}. The hold is good for 30 minutes. "
        f"Say 'confirm' or tap Confirm Booking when you're ready to finalise."
    )
    ui_events = [
        {
            "type": "ui.reservation_confirmed",
            "reservation": {
                "reservation_code": reservation_code,
                "bike_name": bike["name"],
                "rental_days": days,
                "hold_minutes": 30,
            },
        },
        {
            "type": "ui.action_buttons",
            "actions": [
                {"label": "Confirm Booking", "action": "confirm_booking"},
                {"label": "Cancel", "action": "cancel"},
            ],
        },
    ]
    return _sse_response(speech, ui_events)


def _confirm_booking(session: dict) -> StreamingResponse:
    bike = session.get("selected_bike")
    if not bike:
        return _sse_response("No bike selected. Let's browse the rentals first.")

    days = session.get("rental_days", 1)
    total = bike["rental_price_per_day_eur"] * days
    confirmation_code = f"BK-{bike['id']}-{abs(hash(bike['id'] + 'book')) % 10000:04d}"

    session["booking"] = {
        "confirmation_code": confirmation_code,
        "bike": bike,
        "rental_days": days,
        "total_eur": total,
        "deposit_eur": bike["deposit_eur"],
    }
    session["state"] = "booked"

    speech = (
        f"All set! Your rental of the {bike['name']} is confirmed for "
        f"{days} day{'s' if days != 1 else ''}. "
        f"Confirmation code: {confirmation_code}. "
        f"Total: {total:.0f} euros plus a {bike['deposit_eur']:.0f} euro refundable deposit. "
        f"Please bring a photo ID when you pick up the bike. Anything else?"
    )
    ui_events = [
        {
            "type": "ui.booking_confirmed",
            "booking": {
                "confirmation_code": confirmation_code,
                "bike_name": bike["name"],
                "rental_days": days,
                "total_eur": total,
                "deposit_eur": bike["deposit_eur"],
            },
        },
    ]
    return _sse_response(speech, ui_events)


if __name__ == "__main__":
    app.run()

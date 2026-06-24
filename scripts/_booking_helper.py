"""Shared booking-RPC helper for the diagnostic scripts.

Seven scripts in this directory used to copy the same 20-line
``GetBookingResults`` setup (build selected-legs, build filter block,
build f.req payload, POST to ``BOOKING_RPC_URL``). The Passengers /
TransportConfig API migration touched every copy the same way; the
next API change would touch them again. Centralize the duplication
here so future RPC tweaks land in one place.

Not part of the public ``swoop`` API. ``_``-prefixed at the module
level so it stays scoped to ``scripts/`` even if someone adds it
to ``sys.path``.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from swoop import CabinClass, Passengers, TransportConfig
from swoop.exceptions import SwoopUpstreamError
from swoop.rpc import (
    BOOKING_RPC_URL,
    SORT_DEPARTURE_TIME,
    _build_booking_f_req,
    _build_filters_from_legs,
    _build_selected_legs,
    _http_post,
    _normalize_rpc_leg,
)
from swoop._booking import parse_booking_payload


def fetch_booking_results(
    itinerary: Any,
    cabin: CabinClass,
) -> Tuple[Optional[list], Optional[str]]:
    """Make a ``GetBookingResults`` RPC call for ``itinerary``.

    Returns ``(raw_options, response_text)``. Either both are populated
    or both are ``None`` (the latter when ``_build_booking_f_req``
    rejected the inputs and there was nothing to POST). Callers that
    only need one value can ignore the other.
    """
    booking_token = itinerary.booking_token
    origin = itinerary.departure_airport_code
    destination = itinerary.arrival_airport_code
    dep = itinerary.departure_date
    date = f"{dep[0]:04d}-{dep[1]:02d}-{dep[2]:02d}"
    selected_legs = _build_selected_legs(itinerary)

    legs = [_normalize_rpc_leg(origin, destination, date)]
    filters = _build_filters_from_legs(
        legs,
        cabin=cabin,
        passengers=Passengers(),
        sort=SORT_DEPARTURE_TIME,
    )
    filter_block = filters[1]
    encoded_body = _build_booking_f_req(booking_token, filter_block, selected_legs)
    if not encoded_body:
        return None, None

    res = _http_post(
        BOOKING_RPC_URL,
        content=f"f.req={encoded_body}".encode(),
        transport=TransportConfig(),
    )
    try:
        return parse_booking_payload(res.text), res.text
    except SwoopUpstreamError:
        # During a Google outage the response is an ErrorResponse envelope, not
        # booking options. parse_booking_payload now raises rather than returning
        # []; the diagnostic scripts should record/skip the raw text, not crash.
        return [], res.text

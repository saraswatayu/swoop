"""Flexible date prices from Google's grid, picker and calendar graph RPCs."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

from .builders import CabinClass
from .decoder import _decode_price_info, _safe_get, raise_if_error_envelope
from .exceptions import SwoopParseError
from .models import CalendarDay, CalendarResult, Passengers, TransportConfig
from .rpc import (
    _build_filters_from_legs,
    _encode_f_req_payload,
    _http_post,
    _normalize_rpc_leg,
)

_RPC_ROOT = "https://www.google.com/_/FlightsFrontendUi/data/travel.frontend.flights.FlightsFrontendService/"
CALENDAR_GRID_RPC_URL = _RPC_ROOT + "GetCalendarGrid"
CALENDAR_RPC_URL = _RPC_ROOT + "GetCalendarPicker"
CALENDAR_GRAPH_RPC_URL = _RPC_ROOT + "GetCalendarGraph"


@dataclass(frozen=True)
class CalendarPrice:
    """Cheapest shopping fare for a date pair, in major currency units."""

    departure_date: str
    return_date: Optional[str]
    price: int
    currency: Optional[str]


def _calendar_frames(text: str) -> list[Any]:
    """Read naked JSON or every chunk of Google's length-framed stream."""
    remaining = text.lstrip().removeprefix(")]}'").lstrip()
    decoder = json.JSONDecoder()
    frames: list[Any] = []
    try:
        while remaining:
            line, sep, rest = remaining.partition("\n")
            if line.strip().isdigit() and sep:
                remaining = rest.lstrip()
            chunk, end = decoder.raw_decode(remaining)
            if not isinstance(chunk, list):
                raise ValueError("calendar chunk must be a list")
            frames.extend(chunk)
            remaining = remaining[end:].lstrip()
    except (ValueError, TypeError) as exc:
        raise SwoopParseError("Malformed calendar response") from exc
    return frames


def _parse_calendar_days(
    text: str, *, endpoint: str = "GetCalendarGrid"
) -> list[CalendarDay]:
    frames = _calendar_frames(text)
    raise_if_error_envelope(frames, endpoint=endpoint)
    prices: dict[tuple[str, Optional[str]], CalendarDay] = {}
    payloads = 0
    for frame in frames:
        if not (
            isinstance(frame, list)
            and len(frame) > 2
            and frame[0] == "wrb.fr"
            and isinstance(frame[2], str)
        ):
            continue
        try:
            inner = json.loads(frame[2])
        except ValueError as exc:
            raise SwoopParseError("Malformed inner calendar payload") from exc
        if isinstance(inner, list) and len(inner) > 1 and inner[1] is None:
            continue  # Leading metadata frames carry no fare cells.
        if (
            not isinstance(inner, list)
            or len(inner) < 2
            or not isinstance(inner[1], list)
        ):
            raise SwoopParseError("Calendar payload missing price cells")
        payloads += 1
        for cell in inner[1]:
            departure = _safe_get(cell, [0])
            returning = _safe_get(cell, [1])
            amount = _safe_get(cell, [2, 0, 1])
            if (
                not isinstance(departure, str)
                or type(amount) not in (int, float)
                or not math.isfinite(amount)
                or amount < 0
            ):
                continue
            summary = _decode_price_info(_safe_get(cell, [2]) or [])
            returning = returning if isinstance(returning, str) else None
            prices[(departure, returning)] = CalendarDay(
                departure,
                returning,
                int(amount),
                summary.currency if summary and summary.currency else None,
                _safe_get(cell, [2, 1])
                if isinstance(_safe_get(cell, [2, 1]), str)
                else None,
            )
    if not payloads:
        raise SwoopParseError("Calendar response missing inner payload")
    return list(prices.values())


def _parse_calendar_prices(
    text: str, *, endpoint: str = "GetCalendarGrid"
) -> list[CalendarPrice]:
    return [
        CalendarPrice(d.departure_date, d.return_date, d.price, d.currency)
        for d in _parse_calendar_days(text, endpoint=endpoint)
    ]


def _window(start: str, end: str, name: str) -> list[str]:
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{name} requires YYYY-MM-DD dates") from exc
    if first.isoformat() != start or last.isoformat() != end or first > last:
        raise ValueError(f"{name} requires ordered YYYY-MM-DD dates")
    return [start, end]


def _fetch_calendar(
    method: str,
    origin: str,
    destination: str,
    start: str,
    end: str,
    *,
    return_start: Optional[str] = None,
    return_end: Optional[str] = None,
    min_stay: Optional[int] = None,
    max_stay: Optional[int] = None,
    cabin: CabinClass = "economy",
    passengers: Passengers = Passengers(),
    max_stops: Optional[int] = None,
    airlines: Optional[list[str]] = None,
    exclude_basic_economy: bool = False,
    exclude_separate_tickets: bool = False,
    carry_on_bags: int = 0,
    checked_bags: int = 0,
    max_price: Optional[int] = None,
    max_duration: Optional[int] = None,
    connecting_airports: Optional[list[str]] = None,
    exclude_connecting_airports: Optional[list[str]] = None,
    min_layover_duration: Optional[int] = None,
    max_layover_duration: Optional[int] = None,
    earliest_departure: Optional[int] = None,
    latest_departure: Optional[int] = None,
    earliest_arrival: Optional[int] = None,
    latest_arrival: Optional[int] = None,
    return_earliest_departure: Optional[int] = None,
    return_latest_departure: Optional[int] = None,
    transport: TransportConfig = TransportConfig(),
) -> list[CalendarDay]:
    from ._validate import validate_cabin, validate_iata_code, validate_adults, validate_time_range

    validate_iata_code(origin, "origin")
    validate_iata_code(destination, "destination")
    validate_cabin(cabin)
    validate_adults(passengers.adults)
    for earliest_name, earliest, latest_name, latest in (
        ("earliest_departure", earliest_departure, "latest_departure", latest_departure),
        ("earliest_arrival", earliest_arrival, "latest_arrival", latest_arrival),
        ("return_earliest_departure", return_earliest_departure, "return_latest_departure", return_latest_departure),
    ):
        validate_time_range(earliest, earliest_name, 0, 23)
        validate_time_range(latest, latest_name, 1, 24)
        if earliest is not None and latest is not None and earliest >= latest:
            raise ValueError(f"{earliest_name} must be before {latest_name}")
    windows = [_window(start, end, "departure window")]
    if (return_start is None) != (return_end is None):
        raise ValueError("return_start and return_end must be given together")
    if return_start is not None and return_end is not None:
        windows.append(_window(return_start, return_end, "return window"))
    stay = None
    if min_stay is not None or max_stay is not None:
        lo, hi = (
            min_stay if min_stay is not None else 0,
            max_stay if max_stay is not None else min_stay,
        )
        if type(lo) is not int or type(hi) is not int or not 0 <= lo <= hi <= 365:
            raise ValueError("stay requires 0 <= min_stay <= max_stay <= 365")
        stay = [lo, hi]
    route_filters: dict[str, Any] = dict(
        max_stops=max_stops,
        airlines=airlines,
        max_duration=max_duration,
        connecting_airports=connecting_airports,
        exclude_connecting_airports=exclude_connecting_airports,
        min_layover_duration=min_layover_duration,
        max_layover_duration=max_layover_duration,
    )
    legs = [_normalize_rpc_leg(origin, destination, start,
        earliest_departure=earliest_departure, latest_departure=latest_departure,
        earliest_arrival=earliest_arrival, latest_arrival=latest_arrival, **route_filters)]
    if len(windows) == 2 or stay is not None:
        legs.append(_normalize_rpc_leg(destination, origin, return_start or start,
            earliest_departure=return_earliest_departure,
            latest_departure=return_latest_departure, **route_filters))
    filters = _build_filters_from_legs(
        legs,
        cabin=cabin,
        passengers=passengers,
        exclude_basic_economy=exclude_basic_economy,
        carry_on_bags=carry_on_bags,
        checked_bags=checked_bags,
        max_price=max_price,
    )[1]
    # This new endpoint option complements the search/selector work in PR #34.
    filters[18] = 1 if exclude_separate_tickets else None
    payload: list[Any] = [None, filters, *windows]
    if stay is not None:
        payload = [None, filters, windows[0], None, stay]
    response = _http_post(
        _RPC_ROOT + method,
        content=("f.req=" + _encode_f_req_payload(payload)).encode(),
        transport=transport,
    )
    return _parse_calendar_days(response.text, endpoint=method)


def calendar_prices(
    origin: str,
    destination: str,
    departure_start: str,
    departure_end: str,
    *,
    return_start: Optional[str] = None,
    return_end: Optional[str] = None,
    cabin: CabinClass = "economy",
    passengers: Passengers = Passengers(),
    max_stops: Optional[int] = None,
    airlines: Optional[list[str]] = None,
    exclude_basic_economy: bool = False,
    exclude_separate_tickets: bool = False,
    carry_on_bags: int = 0,
    checked_bags: int = 0,
    max_price: Optional[int] = None,
    max_duration: Optional[int] = None,
    connecting_airports: Optional[list[str]] = None,
    exclude_connecting_airports: Optional[list[str]] = None,
    min_layover_duration: Optional[int] = None,
    max_layover_duration: Optional[int] = None,
    earliest_departure: Optional[int] = None,
    latest_departure: Optional[int] = None,
    earliest_arrival: Optional[int] = None,
    latest_arrival: Optional[int] = None,
    return_earliest_departure: Optional[int] = None,
    return_latest_departure: Optional[int] = None,
    transport: TransportConfig = TransportConfig(),
) -> list[CalendarPrice]:
    """Cheapest fares for each departure/return date pair in inclusive windows.

    Duration and layover limits are in minutes; max_price is in major units of
    the display currency. Filters have the same wire format as shopping.
    Time windows apply to the outbound leg; return departure hours are independent.
    Dates with no published fare are omitted.
    """
    prices = _fetch_calendar(
        "GetCalendarGrid",
        origin,
        destination,
        departure_start,
        departure_end,
        return_start=return_start,
        return_end=return_end,
        cabin=cabin,
        passengers=passengers,
        max_stops=max_stops,
        airlines=airlines,
        exclude_basic_economy=exclude_basic_economy,
        exclude_separate_tickets=exclude_separate_tickets,
        carry_on_bags=carry_on_bags,
        checked_bags=checked_bags,
        max_price=max_price,
        max_duration=max_duration,
        connecting_airports=connecting_airports,
        exclude_connecting_airports=exclude_connecting_airports,
        min_layover_duration=min_layover_duration,
        max_layover_duration=max_layover_duration,
        earliest_departure=earliest_departure,
        latest_departure=latest_departure,
        earliest_arrival=earliest_arrival,
        latest_arrival=latest_arrival,
        return_earliest_departure=return_earliest_departure,
        return_latest_departure=return_latest_departure,
        transport=transport,
    )
    return [
        CalendarPrice(d.departure_date, d.return_date, d.price, d.currency)
        for d in prices
    ]


def get_calendar(
    origin: str,
    destination: str,
    window_start: str,
    window_end: str,
    *,
    min_stay: Optional[int] = None,
    max_stay: Optional[int] = None,
    cabin: CabinClass = "economy",
    passengers: Passengers = Passengers(),
    max_stops: Optional[int] = None,
    airlines: Optional[list[str]] = None,
    exclude_basic_economy: bool = False,
    exclude_separate_tickets: bool = False,
    carry_on_bags: int = 0,
    checked_bags: int = 0,
    max_price: Optional[int] = None,
    max_duration: Optional[int] = None,
    connecting_airports: Optional[list[str]] = None,
    exclude_connecting_airports: Optional[list[str]] = None,
    min_layover_duration: Optional[int] = None,
    max_layover_duration: Optional[int] = None,
    earliest_departure: Optional[int] = None,
    latest_departure: Optional[int] = None,
    earliest_arrival: Optional[int] = None,
    latest_arrival: Optional[int] = None,
    return_earliest_departure: Optional[int] = None,
    return_latest_departure: Optional[int] = None,
    transport: TransportConfig = TransportConfig(),
) -> CalendarResult:
    """Cheapest fares per departure date; optional stay bounds select a roundtrip.

    Duration and layover limits are in minutes; max_price is in major units of
    the display currency. Filters have the same wire format as shopping.
    Time windows apply to the outbound leg; return departure hours are independent.
    Dates with no published fare are omitted.
    """
    prices = _fetch_calendar(
        "GetCalendarPicker",
        origin,
        destination,
        window_start,
        window_end,
        min_stay=min_stay,
        max_stay=max_stay,
        cabin=cabin,
        passengers=passengers,
        max_stops=max_stops,
        airlines=airlines,
        exclude_basic_economy=exclude_basic_economy,
        exclude_separate_tickets=exclude_separate_tickets,
        carry_on_bags=carry_on_bags,
        checked_bags=checked_bags,
        max_price=max_price,
        max_duration=max_duration,
        connecting_airports=connecting_airports,
        exclude_connecting_airports=exclude_connecting_airports,
        min_layover_duration=min_layover_duration,
        max_layover_duration=max_layover_duration,
        earliest_departure=earliest_departure,
        latest_departure=latest_departure,
        earliest_arrival=earliest_arrival,
        latest_arrival=latest_arrival,
        return_earliest_departure=return_earliest_departure,
        return_latest_departure=return_latest_departure,
        transport=transport,
    )
    return CalendarResult(days=prices)


def get_price_graph(
    origin: str,
    destination: str,
    window_start: str,
    window_end: str,
    *,
    min_stay: Optional[int] = None,
    max_stay: Optional[int] = None,
    cabin: CabinClass = "economy",
    passengers: Passengers = Passengers(),
    max_stops: Optional[int] = None,
    airlines: Optional[list[str]] = None,
    exclude_basic_economy: bool = False,
    exclude_separate_tickets: bool = False,
    carry_on_bags: int = 0,
    checked_bags: int = 0,
    max_price: Optional[int] = None,
    max_duration: Optional[int] = None,
    connecting_airports: Optional[list[str]] = None,
    exclude_connecting_airports: Optional[list[str]] = None,
    min_layover_duration: Optional[int] = None,
    max_layover_duration: Optional[int] = None,
    earliest_departure: Optional[int] = None,
    latest_departure: Optional[int] = None,
    earliest_arrival: Optional[int] = None,
    latest_arrival: Optional[int] = None,
    return_earliest_departure: Optional[int] = None,
    return_latest_departure: Optional[int] = None,
    transport: TransportConfig = TransportConfig(),
) -> list[CalendarPrice]:
    """Cheapest fares per departure date; optional stay bounds select a roundtrip.

    Duration and layover limits are in minutes; max_price is in major units of
    the display currency. Filters have the same wire format as shopping.
    Time windows apply to the outbound leg; return departure hours are independent.
    Dates with no published fare are omitted.
    """
    prices = _fetch_calendar(
        "GetCalendarGraph",
        origin,
        destination,
        window_start,
        window_end,
        min_stay=min_stay,
        max_stay=max_stay,
        cabin=cabin,
        passengers=passengers,
        max_stops=max_stops,
        airlines=airlines,
        exclude_basic_economy=exclude_basic_economy,
        exclude_separate_tickets=exclude_separate_tickets,
        carry_on_bags=carry_on_bags,
        checked_bags=checked_bags,
        max_price=max_price,
        max_duration=max_duration,
        connecting_airports=connecting_airports,
        exclude_connecting_airports=exclude_connecting_airports,
        min_layover_duration=min_layover_duration,
        max_layover_duration=max_layover_duration,
        earliest_departure=earliest_departure,
        latest_departure=latest_departure,
        earliest_arrival=earliest_arrival,
        latest_arrival=latest_arrival,
        return_earliest_departure=return_earliest_departure,
        return_latest_departure=return_latest_departure,
        transport=transport,
    )
    return [
        CalendarPrice(d.departure_date, d.return_date, d.price, d.currency)
        for d in prices
    ]

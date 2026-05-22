"""Swoop — Search Google Flights programmatically.

Calls Google Flights' internal RPC endpoints with TLS impersonation
and decodes the nested-list responses into typed Python dataclasses.

Basic usage::

    from swoop import price_selector, search

    results = search("JFK", "LAX", "2026-06-01")
    for option in results.results:
        print(f"${option.price}")
        for leg in option.legs:
            if leg.itinerary is not None:
                print(f"  {leg.origin}->{leg.destination} — {leg.itinerary.airline_names}")

    chosen = results.results[0]
    bookable = price_selector(chosen.selector)
    if bookable is not None:
        print(bookable.price)
"""

from __future__ import annotations

__version__ = "0.4.1"

from .decoder import (
    AmenityFlags,
    BookingOption,
    CarbonEmissions,
    Codeshare,
    Segment,
    Itinerary,
    Layover,
    PriceRange,
    QualitySignals,
    RawSearchResult,
    itinerary_matches_flight,
)
from .exceptions import SwoopError, SwoopHTTPError, SwoopParseError, SwoopRateLimitError
from .builders import CabinClass, SearchLeg
from .models import Deal, DealsResult, Passengers, PriceResult, ResolvedLeg, SearchResult, SelectedLeg, TransportConfig, TripLeg, TripOption
from .rpc import (
    SORT_ARRIVAL_TIME,
    SORT_CHEAPEST,
    SORT_DEPARTURE_TIME,
    SORT_DURATION,
    SORT_TOP,
    STOPS_ANY,
    STOPS_NONSTOP,
    STOPS_ONE_OR_FEWER,
    STOPS_TWO_OR_FEWER,
    get_booking_results,
    search_raw,
    set_country,
    set_proxy,
)

# ---------------------------------------------------------------------------
# search() — the primary entry point with friendlier parameter names.
# ---------------------------------------------------------------------------

import logging
from typing import Optional

from ._selection import (
    build_request_legs_from_selected,
    price_selected_trip,
    price_trip_selector,
    resolve_selected_trip,
    search_trip_options,
)
from ._validate import (
    parse_flight_number,
    validate_adults,
    validate_cabin,
    validate_date,
    validate_iata_code,
    validate_search_params,
    validate_time_range,
)
from .rpc import _normalize_rpc_leg

logger = logging.getLogger(__name__)


def _filter_by_flight_number(
    result: Optional[RawSearchResult], carrier: Optional[str], number: str
) -> Optional[RawSearchResult]:
    """Filter a raw search result to only itineraries matching a flight number."""
    if result is None:
        return None
    best = [it for it in result.best if itinerary_matches_flight(it, carrier, number)]
    other = [it for it in result.other if itinerary_matches_flight(it, carrier, number)]
    if not best and not other:
        return None
    return RawSearchResult(best=best, other=other, price_range=result.price_range)


def _filter_trip_options_by_flight_number(
    result: SearchResult,
    carrier: Optional[str],
    number: str,
) -> SearchResult:
    """Filter trip-level search results by the first leg's itinerary."""
    filtered = []
    for option in result.results:
        if not option.legs:
            continue
        itinerary = option.legs[0].itinerary
        if itinerary is None:
            continue
        if itinerary_matches_flight(itinerary, carrier, number):
            filtered.append(option)
    return SearchResult(
        results=filtered,
        price_range=result.price_range,
        is_complete=result.is_complete,
    )


def _validate_leg_search_inputs(
    legs: list[SearchLeg],
    *,
    cabin: CabinClass,
    passengers: Passengers = Passengers(),
    leg_time_windows: Optional[list[dict[str, Optional[int]]]] = None,
) -> None:
    """Validate a list of explicit search legs."""
    if not legs:
        raise ValueError("at least one leg is required")

    validate_cabin(cabin)
    validate_adults(passengers.adults)

    for idx, leg in enumerate(legs):
        validate_iata_code(leg.from_airport, f"legs[{idx}].from_airport")
        validate_iata_code(leg.to_airport, f"legs[{idx}].to_airport")
        validate_date(leg.date, f"legs[{idx}].date")

    if leg_time_windows:
        for idx, window in enumerate(leg_time_windows):
            validate_time_range(window.get("earliest_departure"), f"legs[{idx}].earliest_departure", 0, 23)
            validate_time_range(window.get("latest_departure"), f"legs[{idx}].latest_departure", 1, 24)
            validate_time_range(window.get("earliest_arrival"), f"legs[{idx}].earliest_arrival", 0, 23)
            validate_time_range(window.get("latest_arrival"), f"legs[{idx}].latest_arrival", 1, 24)


def _search_with_normalized_legs(
    request_legs: list[dict[str, object]],
    *,
    cabin: CabinClass = "economy",
    passengers: Passengers = Passengers(),
    sort: int = SORT_DEPARTURE_TIME,
    include_basic_economy: bool = False,
    transport: TransportConfig = TransportConfig(),
    max_results: Optional[int] = None,
    beam_width: Optional[int] = None,
    time_budget: Optional[int] = None,
) -> SearchResult:
    """Execute a staged trip search from normalized leg definitions."""
    return search_trip_options(
        request_legs,
        cabin=cabin,
        passengers=passengers,
        sort=sort,
        include_basic_economy=include_basic_economy,
        transport=transport,
        max_results=max_results,
        beam_width=beam_width,
        time_budget=time_budget,
    )


def search_legs(
    legs: list[SearchLeg],
    *,
    cabin: CabinClass = "economy",
    passengers: Passengers = Passengers(),
    sort: int = SORT_DEPARTURE_TIME,
    include_basic_economy: bool = False,
    transport: TransportConfig = TransportConfig(),
    max_results: Optional[int] = None,
    beam_width: Optional[int] = None,
    time_budget: Optional[int] = None,
) -> SearchResult:
    """Search Google Flights using explicit leg definitions.

    Trip type is determined from ``len(legs)``.
    Per-leg ``max_stops`` and ``airlines`` come from each :class:`SearchLeg`.

    Args:
        legs: List of :class:`SearchLeg` objects (1 or more).
        cabin: Cabin class (default ``"economy"``).
        passengers: Passenger counts (default ``Passengers()``).
        sort: Sort order constant (default ``SORT_DEPARTURE_TIME``).
        include_basic_economy: Include basic economy fares (default ``False``).
        transport: HTTP transport configuration (default ``TransportConfig()``).
        max_results: Maximum trip combinations the beam search targets
            (default 10).  Only affects multi-leg (3+ city) searches.
        beam_width: Number of candidate prefixes carried between stages
            (default 15).  Only affects multi-leg searches.
        time_budget: Seconds before the beam search stops exploring
            (default 90).  Only affects multi-leg searches.

    Returns:
        A trip-level :class:`SearchResult` with shopping totals.
    """
    _validate_leg_search_inputs(legs, cabin=cabin, passengers=passengers)

    request_legs = [
        _normalize_rpc_leg(
            leg.from_airport,
            leg.to_airport,
            leg.date,
            max_stops=leg.max_stops,
            airlines=list(leg.airlines) if leg.airlines else None,
        )
        for leg in legs
    ]

    return _search_with_normalized_legs(
        request_legs,
        cabin=cabin,
        passengers=passengers,
        sort=sort,
        include_basic_economy=include_basic_economy,
        transport=transport,
        max_results=max_results,
        beam_width=beam_width,
        time_budget=time_budget,
    )


def search(
    origin: str,
    destination: str,
    date: str,
    *,
    return_date: Optional[str] = None,
    cabin: CabinClass = "economy",
    passengers: Passengers = Passengers(),
    max_stops: Optional[int] = None,
    sort: int = SORT_DEPARTURE_TIME,
    airlines: Optional[list[str]] = None,
    flight_number: Optional[str] = None,
    include_basic_economy: bool = False,
    earliest_departure: Optional[int] = None,
    latest_departure: Optional[int] = None,
    earliest_arrival: Optional[int] = None,
    latest_arrival: Optional[int] = None,
    return_earliest_departure: Optional[int] = None,
    return_latest_departure: Optional[int] = None,
    transport: TransportConfig = TransportConfig(),
    max_results: Optional[int] = None,
    beam_width: Optional[int] = None,
    time_budget: Optional[int] = None,
) -> SearchResult:
    """Search Google Flights and return decoded results.

    Args:
        origin: Origin airport IATA code (e.g. ``"JFK"``).
        destination: Destination airport IATA code (e.g. ``"LAX"``).
        date: Departure date as ``YYYY-MM-DD``.
        return_date: Return date for roundtrip searches. Omit for one-way.
        cabin: Cabin class — ``"economy"``, ``"premium-economy"``,
            ``"business"``, or ``"first"``.
        passengers: Passenger counts (default ``Passengers()``).
        max_stops: Maximum stops. ``None`` = any, ``0`` = nonstop,
            ``1`` = one stop, ``2`` = two stops.
        sort: Sort order. Use ``SORT_TOP``, ``SORT_CHEAPEST``,
            ``SORT_DEPARTURE_TIME``, ``SORT_ARRIVAL_TIME``, or ``SORT_DURATION``.
        airlines: Filter to specific airline IATA codes (e.g. ``["DL", "UA"]``).
        flight_number: Filter results to a specific flight number (e.g.
            ``"DL 171"``, ``"DL171"``, or ``"171"``). When a carrier is
            included, it is also added to the RPC airline filter.
        include_basic_economy: When ``False`` (default), basic economy fares
            are excluded so prices reflect Main Cabin.  Set ``True`` to
            include basic economy fares.
        earliest_departure: Earliest departure hour (0–23).
        latest_departure: Latest departure hour (1–24).
        earliest_arrival: Earliest arrival hour (0–23).
        latest_arrival: Latest arrival hour (1–24).
        return_earliest_departure: Earliest return departure hour (0–23).
        return_latest_departure: Latest return departure hour (1–24).
        transport: HTTP transport configuration (default ``TransportConfig()``).
        max_results: Maximum trip combinations the beam search targets
            (default 10).  Only affects multi-leg (3+ city) searches.
        beam_width: Number of candidate prefixes carried between stages
            (default 15).  Only affects multi-leg searches.
        time_budget: Seconds before the beam search stops exploring
            (default 90).  Only affects multi-leg searches.

    Returns:
        A trip-level :class:`SearchResult` with shopping totals.

    Raises:
        SwoopHTTPError: If Google Flights returns a non-200 response.
        SwoopRateLimitError: If Google Flights returns HTTP 429.
        SwoopParseError: If the response cannot be parsed.

    Example::

        from swoop import search

        # One-way search
        results = search("SFO", "JFK", "2026-06-15")

        # Roundtrip search
        results = search("SFO", "JFK", "2026-06-15", return_date="2026-06-22")

        # Business class, nonstop only
        results = search("LAX", "NRT", "2026-06-15", cabin="business", max_stops=0)
    """
    # Parse flight number filter if provided
    parsed_carrier = None
    parsed_number = None
    if flight_number is not None:
        parsed_carrier, parsed_number = parse_flight_number(flight_number)

    validate_search_params(
        origin,
        destination,
        date,
        return_date=return_date,
        cabin=cabin,
        adults=passengers.adults,
        earliest_departure=earliest_departure,
        latest_departure=latest_departure,
        earliest_arrival=earliest_arrival,
        latest_arrival=latest_arrival,
        return_earliest_departure=return_earliest_departure,
        return_latest_departure=return_latest_departure,
    )

    first_leg_airlines = list(airlines) if airlines else None
    if parsed_carrier is not None:
        if first_leg_airlines is None:
            first_leg_airlines = [parsed_carrier]
        elif parsed_carrier not in first_leg_airlines:
            first_leg_airlines = list(first_leg_airlines) + [parsed_carrier]

    request_legs = [
        _normalize_rpc_leg(
            origin,
            destination,
            date,
            max_stops=max_stops,
            airlines=first_leg_airlines,
            earliest_departure=earliest_departure,
            latest_departure=latest_departure,
            earliest_arrival=earliest_arrival,
            latest_arrival=latest_arrival,
        )
    ]
    if return_date is not None:
        request_legs.append(
            _normalize_rpc_leg(
                destination,
                origin,
                return_date,
                max_stops=max_stops,
                airlines=list(airlines) if airlines else None,
                earliest_departure=return_earliest_departure,
                latest_departure=return_latest_departure,
            )
        )

    result = _search_with_normalized_legs(
        request_legs,
        cabin=cabin,
        passengers=passengers,
        sort=sort,
        include_basic_economy=include_basic_economy,
        transport=transport,
        max_results=max_results,
        beam_width=beam_width,
        time_budget=time_budget,
    )

    if parsed_number is not None:
        return _filter_trip_options_by_flight_number(result, parsed_carrier, parsed_number)
    return result


# ---------------------------------------------------------------------------
# check_price() — targeted price lookup for a known flight.
# ---------------------------------------------------------------------------


def price_legs(
    legs: list[SelectedLeg],
    *,
    cabin: CabinClass = "economy",
    passengers: Passengers = Passengers(),
    include_basic_economy: bool = False,
    transport: TransportConfig = TransportConfig(),
) -> Optional[PriceResult]:
    """Look up the current bookable fare using explicit leg definitions.

    Args:
        legs: List of :class:`SelectedLeg` objects (1 or more).
        cabin: Cabin class (default ``"economy"``).
        passengers: Passenger counts (default ``Passengers()``).
        include_basic_economy: Include basic economy fares (default ``False``).
        transport: HTTP transport configuration (default ``TransportConfig()``).

    Returns:
        A :class:`PriceResult` or ``None`` if the flight was not found.
    """
    if len(legs) == 0:
        raise ValueError("at least one leg is required")

    validate_cabin(cabin)
    validate_adults(passengers.adults)
    carrier_filters: list[Optional[str]] = []
    for index, leg in enumerate(legs):
        validate_iata_code(leg.origin, f"legs[{index}].origin")
        validate_iata_code(leg.destination, f"legs[{index}].destination")
        validate_date(leg.date, f"legs[{index}].date")
        carrier, _number = parse_flight_number(leg.flight_number)
        carrier_filters.append(carrier)

    request_legs = build_request_legs_from_selected(legs, carrier_filters=carrier_filters)
    resolved, selections, rpc_calls = resolve_selected_trip(
        request_legs,
        [leg.flight_number for leg in legs],
        cabin=cabin,
        passengers=passengers,
        transport=transport,
        exclude_basic_economy=(
            cabin == "economy"
            and len(legs) == 1
            and not include_basic_economy
        ),
    )
    if not resolved:
        return None

    return price_selected_trip(
        request_legs,
        resolved,
        cabin=cabin,
        passengers=passengers,
        include_basic_economy=include_basic_economy,
        transport=transport,
        rpc_calls=rpc_calls,
        selections=selections,
    )


def price_selector(
    selector: str,
    *,
    transport: TransportConfig = TransportConfig(),
) -> Optional[PriceResult]:
    """Look up the current bookable fare for an itinerary selector.

    Selectors come from trip-level search results and preserve the exact
    itinerary identity across CLI or API calls.

    Args:
        selector: Opaque selector from :func:`search` or :func:`search_legs`.
        transport: HTTP transport configuration (default ``TransportConfig()``).

    Returns:
        A :class:`PriceResult`, or ``None`` if the selected itinerary no
        longer exists.
    """
    return price_trip_selector(selector, transport=transport)


def check_price(
    flight_number: str,
    *,
    origin: str,
    destination: str,
    date: str,
    return_flight_number: Optional[str] = None,
    return_date: Optional[str] = None,
    cabin: CabinClass = "economy",
    passengers: Passengers = Passengers(),
    max_stops: Optional[int] = None,
    include_basic_economy: bool = False,
    transport: TransportConfig = TransportConfig(),
) -> Optional[PriceResult]:
    """Look up the current bookable fare for a specific flight.

    Unlike :func:`search` which returns all itineraries on a route,
    ``check_price`` is optimized for the "what does flight X cost today?"
    use case.

    Args:
        flight_number: Outbound flight number (e.g. ``"DL2300"``).
        origin: Origin airport IATA code.
        destination: Destination airport IATA code.
        date: Departure date as ``YYYY-MM-DD``.
        return_flight_number: Return flight number for roundtrip.
        return_date: Return date for roundtrip.
        cabin: Cabin class (default ``"economy"``).
        passengers: Passenger counts (default ``Passengers()``).
        max_stops: Maximum stops (default any).
        include_basic_economy: Include basic economy fares (default ``False``).
        transport: HTTP transport configuration (default ``TransportConfig()``).

    Returns:
        A :class:`PriceResult` with the price and matched itinerary,
        or ``None`` if the flight was not found.

    Example::

        from swoop import check_price

        # One-way
        result = check_price("DL2300", origin="JFK", destination="LAX", date="2026-06-15")
        if result:
            print(f"${result.price}")

        # Roundtrip
        result = check_price(
            "DL2300", origin="JFK", destination="LAX", date="2026-06-15",
            return_flight_number="DL2301", return_date="2026-06-22",
        )
        if result:
            print(f"${result.price} roundtrip")
    """
    # Parse requested flight numbers for airline narrowing
    outbound_carrier, _ = parse_flight_number(flight_number)
    return_carrier = None
    if return_flight_number is not None:
        return_carrier, _ = parse_flight_number(return_flight_number)

    validate_search_params(
        origin, destination, date,
        return_date=return_date, cabin=cabin, adults=passengers.adults,
    )

    request_legs = [
        _normalize_rpc_leg(
            origin,
            destination,
            date,
            max_stops=max_stops,
            airlines=[outbound_carrier] if outbound_carrier else None,
        )
    ]
    requested_flights: list[Optional[str]] = [flight_number]
    if return_date is not None:
        request_legs.append(
            _normalize_rpc_leg(
                destination,
                origin,
                return_date,
                max_stops=max_stops,
                airlines=[return_carrier] if return_carrier else None,
            )
        )
        requested_flights.append(return_flight_number)

    resolved, selections, rpc_calls = resolve_selected_trip(
        request_legs,
        requested_flights,
        cabin=cabin,
        passengers=passengers,
        transport=transport,
        exclude_basic_economy=(
            cabin == "economy"
            and return_date is None
            and not include_basic_economy
        ),
    )
    if not resolved:
        return None

    return price_selected_trip(
        request_legs,
        resolved,
        cabin=cabin,
        passengers=passengers,
        include_basic_economy=include_basic_economy,
        transport=transport,
        rpc_calls=rpc_calls,
        selections=selections,
    )


# ---------------------------------------------------------------------------
# deals() — discover the best flight deals from an origin airport.
# ---------------------------------------------------------------------------


def deals(
    origin: str,
    *,
    cabin: CabinClass = "economy",
    max_stops: Optional[int] = None,
    passengers: Passengers = Passengers(),
    transport: TransportConfig = TransportConfig(),
) -> DealsResult:
    """Discover the best flight deals from an origin airport.

    Returns up to 30 deals that Google Flights considers the best
    value from the given origin. Each deal includes destination, dates,
    price, and discount percentage.

    Args:
        origin: Origin airport IATA code (e.g. ``"JFK"``).
        cabin: Cabin class (default ``"economy"``).
        max_stops: Maximum stops. ``None`` = any, ``0`` = nonstop.
        passengers: Passenger counts (default ``Passengers()``).
        transport: HTTP transport configuration (default ``TransportConfig()``).

    Returns:
        A :class:`DealsResult` containing up to 30 :class:`Deal` objects.

    Example::

        from swoop import deals

        result = deals("JFK", cabin="business", max_stops=0)
        for deal in result.deals:
            print(f"{deal.destination_city} ${deal.price} ({deal.discount_pct}% off)")
    """
    validate_iata_code(origin, "origin")
    validate_cabin(cabin)
    validate_adults(passengers.adults)

    from ._deals import fetch_deals

    return fetch_deals(
        origin,
        cabin=cabin,
        max_stops=max_stops,
        passengers=passengers,
        transport=transport,
    )


__all__ = [
    # Functions
    "search",
    "search_legs",
    "check_price",
    "price_selector",
    "price_legs",
    "deals",
    "get_booking_results",
    "search_raw",
    "set_country",
    "set_proxy",
    "parse_flight_number",
    "itinerary_matches_flight",
    # Types
    "CabinClass",
    "Deal",
    "DealsResult",
    "Passengers",
    "TransportConfig",
    "PriceResult",
    "RawSearchResult",
    "SearchResult",
    "SearchLeg",
    "SelectedLeg",
    "ResolvedLeg",
    "TripLeg",
    "TripOption",
    "Itinerary",
    "Segment",
    "BookingOption",
    "Codeshare",
    "Layover",
    "CarbonEmissions",
    "PriceRange",
    "AmenityFlags",
    "QualitySignals",
    # Exceptions
    "SwoopError",
    "SwoopHTTPError",
    "SwoopParseError",
    "SwoopRateLimitError",
    # Constants
    "SORT_TOP",
    "SORT_CHEAPEST",
    "SORT_DEPARTURE_TIME",
    "SORT_ARRIVAL_TIME",
    "SORT_DURATION",
    "STOPS_ANY",
    "STOPS_NONSTOP",
    "STOPS_ONE_OR_FEWER",
    "STOPS_TWO_OR_FEWER",
]


# Hide implementation details from `dir(swoop)` and tab completion (PEP 562).
# Names remain in globals() so internal call sites still resolve; only the
# perceived public surface — what shows up in tab completion and `dir()` —
# is curated to __all__.
def __dir__() -> list[str]:
    return sorted(__all__)

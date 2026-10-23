"""Staged trip selection, selectors, and exact-trip pricing helpers."""

from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import replace
from typing import Any, Optional

from .builders import CabinClass
from ._pricing import _validate_pricing, _validate_routing
from ._validate import parse_flight_number, validate_cabin, validate_date, validate_iata_code, validate_adults, validate_time_range
from .decoder import Itinerary, RawSearchResult, itinerary_matches_flight
from .exceptions import SwoopError, SwoopUpstreamError
from .models import Passengers, PriceResult, ResolvedLeg, SearchResult, TransportConfig, TripLeg, TripOption
from .rpc import (
    SORT_DEPARTURE_TIME,
    _build_selected_legs,
    _normalize_rpc_leg,
    _search_from_legs,
    get_trip_booking_results,
)

logger = logging.getLogger(__name__)

TARGET_RESULTS = 10
BEAM_WIDTH = 15
TIME_BUDGET_SECONDS = 90
SELECTOR_PREFIX = "swoop:sel:1:"


def _iter_raw_itineraries(result: Optional[RawSearchResult]) -> list[Itinerary]:
    if result is None:
        return []
    return [*result.best, *result.other]


def _copy_request_leg(leg: dict[str, Any]) -> dict[str, Any]:
    copied = dict(leg)
    for key in ("airlines", "connecting_airports", "exclude_connecting_airports"):
        if copied.get(key) is not None:
            copied[key] = list(copied[key])
    if copied.get("selected_legs") is not None:
        copied["selected_legs"] = [list(item) for item in copied["selected_legs"]]
    return copied


def _selector_query_leg(leg: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "origin",
        "destination",
        "date",
        "max_stops",
        "airlines",
        "earliest_departure",
        "latest_departure",
        "earliest_arrival",
        "latest_arrival",
        "max_duration",
        "connecting_airports",
        "exclude_connecting_airports",
        "min_layover_duration",
        "max_layover_duration",
    )
    payload = {key: v for key in keys if (v := leg.get(key)) is not None}
    for key in ("airlines", "connecting_airports", "exclude_connecting_airports"):
        if payload.get(key) is not None:
            payload[key] = list(payload[key])
    return payload


def _encode_payload(payload: dict[str, Any]) -> str:
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    ).decode()
    return encoded.rstrip("=")


def encode_trip_selector(
    *,
    request_legs: list[dict[str, Any]],
    itineraries: list[Itinerary],
    cabin: CabinClass,
    passengers: Passengers = Passengers(),
    include_basic_economy: bool,
    sort: int = SORT_DEPARTURE_TIME,
    carry_on_bags: int = 0,
    checked_bags: int = 0,
    max_price: Optional[int] = None,
) -> str:
    payload = {
        "v": 1,
        "carry_on_bags": carry_on_bags,
        "checked_bags": checked_bags,
        "max_price": max_price,
        "query_legs": [_selector_query_leg(leg) for leg in request_legs],
        "selected_legs": [_build_selected_legs(itinerary) for itinerary in itineraries],
        "cabin": cabin,
        "passengers": {
            "adults": passengers.adults,
            "children": passengers.children,
            "infants_in_seat": passengers.infants_in_seat,
            "infants_on_lap": passengers.infants_on_lap,
        },
        "include_basic_economy": include_basic_economy,
        "sort": sort,
        "booking_token_hint": itineraries[-1].booking_token or None,
    }
    return f"{SELECTOR_PREFIX}{_encode_payload(payload)}"


def decode_trip_selector(selector: str) -> dict[str, Any]:
    if not isinstance(selector, str) or not selector.startswith(SELECTOR_PREFIX):
        raise ValueError("invalid selector format")
    encoded = selector[len(SELECTOR_PREFIX):]
    padding = "=" * (-len(encoded) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(f"{encoded}{padding}"))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid selector payload") from exc
    if payload.get("v") != 1:
        raise ValueError("unsupported selector version")
    # Reconstruct Passengers with backward compat for old selectors
    if "passengers" in payload:
        pax = payload["passengers"]
        payload["passengers"] = Passengers(
            adults=pax.get("adults", 1),
            children=pax.get("children", 0),
            infants_in_seat=pax.get("infants_in_seat", 0),
            infants_on_lap=pax.get("infants_on_lap", 0),
        )
    else:
        # Old selectors with flat keys
        payload["passengers"] = Passengers(
            adults=payload.pop("adults", 1),
            children=payload.pop("children", 0),
            infants_in_seat=payload.pop("infants_in_seat", 0),
            infants_on_lap=payload.pop("infants_on_lap", 0),
        )
    return payload


def _resolved_leg_from_itinerary(
    itinerary: Itinerary,
    *,
    origin: str,
    destination: str,
    date: str,
    selection: str,
) -> ResolvedLeg:
    first = itinerary.segments[0] if itinerary.segments else None
    flight_summary = ""
    if first is not None:
        flight_summary = (
            f"{first.airline} {first.flight_number}"
            if first.airline
            else str(first.flight_number or "")
        )
    return ResolvedLeg(
        flight_summary=flight_summary,
        origin=origin,
        destination=destination,
        date=date,
        itinerary=itinerary,
        selection=selection,
    )


def _clone_leg_itinerary(itinerary: Itinerary) -> Itinerary:
    return replace(itinerary, price_info=None, direct_price=None)


def _trip_legs_from_itineraries(
    request_legs: list[dict[str, Any]],
    itineraries: list[Itinerary],
) -> list[TripLeg]:
    return [
        TripLeg(
            origin=str(request_legs[index]["origin"]),
            destination=str(request_legs[index]["destination"]),
            date=str(request_legs[index]["date"]),
            itinerary=_clone_leg_itinerary(itinerary),
        )
        for index, itinerary in enumerate(itineraries)
    ]


def _build_trip_option(
    request_legs: list[dict[str, Any]],
    itineraries: list[Itinerary],
    *,
    cabin: CabinClass,
    passengers: Passengers = Passengers(),
    include_basic_economy: bool,
    sort: int = SORT_DEPARTURE_TIME,
    carry_on_bags: int = 0,
    checked_bags: int = 0,
    max_price: Optional[int] = None,
) -> TripOption:
    return TripOption(
        selector=encode_trip_selector(
            carry_on_bags=carry_on_bags,
            checked_bags=checked_bags,
            max_price=max_price,
            request_legs=request_legs,
            itineraries=itineraries,
            cabin=cabin,
            passengers=passengers,
            include_basic_economy=include_basic_economy,
            sort=sort,
        ),
        price=itineraries[-1].price,
        currency=itineraries[-1].currency,
        legs=_trip_legs_from_itineraries(request_legs, itineraries),
    )


def _with_selected_prefix(
    request_legs: list[dict[str, Any]],
    selected_payloads: list[list[list[Any]]],
) -> list[dict[str, Any]]:
    staged: list[dict[str, Any]] = []
    for index, leg in enumerate(request_legs):
        staged_leg = _copy_request_leg(leg)
        if index < len(selected_payloads):
            staged_leg["selected_legs"] = [list(item) for item in selected_payloads[index]]
        else:
            staged_leg.pop("selected_legs", None)
        staged.append(staged_leg)
    return staged


def _selected_payloads_for_itineraries(
    itineraries: list[Itinerary],
) -> Optional[list[list[list[Any]]]]:
    selected_payloads: list[list[list[Any]]] = []
    for itinerary in itineraries:
        selected = _build_selected_legs(itinerary)
        if not selected:
            return None
        selected_payloads.append(selected)
    return selected_payloads


def fetch_trip_booking_options(
    request_legs: list[dict[str, Any]],
    itineraries: list[Itinerary],
    *,
    cabin: CabinClass,
    passengers: Passengers = Passengers(),
    transport: TransportConfig = TransportConfig(),
    carry_on_bags: int = 0,
    checked_bags: int = 0,
    max_price: Optional[int] = None,
) -> list:
    selected_payloads = _selected_payloads_for_itineraries(itineraries)
    if selected_payloads is None:
        return []
    final_token = itineraries[-1].booking_token
    if not final_token:
        return []
    staged_legs = _with_selected_prefix(request_legs, selected_payloads)
    return get_trip_booking_results(
        final_token,
        staged_legs,
        carry_on_bags=carry_on_bags,
        checked_bags=checked_bags,
        max_price=max_price,
        cabin=cabin,
        passengers=passengers,
        transport=transport,
    )


def _eligible_booking_options(
    options: list,
    include_basic_economy: bool,
    *,
    cabin: CabinClass,
) -> list:
    priced = [option for option in options if option.price > 0]
    if cabin == "economy":
        economy_opts = [
            option for option in priced if option._cabin_bucket in ("", "economy")
        ]
        if not include_basic_economy:
            economy_opts = [opt for opt in economy_opts if not opt.is_basic]
        return economy_opts

    return [
        option for option in priced if option._cabin_bucket == cabin
    ]



def search_trip_options(
    request_legs: list[dict[str, Any]],
    *,
    cabin: CabinClass = "economy",
    passengers: Passengers = Passengers(),
    sort: int = SORT_DEPARTURE_TIME,
    include_basic_economy: bool = False,
    transport: TransportConfig = TransportConfig(),
    max_results: Optional[int] = None,
    beam_width: Optional[int] = None,
    time_budget: Optional[int] = None,
    carry_on_bags: int = 0,
    checked_bags: int = 0,
    max_price: Optional[int] = None,
) -> SearchResult:
    if not request_legs:
        return SearchResult()

    max_results = max_results if max_results is not None else TARGET_RESULTS
    beam_width = beam_width if beam_width is not None else BEAM_WIDTH
    time_budget = time_budget if time_budget is not None else TIME_BUDGET_SECONDS

    exclude_basic = cabin == "economy" and not include_basic_economy
    first_pass = _search_from_legs(
        request_legs,
        carry_on_bags=carry_on_bags,
        checked_bags=checked_bags,
        max_price=max_price,
        cabin=cabin,
        passengers=passengers,
        sort=sort,
        transport=transport,
        exclude_basic_economy=exclude_basic,
        retain_raw=False,
    )
    if first_pass is None:
        return SearchResult()

    first_candidates = _iter_raw_itineraries(first_pass)
    if len(request_legs) <= 2:
        # One-way and roundtrip: first pass already returns full prices.
        # For roundtrip, Google prices both legs in one call — the itinerary
        # price is the roundtrip total.  Beam search is only needed for
        # 3+ leg multi-city trips.
        return SearchResult(
            results=[
                _build_trip_option(
                    request_legs,
                    [itinerary],
                    carry_on_bags=carry_on_bags,
                    checked_bags=checked_bags,
                    max_price=max_price,
                    cabin=cabin,
                    passengers=passengers,
                    include_basic_economy=include_basic_economy,
                    sort=sort,
                )
                for itinerary in first_candidates
            ],
            price_range=first_pass.price_range,
            is_complete=True,
        )

    started_at = time.monotonic()
    is_complete = len(first_candidates) <= beam_width
    prefixes = [[itinerary] for itinerary in first_candidates[:beam_width]]
    # Remember the last staged upstream rejection. A single bad branch degrades
    # gracefully (below), but if the beam collapses to zero results entirely it
    # was an outage, not "no such trip" — surface it rather than returning an
    # empty SearchResult the CLI would render as "No flights found".
    upstream_error: Optional[SwoopUpstreamError] = None

    for _ in range(1, len(request_legs)):
        next_prefixes: list[list[Itinerary]] = []
        if not prefixes:
            break

        for prefix_index, prefix in enumerate(prefixes):
            if time.monotonic() - started_at >= time_budget:
                is_complete = False
                break

            selected_payloads = _selected_payloads_for_itineraries(prefix)
            if selected_payloads is None:
                is_complete = False
                continue

            staged_legs = _with_selected_prefix(request_legs, selected_payloads)
            try:
                stage_result = _search_from_legs(
                    staged_legs,
                    carry_on_bags=carry_on_bags,
                    checked_bags=checked_bags,
                    max_price=max_price,
                    cabin=cabin,
                    passengers=passengers,
                    sort=sort,
                    transport=transport,
                    exclude_basic_economy=exclude_basic,
                    retain_raw=False,
                )
            except SwoopUpstreamError as exc:
                # A transient upstream rejection on one beam branch must not
                # sink the whole multi-city search. Before SwoopUpstreamError
                # existed this path returned None and was absorbed here as an
                # empty stage; preserve that graceful degradation and just mark
                # the result incomplete. The first pass (no prefix yet) still
                # raises, since a rejected first call means no results at all.
                upstream_error = exc
                is_complete = False
                continue
            stage_candidates = _iter_raw_itineraries(stage_result)
            if not stage_candidates:
                continue

            remaining = beam_width - len(next_prefixes)
            if len(stage_candidates) > remaining:
                is_complete = False
            for candidate in stage_candidates[:remaining]:
                next_prefixes.append(prefix + [candidate])

            if len(next_prefixes) >= beam_width:
                if prefix_index < len(prefixes) - 1 or len(stage_candidates) > remaining:
                    is_complete = False
                break

        prefixes = next_prefixes

    if len(prefixes) > max_results:
        is_complete = False

    options = [
        _build_trip_option(
            request_legs,
            prefix,
            carry_on_bags=carry_on_bags,
            checked_bags=checked_bags,
            max_price=max_price,
            cabin=cabin,
            passengers=passengers,
            include_basic_economy=include_basic_economy,
            sort=sort,
        )
        for prefix in prefixes[:max_results]
    ]
    if not options and upstream_error is not None:
        # Every beam branch was rejected upstream — this is an outage, not an
        # empty itinerary set. Surface it instead of an empty SearchResult.
        raise upstream_error
    result = SearchResult(results=options, price_range=None, is_complete=is_complete)

    return result


def _match_itinerary_by_selected_segments(
    candidates: list[Itinerary],
    selected_segments: list[list[Any]],
) -> Optional[Itinerary]:
    for itinerary in candidates:
        if _build_selected_legs(itinerary) == selected_segments:
            return itinerary
    return None


def resolve_trip_selector(
    selector: str,
    *,
    transport: TransportConfig = TransportConfig(),
) -> tuple[dict[str, Any], list[dict[str, Any]], list[Itinerary], int]:
    try:
        payload = decode_trip_selector(selector)
        query_legs = payload["query_legs"]
        selected_legs = payload["selected_legs"]
        if (not isinstance(query_legs, list) or not 1 <= len(query_legs) <= 6
                or not all(isinstance(leg, dict) for leg in query_legs)):
            raise ValueError("invalid selector query legs")
        if (not isinstance(selected_legs, list) or len(selected_legs) > len(query_legs)
                or not all(isinstance(leg, list) and leg and all(
                    isinstance(flight, list) and len(flight) == 6 for flight in leg)
                    for leg in selected_legs)):
            raise ValueError("invalid selector selected legs")
        validate_cabin(payload["cabin"])
        validate_adults(payload["passengers"].adults)
        party = payload["passengers"]
        if any(type(count) is not int or count < 0 for count in (
                party.adults, party.children, party.infants_in_seat, party.infants_on_lap)):
            raise ValueError("invalid selector passenger counts")
        if type(payload.get("sort", SORT_DEPARTURE_TIME)) is not int:
            raise ValueError("invalid selector sort")
        if not isinstance(payload["include_basic_economy"], bool):
            raise ValueError("invalid selector fare filter")
        _validate_pricing(payload.get("carry_on_bags", 0), payload.get("checked_bags", 0), payload.get("max_price"))
        for leg in query_legs:
            validate_iata_code(leg["origin"], "origin")
            validate_iata_code(leg["destination"], "destination")
            validate_date(leg["date"], "date")
            stops = leg.get("max_stops")
            if stops is not None and (type(stops) is not int or stops < 0):
                raise ValueError("invalid selector stop filter")
            airlines = leg.get("airlines")
            if airlines is not None and (not isinstance(airlines, list)
                    or not all(isinstance(code, str) for code in airlines)):
                raise ValueError("invalid selector airline filter")
            for field, minimum, maximum in (("earliest_departure", 0, 23), ("latest_departure", 1, 24),
                    ("earliest_arrival", 0, 23), ("latest_arrival", 1, 24)):
                validate_time_range(leg.get(field), field, minimum, maximum)
            _validate_routing(leg.get("max_duration"), leg.get("connecting_airports"),
                              leg.get("exclude_connecting_airports"), leg.get("min_layover_duration"),
                              leg.get("max_layover_duration"))
        for flights in selected_legs:
            for flight in flights:
                validate_iata_code(flight[0], "selected origin")
                validate_iata_code(flight[2], "selected destination")
                validate_date(flight[1], "selected date")
                if not isinstance(flight[4], str) or not isinstance(flight[5], str):
                    raise ValueError("invalid selected flight identity")
                parse_flight_number(f"{flight[4]}{flight[5]}")
        request_legs = [_copy_request_leg(leg) for leg in query_legs]
    except (TypeError, AttributeError, KeyError) as exc:
        raise ValueError("invalid selector context") from exc
    resolved: list[Itinerary] = []
    rpc_calls = 0

    replay_sort = payload.get("sort", SORT_DEPARTURE_TIME)
    exclude_basic = payload["cabin"] == "economy" and not payload["include_basic_economy"]

    for index in range(len(request_legs)):
        staged_legs = _with_selected_prefix(request_legs, selected_legs[:index])
        stage_result = _search_from_legs(
            staged_legs,
            carry_on_bags=payload.get("carry_on_bags", 0),
            checked_bags=payload.get("checked_bags", 0),
            max_price=payload.get("max_price"),
            cabin=payload["cabin"],
            passengers=payload["passengers"],
            sort=replay_sort,
            transport=transport,
            exclude_basic_economy=exclude_basic,
            retain_raw=False,
        )
        rpc_calls += 1
        candidates = _iter_raw_itineraries(stage_result)
        if index < len(selected_legs):
            itinerary = _match_itinerary_by_selected_segments(candidates, selected_legs[index])
        else:
            # Auto-select first candidate (e.g. return leg from roundtrip fast path)
            itinerary = candidates[0] if candidates else None
        if itinerary is None:
            raise ValueError("selector itinerary no longer available")
        resolved.append(itinerary)

    return payload, request_legs, resolved, rpc_calls


def price_selected_trip(
    request_legs: list[dict[str, Any]],
    itineraries: list[Itinerary],
    *,
    cabin: CabinClass = "economy",
    passengers: Passengers = Passengers(),
    include_basic_economy: bool = False,
    transport: TransportConfig = TransportConfig(),
    rpc_calls: int = 0,
    selections: Optional[list[str]] = None,
    carry_on_bags: int = 0,
    checked_bags: int = 0,
    max_price: Optional[int] = None,
) -> Optional[PriceResult]:
    if not itineraries:
        return None

    if selections is None:
        selections = ["explicit"] * len(itineraries)

    resolved_legs = [
        _resolved_leg_from_itinerary(
            itinerary,
            origin=str(request_legs[index]["origin"]),
            destination=str(request_legs[index]["destination"]),
            date=str(request_legs[index]["date"]),
            selection=selections[index],
        )
        for index, itinerary in enumerate(itineraries)
    ]

    final_itinerary = itineraries[-1]
    base_price = final_itinerary.price
    booking_options = []
    selected_payloads = _selected_payloads_for_itineraries(itineraries)
    if selected_payloads is not None and final_itinerary.booking_token:
        try:
            booking_options = fetch_trip_booking_options(
                request_legs,
                itineraries,
                carry_on_bags=carry_on_bags,
                checked_bags=checked_bags,
                max_price=max_price,
                cabin=cabin,
                passengers=passengers,
                transport=transport,
            )
            rpc_calls += 1
        except SwoopUpstreamError:
            # A Google outage during the bookable-price lookup must surface, not
            # silently fall back to the unverified search estimate — the price
            # docstrings promise SwoopUpstreamError. Other booking failures
            # (parse, transient HTTP) stay best-effort and degrade to base_price.
            raise
        except SwoopError as exc:
            logger.debug("Trip booking lookup failed: %s", exc)

    if booking_options:
        eligible = _eligible_booking_options(
            booking_options,
            include_basic_economy,
            cabin=cabin,
        )
        if max_price is not None:
            eligible = [o for o in eligible if o.price <= max_price]
        if eligible:
            best_option = min(eligible, key=lambda option: option.price)
            return PriceResult(
                price=best_option.price,
                currency=final_itinerary.currency,
                fare_brand=best_option.brand_label or best_option.brand_code or None,
                is_basic_economy=best_option.is_basic,
                is_estimate=False,  # price came from a real booking option
                booking_options=booking_options,
                itinerary=final_itinerary,
                resolved_legs=resolved_legs,
                rpc_calls=rpc_calls,
            )

    if (
        base_price is None
        or base_price <= 0
        or (max_price is not None and base_price > max_price)
    ):
        return None

    # No eligible booking option (lookup skipped, degraded, or none in cabin):
    # the price is the search-derived shopping estimate, not a confirmed fare.
    return PriceResult(
        price=base_price,
        currency=final_itinerary.currency,
        is_estimate=True,
        booking_options=booking_options,
        itinerary=final_itinerary,
        resolved_legs=resolved_legs,
        rpc_calls=rpc_calls,
    )


def resolve_selected_trip(
    request_legs: list[dict[str, Any]],
    requested_flights: list[Optional[str]],
    *,
    cabin: CabinClass = "economy",
    passengers: Passengers = Passengers(),
    transport: TransportConfig = TransportConfig(),
    exclude_basic_economy: bool = False,
    carry_on_bags: int = 0,
    checked_bags: int = 0,
    max_price: Optional[int] = None,
) -> tuple[list[Itinerary], list[str], int]:
    resolved: list[Itinerary] = []
    selections: list[str] = []
    rpc_calls = 0

    for requested_flight in requested_flights:
        selected_payloads = _selected_payloads_for_itineraries(resolved)
        if resolved and selected_payloads is None:
            return [], [], rpc_calls
        staged_legs = _with_selected_prefix(request_legs, selected_payloads or [])
        stage_result = _search_from_legs(
            staged_legs,
            carry_on_bags=carry_on_bags,
            checked_bags=checked_bags,
            max_price=max_price,
            cabin=cabin,
            passengers=passengers,
            sort=SORT_DEPARTURE_TIME,
            transport=transport,
            exclude_basic_economy=exclude_basic_economy,
            retain_raw=False,
        )
        rpc_calls += 1
        candidates = _iter_raw_itineraries(stage_result)
        if not candidates:
            return [], [], rpc_calls

        selection = "auto"
        selected_itinerary = candidates[0]
        if requested_flight is not None:
            carrier, number = parse_flight_number(requested_flight)
            matched = [
                itinerary
                for itinerary in candidates
                if itinerary_matches_flight(itinerary, carrier, number)
            ]
            if not matched:
                return [], [], rpc_calls
            selected_itinerary = matched[0]
            selection = "explicit"

        resolved.append(selected_itinerary)
        selections.append(selection)

    return resolved, selections, rpc_calls


def price_trip_selector(
    selector: str,
    *,
    transport: TransportConfig = TransportConfig(),
) -> Optional[PriceResult]:
    try:
        payload, request_legs, itineraries, rpc_calls = resolve_trip_selector(
            selector,
            transport=transport,
        )
    except ValueError:
        return None
    return price_selected_trip(
        request_legs,
        itineraries,
        carry_on_bags=payload.get("carry_on_bags", 0),
        checked_bags=payload.get("checked_bags", 0),
        max_price=payload.get("max_price"),
        cabin=payload["cabin"],
        passengers=payload["passengers"],
        include_basic_economy=payload["include_basic_economy"],
        transport=transport,
        rpc_calls=rpc_calls,
    )


def build_request_legs_from_selected(
    legs: list,
    *,
    carrier_filters: Optional[list[Optional[str]]] = None,
) -> list[dict[str, Any]]:
    request_legs: list[dict[str, Any]] = []
    for index, leg in enumerate(legs):
        airlines: Optional[list[str]] = None
        if carrier_filters:
            filter_val = carrier_filters[index]
            if filter_val is not None:
                airlines = [filter_val]
        request_legs.append(
            _normalize_rpc_leg(
                leg.origin,
                leg.destination,
                leg.date,
                airlines=airlines,
            )
        )
    return request_legs

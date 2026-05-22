"""Staged trip selection, selectors, and exact-trip pricing helpers."""

from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import replace
from typing import Any, Optional

from .builders import CabinClass
from ._validate import parse_flight_number
from .decoder import Itinerary, RawSearchResult, itinerary_matches_flight
from .exceptions import SwoopError
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
    if copied.get("airlines") is not None:
        copied["airlines"] = list(copied["airlines"])
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
    )
    payload = {key: v for key in keys if (v := leg.get(key)) is not None}
    if payload.get("airlines") is not None:
        payload["airlines"] = list(payload["airlines"])
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
) -> str:
    payload = {
        "v": 1,
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
    if not selector.startswith(SELECTOR_PREFIX):
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
) -> TripOption:
    return TripOption(
        selector=encode_trip_selector(
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
) -> SearchResult:
    if not request_legs:
        return SearchResult()

    max_results = max_results if max_results is not None else TARGET_RESULTS
    beam_width = beam_width if beam_width is not None else BEAM_WIDTH
    time_budget = time_budget if time_budget is not None else TIME_BUDGET_SECONDS

    exclude_basic = cabin == "economy" and not include_basic_economy
    first_pass = _search_from_legs(
        request_legs,
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

    for stage_index in range(1, len(request_legs)):
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
            stage_result = _search_from_legs(
                staged_legs,
                cabin=cabin,
                passengers=passengers,
                sort=sort,
                transport=transport,
                exclude_basic_economy=exclude_basic,
                retain_raw=False,
            )
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
            cabin=cabin,
            passengers=passengers,
            include_basic_economy=include_basic_economy,
            sort=sort,
        )
        for prefix in prefixes[:max_results]
    ]
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
    payload = decode_trip_selector(selector)
    request_legs = [_copy_request_leg(leg) for leg in payload["query_legs"]]
    selected_legs = payload["selected_legs"]
    resolved: list[Itinerary] = []
    rpc_calls = 0

    replay_sort = payload.get("sort", SORT_DEPARTURE_TIME)
    exclude_basic = payload["cabin"] == "economy" and not payload["include_basic_economy"]

    for index in range(len(request_legs)):
        staged_legs = _with_selected_prefix(request_legs, selected_legs[:index])
        stage_result = _search_from_legs(
            staged_legs,
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
                cabin=cabin,
                passengers=passengers,
                transport=transport,
            )
            rpc_calls += 1
        except SwoopError as exc:
            logger.debug("Trip booking lookup failed: %s", exc)

    if booking_options:
        eligible = _eligible_booking_options(
            booking_options,
            include_basic_economy,
            cabin=cabin,
        )
        if eligible:
            best_option = min(eligible, key=lambda option: option.price)
            return PriceResult(
                price=best_option.price,
                currency=final_itinerary.currency,
                fare_brand=best_option.brand_label or best_option.brand_code or None,
                is_basic_economy=best_option.is_basic,
                booking_options=booking_options,
                itinerary=final_itinerary,
                resolved_legs=resolved_legs,
                rpc_calls=rpc_calls,
            )

    if base_price is None or base_price <= 0:
        return None

    return PriceResult(
        price=base_price,
        currency=final_itinerary.currency,
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
) -> tuple[list[Itinerary], list[str], int]:
    resolved: list[Itinerary] = []
    selections: list[str] = []
    rpc_calls = 0

    for index, requested_flight in enumerate(requested_flights):
        selected_payloads = _selected_payloads_for_itineraries(resolved)
        if resolved and selected_payloads is None:
            return [], [], rpc_calls
        staged_legs = _with_selected_prefix(request_legs, selected_payloads or [])
        stage_result = _search_from_legs(
            staged_legs,
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

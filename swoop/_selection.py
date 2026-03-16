"""Staged trip selection, selectors, and exact-trip pricing helpers."""

from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import replace
from typing import Any, Optional

from ._validate import parse_flight_number
from .decoder import Itinerary, RawSearchResult, itinerary_matches_flight
from .exceptions import SwoopHTTPError, SwoopParseError
from .models import PriceResult, ResolvedLeg, SearchResult, TripLeg, TripOption
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
    payload = {key: leg.get(key) for key in keys if leg.get(key) is not None}
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
    cabin: str,
    adults: int,
    include_basic_economy: bool,
    sort: int = SORT_DEPARTURE_TIME,
) -> str:
    payload = {
        "v": 1,
        "query_legs": [_selector_query_leg(leg) for leg in request_legs],
        "selected_legs": [_build_selected_legs(itinerary) for itinerary in itineraries],
        "cabin": cabin,
        "adults": adults,
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
    return payload


def _resolved_leg_from_itinerary(
    itinerary: Itinerary,
    *,
    origin: str,
    destination: str,
    date: str,
    selection: str,
) -> ResolvedLeg:
    first = itinerary.flights[0] if itinerary.flights else None
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
    cabin: str,
    adults: int,
    include_basic_economy: bool,
    sort: int = SORT_DEPARTURE_TIME,
) -> TripOption:
    return TripOption(
        selector=encode_trip_selector(
            request_legs=request_legs,
            itineraries=itineraries,
            cabin=cabin,
            adults=adults,
            include_basic_economy=include_basic_economy,
            sort=sort,
        ),
        price=itineraries[-1].price,
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
    cabin: str,
    adults: int,
    timeout: int = 90,
    retries: int = 2,
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
        adults=adults,
        timeout=timeout,
        retries=retries,
    )


def _eligible_booking_options(
    options: list,
    include_basic_economy: bool,
    *,
    cabin: str,
    base_price: int | None = None,
) -> list:
    priced = [option for option in options if option.price > 0]
    if cabin == "economy":
        if include_basic_economy:
            return [
                option
                for option in priced
                if option._cabin_bucket in ("", "economy", "unknown")
            ]
        return [
            option
            for option in priced
            if not option.is_basic
            and option._cabin_bucket in ("", "economy", "unknown")
        ]

    exact_bucket = [
        option for option in priced if option._cabin_bucket == cabin
    ]
    if exact_bucket:
        # Price sanity check: reject options below 40% of base_price
        if base_price and base_price > 0 and cabin != "economy":
            sane = [opt for opt in exact_bucket if opt.price >= base_price * 0.4]
            if sane:
                return sane
            return []  # all suspiciously cheap → fall back to base_price
        return exact_bucket

    return []


def correct_trip_option_prices(
    result: SearchResult,
    *,
    request_legs: list[dict[str, Any]],
    include_basic_economy: bool,
    cabin: str,
    adults: int,
    timeout: int = 90,
    retries: int = 2,
) -> None:
    if cabin != "economy" or not result.results:
        return

    for option in result.results:
        itineraries = [leg.itinerary for leg in option.legs if leg.itinerary is not None]
        if len(itineraries) != len(request_legs):
            continue
        try:
            booking_options = fetch_trip_booking_options(
                request_legs,
                itineraries,
                cabin=cabin,
                adults=adults,
                timeout=timeout,
                retries=retries,
            )
        except (SwoopHTTPError, SwoopParseError) as exc:
            logger.debug("Trip booking correction failed: %s", exc)
            continue
        eligible = _eligible_booking_options(
            booking_options,
            include_basic_economy,
            cabin=cabin,
        )
        if not eligible:
            continue
        option.price = min(eligible, key=lambda booking_option: booking_option.price).price


def search_trip_options(
    request_legs: list[dict[str, Any]],
    *,
    cabin: str = "economy",
    adults: int = 1,
    sort: int = SORT_DEPARTURE_TIME,
    include_basic_economy: bool = False,
    correct_prices: bool = False,
    timeout: int = 90,
    retries: int = 2,
) -> SearchResult:
    if not request_legs:
        return SearchResult()

    exclude_basic = (
        cabin == "economy"
        and len(request_legs) == 1
        and not include_basic_economy
    )
    first_pass = _search_from_legs(
        request_legs,
        cabin=cabin,
        adults=adults,
        sort=sort,
        timeout=timeout,
        retries=retries,
        exclude_basic_economy=exclude_basic,
    )
    if first_pass is None:
        return SearchResult()

    first_candidates = _iter_raw_itineraries(first_pass)
    if len(request_legs) == 1:
        return SearchResult(
            results=[
                _build_trip_option(
                    request_legs,
                    [itinerary],
                    cabin=cabin,
                    adults=adults,
                    include_basic_economy=include_basic_economy,
                    sort=sort,
                )
                for itinerary in first_candidates
            ],
            price_range=first_pass.price_range,
            is_complete=True,
        )

    started_at = time.monotonic()
    is_complete = len(first_candidates) <= BEAM_WIDTH
    prefixes = [[itinerary] for itinerary in first_candidates[:BEAM_WIDTH]]

    for stage_index in range(1, len(request_legs)):
        next_prefixes: list[list[Itinerary]] = []
        if not prefixes:
            break

        for prefix_index, prefix in enumerate(prefixes):
            if time.monotonic() - started_at >= TIME_BUDGET_SECONDS:
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
                adults=adults,
                sort=sort,
                timeout=timeout,
                retries=retries,
                exclude_basic_economy=False,
            )
            stage_candidates = _iter_raw_itineraries(stage_result)
            if not stage_candidates:
                continue

            remaining = BEAM_WIDTH - len(next_prefixes)
            if len(stage_candidates) > remaining:
                is_complete = False
            for candidate in stage_candidates[:remaining]:
                next_prefixes.append(prefix + [candidate])

            if len(next_prefixes) >= BEAM_WIDTH:
                if prefix_index < len(prefixes) - 1 or len(stage_candidates) > remaining:
                    is_complete = False
                break

        prefixes = next_prefixes

    if len(prefixes) > TARGET_RESULTS:
        is_complete = False

    options = [
        _build_trip_option(
            request_legs,
            prefix,
            cabin=cabin,
            adults=adults,
            include_basic_economy=include_basic_economy,
            sort=sort,
        )
        for prefix in prefixes[:TARGET_RESULTS]
    ]
    result = SearchResult(results=options, price_range=None, is_complete=is_complete)

    if cabin == "economy" and not include_basic_economy and correct_prices:
        correct_trip_option_prices(
            result,
            request_legs=request_legs,
            include_basic_economy=include_basic_economy,
            cabin=cabin,
            adults=adults,
            timeout=timeout,
            retries=retries,
        )

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
    timeout: int = 90,
    retries: int = 2,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[Itinerary], int]:
    payload = decode_trip_selector(selector)
    request_legs = [_copy_request_leg(leg) for leg in payload["query_legs"]]
    selected_legs = payload["selected_legs"]
    resolved: list[Itinerary] = []
    rpc_calls = 0

    replay_sort = payload.get("sort", SORT_DEPARTURE_TIME)
    exclude_basic = (
        payload["cabin"] == "economy"
        and len(request_legs) == 1
        and not payload["include_basic_economy"]
    )

    for index in range(len(request_legs)):
        staged_legs = _with_selected_prefix(request_legs, selected_legs[:index])
        stage_result = _search_from_legs(
            staged_legs,
            cabin=payload["cabin"],
            adults=payload["adults"],
            sort=replay_sort,
            timeout=timeout,
            retries=retries,
            exclude_basic_economy=exclude_basic if index == 0 else False,
        )
        rpc_calls += 1
        candidates = _iter_raw_itineraries(stage_result)
        itinerary = _match_itinerary_by_selected_segments(candidates, selected_legs[index])
        if itinerary is None:
            raise ValueError("selector itinerary no longer available")
        resolved.append(itinerary)

    return payload, request_legs, resolved, rpc_calls


def price_selected_trip(
    request_legs: list[dict[str, Any]],
    itineraries: list[Itinerary],
    *,
    cabin: str = "economy",
    adults: int = 1,
    include_basic_economy: bool = False,
    timeout: int = 90,
    retries: int = 2,
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
    if len(itineraries) == 1:
        if base_price is None or base_price <= 0:
            return None
        return PriceResult(
            price=base_price,
            itinerary=final_itinerary,
            resolved_legs=resolved_legs,
            rpc_calls=rpc_calls,
        )

    try:
        booking_options = fetch_trip_booking_options(
            request_legs,
            itineraries,
            cabin=cabin,
            adults=adults,
            timeout=timeout,
            retries=retries,
        )
        rpc_calls += 1
    except (SwoopHTTPError, SwoopParseError) as exc:
        logger.debug("Trip booking lookup failed: %s", exc)
        booking_options = []

    if booking_options:
        eligible = _eligible_booking_options(
            booking_options,
            include_basic_economy,
            cabin=cabin,
            base_price=base_price,
        )
        if eligible:
            best_option = min(eligible, key=lambda option: option.price)
            return PriceResult(
                price=best_option.price,
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
        booking_options=booking_options,
        itinerary=final_itinerary,
        resolved_legs=resolved_legs,
        rpc_calls=rpc_calls,
    )


def resolve_selected_trip(
    request_legs: list[dict[str, Any]],
    requested_flights: list[Optional[str]],
    *,
    cabin: str = "economy",
    adults: int = 1,
    timeout: int = 90,
    retries: int = 2,
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
            adults=adults,
            sort=SORT_DEPARTURE_TIME,
            timeout=timeout,
            retries=retries,
            exclude_basic_economy=exclude_basic_economy if index == 0 else False,
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
    timeout: int = 90,
    retries: int = 2,
) -> Optional[PriceResult]:
    try:
        payload, request_legs, itineraries, rpc_calls = resolve_trip_selector(
            selector,
            timeout=timeout,
            retries=retries,
        )
    except ValueError:
        return None
    return price_selected_trip(
        request_legs,
        itineraries,
        cabin=payload["cabin"],
        adults=payload["adults"],
        include_basic_economy=payload["include_basic_economy"],
        timeout=timeout,
        retries=retries,
        rpc_calls=rpc_calls,
    )


def build_request_legs_from_selected(
    legs: list,
    *,
    carrier_filters: Optional[list[Optional[str]]] = None,
) -> list[dict[str, Any]]:
    request_legs: list[dict[str, Any]] = []
    for index, leg in enumerate(legs):
        airlines = None
        if carrier_filters and carrier_filters[index] is not None:
            airlines = [carrier_filters[index]]
        request_legs.append(
            _normalize_rpc_leg(
                leg.origin,
                leg.destination,
                leg.date,
                airlines=airlines,
            )
        )
    return request_legs

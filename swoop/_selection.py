"""Staged trip selection, selectors, and exact-trip pricing helpers."""

from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import replace
from typing import Any, Optional

from .builders import CabinClass
from ._validate import parse_flight_number, validate_cabin, validate_date, validate_iata_code
from .decoder import Itinerary, RawSearchResult, itinerary_matches_flight
from .exceptions import SwoopError, SwoopTransportError, SwoopUpstreamError
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
    exclude_separate_tickets: bool = False,
    show_all_results: bool = True,
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
        "show_all_results": show_all_results,
        "exclude_separate_tickets": exclude_separate_tickets,
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
    if not isinstance(payload, dict) or payload.get("v") != 1:
        raise ValueError("unsupported selector version")
    payload.setdefault("exclude_separate_tickets", False)
    if not isinstance(payload["exclude_separate_tickets"], bool):
        raise ValueError("invalid selector separate-ticket filter")
    # Old selectors were created from the shortened provider list.
    payload.setdefault("show_all_results", False)
    if not isinstance(payload["show_all_results"], bool):
        raise ValueError("invalid selector results mode")
    # Reconstruct Passengers with backward compat for old selectors
    if "passengers" in payload:
        pax = payload["passengers"]
        if not isinstance(pax, dict):
            raise ValueError("invalid selector passengers")
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
    exclude_separate_tickets: bool = False,
    show_all_results: bool = True,
) -> TripOption:
    return TripOption(
        selector=encode_trip_selector(
            request_legs=request_legs,
            itineraries=itineraries,
            cabin=cabin,
            passengers=passengers,
            include_basic_economy=include_basic_economy,
            sort=sort,
            show_all_results=show_all_results,
            exclude_separate_tickets=exclude_separate_tickets,
        ),
        price=itineraries[-1].price,
        currency=itineraries[-1].currency,
        legs=_trip_legs_from_itineraries(request_legs, itineraries),
        is_resolved=len(itineraries) == len(request_legs),
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
    exclude_separate_tickets: bool = False,
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
        exclude_separate_tickets=exclude_separate_tickets,
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



class _Coverage:
    """Accumulate coverage across provider calls without retaining raw bodies."""

    def __init__(self, show_all_results: bool):
        self.result = SearchResult(result_scope="all" if show_all_results else "default")
        if not show_all_results:
            self.reason("default_results")

    def reason(self, reason: str) -> None:
        if reason not in self.result.truncation_reasons:
            self.result.truncation_reasons.append(reason)
        self.result.is_complete = False

    def add(self, raw: Optional[RawSearchResult]) -> None:
        if raw is None:
            return
        decoded = len(raw.best) + len(raw.other)
        count = raw._raw_result_count if raw._raw_result_count is not None else decoded
        self.result.raw_result_count += count
        self.result.decoded_result_count += decoded
        if decoded < count:
            self.reason("parse_loss")
        if raw._result_scope == "limited":
            self.result.result_scope = "limited"
            self.reason("limited_transport")
        elif raw._result_scope == "default" and self.result.result_scope != "limited":
            self.result.result_scope = "default"
            self.reason("default_results")


def _budget_transport(transport: TransportConfig, deadline: Optional[float]) -> TransportConfig:
    if deadline is None:
        return transport
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise SwoopTransportError("Google Flights request budget exhausted")
    return replace(transport, timeout=min(transport.timeout, remaining))


def _deadline(time_budget: Optional[float]) -> Optional[float]:
    if time_budget is None:
        return None
    if time_budget <= 0:
        raise ValueError("time_budget must be positive")
    return time.monotonic() + time_budget


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
    time_budget: Optional[float] = None,
    expand_legs: bool = False,
    first_flight_filter: Optional[tuple[Optional[str], str]] = None,
    exclude_separate_tickets: bool = False,
    show_all_results: bool = True,
) -> SearchResult:
    coverage = _Coverage(show_all_results)
    if not request_legs:
        return coverage.result
    max_results = max_results if max_results is not None else TARGET_RESULTS
    beam_width = beam_width if beam_width is not None else BEAM_WIDTH
    time_budget = time_budget if time_budget is not None else TIME_BUDGET_SECONDS
    staged_search = expand_legs and len(request_legs) > 1
    if max_results <= 0 or beam_width <= 0:
        raise ValueError("max_results and beam_width must be positive")
    deadline = _deadline(time_budget)
    exclude_basic = cabin == "economy" and not include_basic_economy

    def fetch(legs: list[dict[str, Any]]) -> Optional[RawSearchResult]:
        bounded = _budget_transport(transport, deadline)
        coverage.result.rpc_calls += 1
        raw = _search_from_legs(legs, cabin=cabin, passengers=passengers, sort=sort,
            transport=bounded, exclude_basic_economy=exclude_basic,
            retain_raw=False, show_all_results=show_all_results,
            exclude_separate_tickets=exclude_separate_tickets)
        coverage.add(raw)
        return raw

    first_pass = fetch(request_legs)
    first_candidates = _iter_raw_itineraries(first_pass)
    if first_flight_filter is not None:
        carrier, number = first_flight_filter
        first_candidates = [itinerary for itinerary in first_candidates
                            if itinerary_matches_flight(itinerary, carrier, number)]

    def option(prefix: list[Itinerary]) -> TripOption:
        return _build_trip_option(request_legs, prefix, cabin=cabin, passengers=passengers,
            include_basic_economy=include_basic_economy, sort=sort,
            show_all_results=show_all_results,
            exclude_separate_tickets=exclude_separate_tickets)

    if not staged_search:
        coverage.result.results = [option([itinerary]) for itinerary in first_candidates]
        coverage.result.price_range = first_pass.price_range if first_pass else None
        return coverage.result

    if len(first_candidates) > beam_width:
        coverage.reason("beam_limit")
        coverage.result.unexpanded_prefixes += len(first_candidates) - beam_width
    prefixes = [[itinerary] for itinerary in first_candidates[:beam_width]]
    stage_error: Optional[SwoopError] = None
    for stage in range(1, len(request_legs)):
        branches: list[list[list[Itinerary]]] = []
        for index, prefix in enumerate(prefixes):
            if deadline is not None and time.monotonic() >= deadline:
                coverage.reason("time_budget")
                coverage.result.unexpanded_prefixes += len(prefixes) - index
                break
            selected = _selected_payloads_for_itineraries(prefix)
            if selected is None:
                coverage.reason("invalid_selection")
                coverage.result.unexpanded_prefixes += 1
                continue
            try:
                raw = fetch(_with_selected_prefix(request_legs, selected))
            except (SwoopUpstreamError, SwoopTransportError) as exc:
                if deadline is not None and time.monotonic() >= deadline:
                    coverage.reason("time_budget")
                    coverage.result.unexpanded_prefixes += len(prefixes) - index
                    break
                stage_error = exc
                coverage.reason("upstream_error" if isinstance(exc, SwoopUpstreamError) else "transport_error")
                coverage.result.unexpanded_prefixes += 1
                continue
            children = _iter_raw_itineraries(raw)
            if children:
                branches.append([prefix + [child] for child in children])
        size = sum(len(branch) for branch in branches)
        if size > beam_width:
            coverage.reason("beam_limit")
            if stage < len(request_legs) - 1:
                coverage.result.unexpanded_prefixes += size - beam_width
        prefixes = []
        for index in range(max((len(branch) for branch in branches), default=0)):
            for branch in branches:
                if index < len(branch):
                    prefixes.append(branch[index])
                    if len(prefixes) == beam_width:
                        break
            if len(prefixes) == beam_width:
                break
    if len(prefixes) > max_results:
        coverage.reason("result_limit")
    coverage.result.results = [option(prefix) for prefix in prefixes[:max_results]
                               if len(prefix) == len(request_legs)]
    if not coverage.result.results and stage_error is not None:
        raise stage_error
    return coverage.result


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
    deadline: Optional[float] = None,
    allow_partial: bool = False,
    coverage: Optional[_Coverage] = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[Itinerary], int]:
    payload = decode_trip_selector(selector)
    query_legs = payload.get("query_legs")
    selected_legs = payload.get("selected_legs")
    if not isinstance(query_legs, list) or not query_legs or len(query_legs) > 6 or not all(isinstance(leg, dict) for leg in query_legs):
        raise ValueError("invalid selector query legs")
    request_legs = [_copy_request_leg(leg) for leg in query_legs]
    if not isinstance(selected_legs, list) or not selected_legs or not all(isinstance(leg, list) and leg for leg in selected_legs):
        raise ValueError("incomplete selector: every requested leg must be selected")
    if (len(selected_legs) > len(request_legs)
            or (not allow_partial and len(selected_legs) != len(request_legs))):
        raise ValueError("incomplete selector: every requested leg must be selected")
    if allow_partial and len(selected_legs) == len(request_legs):
        raise ValueError("continuation requires an unselected leg")
    try:
        validate_cabin(payload["cabin"])
        for index, leg in enumerate(request_legs):
            validate_iata_code(leg["origin"], "origin")
            validate_iata_code(leg["destination"], "destination")
            validate_date(leg["date"], "date")
            if index < len(selected_legs):
                flights = selected_legs[index]
                if not all(isinstance(flight, list) and len(flight) == 6 for flight in flights):
                    raise ValueError("invalid selected segments")
                if flights[0][0] != leg["origin"] or flights[-1][2] != leg["destination"] or flights[0][1] != leg["date"]:
                    raise ValueError("selected segments do not match requested bounds")
                for flight_index, flight in enumerate(flights):
                    validate_iata_code(flight[0], "selected origin")
                    validate_iata_code(flight[2], "selected destination")
                    validate_date(flight[1], "selected date")
                    parse_flight_number(f"{flight[4]}{flight[5]}")
                    if flight_index and flights[flight_index - 1][2] != flight[0]:
                        raise ValueError("selected segments are disconnected")
        if not isinstance(payload["include_basic_economy"], bool):
            raise ValueError("invalid selector fare filter")
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("invalid selector context") from exc
    resolved: list[Itinerary] = []
    rpc_calls = 0

    replay_sort = payload.get("sort", SORT_DEPARTURE_TIME)
    exclude_basic = payload["cabin"] == "economy" and not payload["include_basic_economy"]

    for index in range(len(selected_legs)):
        staged_legs = _with_selected_prefix(request_legs, selected_legs[:index])
        bounded = _budget_transport(transport, deadline)
        if coverage is not None:
            coverage.result.rpc_calls += 1
        stage_result = _search_from_legs(
            staged_legs,
            cabin=payload["cabin"],
            passengers=payload["passengers"],
            sort=replay_sort,
            transport=bounded,
            exclude_basic_economy=exclude_basic,
            retain_raw=False,
            show_all_results=payload["show_all_results"],
            exclude_separate_tickets=payload["exclude_separate_tickets"],
        )
        if coverage is not None:
            coverage.add(stage_result)
        rpc_calls += 1
        candidates = _iter_raw_itineraries(stage_result)
        itinerary = _match_itinerary_by_selected_segments(candidates, selected_legs[index])
        if itinerary is None:
            raise ValueError("selector itinerary no longer available")
        resolved.append(itinerary)

    return payload, request_legs, resolved, rpc_calls


def search_next_leg(
    selector: str, *, transport: TransportConfig = TransportConfig(),
    time_budget: Optional[float] = None,
) -> SearchResult:
    """Replay a selected prefix and return every choice for its next bound.

    This is a progressive search, with no beam or trip-result cap. Each result
    preserves the selected prefix and the original search constraints. Only a
    final-bound result has ``is_resolved=True`` and can be exactly priced.
    """
    deadline = _deadline(time_budget if time_budget is not None else TIME_BUDGET_SECONDS)
    payload = decode_trip_selector(selector)
    coverage = _Coverage(payload["show_all_results"])
    payload, legs, prefix, _ = resolve_trip_selector(selector, transport=transport,
        deadline=deadline, allow_partial=True, coverage=coverage)
    selected = _selected_payloads_for_itineraries(prefix)
    if selected is None:
        raise ValueError("invalid selected prefix")
    bounded = _budget_transport(transport, deadline)
    coverage.result.rpc_calls += 1
    raw = _search_from_legs(_with_selected_prefix(legs, selected), cabin=payload["cabin"],
        passengers=payload["passengers"], sort=payload.get("sort", SORT_DEPARTURE_TIME),
        exclude_basic_economy=payload["cabin"] == "economy" and not payload["include_basic_economy"],
        transport=bounded, retain_raw=False, show_all_results=payload["show_all_results"],
        exclude_separate_tickets=payload["exclude_separate_tickets"])
    coverage.add(raw)
    coverage.result.results = [_build_trip_option(legs, [*prefix, candidate],
        cabin=payload["cabin"], passengers=payload["passengers"],
        include_basic_economy=payload["include_basic_economy"],
        sort=payload.get("sort", SORT_DEPARTURE_TIME), show_all_results=payload["show_all_results"],
        exclude_separate_tickets=payload["exclude_separate_tickets"])
        for candidate in _iter_raw_itineraries(raw)]
    coverage.result.price_range = raw.price_range if raw else None
    return coverage.result


def price_selected_trip(
    request_legs: list[dict[str, Any]],
    itineraries: list[Itinerary],
    *,
    cabin: CabinClass = "economy",
    passengers: Passengers = Passengers(),
    include_basic_economy: bool = False,
    exclude_separate_tickets: bool = False,
    transport: TransportConfig = TransportConfig(),
    rpc_calls: int = 0,
    selections: Optional[list[str]] = None,
    deadline: Optional[float] = None,
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
        bounded = _budget_transport(transport, deadline)
        try:
            booking_options = fetch_trip_booking_options(
                request_legs,
                itineraries,
                cabin=cabin,
                passengers=passengers,
                transport=bounded,
                exclude_separate_tickets=exclude_separate_tickets,
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

    if base_price is None or base_price <= 0:
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
    exclude_separate_tickets: bool = False,
    transport: TransportConfig = TransportConfig(),
    exclude_basic_economy: bool = False,
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
            cabin=cabin,
            passengers=passengers,
            sort=SORT_DEPARTURE_TIME,
            transport=transport,
            exclude_basic_economy=exclude_basic_economy,
            retain_raw=False,
            exclude_separate_tickets=exclude_separate_tickets,
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
    time_budget: Optional[float] = None,
) -> Optional[PriceResult]:
    deadline = _deadline(time_budget)
    try:
        payload, request_legs, itineraries, rpc_calls = resolve_trip_selector(
            selector,
            transport=transport,
            deadline=deadline,
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
        deadline=deadline,
        exclude_separate_tickets=payload["exclude_separate_tickets"],
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

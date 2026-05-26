"""Live Google Flights contract canary.

These tests are marked ``live`` and excluded from normal PR runs. They serve
two purposes:

1. Manual pre-release verification against the real Google Flights RPCs.
2. Scheduled canary coverage in GitHub Actions with artifact capture.

Set ``SWOOP_LIVE_ARTIFACT_DIR`` to write raw responses plus decoded summaries
outside the repo. Set ``SWOOP_UPDATE_LIVE_CORPUS=1`` to also save the same
artifacts under ``tests/fixtures/live_corpus`` for manual corpus promotion.
"""

from __future__ import annotations

from datetime import date, timedelta
import json
import os
from pathlib import Path
from typing import Any

import pytest

import swoop
import swoop.rpc as rpc
from swoop.decoder import Itinerary
from swoop.models import TransportConfig


pytestmark = pytest.mark.live

LIVE_FIXTURES = Path(__file__).parent / "fixtures" / "live_corpus"
ALLOWED_CABIN_BUCKETS = {"economy", "premium-economy", "business", "first", "unknown"}
ALLOWED_FARE_FAMILIES = {"basic", "standard", "enhanced", "premium", "unknown"}


def _future_date(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def _artifact_roots() -> list[Path]:
    roots: list[Path] = []
    artifact_dir = os.environ.get("SWOOP_LIVE_ARTIFACT_DIR")
    if artifact_dir:
        roots.append(Path(artifact_dir))
    if os.environ.get("SWOOP_UPDATE_LIVE_CORPUS") == "1":
        roots.append(LIVE_FIXTURES)

    deduped: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root.resolve())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(root)
    return deduped


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))


def _record_text_artifact(name: str, text: str) -> None:
    for root in _artifact_roots():
        root.mkdir(parents=True, exist_ok=True)
        (root / f"{name}.raw.txt").write_text(text)


def _record_json_artifact(name: str, payload: Any) -> None:
    for root in _artifact_roots():
        root.mkdir(parents=True, exist_ok=True)
        _write_json(root / f"{name}.json", payload)


def _extract_inner_json(text: str) -> Any:
    stripped = text.lstrip(")]}'")
    outer = json.loads(stripped)
    return json.loads(outer[0][2])


def _capture_rpc_texts(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    captured: list[dict[str, str]] = []
    original = rpc._http_post

    def wrapped(url: str, content: bytes, *, transport: TransportConfig = TransportConfig()) -> Any:
        response = original(url, content=content, transport=transport)
        captured.append({"url": url, "text": response.text})
        return response

    monkeypatch.setattr(rpc, "_http_post", wrapped)
    return captured


def _assert_itinerary_fields(itinerary: Itinerary, *, expected_origin: str, expected_destination: str) -> None:
    assert itinerary.departure_airport_code == expected_origin
    assert itinerary.arrival_airport_code == expected_destination
    assert itinerary.airline_code != ""
    assert itinerary.segments
    assert itinerary.travel_time > 0

    first_segment = itinerary.segments[0]
    assert first_segment.airline != ""
    assert first_segment.flight_number != ""
    assert first_segment.departure_airport_code == expected_origin
    assert first_segment.arrival_airport_code != ""


def _assert_trip_option(option: Any, query_legs: list[dict[str, str]]) -> None:
    assert option.price is not None
    assert option.selector
    assert len(option.legs) == len(query_legs)

    for leg, expected in zip(option.legs, query_legs):
        assert leg.origin == expected["origin"]
        assert leg.destination == expected["destination"]
        assert leg.date == expected["date"]
        assert leg.itinerary is not None
        _assert_itinerary_fields(
            leg.itinerary,
            expected_origin=expected["origin"],
            expected_destination=expected["destination"],
        )


def _trip_report(case_id: str, query_legs: list[dict[str, str]], result: Any, rpc_capture_count: int) -> dict[str, Any]:
    first = result.results[0]
    return {
        "captured_at": date.today().isoformat(),
        "case_id": case_id,
        "query_legs": query_legs,
        "result_count": len(result.results),
        "rpc_capture_count": rpc_capture_count,
        "is_complete": result.is_complete,
        "price_range": (
            {
                "low": result.price_range.low,
                "high": result.price_range.high,
            }
            if result.price_range is not None
            else None
        ),
        "first_option": {
            "price": first.price,
            "selector_present": bool(first.selector),
            "legs": [
                {
                    "origin": leg.origin,
                    "destination": leg.destination,
                    "date": leg.date,
                    "selection": getattr(leg, "selection", None),
                    "flight_number": (
                        leg.itinerary.segments[0].flight_number
                        if leg.itinerary and leg.itinerary.segments
                        else None
                    ),
                    "airline_code": leg.itinerary.airline_code if leg.itinerary else None,
                }
                for leg in first.legs
            ],
        },
    }


def _booking_report(case_id: str, itinerary: Itinerary, options: list[Any], rpc_capture_count: int) -> dict[str, Any]:
    query_date = f"{itinerary.departure_date[0]:04d}-{itinerary.departure_date[1]:02d}-{itinerary.departure_date[2]:02d}"
    return {
        "captured_at": date.today().isoformat(),
        "case_id": case_id,
        "origin": itinerary.departure_airport_code,
        "destination": itinerary.arrival_airport_code,
        "date": query_date,
        "rpc_capture_count": rpc_capture_count,
        "option_count": len(options),
        "options": [
            {
                "price": option.price,
                "brand_label": option.brand_label,
                "brand_code": option.brand_code,
                "fare_family": option.fare_family,
                "cabin_bucket": option._cabin_bucket,
            }
            for option in options
        ],
    }


def _record_shopping_artifacts(case_id: str, rpc_captures: list[dict[str, str]], report: dict[str, Any]) -> None:
    _record_json_artifact(f"{case_id}.report", report)
    for index, capture in enumerate(rpc_captures, start=1):
        prefix = f"{case_id}.rpc{index}"
        _record_text_artifact(prefix, capture["text"])
        _record_json_artifact(
            f"{prefix}.meta",
            {
                "url": capture["url"],
                "request_index": index,
                "response_bytes": len(capture["text"]),
            },
        )
        if capture["url"] == rpc.SHOPPING_RPC_URL:
            _record_json_artifact(f"{prefix}.decoded", _extract_inner_json(capture["text"]))


def _record_booking_artifacts(case_id: str, rpc_captures: list[dict[str, str]], report: dict[str, Any]) -> None:
    _record_json_artifact(f"{case_id}.report", report)
    for index, capture in enumerate(rpc_captures, start=1):
        prefix = f"{case_id}.rpc{index}"
        _record_text_artifact(prefix, capture["text"])
        _record_json_artifact(
            f"{prefix}.meta",
            {
                "url": capture["url"],
                "request_index": index,
                "response_bytes": len(capture["text"]),
            },
        )
        if capture["url"] == rpc.BOOKING_RPC_URL:
            options = rpc._parse_booking_rpc_response(
                capture["text"],
                registry_version=date.today().isoformat(),
            )
            _record_json_artifact(
                f"{prefix}.parsed",
                [
                    {
                        "price": option.price,
                        "brand_label": option.brand_label,
                        "brand_code": option.brand_code,
                        "fare_family": option.fare_family,
                        "cabin_bucket": option._cabin_bucket,
                    }
                    for option in options
                ],
            )


def _bookable_candidates(search_result: Any, *, limit: int = 5) -> list[Itinerary]:
    candidates: list[Itinerary] = []
    for option in search_result.results:
        for leg in option.legs:
            itinerary = leg.itinerary
            if itinerary is None:
                continue
            if itinerary.booking_token and rpc._build_selected_legs(itinerary):
                candidates.append(itinerary)
                if len(candidates) >= limit:
                    return candidates
    return candidates


class TestShoppingContract:
    def test_oneway_shopping_canary_has_expected_fields_and_artifacts(self, monkeypatch: pytest.MonkeyPatch):
        query_legs = [
            {"origin": "JFK", "destination": "LAX", "date": _future_date(50)},
        ]
        rpc_captures = _capture_rpc_texts(monkeypatch)

        result = swoop.search(
            "JFK",
            "LAX",
            query_legs[0]["date"],
            max_stops=0,
            transport=TransportConfig(timeout=30, retries=1),
        )

        assert result.results, "Expected at least one live one-way result"
        assert rpc_captures, "Expected at least one live RPC capture"
        _assert_trip_option(result.results[0], query_legs)

        report = _trip_report("shopping_oneway_jfk_lax", query_legs, result, len(rpc_captures))
        _record_shopping_artifacts("shopping_oneway_jfk_lax", rpc_captures, report)

    def test_roundtrip_shopping_canary_has_expected_fields_and_artifacts(self, monkeypatch: pytest.MonkeyPatch):
        outbound = _future_date(50)
        inbound = _future_date(57)
        query_legs = [
            {"origin": "JFK", "destination": "LAX", "date": outbound},
            {"origin": "LAX", "destination": "JFK", "date": inbound},
        ]
        rpc_captures = _capture_rpc_texts(monkeypatch)

        result = swoop.search(
            "JFK",
            "LAX",
            outbound,
            return_date=inbound,
            max_stops=0,
            transport=TransportConfig(timeout=30, retries=1),
        )

        assert result.results, "Expected at least one live roundtrip result"
        assert rpc_captures, "Expected at least one live RPC capture"
        # Roundtrip fast path: TripOption holds the outbound leg plus the
        # roundtrip total price; the return leg is resolved only at booking
        # time, so assert against the outbound query leg only.
        _assert_trip_option(result.results[0], query_legs[:1])

        report = _trip_report("shopping_roundtrip_jfk_lax", query_legs, result, len(rpc_captures))
        _record_shopping_artifacts("shopping_roundtrip_jfk_lax", rpc_captures, report)

    # Staged multi-leg (3+ legs, beam search path) is intentionally NOT covered
    # by the live canary: Google's multi-city RPC does not reliably surface
    # chained itineraries for arbitrary 3-leg sequences, so a live assertion
    # is too flaky to be a useful early-warning signal. Beam search logic
    # should be covered by offline unit tests that replay captured staged
    # responses; promote a successful capture from artifacts when adding
    # that coverage.


class TestBookingContract:
    def test_booking_results_parseable_and_artifacted(self, monkeypatch: pytest.MonkeyPatch):
        query_date = _future_date(50)
        search_result = swoop.search(
            "JFK",
            "LAX",
            query_date,
            max_stops=0,
            transport=TransportConfig(timeout=30, retries=1),
        )
        assert search_result.results, "Expected at least one itinerary before live booking lookup"

        candidates = _bookable_candidates(search_result)
        assert candidates, "Expected at least one itinerary with booking token and selected legs"

        rpc_captures = _capture_rpc_texts(monkeypatch)
        options: list[Any] = []
        itinerary: Itinerary | None = None
        for candidate in candidates:
            options = rpc.get_booking_results(
                candidate,
                registry_version=date.today().isoformat(),
                transport=TransportConfig(timeout=30, retries=1),
            )
            if options:
                itinerary = candidate
                break

        assert rpc_captures, "Expected a live booking RPC capture"
        assert options and itinerary is not None, (
            f"Expected at least one booking option after trying {len(candidates)} "
            "itineraries (upstream returned empty for every candidate)"
        )

        for option in options[:5]:
            assert option.price > 0
            assert option.brand_label or option.brand_code
            assert option.fare_family in ALLOWED_FARE_FAMILIES
            assert option._cabin_bucket in ALLOWED_CABIN_BUCKETS

        report = _booking_report("booking_jfk_lax", itinerary, options, len(rpc_captures))
        _record_booking_artifacts("booking_jfk_lax", rpc_captures, report)

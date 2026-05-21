"""Offline contract corpus replay for captured Google Flights responses."""

from __future__ import annotations

import json
from pathlib import Path

from swoop._booking import _parse_booking_rpc_response
from swoop.decoder import decode_result


FIXTURES = Path(__file__).parent / "fixtures"
MANIFEST_PATH = FIXTURES / "contract_corpus_manifest.json"
MANIFEST = json.loads(MANIFEST_PATH.read_text())


def _first_itinerary(result, section: str):
    bucket = result.best if section == "best" else result.other
    assert bucket, f"Expected at least one itinerary in {section}"
    return bucket[0]


def test_manifest_has_version_and_cases():
    assert MANIFEST["version"]
    assert MANIFEST["shopping"]
    assert MANIFEST["booking"]


def test_manifest_paths_exist():
    for group in ("shopping", "booking"):
        for case in MANIFEST[group]:
            assert (FIXTURES / case["path"]).exists(), f"Missing fixture for {case['id']}"


def test_shopping_corpus_cases_decode_critical_fields():
    for case in MANIFEST["shopping"]:
        data = json.loads((FIXTURES / case["path"]).read_text())
        result = decode_result(data)
        expected = case["expected"]
        first = _first_itinerary(result, expected["first_section"])

        assert len(result.best) == expected["best_count"], f"{case['id']}: best_count"
        assert len(result.other) == expected["other_count"], f"{case['id']}: other_count"
        assert first.airline_code == expected["first_airline_code"], f"{case['id']}: airline"
        assert first.departure_airport_code == expected["first_origin"], f"{case['id']}: origin"
        assert first.arrival_airport_code == expected["first_destination"], f"{case['id']}: dest"
        assert first.price == expected["first_price"], f"{case['id']}: price"
        assert len(first.segments) == expected["first_segments"], f"{case['id']}: flights"


def test_shopping_corpus_currency_fields():
    """Verify currency is decoded correctly for all corpus fixtures."""
    for case in MANIFEST["shopping"]:
        expected = case["expected"]
        if "currency" not in expected or expected["currency"] is None:
            continue

        data = json.loads((FIXTURES / case["path"]).read_text())
        result = decode_result(data)
        all_itins = result.best + result.other
        expected_currency = expected["currency"]

        # At least one itinerary should report the expected currency
        currencies = {
            itin.currency for itin in all_itins if itin.currency
        }
        assert expected_currency in currencies, (
            f"{case['id']}: expected currency {expected_currency}, got {currencies}"
        )


def test_shopping_corpus_structural_invariants():
    """Verify structural invariants hold across all corpus fixtures."""
    for case in MANIFEST["shopping"]:
        data = json.loads((FIXTURES / case["path"]).read_text())
        result = decode_result(data)

        for itin in result.best + result.other:
            # Every itinerary must have at least one flight
            assert itin.segments, f"{case['id']}: itinerary has no flights"
            # Every flight must have airline info
            for segment in itin.segments:
                assert segment.flight_number, f"{case['id']}: segment missing number"
                assert segment.departure_airport_code, f"{case['id']}: segment missing dep"
                assert segment.arrival_airport_code, f"{case['id']}: segment missing arr"
            # Travel time must be positive
            assert itin.travel_time > 0, f"{case['id']}: travel_time={itin.travel_time}"
            # Layover count = flights - 1
            if len(itin.segments) > 1:
                assert len(itin.layovers) == len(itin.segments) - 1, (
                    f"{case['id']}: {len(itin.layovers)} layovers for {len(itin.segments)} flights"
                )


def test_booking_corpus_cases_parse_critical_fields():
    for case in MANIFEST["booking"]:
        text = (FIXTURES / case["path"]).read_text()
        expected = case["expected"]
        options = _parse_booking_rpc_response(text, registry_version=case["registry_version"])

        assert [option.price for option in options] == expected["prices"]
        assert [option.brand_label for option in options] == expected["brands"]
        assert [option._cabin_bucket for option in options] == expected["cabin_buckets"]
        assert [option.fare_family for option in options] == expected["fare_families"]
        assert [option._registry_version for option in options] == [case["registry_version"]] * len(options)
        assert [option.seller_code for option in options] == expected["seller_codes"], (
            f"{case['id']}: seller_code drift — Google may have reshaped opt[1]"
        )
        assert [option.is_airline_direct for option in options] == expected["is_airline_direct"], (
            f"{case['id']}: is_airline_direct drift"
        )
        # Every option in our corpus has a non-empty booking_url; assert this
        # contract so a future reshape that empties opt[5] fails loudly.
        assert all(option.booking_url for option in options), (
            f"{case['id']}: at least one option missing booking_url"
        )

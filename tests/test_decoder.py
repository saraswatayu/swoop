"""Tests for the Google Flights decoder.

Happy-path tests use real fixture data captured from the live API.
Edge-case tests (malformed input, missing fields) use synthetic data
since Google doesn't send us garbage on purpose.
"""

import json
from pathlib import Path

import pytest

from swoop.decoder import (
    AmenityFlags,
    Codeshare,
    RawSearchResult,
    Segment,
    Itinerary,
    Layover,
    QualitySignals,
    _decode_amenities,
    _decode_codeshare,
    _decode_segment,
    _decode_itinerary,
    _decode_layover,
    _safe_get,
    _safe_tuple,
    decode_result,
)


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "responses"

# All non-empty fixtures for parametrized tests
FIXTURE_FILES = sorted(
    f.name for f in FIXTURES_DIR.glob("*.json")
    if f.stat().st_size > 10  # skip empty/stub files
)


def _load_fixture(name: str) -> list:
    with open(FIXTURES_DIR / name) as f:
        return json.load(f)


def _all_itineraries(data: list) -> list[Itinerary]:
    result = decode_result(data)
    return result.best + result.other


def _extract_raw_segments(data: list) -> list[list]:
    """Extract raw flight segment arrays from fixture data."""
    segments = []
    for bucket_idx in (2, 3):
        bucket = _safe_get(data, [bucket_idx, 0])
        if not isinstance(bucket, list):
            continue
        for itin_el in bucket:
            if not isinstance(itin_el, list):
                continue
            itin_data = _safe_get(itin_el, [0])
            if not isinstance(itin_data, list):
                continue
            flights_raw = _safe_get(itin_data, [2])
            if isinstance(flights_raw, list):
                for seg in flights_raw:
                    if isinstance(seg, list) and len(seg) > 20:
                        segments.append(seg)
    return segments


def _extract_raw_layovers(data: list) -> list[list]:
    """Extract raw layover arrays from fixture data."""
    layovers = []
    for bucket_idx in (2, 3):
        bucket = _safe_get(data, [bucket_idx, 0])
        if not isinstance(bucket, list):
            continue
        for itin_el in bucket:
            if not isinstance(itin_el, list):
                continue
            itin_data = _safe_get(itin_el, [0])
            if not isinstance(itin_data, list):
                continue
            layovers_raw = _safe_get(itin_data, [13])
            if isinstance(layovers_raw, list):
                for lay in layovers_raw:
                    if isinstance(lay, list):
                        layovers.append(lay)
    return layovers


# ---------------------------------------------------------------------------
# Utility function tests (pure logic, no fixtures needed)
# ---------------------------------------------------------------------------


class TestSafeGet:
    def test_simple_path(self):
        assert _safe_get([1, 2, 3], [1]) == 2

    def test_nested_path(self):
        assert _safe_get([[10, 20], [30, 40]], [1, 0]) == 30

    def test_out_of_bounds(self):
        assert _safe_get([1, 2], [5]) is None

    def test_out_of_bounds_default(self):
        assert _safe_get([1, 2], [5], "default") == "default"

    def test_none_data(self):
        assert _safe_get(None, [0]) is None

    def test_empty_path(self):
        data = [1, 2, 3]
        assert _safe_get(data, []) == data

    def test_non_list_intermediate(self):
        assert _safe_get([1, "hello", 3], [1, 0]) is None


class TestSafeTuple:
    def test_list_input(self):
        assert _safe_tuple([2026, 3, 15], 3, [0, 0, 0]) == (2026, 3, 15)

    def test_short_list(self):
        assert _safe_tuple([8], 2, [0, 0]) == (8, 0)

    def test_none_input(self):
        assert _safe_tuple(None, 3, [0, 0, 0]) == (0, 0, 0)

    def test_none_element_within_list(self):
        # Google Flights omits the leading-zero hour for midnight times,
        # encoding 00:05 as [None, 5]. The decoder must substitute the
        # default rather than propagate the None to formatters.
        assert _safe_tuple([None, 5], 2, [0, 0]) == (0, 5)
        assert _safe_tuple([14, None], 2, [0, 0]) == (14, 0)
        assert _safe_tuple([None, None, 15], 3, [0, 0, 0]) == (0, 0, 15)

    def test_midnight_time_does_not_crash_repr(self):
        # End-to-end regression for the same bug: with the broken _safe_tuple,
        # decoded segments/itineraries carried (None, 5) in departure_time, and
        # fmt_clock then crashed in __repr__ with "unsupported format string
        # passed to NoneType.__format__". This guards the formatter path, not
        # just the helper.
        seg = Segment(
            departure_time=_safe_tuple([None, 5], 2, [0, 0]),
            arrival_time=_safe_tuple([14, None], 2, [0, 0]),
        )
        assert "00:05" in repr(seg)
        assert "14:00" in repr(seg)
        itin = Itinerary(
            departure_time=_safe_tuple([None, 50], 2, [0, 0]),
            arrival_time=_safe_tuple([6, 30], 2, [0, 0]),
        )
        assert "00:50" in repr(itin)
        assert "06:30" in repr(itin)


# ---------------------------------------------------------------------------
# Edge-case tests (synthetic — testing error handling)
# ---------------------------------------------------------------------------


class TestDecodeEdgeCases:
    """Decoder graceful degradation with malformed input."""

    def test_codeshare_missing_fields(self):
        cs = _decode_codeshare(["AA"])
        assert cs.airline_code == "AA"
        assert cs.flight_number == ""

    def test_segment_malformed_returns_empty(self):
        segment = _decode_segment("not a list")
        assert segment is not None
        assert segment.airline == ""

    def test_segment_empty_list(self):
        segment = _decode_segment([])
        assert segment is not None
        assert segment.airline == ""

    def test_amenities_empty_array(self):
        el = [None] * 33
        el[12] = []
        assert _decode_amenities(el) is None

    def test_amenities_none(self):
        el = [None] * 33
        el[12] = None
        assert _decode_amenities(el) is None

    def test_itinerary_malformed_returns_none(self):
        assert _decode_itinerary("not a list") is None

    def test_itinerary_none_data_returns_none(self):
        assert _decode_itinerary([None, None]) is None

    def test_decode_result_empty(self):
        result = decode_result([None, None, None, None])
        assert isinstance(result, RawSearchResult)
        assert len(result.best) == 0
        assert len(result.other) == 0

    def test_decode_result_skips_malformed(self):
        """Malformed itinerary elements in the list are skipped, not fatal."""
        data = [None, None, [["not a real itinerary"]], None]
        result = decode_result(data)
        assert isinstance(result, RawSearchResult)

    def test_decode_result_preserves_raw(self):
        data = [None, None, None, None]
        result = decode_result(data)
        assert result._raw is data


# ---------------------------------------------------------------------------
# Fixture-backed tests — real Google Flights responses
# ---------------------------------------------------------------------------


class TestDecodeSegmentFromFixtures:
    """Segment decoding against real data."""

    @pytest.fixture(params=FIXTURE_FILES)
    def segments(self, request):
        data = _load_fixture(request.param)
        segs = _extract_raw_segments(data)
        if not segs:
            pytest.skip(f"No segments in {request.param}")
        return segs

    def test_all_segments_decode_successfully(self, segments):
        for seg in segments:
            segment = _decode_segment(seg)
            assert segment is not None

    def test_all_segments_have_airline(self, segments):
        for seg in segments:
            segment = _decode_segment(seg)
            assert segment.airline != "", "Segment missing airline code"

    def test_all_segments_have_flight_number(self, segments):
        for seg in segments:
            segment = _decode_segment(seg)
            assert segment.flight_number != "", "Segment missing flight number"

    def test_all_segments_have_airports(self, segments):
        for seg in segments:
            segment = _decode_segment(seg)
            assert len(segment.departure_airport_code) == 3
            assert len(segment.arrival_airport_code) == 3

    def test_all_segments_have_positive_travel_time(self, segments):
        for seg in segments:
            segment = _decode_segment(seg)
            assert segment.travel_time > 0

    def test_all_segments_have_times(self, segments):
        for seg in segments:
            segment = _decode_segment(seg)
            assert len(segment.departure_time) == 2
            assert len(segment.arrival_time) == 2

    def test_all_segments_have_valid_dates(self, segments):
        for seg in segments:
            segment = _decode_segment(seg)
            y, mo, d = segment.departure_date
            assert y >= 2024
            assert 1 <= mo <= 12
            assert 1 <= d <= 31


class TestDecodeLayoverFromFixtures:
    """Layover decoding against real data."""

    @pytest.fixture(params=FIXTURE_FILES)
    def layovers(self, request):
        data = _load_fixture(request.param)
        lays = _extract_raw_layovers(data)
        if not lays:
            pytest.skip(f"No layovers in {request.param}")
        return lays

    def test_all_layovers_decode_successfully(self, layovers):
        for lay in layovers:
            result = _decode_layover(lay)
            assert result is not None

    def test_all_layovers_have_positive_minutes(self, layovers):
        for lay in layovers:
            result = _decode_layover(lay)
            assert result.minutes > 0

    def test_all_layovers_have_airport(self, layovers):
        for lay in layovers:
            result = _decode_layover(lay)
            assert result.departure_airport_code or result.arrival_airport_code


class TestDecodeItineraryFromFixtures:
    """Full itinerary decoding against real data."""

    @pytest.fixture(params=FIXTURE_FILES)
    def fixture_name(self, request):
        return request.param

    @pytest.fixture
    def itineraries(self, fixture_name):
        data = _load_fixture(fixture_name)
        itins = _all_itineraries(data)
        if not itins:
            pytest.skip(f"No itineraries in {fixture_name}")
        return itins

    def test_all_itineraries_have_segments(self, itineraries):
        for itin in itineraries:
            assert len(itin.segments) >= 1

    def test_all_itineraries_have_airline_code(self, itineraries):
        for itin in itineraries:
            assert itin.airline_code != ""

    def test_all_itineraries_have_airports(self, itineraries):
        for itin in itineraries:
            assert len(itin.departure_airport_code) == 3
            assert len(itin.arrival_airport_code) == 3

    def test_all_itineraries_have_positive_travel_time(self, itineraries):
        for itin in itineraries:
            assert itin.travel_time > 0

    def test_layover_count_matches_connections(self, itineraries):
        for itin in itineraries:
            if len(itin.segments) > 1:
                assert len(itin.layovers) == len(itin.segments) - 1

    def test_stop_count_consistent(self, itineraries):
        for itin in itineraries:
            expected = len(itin.segments) - 1
            if itin.stop_count is not None:
                assert itin.stop_count == expected

    def test_booking_token_present_in_live_fixtures(self, itineraries, fixture_name):
        """Live-captured itineraries should have booking tokens."""
        if fixture_name in ("shopping_oneway.json", "shopping_roundtrip.json"):
            pytest.skip("Hand-built fixture, no booking tokens")
        with_token = sum(1 for i in itineraries if i.booking_token)
        assert with_token > 0

    def test_codeshares_are_valid(self, itineraries):
        for itin in itineraries:
            for segment in itin.segments:
                for cs in segment.codeshares:
                    assert cs.airline_code != ""
                    assert cs.flight_number != ""

    def test_carbon_emissions_when_present(self, itineraries):
        for itin in itineraries:
            if itin.carbon_emissions is not None:
                ce = itin.carbon_emissions
                assert ce.this_flight_grams is None or ce.this_flight_grams > 0
                assert ce.typical_for_route_grams is None or ce.typical_for_route_grams > 0

    def test_currency_when_present(self, itineraries):
        for itin in itineraries:
            if itin.currency is not None:
                assert len(itin.currency) == 3  # ISO 4217


class TestDecodeResultFromFixtures:
    """Full decode_result against real data."""

    @pytest.fixture(params=FIXTURE_FILES)
    def fixture_name(self, request):
        return request.param

    @pytest.fixture
    def result(self, fixture_name):
        data = _load_fixture(fixture_name)
        return decode_result(data)

    def test_returns_search_result(self, result):
        assert isinstance(result, RawSearchResult)

    def test_has_itineraries(self, result, fixture_name):
        if fixture_name == "shopping_empty.json":
            assert len(result.best) + len(result.other) == 0
        else:
            assert len(result.best) + len(result.other) >= 1

    def test_preserves_raw(self, result):
        assert result._raw is not None

    def test_snapshot_stability(self, result):
        """Re-decoding produces identical results."""
        from dataclasses import asdict
        result2 = decode_result(result._raw)
        assert asdict(result) == asdict(result2)

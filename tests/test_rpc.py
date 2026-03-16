"""Tests for _build_f_req in the RPC client.

_build_f_req is a pure function that constructs the URL-encoded protobuf request
body for Google Flights' GetShoppingResults RPC endpoint. We decode the output
and verify the nested structure matches expectations.
"""

from __future__ import annotations

import base64
import json
import logging
import types
import urllib.parse

import pytest

import swoop._booking as _booking
import swoop.rpc as rpc
from swoop.rpc import (
    _build_booking_f_req,
    _build_f_req,
    _build_filters_from_legs,
    _normalize_rpc_leg,
    _parse_booking_rpc_response,
    CABIN_CLASS_MAP,
    STOPS_ANY,
    STOPS_NONSTOP,
)
from swoop import flights_pb2 as PB

from tests.factories import (
    decode_f_req,
    encode_rpc_outer,
    make_brand_block,
    make_booking_option,
    make_price_block,
    FakeHTTPResponse,
)


class TestBuildFReqOneWay:
    """One-way flight request tests."""

    def test_one_way_baseline(self):
        encoded = _build_f_req("JFK", "LAX", "2026-03-15")
        filters = decode_f_req(encoded)

        # Trip type = 2 (one-way)
        assert filters[1][2] == 2

        # One segment
        segments = filters[1][13]
        assert len(segments) == 1

        # Airport nesting is 3 levels: [[[code, 0]]]
        assert segments[0][0] == [[["JFK", 0]]]
        assert segments[0][1] == [[["LAX", 0]]]

        # Date
        assert segments[0][6] == "2026-03-15"

    def test_airport_nesting_is_3_level(self):
        """Verify airports use exactly 3 levels of nesting, not 4."""
        encoded = _build_f_req("SFO", "ORD", "2026-06-01")
        filters = decode_f_req(encoded)
        segments = filters[1][13]
        dep = segments[0][0]
        # Must be [[[code, 0]]] — exactly 3 levels
        assert dep == [[["SFO", 0]]]
        assert isinstance(dep, list)
        assert isinstance(dep[0], list)
        assert isinstance(dep[0][0], list)
        assert len(dep[0][0]) == 2  # [code, 0]

    def test_no_time_restrictions(self):
        encoded = _build_f_req("JFK", "LAX", "2026-03-15")
        filters = decode_f_req(encoded)
        segments = filters[1][13]
        # time_restrictions should be None when no time params
        assert segments[0][2] is None


class TestBuildFReqRoundtrip:
    """Roundtrip flight request tests."""

    def test_roundtrip_with_return_date(self):
        encoded = _build_f_req("JFK", "LAX", "2026-03-15", return_date="2026-03-22")
        filters = decode_f_req(encoded)

        # Trip type = 1 (roundtrip)
        assert filters[1][2] == 1

        # Two segments
        segments = filters[1][13]
        assert len(segments) == 2

    def test_return_segment_has_reversed_airports(self):
        encoded = _build_f_req("JFK", "LAX", "2026-03-15", return_date="2026-03-22")
        filters = decode_f_req(encoded)
        segments = filters[1][13]

        # Return segment: departure = LAX, arrival = JFK (reversed)
        assert segments[1][0] == [[["LAX", 0]]]
        assert segments[1][1] == [[["JFK", 0]]]

    def test_return_segment_date(self):
        encoded = _build_f_req("JFK", "LAX", "2026-03-15", return_date="2026-03-22")
        filters = decode_f_req(encoded)
        segments = filters[1][13]
        assert segments[1][6] == "2026-03-22"

    def test_selected_outbound_legs_attached_for_roundtrip_expansion(self):
        selected = [["JFK", "2026-03-15", "LAX", None, "DL", "4938"]]
        encoded = _build_f_req(
            "JFK",
            "LAX",
            "2026-03-15",
            return_date="2026-03-22",
            selected_outbound_legs=selected,
        )
        filters = decode_f_req(encoded)
        segments = filters[1][13]
        assert segments[0][8] == selected
        assert segments[1][8] is None


class TestBuildFiltersFromLegs:
    """Leg-normalized filter builder should preserve legacy request shapes."""

    def test_one_way_matches_legacy_builder(self):
        legacy = decode_f_req(_build_f_req("JFK", "LAX", "2026-03-15"))
        normalized = _build_filters_from_legs([
            _normalize_rpc_leg("JFK", "LAX", "2026-03-15"),
        ])
        assert normalized == legacy

    def test_roundtrip_matches_legacy_builder(self):
        selected = [["JFK", "2026-03-15", "LAX", None, "DL", "4938"]]
        legacy = decode_f_req(_build_f_req(
            "JFK",
            "LAX",
            "2026-03-15",
            cabin="business",
            adults=2,
            sort=rpc.SORT_CHEAPEST,
            max_stops=1,
            airlines=["DL"],
            earliest_departure=6,
            latest_departure=10,
            return_date="2026-03-22",
            return_earliest_departure=14,
            return_latest_departure=18,
            selected_outbound_legs=selected,
            exclude_basic_economy=True,
        ))
        normalized = _build_filters_from_legs([
            _normalize_rpc_leg(
                "JFK",
                "LAX",
                "2026-03-15",
                max_stops=1,
                airlines=["DL"],
                earliest_departure=6,
                latest_departure=10,
                selected_legs=selected,
            ),
            _normalize_rpc_leg(
                "LAX",
                "JFK",
                "2026-03-22",
                max_stops=1,
                airlines=["DL"],
                earliest_departure=14,
                latest_departure=18,
            ),
        ], cabin="business", adults=2, sort=rpc.SORT_CHEAPEST, exclude_basic_economy=True)
        assert normalized == legacy


class TestBuildFReqCabinAndStops:
    """Cabin class and stops mapping tests."""

    @pytest.mark.parametrize("cabin,expected", [
        ("economy", 1),
        ("business", 3),
        ("first", 4),
        ("premium-economy", 2),
    ])
    def test_cabin_mapping(self, cabin, expected):
        encoded = _build_f_req("JFK", "LAX", "2026-03-15", cabin=cabin)
        filters = decode_f_req(encoded)
        assert filters[1][5] == expected

    @pytest.mark.parametrize("max_stops,expected_val", [
        (None, 0),   # any stops -> STOPS_ANY = 0
        (0, 1),      # nonstop -> 0+1 = 1
        (1, 2),      # 1 stop -> 1+1 = 2
        (2, 3),      # 2 stops -> 2+1 = 3
    ])
    def test_max_stops_mapping(self, max_stops, expected_val):
        encoded = _build_f_req("JFK", "LAX", "2026-03-15", max_stops=max_stops)
        filters = decode_f_req(encoded)
        segments = filters[1][13]
        assert segments[0][3] == expected_val


class TestBuildFReqFilters:
    """Airlines and time filter tests."""

    def test_airlines_filter_sorted(self):
        encoded = _build_f_req("JFK", "LAX", "2026-03-15", airlines=["UA", "DL", "AA"])
        filters = decode_f_req(encoded)
        segments = filters[1][13]
        assert segments[0][4] == ["AA", "DL", "UA"]

    def test_outbound_time_restrictions(self):
        encoded = _build_f_req("JFK", "LAX", "2026-03-15", earliest_departure=6, latest_departure=10)
        filters = decode_f_req(encoded)
        segments = filters[1][13]
        assert segments[0][2] == [6, 10, None, None]

    def test_return_time_restrictions(self):
        encoded = _build_f_req("JFK", "LAX", "2026-03-15", return_date="2026-03-22",
                               return_earliest_departure=14, return_latest_departure=18)
        filters = decode_f_req(encoded)
        segments = filters[1][13]
        assert segments[1][2] == [14, 18, None, None]

    def test_adults_count(self):
        encoded = _build_f_req("JFK", "LAX", "2026-03-15", adults=2)
        filters = decode_f_req(encoded)
        assert filters[1][6] == [2, 0, 0, 0]


class TestBuildFReqAirportPairs:
    """Verify f.req construction with various airport pairs."""

    @pytest.mark.parametrize("origin,destination", [
        ("JFK", "LAX"),
        ("SFO", "NRT"),
        ("LHR", "CDG"),
        ("ORD", "MIA"),
    ])
    def test_various_airports(self, origin, destination):
        encoded = _build_f_req(origin, destination, "2026-03-15")
        filters = decode_f_req(encoded)
        segments = filters[1][13]
        assert segments[0][0] == [[[origin, 0]]]
        assert segments[0][1] == [[[destination, 0]]]


class TestBookingRequestHelpers:
    def test_build_booking_f_req_sets_selected_legs(self):
        encoded = _build_f_req("LGA", "BHM", "2026-03-13", airlines=["DL"])
        filters = decode_f_req(encoded)
        selected = [["LGA", "2026-03-13", "BHM", None, "DL", "4938"]]

        booking_encoded = _build_booking_f_req("token123", filters[1], selected)
        booking_outer = json.loads(urllib.parse.unquote(booking_encoded))
        booking_inner = json.loads(booking_outer[1])

        # [0] = token wrapper
        assert booking_inner[0] == [None, "token123"]
        # [1][13][0][8] = selected leg list
        assert booking_inner[1][13][0][8] == selected

    def test_parse_booking_response_extracts_fare_options(self):
        def _make_price_token(option_index: int, price_cents: int) -> str:
            summary = PB.ItinerarySummary()
            summary.flights = f"options:{option_index}"
            summary.price.price = price_cents
            summary.price.currency = "USD"
            return base64.b64encode(summary.SerializeToString()).decode()

        def _make_context_token(price_cents: int) -> str:
            # Minimal protobuf payload that includes field 3.1 = display price cents.
            # Hex corresponds to: field1=2, field2='USD', field3={field1=price_cents}
            # and omits other optional context fields.
            price_bytes = []
            val = price_cents
            while True:
                b = val & 0x7F
                val >>= 7
                if val:
                    price_bytes.append(b | 0x80)
                else:
                    price_bytes.append(b)
                    break
            nested = bytes([0x08, *price_bytes])  # field 1 (varint)
            payload = bytes([0x08, 0x02, 0x12, 0x03]) + b"USD" + bytes([0x1A, len(nested)]) + nested
            return base64.b64encode(payload).decode()

        def _make_segment_context_token() -> str:
            def _ld(field: int, value: str) -> bytes:
                raw = value.encode()
                return bytes([(field << 3) | 2, len(raw)]) + raw

            nested = (
                _ld(1, "LGA")
                + _ld(2, "2026-03-13T20:59:00-04:00")
                + _ld(3, "BHM")
                + _ld(4, "2026-03-13T22:45:00-05:00")
                + _ld(5, "DL")
                + _ld(6, "4938")
                + _ld(10, "CR9")
            )
            outer = bytes([0x0A, len(nested)]) + nested
            return base64.b64encode(outer).decode()

        def _make_option_with_tokens(
            price: int,
            brand_code: str,
            brand_label: str,
            *,
            flag2=None,
            flag16=None,
            tail_flag=None,
            option_index=0,
        ) -> list:
            option = [None] * 25
            option[7] = [[None, price], _make_price_token(option_index, price * 100)]
            option[19] = json.dumps([_make_context_token(price * 100), [_make_segment_context_token()]])
            option[21] = [["DL", brand_code], [], flag2, brand_label, None, None, None, None, None, None, None, None, None, None, None, None, flag16]
            option[24] = tail_flag
            return option

        payload = [
            _make_option_with_tokens(249, "DELTA MAIN BASIC", "Delta Main Basic", flag2=True, flag16=True, tail_flag=True, option_index=0),
            _make_option_with_tokens(284, "DELTA MAIN CLASSIC", "Delta Main Classic", option_index=1),
        ]
        response = [
            ["wrb.fr", None, json.dumps([None, []])],
            ["wrb.fr", None, json.dumps([None, [payload]])],
        ]
        text = ")]}'" + json.dumps(response)

        options = _parse_booking_rpc_response(text, registry_version="2026-02-21")

        assert len(options) == 2
        assert options[0]["price"] == 249
        assert options[0]["brand_label"] == "Delta Main Basic"
        assert options[0]["brand_code"] == "DELTA MAIN BASIC"
        assert options[0]["is_basic"] is True
        assert options[0]["_is_basic_by_flags"] is True
        assert options[0]["_is_basic_by_text"] is True
        assert options[0]["_option_index"] == 0
        assert options[0]["_token_price_cents"] == 24900
        assert options[0]["_display_price_cents"] == 24900
        assert options[0]["_price_delta_cents"] == 0
        assert options[0]["_context_origin_iata"] == "LGA"
        assert options[0]["_context_destination_iata"] == "BHM"
        assert options[0]["_context_departure_local_iso"] == "2026-03-13T20:59:00-04:00"
        assert options[0]["_context_arrival_local_iso"] == "2026-03-13T22:45:00-05:00"
        assert options[0]["_context_carrier_code"] == "DL"
        assert options[0]["_context_flight_number"] == "4938"
        assert options[0]["_context_aircraft_code"] == "CR9"
        assert options[0]["_cabin_bucket"] == "economy"
        assert options[0]["fare_family"] == "basic"
        assert options[0]["rebookability_signal"] == "restricted"
        assert options[0]["_registry_version"] == "2026-02-21"

        assert options[1]["price"] == 284
        assert options[1]["brand_label"] == "Delta Main Classic"
        assert options[1]["brand_code"] == "DELTA MAIN CLASSIC"
        assert options[1]["is_basic"] is False
        assert options[1]["_option_index"] == 1
        assert options[1]["_cabin_bucket"] == "economy"
        assert options[1]["fare_family"] == "standard"
        assert options[1]["rebookability_signal"] == "standard_rebookable"


# --- Booking/Branch coverage (merged from test_rpc_client_branches) ---


def test_build_booking_f_req_validation_and_padding() -> None:
    assert rpc._build_booking_f_req("", [], [["LGA"]]) == ""
    assert rpc._build_booking_f_req("token", [], []) == ""
    assert rpc._build_booking_f_req("token", None, [["LGA"]]) == ""

    filter_block = [None] * 14
    filter_block[13] = [[]]
    encoded = rpc._build_booking_f_req("token", filter_block, [["LGA", "2026-03-12", "BHM", None, "DL", "4938"]])
    assert encoded

    decoded_outer = json.loads(rpc.urllib.parse.unquote(encoded))
    decoded_inner = json.loads(decoded_outer[1])
    assert decoded_inner[1][13][0][8] == [["LGA", "2026-03-12", "BHM", None, "DL", "4938"]]

    bad_filter_block = [None] * 14
    bad_filter_block[13] = "not-a-segment-list"
    assert rpc._build_booking_f_req("token", bad_filter_block, [["LGA"]]) == ""


def test_search_raw_success_and_http_error(monkeypatch) -> None:
    def fake_http_post(url, content, *, timeout=90, retries=0):
        return FakeHTTPResponse(200, "ok")

    monkeypatch.setattr(rpc, "_http_post", fake_http_post)
    monkeypatch.setattr(rpc, "_parse_rpc_response", lambda text: {"parsed": text})

    parsed = rpc.search_raw("LGA", "BHM", "2026-03-12")
    assert parsed == {"parsed": "ok"}

    def error_http_post(url, content, *, timeout=90, retries=0):
        raise rpc.SwoopHTTPError(503)

    monkeypatch.setattr(rpc, "_http_post", error_http_post)
    with pytest.raises(rpc.SwoopHTTPError, match="HTTP 503"):
        rpc.search_raw("LGA", "BHM", "2026-03-12")


def test_get_booking_results_branches(monkeypatch) -> None:
    assert rpc.get_booking_results("", origin="LGA", destination="BHM", date="2026-03-12", selected_legs=[["LGA"]]) == []
    assert rpc.get_booking_results("token", origin="LGA", destination="BHM", date="2026-03-12", selected_legs=[]) == []

    monkeypatch.setattr(rpc, "_build_booking_f_req", lambda *_args, **_kwargs: "")
    assert rpc.get_booking_results(
        "token",
        origin="LGA",
        destination="BHM",
        date="2026-03-12",
        selected_legs=[["LGA", "2026-03-12", "BHM", None, "DL", "4938"]],
    ) == []

    monkeypatch.undo()

    def error_http_post(url, content, *, timeout=90, retries=0):
        raise rpc.SwoopHTTPError(500)

    monkeypatch.setattr(rpc, "_http_post", error_http_post)
    with pytest.raises(rpc.SwoopHTTPError, match="HTTP 500"):
        rpc.get_booking_results(
            "token",
            origin="LGA",
            destination="BHM",
            date="2026-03-12",
            selected_legs=[["LGA", "2026-03-12", "BHM", None, "DL", "4938"]],
        )

    def success_http_post(url, content, *, timeout=90, retries=0):
        return FakeHTTPResponse(200, "payload")

    monkeypatch.setattr(rpc, "_http_post", success_http_post)
    monkeypatch.setattr(rpc, "_parse_booking_rpc_response", lambda text, **_kw: [{"raw": text}])
    parsed = rpc.get_booking_results(
        "token",
        origin="LGA",
        destination="BHM",
        date="2026-03-12",
        selected_legs=[["LGA", "2026-03-12", "BHM", None, "DL", "4938"]],
    )
    assert parsed == [{"raw": "payload"}]


def test_block_extractors_and_wire_helpers() -> None:
    assert rpc._looks_like_price_block([["x", 123], "token"]) is True
    assert rpc._looks_like_price_block([["x"], "token"]) is False
    assert rpc._extract_price_block([None, [["x", 200], "token"]]) == [["x", 200], "token"]

    assert rpc._looks_like_brand_block([["DL", "CODE"], [], None, "Label"]) is True
    assert rpc._looks_like_brand_block([["DL"], [], None, "Label"]) is False
    assert rpc._extract_brand_block([None, [["DL", "CODE"], [], None, "Label"]]) == [["DL", "CODE"], [], None, "Label"]

    value, pos = rpc._read_varint(bytes([0x96, 0x01]), 0)
    assert value == 150
    assert pos == 2

    with pytest.raises(ValueError, match="protobuf varint too long"):
        rpc._read_varint(bytes([0x80] * 11), 0)
    with pytest.raises(ValueError, match="truncated protobuf varint"):
        rpc._read_varint(bytes([0x80]), 0)

    assert rpc._skip_wire_value(bytes([0x08]), 0, 1) == 8
    assert rpc._skip_wire_value(bytes([0x03, 0xAA, 0xBB, 0xCC]), 0, 2) == 4
    assert rpc._skip_wire_value(bytes([0x08]), 0, 5) == 4
    with pytest.raises(ValueError, match="unsupported wire type"):
        rpc._skip_wire_value(bytes([0x08]), 0, 3)


def test_token_and_context_decoders(monkeypatch) -> None:
    class FakeSummary:
        def __init__(self, flights: str, price: float):
            self.flights = flights
            self.price = price

    class FakeItinerarySummary:
        @staticmethod
        def from_b64(token: str):
            if token == "explode":
                raise ValueError("bad token")
            if token == "bad-index":
                return FakeSummary("options:not-int", 123.45)
            return FakeSummary("options:7", 123.45)

    monkeypatch.setattr(_booking, "ItinerarySummary", FakeItinerarySummary)

    assert rpc._extract_option_index_and_token_price_cents("") == (None, None)
    assert rpc._extract_option_index_and_token_price_cents("explode") == (None, None)
    assert rpc._extract_option_index_and_token_price_cents("bad-index") == (None, 12345)
    assert rpc._extract_option_index_and_token_price_cents("ok") == (7, 12345)

    assert rpc._extract_display_price_cents_from_context("") is None
    assert rpc._extract_display_price_cents_from_context("not-base64!") is None

    # Outer field != 3 should hit skip path and return None.
    skip_payload = base64.b64encode(bytes([0x20, 0x01])).decode()
    assert rpc._extract_display_price_cents_from_context(skip_payload) is None

    # Nested field != 1 should also skip and return None.
    nested = bytes([0x12, 0x01, 0x00])  # field2 len-delimited
    payload = bytes([0x1A, len(nested)]) + nested  # field3 len-delimited
    nested_skip_payload = base64.b64encode(payload).decode()
    assert rpc._extract_display_price_cents_from_context(nested_skip_payload) is None

    assert rpc._extract_context_tokens([None] * 19 + [123]) == ("", "")
    assert rpc._extract_context_tokens([None] * 19 + ["{bad json"]) == ("", "")
    assert rpc._extract_context_tokens([None] * 19 + [json.dumps(["t0", ["t1"]])]) == ("t0", "t1")

    assert rpc._extract_segment_identity_from_context("") == {}
    assert rpc._extract_segment_identity_from_context("not-base64!") == {}
    assert rpc._extract_segment_identity_from_context(base64.b64encode(bytes([0x08, 0x01])).decode()) == {}

    def _ld(field: int, value: str) -> bytes:
        raw = value.encode()
        return bytes([(field << 3) | 2, len(raw)]) + raw

    nested = (
        _ld(1, "LGA")
        + _ld(2, "2026-03-13T20:59:00-04:00")
        + _ld(3, "BHM")
        + _ld(4, "2026-03-13T22:45:00-05:00")
        + _ld(5, "DL")
        + _ld(6, "4938")
        + _ld(10, "CR9")
    )
    payload = bytes([0x0A, len(nested)]) + nested
    identity = rpc._extract_segment_identity_from_context(base64.b64encode(payload).decode())
    assert identity["context_origin_iata"] == "LGA"
    assert identity["context_destination_iata"] == "BHM"
    assert identity["context_carrier_code"] == "DL"
    assert identity["context_flight_number"] == "4938"
    assert identity["context_aircraft_code"] == "CR9"


def test_attribute_and_fare_classification_helpers() -> None:
    assert rpc._normalize_attribute_vector("not-a-list") == []
    normalized = rpc._normalize_attribute_vector([None, True, 1, 1.5, "x", [], {}, object()])
    assert normalized == [None, True, 1, 1.5, "x", "list", "dict", "object"]

    assert rpc._classify_fare_family("DELTA MAIN BASIC", "Delta Main Basic", is_basic=True) == "basic"
    assert rpc._classify_fare_family("BUSINESS", "Business", is_basic=False) == "premium"
    assert rpc._classify_fare_family("DELTA MAIN CLASSIC", "Delta Main Classic", is_basic=False) == "standard"
    assert rpc._classify_fare_family("MAIN CABIN", "Main Cabin", is_basic=False) == "standard"
    assert rpc._classify_fare_family("ECONOMY", "Economy", is_basic=False) == "standard"
    assert rpc._classify_fare_family("ECONOMY PLUS", "Economy Plus", is_basic=False) == "enhanced"
    assert rpc._classify_fare_family("MYSTERY", "Mystery", is_basic=False) == "unknown"
    assert _booking._classify_cabin_bucket("", "") == "unknown"
    assert _booking._classify_cabin_bucket("DELTA MAIN BASIC", "Delta Main Basic") == "economy"
    assert _booking._classify_cabin_bucket("ECONOMY PLUS", "Economy Plus") == "economy"
    assert _booking._classify_cabin_bucket("COMFORT+", "Comfort+") == "economy"
    assert _booking._classify_cabin_bucket("MAIN CABIN EXTRA", "Main Cabin Extra") == "economy"
    assert _booking._classify_cabin_bucket("PREMIUM SELECT", "Premium Select") == "premium-economy"
    assert _booking._classify_cabin_bucket("DELTA ONE", "Delta One") == "business"
    assert _booking._classify_cabin_bucket("FIRST", "First") == "first"
    assert _booking._classify_cabin_bucket("MYSTERY", "Mystery") == "unknown"
    # Alaska, JetBlue, and suite brands
    assert _booking._classify_cabin_bucket("", "Main") == "economy"
    assert _booking._classify_cabin_bucket("MAIN", "Main") == "economy"
    assert _booking._classify_cabin_bucket("BLUE", "Blue") == "economy"
    assert _booking._classify_cabin_bucket("BLUE EXTRA", "Blue Extra") == "economy"
    assert _booking._classify_cabin_bucket("EVEN MORE", "Even More") == "economy"
    assert _booking._classify_cabin_bucket("SAVER", "Saver") == "economy"
    assert _booking._classify_cabin_bucket("VALUE", "Value") == "economy"
    assert _booking._classify_cabin_bucket("CLUB SUITE", "Club Suite") == "business"
    assert _booking._classify_cabin_bucket("QSUITE", "Qsuite") == "business"
    assert _booking._classify_cabin_bucket("SKY SUITE", "Sky Suite") == "business"
    assert _booking._classify_cabin_bucket("PRESTIGE SUITE", "Prestige Suite") == "business"
    assert _booking._classify_cabin_bucket("CLUB WORLD", "Club World") == "business"
    assert _booking._classify_cabin_bucket("WORLD TRAVELLER PLUS", "World Traveller Plus") == "premium-economy"
    assert _booking._classify_cabin_bucket("SUITE", "Suite") == "first"

    assert rpc._infer_rebookability_signal("basic", is_basic=True) == "restricted"
    assert rpc._infer_rebookability_signal("standard", is_basic=False) == "standard_rebookable"
    assert rpc._infer_rebookability_signal("enhanced", is_basic=False) == "upgraded_rebookable"
    assert rpc._infer_rebookability_signal("premium", is_basic=False) == "upgraded_rebookable"
    assert rpc._infer_rebookability_signal("unknown", is_basic=False) == "unknown"


def test_extract_booking_payload_and_rpc_parsers(monkeypatch) -> None:
    assert rpc.parse_booking_payload(")]}'   ") == []
    assert rpc.parse_booking_payload(")]}'not-json") == []
    assert rpc.parse_booking_payload(")]}'{}") == []

    payload = [
        make_booking_option(price=250, brand_code="DELTA MAIN CLASSIC", brand_label="Delta Main Classic"),
    ]
    text = encode_rpc_outer([None, [payload]])
    extracted = rpc.parse_booking_payload(text)
    assert len(extracted) == 1

    # Build options that trigger dropped counters/logging branches.
    dropped_payload = [
        make_booking_option(price=None, brand_code="DELTA MAIN CLASSIC", brand_label="Delta Main Classic"),  # missing price
        make_booking_option(price="bad", brand_code="DELTA MAIN CLASSIC", brand_label="Delta Main Classic"),  # invalid price
        make_booking_option(price=250, brand_code=None, brand_label=None),  # missing brand
    ]
    dropped_text = encode_rpc_outer([None, [dropped_payload]])
    assert rpc._parse_booking_rpc_response(dropped_text) == []

    # Trigger missing required field warning path by requiring a fake key.
    parsed = rpc._parse_booking_rpc_response(text, required_keys=("price", "missing_key"))
    assert parsed and parsed[0]["price"] == 250

    assert rpc._parse_rpc_response(")]}'  ") is None
    with pytest.raises(rpc.SwoopParseError, match="Failed to parse RPC response JSON"):
        rpc._parse_rpc_response(")]}'not-json")

    missing_inner = ")]}'" + json.dumps([["wrb.fr", None]])
    assert rpc._parse_rpc_response(missing_inner) is None

    empty_inner = ")]}'" + json.dumps([["wrb.fr", None, None]])
    assert rpc._parse_rpc_response(empty_inner) is None

    bad_inner_json = ")]}'" + json.dumps([["wrb.fr", None, "{bad"]])
    with pytest.raises(rpc.SwoopParseError, match="Failed to parse inner RPC response JSON"):
        rpc._parse_rpc_response(bad_inner_json)


def test_booking_parser_logs_debug_for_partial_drops_and_warning_for_total_drop(caplog) -> None:
    mixed_payload = [
        make_booking_option(price=250, brand_code="DELTA MAIN CLASSIC", brand_label="Delta Main Classic"),
        make_booking_option(price=300, brand_code=None, brand_label=None),
    ]
    mixed_text = encode_rpc_outer([None, [mixed_payload]])

    caplog.set_level(logging.DEBUG, logger="swoop._booking")
    parsed = rpc._parse_booking_rpc_response(mixed_text)
    assert len(parsed) == 1
    assert "Booking options parser dropped options" in caplog.text
    assert not any(record.levelno >= logging.WARNING for record in caplog.records)

    caplog.clear()

    dropped_payload = [
        make_booking_option(price=250, brand_code=None, brand_label=None),
    ]
    dropped_text = encode_rpc_outer([None, [dropped_payload]])
    assert rpc._parse_booking_rpc_response(dropped_text) == []
    assert any(record.levelno >= logging.WARNING for record in caplog.records)


def test_parse_rpc_response_null_and_decode(monkeypatch) -> None:
    none_inner_data = ")]}'" + json.dumps([["wrb.fr", None, "null"]])
    assert rpc._parse_rpc_response(none_inner_data) is None

    monkeypatch.setattr(rpc, "decode_result", lambda value: {"decoded": value})
    good = ")]}'" + json.dumps([["wrb.fr", None, json.dumps({"k": "v"})]])
    assert rpc._parse_rpc_response(good) == {"decoded": {"k": "v"}}

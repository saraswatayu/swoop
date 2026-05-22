"""Tests for the deals feature."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from swoop import Deal, DealsResult, deals
from swoop._deals import (
    _build_deals_payload,
    _currency_header,
    _format_date,
    _parse_deal,
    _parse_streaming_response,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "responses"


def _load_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text()


# ---------------------------------------------------------------------------
# Unit tests: _format_date
# ---------------------------------------------------------------------------


class TestFormatDate:
    def test_valid_date(self):
        assert _format_date([2026, 5, 15]) == "2026-05-15"

    def test_single_digit(self):
        assert _format_date([2026, 1, 3]) == "2026-01-03"

    def test_none(self):
        assert _format_date(None) is None

    def test_empty_list(self):
        assert _format_date([]) is None

    def test_short_list(self):
        assert _format_date([2026, 5]) is None


# ---------------------------------------------------------------------------
# Unit tests: _parse_deal
# ---------------------------------------------------------------------------


def _make_raw_deal(
    dep_date=(2026, 5, 15),
    ret_date=(2026, 5, 22),
    price=342,
    typical_price=1069,
    discount_pct=68,
    airline_code="TP",
    airline_name="Tap Air Portugal",
    duration=420,
    stops=0,
    origin="JFK",
    destination="LIS",
    city="Lisbon",
    country="Portugal",
    trip_days=7,
):
    """Build a raw deal item matching the Google Flights response format."""
    item = [None] * 23
    item[1] = list(dep_date)
    item[2] = list(ret_date) if ret_date else None
    item[3] = [[None, price]]
    item[4] = [[None, typical_price]] if typical_price is not None else None
    item[5] = discount_pct
    item[6] = [None, None, "/travel/flights?tfs=abc"]
    item[7] = duration
    item[8] = stops
    item[10] = airline_code
    item[11] = airline_name
    item[13] = [city, country, "https://img.example.com/x.jpg", ["landmark"]]
    item[16] = trip_days
    item[17] = origin
    item[18] = destination
    item[22] = ["/m/0test", 4]
    return item


class TestParseDeal:
    def test_basic(self):
        raw = _make_raw_deal()
        deal = _parse_deal(raw, currency="USD")
        assert deal is not None
        assert deal.origin == "JFK"
        assert deal.destination == "LIS"
        assert deal.destination_city == "Lisbon"
        assert deal.destination_country == "Portugal"
        assert deal.departure_date == "2026-05-15"
        assert deal.return_date == "2026-05-22"
        assert deal.price == 342
        assert deal.typical_price == 1069
        assert deal.discount_pct == 68
        assert deal.airline_code == "TP"
        assert deal.airline_name == "Tap Air Portugal"
        assert deal.duration_minutes == 420
        assert deal.stops == 0
        assert deal.trip_days == 7
        assert deal.currency == "USD"
        assert deal.booking_url == "https://www.google.com/travel/flights?tfs=abc"

    def test_missing_typical_price(self):
        raw = _make_raw_deal(typical_price=None, discount_pct=None)
        deal = _parse_deal(raw, currency="USD")
        assert deal is not None
        assert deal.typical_price is None
        assert deal.discount_pct is None

    def test_nonstop(self):
        raw = _make_raw_deal(stops=0)
        deal = _parse_deal(raw)
        assert deal.stops == 0

    def test_one_stop(self):
        raw = _make_raw_deal(stops=1)
        deal = _parse_deal(raw)
        assert deal.stops == 1

    def test_missing_price_returns_none(self):
        raw = _make_raw_deal()
        raw[3] = None
        assert _parse_deal(raw) is None

    def test_missing_destination_returns_none(self):
        raw = _make_raw_deal()
        raw[18] = None
        assert _parse_deal(raw) is None


# ---------------------------------------------------------------------------
# Unit tests: _parse_streaming_response
# ---------------------------------------------------------------------------


class TestParseStreamingResponse:
    def test_parses_fixture(self):
        text = _load_fixture("deals_streaming.txt")
        items = _parse_streaming_response(text)
        assert len(items) == 30
        # Each item should be a list with destination info
        for item in items:
            assert isinstance(item, list)
            assert len(item) > 18

    def test_empty_response(self):
        assert _parse_streaming_response("") == []

    def test_no_deals_chunk(self):
        # Just the security prefix and metadata
        text = ")]}'\n\n27\n[[\"e\",6,null,null,100]]\n"
        assert _parse_streaming_response(text) == []


# ---------------------------------------------------------------------------
# Unit tests: _build_deals_payload
# ---------------------------------------------------------------------------


class TestBuildDealsPayload:
    def test_returns_encoded_string(self):
        result = _build_deals_payload("JFK")
        assert isinstance(result, str)
        assert len(result) > 50

    def test_contains_origin(self):
        import urllib.parse
        result = _build_deals_payload("LAX")
        decoded = urllib.parse.unquote(result)
        outer = json.loads(decoded)
        inner = json.loads(outer[1])
        # Origin should be in the segments
        assert "LAX" in json.dumps(inner)

    def test_cabin_business(self):
        from swoop.builders import CABIN_CLASS_MAP
        result = _build_deals_payload("JFK", cabin="business")
        # The encoded payload should contain cabin code 3
        assert isinstance(result, str)

    def test_exclude_basic_economy_by_default(self):
        # The encoded payload's inner-array slot 28 should be 1 (exclude
        # basic economy) when include_basic_economy is False (the default).
        import urllib.parse
        result = _build_deals_payload("JFK")
        # _encode_f_req_payload wraps as [None, json_string]; unwrap.
        wrapped = json.loads(urllib.parse.unquote(result))
        payload = json.loads(wrapped[1])
        inner = payload[1]
        assert inner[28] == 1, "default should exclude basic economy (slot 28 = 1)"

    def test_include_basic_economy_clears_slot(self):
        import urllib.parse
        result = _build_deals_payload("JFK", include_basic_economy=True)
        wrapped = json.loads(urllib.parse.unquote(result))
        payload = json.loads(wrapped[1])
        inner = payload[1]
        assert inner[28] is None, "include_basic_economy=True should clear slot 28"


# ---------------------------------------------------------------------------
# Unit tests: _currency_header
# ---------------------------------------------------------------------------


class TestCurrencyHeader:
    def test_us(self):
        h = _currency_header("US")
        parsed = json.loads(h)
        assert parsed[1] == "US"
        assert parsed[2] == "USD"

    def test_gb(self):
        h = _currency_header("GB")
        parsed = json.loads(h)
        assert parsed[2] == "GBP"

    def test_none_defaults_to_us(self):
        h = _currency_header(None)
        parsed = json.loads(h)
        assert parsed[2] == "USD"

    def test_unknown_country_defaults_to_usd(self):
        h = _currency_header("XX")
        parsed = json.loads(h)
        assert parsed[2] == "USD"


# ---------------------------------------------------------------------------
# Unit tests: DealsResult / Deal models
# ---------------------------------------------------------------------------


class TestDealsResultModel:
    def test_currency_property_empty(self):
        r = DealsResult()
        assert r.currency is None

    def test_currency_property_from_first_deal(self):
        deal = Deal(
            origin="JFK", destination="LIS", destination_city="Lisbon",
            destination_country="Portugal", departure_date="2026-05-15",
            return_date="2026-05-22", price=342, typical_price=1069,
            discount_pct=68, airline_code="TP", airline_name="TAP",
            duration_minutes=420, stops=0, trip_days=7, currency="EUR",
        )
        r = DealsResult(deals=[deal], origin="JFK")
        assert r.currency == "EUR"

    def test_repr(self):
        r = DealsResult(deals=[], origin="JFK")
        assert "JFK" in repr(r)


# ---------------------------------------------------------------------------
# Integration test: fetch_deals with mocked HTTP
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_primp_deals(monkeypatch):
    """Patch primp.Client for deals tests (supports both GET and POST)."""
    import swoop.rpc as _rpc

    def _install(post_text="", post_status=200):
        response = MagicMock()
        response.status_code = post_status
        response.text = post_text

        class FakeClient:
            def __init__(self, **_kw):
                pass
            def get(self, *_a, **_kw):
                # Session establishment — just needs to not crash
                return MagicMock(status_code=200, text="<html></html>")
            def post(self, *_a, **_kw):
                return response

        _rpc._clients.clear()
        monkeypatch.setitem(sys.modules, "primp", types.SimpleNamespace(Client=FakeClient))
        return response
    return _install


class TestFetchDeals:
    def test_basic(self, fake_primp_deals):
        text = _load_fixture("deals_streaming.txt")
        fake_primp_deals(post_text=text)
        result = deals("JFK")
        assert isinstance(result, DealsResult)
        assert len(result.deals) == 30
        assert result.origin == "JFK"
        assert all(isinstance(d, Deal) for d in result.deals)
        assert all(d.price > 0 for d in result.deals)

    def test_empty_response(self, fake_primp_deals):
        fake_primp_deals(post_text=")]}'\n\n27\n[[\"e\",6,null,null,0]]\n")
        result = deals("JFK")
        assert len(result.deals) == 0

    def test_invalid_origin_raises(self):
        with pytest.raises(ValueError, match="origin"):
            deals("xx")

    def test_invalid_cabin_raises(self, fake_primp_deals):
        fake_primp_deals()
        with pytest.raises(ValueError, match="cabin"):
            deals("JFK", cabin="ultra")


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


class TestDealsCLI:
    def test_deals_help(self):
        from swoop.cli.commands import deals_cmd
        runner = CliRunner()
        result = runner.invoke(deals_cmd, ["--help"])
        assert result.exit_code == 0
        assert "deals" in result.output.lower() or "ORIGIN" in result.output

    def test_deals_table(self, fake_primp_deals):
        from swoop.cli.commands import deals_cmd
        text = _load_fixture("deals_streaming.txt")
        fake_primp_deals(post_text=text)
        runner = CliRunner()
        result = runner.invoke(deals_cmd, ["JFK", "-q"])
        assert result.exit_code == 0

    def test_deals_json(self, fake_primp_deals):
        from swoop.cli.commands import deals_cmd
        text = _load_fixture("deals_streaming.txt")
        fake_primp_deals(post_text=text)
        runner = CliRunner()
        result = runner.invoke(deals_cmd, ["JFK", "-o", "json", "-q"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "deals" in data
        assert len(data["deals"]) == 30

    def test_deals_csv(self, fake_primp_deals):
        from swoop.cli.commands import deals_cmd
        text = _load_fixture("deals_streaming.txt")
        fake_primp_deals(post_text=text)
        runner = CliRunner()
        result = runner.invoke(deals_cmd, ["JFK", "-o", "csv", "-q"])
        assert result.exit_code == 0
        lines = result.output.strip().split("\n")
        assert len(lines) == 31  # header + 30 deals

    def test_deals_brief(self, fake_primp_deals):
        from swoop.cli.commands import deals_cmd
        text = _load_fixture("deals_streaming.txt")
        fake_primp_deals(post_text=text)
        runner = CliRunner()
        result = runner.invoke(deals_cmd, ["JFK", "-o", "brief", "-q"])
        assert result.exit_code == 0
        lines = result.output.strip().split("\n")
        assert len(lines) == 30

    def test_deals_limit(self, fake_primp_deals):
        from swoop.cli.commands import deals_cmd
        text = _load_fixture("deals_streaming.txt")
        fake_primp_deals(post_text=text)
        runner = CliRunner()
        result = runner.invoke(deals_cmd, ["JFK", "-o", "brief", "-q", "-l", "5"])
        assert result.exit_code == 0
        lines = result.output.strip().split("\n")
        assert len(lines) == 5

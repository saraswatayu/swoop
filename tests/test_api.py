"""Tests for the public swoop API (search function and package exports)."""

from __future__ import annotations

import json
import sys
import types
from unittest.mock import patch

import pytest

import swoop
import swoop.rpc as rpc
from swoop.decoder import BookingOption, Segment, Itinerary
from swoop.exceptions import SwoopHTTPError, SwoopRateLimitError

from tests.factories import FakeHTTPResponse


def test_version():
    assert hasattr(swoop, "__version__")
    assert swoop.__version__ == "0.4.1"


def test_all_exports_importable():
    for name in swoop.__all__:
        assert hasattr(swoop, name), f"swoop.__all__ lists {name!r} but it's not importable"


def test_search_validates_cabin():
    with pytest.raises(ValueError, match="Invalid cabin"):
        swoop.search("JFK", "LAX", "2026-06-01", cabin="biz")

    with pytest.raises(ValueError, match="Invalid cabin"):
        swoop.search("JFK", "LAX", "2026-06-01", cabin="coach")


def test_search_validates_iata():
    with pytest.raises(ValueError, match="origin"):
        swoop.search("jfk", "LAX", "2026-06-01")

    with pytest.raises(ValueError, match="destination"):
        swoop.search("JFK", "la", "2026-06-01")


def test_search_validates_date():
    with pytest.raises(ValueError, match="date"):
        swoop.search("JFK", "LAX", "not-a-date")


def test_search_validates_adults():
    with pytest.raises(ValueError, match="adults"):
        swoop.search("JFK", "LAX", "2026-06-01", passengers=swoop.Passengers(adults=0))


def test_search_accepts_valid_cabins(fake_primp):
    """search() should accept all valid cabin values without raising."""
    fake_primp(200, ")]}'" + json.dumps([["wrb.fr", None, "null"]]))

    for cabin in ("economy", "premium-economy", "business", "first"):
        result = swoop.search("JFK", "LAX", "2026-06-01", cabin=cabin)
        assert isinstance(result, swoop.SearchResult)
        assert result.results == []


def test_search_delegates_to_leg_search_core(monkeypatch):
    """search() should normalize request legs and pass filters through."""
    captured = {}

    def fake_search_trip_options(legs, **kwargs):
        captured["legs"] = legs
        captured.update(kwargs)
        return swoop.SearchResult()

    monkeypatch.setattr(swoop, "search_trip_options", fake_search_trip_options)

    transport = swoop.TransportConfig(timeout=60, retries=3)
    swoop.search(
        "SFO", "NRT", "2026-07-01",
        return_date="2026-07-15",
        cabin="business",
        passengers=swoop.Passengers(adults=2),
        max_stops=1,
        sort=swoop.SORT_CHEAPEST,
        airlines=["NH"],
        earliest_departure=8,
        latest_departure=16,
        transport=transport,
    )

    assert captured["legs"][0]["origin"] == "SFO"
    assert captured["legs"][0]["destination"] == "NRT"
    assert captured["legs"][0]["date"] == "2026-07-01"
    assert captured["legs"][0]["max_stops"] == 1
    assert captured["legs"][0]["airlines"] == ["NH"]
    assert captured["legs"][0]["earliest_departure"] == 8
    assert captured["legs"][0]["latest_departure"] == 16
    assert captured["legs"][1]["origin"] == "NRT"
    assert captured["legs"][1]["destination"] == "SFO"
    assert captured["legs"][1]["date"] == "2026-07-15"
    assert captured["cabin"] == "business"
    assert captured["passengers"].adults == 2
    assert captured["sort"] == swoop.SORT_CHEAPEST
    assert captured["transport"].timeout == 60
    assert captured["transport"].retries == 3


def test_search_legs_uses_per_leg_filters(monkeypatch):
    captured = {}

    def fake_search_trip_options(legs, **kwargs):
        captured["legs"] = legs
        captured.update(kwargs)
        return swoop.SearchResult()

    monkeypatch.setattr(swoop, "search_trip_options", fake_search_trip_options)

    swoop.search_legs([
        swoop.SearchLeg(
            date="2026-07-01",
            from_airport="SFO",
            to_airport="NRT",
            max_stops=0,
            airlines=["NH"],
        ),
        swoop.SearchLeg(
            date="2026-07-15",
            from_airport="NRT",
            to_airport="SFO",
            max_stops=1,
            airlines=["JL"],
        ),
    ], cabin="business", passengers=swoop.Passengers(adults=2))

    assert captured["legs"][0]["origin"] == "SFO"
    assert captured["legs"][0]["max_stops"] == 0
    assert captured["legs"][0]["airlines"] == ["NH"]
    assert captured["legs"][1]["origin"] == "NRT"
    assert captured["legs"][1]["max_stops"] == 1
    assert captured["legs"][1]["airlines"] == ["JL"]


def test_search_legs_accepts_more_than_two_legs(monkeypatch):
    captured = {}

    def fake_search_trip_options(legs, **kwargs):
        captured["legs"] = legs
        captured.update(kwargs)
        return swoop.SearchResult()

    monkeypatch.setattr(swoop, "search_trip_options", fake_search_trip_options)

    result = swoop.search_legs([
        swoop.SearchLeg(date="2026-07-01", from_airport="JFK", to_airport="LAX"),
        swoop.SearchLeg(date="2026-07-03", from_airport="LAX", to_airport="SFO"),
        swoop.SearchLeg(date="2026-07-07", from_airport="SFO", to_airport="JFK"),
    ])

    assert isinstance(result, swoop.SearchResult)
    assert len(captured["legs"]) == 3


def test_price_legs_accepts_more_than_two_legs(monkeypatch):
    monkeypatch.setattr(
        swoop,
        "resolve_selected_trip",
        lambda *args, **kwargs: (
            [
                Itinerary(
                    segments=[Segment(
                        airline="DL",
                        flight_number="2300",
                        departure_airport_code="JFK",
                        arrival_airport_code="LAX",
                        departure_date=(2026, 7, 1),
                    )],
                    direct_price=300,
                    booking_token="token-1",
                ),
                Itinerary(
                    segments=[Segment(
                        airline="UA",
                        flight_number="500",
                        departure_airport_code="LAX",
                        arrival_airport_code="SFO",
                        departure_date=(2026, 7, 3),
                    )],
                    direct_price=350,
                    booking_token="token-2",
                ),
                Itinerary(
                    segments=[Segment(
                        airline="DL",
                        flight_number="2301",
                        departure_airport_code="SFO",
                        arrival_airport_code="JFK",
                        departure_date=(2026, 7, 7),
                    )],
                    direct_price=900,
                    booking_token="token-3",
                ),
            ],
            ["explicit", "explicit", "explicit"],
            3,
        ),
    )
    monkeypatch.setattr(
        swoop,
        "price_selected_trip",
        lambda request_legs, resolved, **kwargs: swoop.PriceResult(
            price=900,
            itinerary=resolved[-1],
            rpc_calls=kwargs["rpc_calls"],
        ),
    )

    result = swoop.price_legs([
        swoop.SelectedLeg(flight_number="DL2300", origin="JFK", destination="LAX", date="2026-07-01"),
        swoop.SelectedLeg(flight_number="UA500", origin="LAX", destination="SFO", date="2026-07-03"),
        swoop.SelectedLeg(flight_number="DL2301", origin="SFO", destination="JFK", date="2026-07-07"),
    ])

    assert result is not None
    assert result.price == 900


def test_search_raw_rate_limit_raises_specific_error(fake_primp):
    """429 should raise SwoopRateLimitError, not generic SwoopHTTPError."""
    fake_primp(429, "")
    # search() retries internally on 429 with a sleep — skip the delay.
    with patch("time.sleep", lambda _: None):
        with pytest.raises(SwoopRateLimitError) as exc_info:
            swoop.search("JFK", "LAX", "2026-06-01")

    assert exc_info.value.status_code == 429
    assert isinstance(exc_info.value, SwoopHTTPError)  # subclass check


def test_passengers_validation():
    """_PBPassengers should raise ValueError, not AssertionError."""
    from swoop.builders import _PBPassengers

    with pytest.raises(ValueError, match="Too many passengers"):
        _PBPassengers(adults=8, children=2)

    with pytest.raises(ValueError, match="infant"):
        _PBPassengers(adults=1, infants_on_lap=2)


def test_exception_hierarchy():
    """All exceptions should inherit from SwoopError."""
    assert issubclass(SwoopHTTPError, swoop.SwoopError)
    assert issubclass(SwoopRateLimitError, swoop.SwoopError)
    assert issubclass(swoop.SwoopParseError, swoop.SwoopError)
    assert issubclass(SwoopRateLimitError, SwoopHTTPError)


# --- BookingOption tests ---


def test_booking_option_attribute_access():
    opt = BookingOption(price=250, brand_label="Main Cabin", brand_code="MAIN", fare_family="standard")
    assert opt.price == 250
    assert opt.brand_label == "Main Cabin"
    assert opt.brand_code == "MAIN"
    assert opt.fare_family == "standard"


# --- Itinerary-based get_booking_results ---


def test_get_booking_results_with_itinerary(monkeypatch):
    """get_booking_results accepts an Itinerary object."""
    itin = Itinerary(
        departure_airport_code="JFK",
        arrival_airport_code="LAX",
        departure_date=(2026, 6, 15),
        booking_token="test-token",
        segments=[
            Segment(
                departure_airport_code="JFK",
                arrival_airport_code="LAX",
                departure_date=(2026, 6, 15),
                airline="DL",
                flight_number="123",
            ),
        ],
    )

    captured = {}

    def fake_http_post(url, content, **kwargs):
        captured["url"] = url
        captured["timeout"] = kwargs.get("timeout", 90)
        captured["retries"] = kwargs.get("retries", 0)
        return FakeHTTPResponse(200, ")]}'" + json.dumps([["wrb.fr", None, json.dumps([None, []])]]))

    monkeypatch.setattr(rpc, "_http_post", fake_http_post)

    result = rpc.get_booking_results(itin)
    assert result == []  # empty payload -> empty list
    assert captured["timeout"] == 90


def test_get_booking_results_string_still_works(monkeypatch):
    """Old string-based API still works."""
    result = rpc.get_booking_results("", origin="JFK", destination="LAX", date="2026-06-15", selected_legs=[])
    assert result == []

    result = rpc.get_booking_results("token", origin="JFK", destination="LAX", date="2026-06-15", selected_legs=None)
    assert result == []


def test_build_selected_legs():
    itin = Itinerary(
        segments=[
            Segment(
                departure_airport_code="JFK",
                arrival_airport_code="ORD",
                departure_date=(2026, 6, 15),
                airline="AA",
                flight_number="100",
            ),
            Segment(
                departure_airport_code="ORD",
                arrival_airport_code="LAX",
                departure_date=(2026, 6, 15),
                airline="AA",
                flight_number="200",
            ),
        ],
    )
    legs = rpc._build_selected_legs(itin)
    assert len(legs) == 2
    assert legs[0] == ["JFK", "2026-06-15", "ORD", None, "AA", "100"]
    assert legs[1] == ["ORD", "2026-06-15", "LAX", None, "AA", "200"]


def test_build_selected_legs_skips_bad_dates():
    itin = Itinerary(
        segments=[
            Segment(departure_airport_code="JFK", arrival_airport_code="LAX", departure_date=(0, 0, 0)),
        ],
    )
    assert rpc._build_selected_legs(itin) == []


# --- Retry tests ---


def test_http_post_retries_on_429(monkeypatch):
    """_http_post retries on 429 with backoff."""
    call_count = 0

    class FakeClient:
        def __init__(self, **_kw):
            pass
        def post(self, *_a, **_kw):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return FakeHTTPResponse(429, "ok")
            return FakeHTTPResponse(200, "ok")

    rpc._clients.clear()
    monkeypatch.setitem(sys.modules, "primp", types.SimpleNamespace(Client=FakeClient))
    monkeypatch.setattr("time.sleep", lambda _: None)  # skip actual sleep

    from swoop.models import TransportConfig
    res = rpc._http_post("http://test", b"body", transport=TransportConfig(timeout=10, retries=3))
    assert res.status_code == 200
    assert call_count == 3


def test_http_post_raises_after_exhausting_retries(fake_primp, monkeypatch):
    """_http_post raises SwoopRateLimitError after all retries fail."""
    fake_primp(429, "")
    monkeypatch.setattr("time.sleep", lambda _: None)

    from swoop.models import TransportConfig
    with pytest.raises(SwoopRateLimitError):
        rpc._http_post("http://test", b"body", transport=TransportConfig(timeout=10, retries=2))


def test_http_post_no_retry_on_non_429(monkeypatch):
    """Non-429 errors are raised immediately, never retried."""
    call_count = 0

    class FakeClient:
        def __init__(self, **_kw):
            pass
        def post(self, *_a, **_kw):
            nonlocal call_count
            call_count += 1
            return FakeHTTPResponse(503, "")

    rpc._clients.clear()
    monkeypatch.setitem(sys.modules, "primp", types.SimpleNamespace(Client=FakeClient))

    from swoop.models import TransportConfig
    with pytest.raises(SwoopHTTPError, match="HTTP 503"):
        rpc._http_post("http://test", b"body", transport=TransportConfig(timeout=10, retries=3))
    assert call_count == 1


def test_http_post_passes_timeout(monkeypatch):
    """timeout is passed through to primp."""
    captured_kwargs = {}

    class FakeClient:
        def __init__(self, **_kw):
            pass
        def post(self, *_a, **kwargs):
            captured_kwargs.update(kwargs)
            return FakeHTTPResponse(200, "ok")

    rpc._clients.clear()
    monkeypatch.setitem(sys.modules, "primp", types.SimpleNamespace(Client=FakeClient))

    from swoop.models import TransportConfig
    rpc._http_post("http://test", b"body", transport=TransportConfig(timeout=45))
    assert captured_kwargs["timeout"] == 45

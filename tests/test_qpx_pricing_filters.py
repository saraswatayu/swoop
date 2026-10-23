"""Captured Chrome wire formats and complete search → selector → booking paths."""

import base64
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote

import pytest
from click.testing import CliRunner

import swoop
import swoop.rpc as rpc
import swoop._selection as selection
from swoop import flights_pb2 as PB
from swoop.builders import TFSData, _PBPassengers
from swoop.cli import main
from tests.factories import make_simple_itinerary, make_raw_result

FIXTURES = Path(__file__).parent / "fixtures" / "qpx_pricing"
PRICING = dict(carry_on_bags=1, checked_bags=1, max_price=500)
ROUTING = dict(
    max_duration=700,
    connecting_airports=["ATL"],
    min_layover_duration=30,
    max_layover_duration=870,
)


def decode_body(content):
    return json.loads(json.loads(parse_qs(content.decode())["f.req"][0])[1])


def test_chrome_wire_slots():
    captures = json.loads((FIXTURES / "browser_filters.json").read_text())
    inputs = {
        "carry": dict(carry_on_bags=1),
        "checked": dict(carry_on_bags=1, checked_bags=1),
        "Duration": dict(max_duration=1320),
        "Price": dict(max_price=1650, max_duration=1320),
        "exclude": dict(exclude_connecting_airports=["DEN"]),
        "include": dict(connecting_airports=["ATL"]),
        "minlayover": dict(connecting_airports=["ATL"], min_layover_duration=30),
        "maxlayover": dict(
            connecting_airports=["ATL"],
            min_layover_duration=30,
            max_layover_duration=870,
        ),
    }
    for name, kwargs in inputs.items():
        leg_args = {k: v for k, v in kwargs.items() if k not in PRICING}
        global_args = {k: v for k, v in kwargs.items() if k in PRICING}
        leg = rpc._normalize_rpc_leg("JFK", "LAX", "2026-12-03", **leg_args)
        filters = rpc._build_filters_from_legs([leg], **global_args)[1]
        assert filters[8] is None  # Not Google's baggage slot.
        for index, expected in captures[name]["filter_slots"].items():
            assert filters[int(index)] == expected
        for index, expected in captures[name]["segment_slots"].items():
            assert filters[13][0][int(index)] == expected


def test_tfs_filters_match_captured_google_urls():
    captures = json.loads((FIXTURES / "browser_filters.json").read_text())
    combined = TFSData.from_interface(
        flight_data=[
            swoop.SearchLeg(
                date="2026-12-03", from_airport="JFK", to_airport="LAX", **ROUTING
            )
        ],
        trip="one-way",
        passengers=_PBPassengers(),
        seat="economy",
        carry_on_bags=1,
        checked_bags=1,
        max_price=1650,
    ).pb()
    for name in [
        "checked",
        "Duration",
        "Price",
        "include",
        "exclude",
        "minlayover",
        "maxlayover",
    ]:
        captured = PB.Info()
        token = captures[name]["tfs"]
        captured.ParseFromString(
            base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        )
        if name == "checked":
            assert combined.baggage == captured.baggage
        elif name == "Price":
            assert combined.max_price == captured.max_price
        elif name == "Duration":
            own = swoop.SearchLeg(
                date="2026-12-03",
                from_airport="JFK",
                to_airport="LAX",
                max_duration=1320,
            )
            info = PB.Info()
            own.apply_to(info)
            assert info.data[0].max_duration == captured.data[0].max_duration == 1320
        elif name == "exclude":
            own = swoop.SearchLeg(
                date="2026-12-03",
                from_airport="JFK",
                to_airport="LAX",
                exclude_connecting_airports=["DEN"],
            )
            info = PB.Info()
            own.apply_to(info)
            assert (
                info.data[0].exclude_connecting_airports
                == captured.data[0].exclude_connecting_airports
            )
        else:
            assert (
                combined.data[0].connecting_airports
                == captured.data[0].connecting_airports
            )
            if name in ["minlayover", "maxlayover"]:
                assert (
                    combined.data[0].min_layover_duration
                    == captured.data[0].min_layover_duration
                )
            if name == "maxlayover":
                assert (
                    combined.data[0].max_layover_duration
                    == captured.data[0].max_layover_duration
                )


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(carry_on_bags=-1),
        dict(carry_on_bags=2),
        dict(carry_on_bags=True),
        dict(checked_bags=3),
        dict(checked_bags=None),
        dict(max_price=-1),
        dict(max_price=3.5),
    ],
)
def test_invalid_global_filters(kwargs):
    with pytest.raises(ValueError):
        rpc._build_filters_from_legs(
            [rpc._normalize_rpc_leg("JFK", "LAX", "2026-12-03")], **kwargs
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(max_duration=-1),
        dict(max_duration=True),
        dict(min_layover_duration=90, max_layover_duration=30),
        dict(connecting_airports=["ATL"], exclude_connecting_airports=["DEN"]),
        dict(connecting_airports="ATL"),
        dict(connecting_airports=["XX"]),
    ],
)
def test_invalid_routing_filters(kwargs):
    with pytest.raises(ValueError):
        rpc._normalize_rpc_leg("JFK", "LAX", "2026-12-03", **kwargs)
    with pytest.raises(ValueError):
        swoop.SearchLeg(
            date="2026-12-03", from_airport="JFK", to_airport="LAX", **kwargs
        )


@pytest.fixture
def wire_calls(monkeypatch):
    calls = []

    def post(url, content, **kwargs):
        payload = decode_body(content)
        calls.append((url, payload))
        return SimpleNamespace(text="synthetic")

    def parse(text):
        segments = calls[-1][1][1][13]
        leg = next((s for s in segments if not s[8]), segments[-1])
        itinerary = make_simple_itinerary(
            origin=leg[0][0][0][0],
            destination=leg[1][0][0][0],
            date=leg[6],
            airline="AA",
            flight_number="123",
            price=250,
            booking_token="synthetic-token",
        )
        return make_raw_result(itinerary)

    monkeypatch.setattr(rpc, "_http_post", post)
    monkeypatch.setattr(rpc, "_parse_rpc_response", parse)
    monkeypatch.setattr(
        rpc,
        "_parse_booking_rpc_response",
        lambda *a, **k: [swoop.BookingOption(price=300, _cabin_bucket="economy")],
    )
    return calls


def assert_wire(calls):
    assert calls
    for _url, payload in calls:
        filters = payload[1]
        assert filters[7] == [None, 500]
        assert filters[10] == [1, 1]
        for segment in filters[13]:
            assert segment[7] == [700]
            assert segment[9] == ["ATL"]
            assert segment[11:13] == [30, 870]


def test_public_search_roundtrip_selector_and_exact_booking(wire_calls):
    result = swoop.search(
        "JFK", "LAX", "2026-12-03", return_date="2026-12-08", **PRICING, **ROUTING
    )
    payload = selection.decode_trip_selector(result.results[0].selector)
    assert payload["carry_on_bags"] == payload["checked_bags"] == 1
    assert payload["max_price"] == 500
    priced = swoop.price_selector(result.results[0].selector)
    assert priced and priced.price == 300 and not priced.is_estimate
    assert_wire(wire_calls)
    assert all(s[8] for s in wire_calls[-1][1][1][13])


def test_staged_multicity_preserves_each_leg_and_global_filters(wire_calls):
    legs = [
        swoop.SearchLeg(date=day, from_airport=origin, to_airport=dest, **ROUTING)
        for origin, dest, day in [
            ("JFK", "LAX", "2026-12-03"),
            ("LAX", "SFO", "2026-12-08"),
            ("SFO", "JFK", "2026-12-10"),
        ]
    ]
    result = swoop.search_legs(legs, **PRICING)
    assert len(wire_calls) == 3
    assert len(result.results[0].legs) == 3
    assert swoop.price_selector(result.results[0].selector)
    assert_wire(wire_calls)


@pytest.mark.parametrize("api", ["raw", "booking", "check", "legs"])
def test_raw_and_exact_pricing_entry_points(wire_calls, api):
    if api == "raw":
        swoop.search_raw("JFK", "LAX", "2026-12-03", **PRICING, **ROUTING)
    elif api == "booking":
        itinerary = make_simple_itinerary(
            origin="JFK",
            destination="LAX",
            date="2026-12-03",
            booking_token="synthetic-token",
        )
        swoop.get_booking_results(itinerary, **PRICING, **ROUTING)
    elif api == "check":
        assert swoop.check_price(
            "AA123",
            origin="JFK",
            destination="LAX",
            date="2026-12-03",
            **PRICING,
            **ROUTING,
        )
    else:
        assert swoop.price_legs(
            [
                swoop.SelectedLeg(
                    flight_number="AA123",
                    origin="JFK",
                    destination="LAX",
                    date="2026-12-03",
                )
            ],
            **PRICING,
            **ROUTING,
        )
    assert_wire(wire_calls)


def test_old_selectors_retain_default_pricing(wire_calls):
    result = swoop.search("JFK", "LAX", "2026-12-03")
    token = result.results[0].selector
    payload = json.loads(
        base64.urlsafe_b64decode(
            token.split(":")[-1] + "=" * (-len(token.split(":")[-1]) % 4)
        )
    )
    for key in PRICING:
        payload.pop(key)
    old = selection.SELECTOR_PREFIX + selection._encode_payload(payload)
    wire_calls.clear()
    assert swoop.price_selector(old)
    assert all(p[1][10] is None and p[1][7] is None for _, p in wire_calls)


@pytest.mark.parametrize(
    "command",
    [
        ["search", "JFK", "LAX", "2026-12-03"],
        ["search", "--leg", "JFK", "LAX", "2026-12-03"],
        ["price", "JFK", "LAX", "--depart", "2026-12-03", "AA123"],
        ["price", "--leg", "JFK", "LAX", "2026-12-03", "AA123"],
    ],
)
def test_cli_filters_reach_the_wire(wire_calls, command):
    result = CliRunner().invoke(
        main,
        [
            *command,
            "--carry-on-bags",
            "1",
            "--checked-bags",
            "1",
            "--max-price",
            "500",
            "--max-duration",
            "700",
            "--connect-via",
            "ATL",
            "--min-layover",
            "30",
            "--max-layover",
            "870",
            "-q",
        ],
    )
    assert result.exit_code == 0, (result.output, result.exception)
    assert_wire(wire_calls)


def test_zero_bounds_are_serialized_without_becoming_unspecified():
    leg = rpc._normalize_rpc_leg(
        "JFK",
        "LAX",
        "2026-12-03",
        max_duration=0,
        min_layover_duration=0,
        max_layover_duration=0,
    )
    f = rpc._build_filters_from_legs([leg], max_price=0)[1]
    assert f[7] == [None, 0] and f[10] is None
    assert f[13][0][7] == [0] and f[13][0][11:13] == [0, 0]


def test_price_ceiling_applies_to_booking_and_estimate(wire_calls, monkeypatch):
    monkeypatch.setattr(
        rpc,
        "_parse_booking_rpc_response",
        lambda *a, **k: [swoop.BookingOption(price=600, _cabin_bucket="economy")],
    )
    itinerary = make_simple_itinerary(price=550, booking_token="synthetic-token")
    assert (
        selection.price_selected_trip(
            [{"origin": "JFK", "destination": "LAX", "date": "2026-12-03"}],
            [itinerary],
            max_price=500,
        )
        is None
    )


@pytest.mark.live
@pytest.mark.parametrize("roundtrip", [False, True])
def test_live_baggage_survives_selector_booking(roundtrip):
    from datetime import date, timedelta

    day = date.today() + timedelta(days=42)
    transport = swoop.TransportConfig(country="US", timeout=45, retries=0)
    extra = dict(return_date=(day + timedelta(days=5)).isoformat()) if roundtrip else {}
    result = swoop.search(
        "JFK",
        "LAX",
        day.isoformat(),
        airlines=["B6"],
        max_stops=0,
        carry_on_bags=1,
        checked_bags=1,
        max_price=2000,
        max_duration=450,
        transport=transport,
        **extra,
    )
    assert result.results
    selector = result.results[0].selector
    payload = selection.decode_trip_selector(selector)
    assert payload["carry_on_bags"] == payload["checked_bags"] == 1
    priced = swoop.price_selector(selector, transport=transport)
    assert priced and 0 < priced.price <= 2000 and priced.currency == "USD"
    assert not priced.is_estimate

"""Reject invalid transport and selector context before issuing provider calls."""

import base64
import json

import pytest

import swoop._selection as selection
from swoop.models import TransportConfig
from tests.factories import make_simple_itinerary


@pytest.mark.parametrize("value", [123, True, [], {}, b"USD"])
def test_nonstring_display_currency_raises_value_error(value):
    with pytest.raises(ValueError, match="currency"):
        TransportConfig(currency=value)


@pytest.mark.parametrize("value", [123, True, [], {}, b"US"])
def test_nonstring_point_of_sale_raises_value_error(value):
    with pytest.raises(ValueError, match="country"):
        TransportConfig(country=value)


def _selector_payload():
    itinerary = make_simple_itinerary(origin="JFK", destination="LAX", date="2026-12-03")
    selector = selection.encode_trip_selector(
        request_legs=[{"origin": "JFK", "destination": "LAX", "date": "2026-12-03"}],
        itineraries=[itinerary], cabin="economy", include_basic_economy=False,
    )
    encoded = selector[len(selection.SELECTOR_PREFIX):]
    return json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))


@pytest.mark.parametrize("field,value", [
    ("currency", 123), ("currency", []), ("currency", {}), ("currency", "USDD"),
    ("country", 123), ("country", []), ("country", {}),
    ("query_legs", None), ("query_legs", 42), ("query_legs", []),
    ("query_legs", [None]), ("query_legs", [{}]),
    ("query_legs", [{"origin": "JFK", "destination": "LAX", "date": "2026-12-03", "airlines": 3}]),
    ("selected_legs", None), ("selected_legs", [None]), ("cabin", []),
])
def test_tampered_context_is_a_value_error_and_cannot_reach_http(monkeypatch, field, value):
    payload = _selector_payload()
    payload[field] = value
    selector = selection.SELECTOR_PREFIX + selection._encode_payload(payload)
    monkeypatch.setattr(selection, "_search_from_legs", lambda *_a, **_k: pytest.fail("invalid selector reached shopping"))
    with pytest.raises(ValueError):
        selection.resolve_trip_selector(selector)
    assert selection.price_trip_selector(selector) is None


@pytest.mark.parametrize("value", [None, 42, [], {}, b"swoop:sel:1:"])
def test_nonstring_selector_is_not_a_transport_failure(value):
    with pytest.raises(ValueError):
        selection.resolve_trip_selector(value)
    assert selection.price_trip_selector(value) is None


@pytest.mark.parametrize("value", [[], {}, "1", -1, True])
def test_malformed_stops_are_rejected_before_building_a_request(monkeypatch, value):
    payload = _selector_payload()
    payload["query_legs"][0]["max_stops"] = value
    selector = selection.SELECTOR_PREFIX + selection._encode_payload(payload)
    monkeypatch.setattr(selection, "_search_from_legs", lambda *_a, **_k: pytest.fail("invalid stops reached shopping"))
    with pytest.raises(ValueError):
        selection.resolve_trip_selector(selector)
    assert selection.price_trip_selector(selector) is None
@pytest.mark.parametrize("path,value", [
    (("passengers", "children"), []), (("passengers", "infants_in_seat"), {}),
    (("sort",), []), (("sort",), "3"),
    (("query_legs", 0, "airlines"), [1]), (("query_legs", 0, "airlines"), {"AA": 1}),
    (("query_legs", 0, "earliest_departure"), {}), (("query_legs", 0, "latest_arrival"), "18"),
    (("selected_legs", 0, 0, 0), {}), (("selected_legs", 0, 0, 4), []),
])
def test_legacy_filter_shapes_cannot_trigger_fallback_builder_errors(monkeypatch, path, value):
    payload = _selector_payload()
    target = payload
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    _assert_no_rpc(monkeypatch, payload)


def _assert_no_rpc(monkeypatch, payload):
    monkeypatch.setattr(selection, "_search_from_legs", lambda *_a, **_k: pytest.fail("invalid selector reached shopping"))
    selector = selection.SELECTOR_PREFIX + selection._encode_payload(payload)
    with pytest.raises(ValueError):
        selection.resolve_trip_selector(selector)
    assert selection.price_trip_selector(selector) is None

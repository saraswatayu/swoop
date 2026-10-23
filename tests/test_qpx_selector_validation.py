"""Malformed QPX selector filters fail before any shopping or booking request."""

import base64
import json

import pytest

import swoop._selection as selection
from swoop.models import TransportConfig
from tests.factories import make_simple_itinerary


@pytest.mark.parametrize("value", [123, True, [], {}, b"US"])
def test_nonstring_country_is_rejected(value):
    with pytest.raises(ValueError, match="country"):
        TransportConfig(country=value)


def _payload():
    itinerary = make_simple_itinerary(origin="JFK", destination="LAX", date="2026-12-03")
    selector = selection.encode_trip_selector(
        request_legs=[{"origin": "JFK", "destination": "LAX", "date": "2026-12-03"}],
        itineraries=[itinerary], cabin="economy", include_basic_economy=False,
    )
    encoded = selector[len(selection.SELECTOR_PREFIX):]
    return json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))


@pytest.mark.parametrize("field,value", [
    ("carry_on_bags", "1"), ("carry_on_bags", True), ("carry_on_bags", 2),
    ("checked_bags", {}), ("checked_bags", 3), ("checked_bags", -1),
    ("max_price", []), ("max_price", -1), ("max_price", 100.5),
])
def test_tampered_global_pricing_is_rejected_without_rpc(monkeypatch, field, value):
    payload = _payload()
    payload[field] = value
    _assert_no_rpc(monkeypatch, payload)


@pytest.mark.parametrize("field,value", [
    ("max_stops", []), ("max_stops", {}), ("max_stops", "1"), ("max_stops", -1), ("max_stops", True),
    ("max_duration", "600"), ("max_duration", {}), ("max_duration", -1),
    ("connecting_airports", True), ("connecting_airports", "ATL"),
    ("connecting_airports", {"ATL": 1}), ("connecting_airports", [None]),
    ("exclude_connecting_airports", 1), ("exclude_connecting_airports", ["ATL", 1]),
    ("min_layover_duration", "60"), ("max_layover_duration", -1),
])
def test_tampered_routing_is_rejected_without_rpc(monkeypatch, field, value):
    payload = _payload()
    payload["query_legs"][0][field] = value
    _assert_no_rpc(monkeypatch, payload)


def test_reversed_layover_bounds_are_rejected_without_rpc(monkeypatch):
    payload = _payload()
    payload["query_legs"][0].update(min_layover_duration=120, max_layover_duration=60)
    _assert_no_rpc(monkeypatch, payload)


@pytest.mark.parametrize("field,value", [("query_legs", None), ("query_legs", [None]),
    ("query_legs", [{}]), ("selected_legs", None), ("selected_legs", [None]), ("cabin", {})])
def test_malformed_context_is_rejected_without_rpc(monkeypatch, field, value):
    payload = _payload()
    payload[field] = value
    _assert_no_rpc(monkeypatch, payload)


def _assert_no_rpc(monkeypatch, payload):
    monkeypatch.setattr(selection, "_search_from_legs", lambda *_a, **_k: pytest.fail("invalid selector reached shopping"))
    selector = selection.SELECTOR_PREFIX + selection._encode_payload(payload)
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
    payload = _payload()
    target = payload
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    _assert_no_rpc(monkeypatch, payload)

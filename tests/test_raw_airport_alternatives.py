"""Low-level airport alternatives use one native RPC and exact decoded routes."""

import json
import urllib.parse
from copy import deepcopy
from pathlib import Path

import pytest

import swoop.rpc as rpc
from tests.factories import FakeHTTPResponse, encode_rpc_outer


def _capture_search(monkeypatch, origin, destination, **kwargs):
    requests = []
    raw = json.loads((Path(__file__).parent / "fixtures" / "responses" / "shopping_oneway_us.json").read_text())

    def post(url, content, **_kwargs):
        requests.append((url, json.loads(json.loads(urllib.parse.unquote(content.decode().split("=", 1)[1]))[1])))
        return FakeHTTPResponse(text=encode_rpc_outer(raw))

    monkeypatch.setattr(rpc, "_http_post", post)
    result = rpc.search_raw(origin, destination, "2026-10-26", **kwargs)
    assert len(requests) == 1
    return requests[0][1], result


@pytest.mark.parametrize("origin,destination,expected_origin,expected_destination", [
    ("JFK", ["ORD", "DFW", "AUS"], [[["JFK", 0]]], [[["ORD", 0], ["DFW", 0], ["AUS", 0]]]),
    (["ORD", "DFW", "AUS"], "MIA", [[["ORD", 0], ["DFW", 0], ["AUS", 0]]], [[["MIA", 0]]]),
    (["JFK", "LGA"], ["ORD", "MDW"], [[["JFK", 0], ["LGA", 0]]], [[["ORD", 0], ["MDW", 0]]]),
    (["JFK"], ["LAX"], [[["JFK", 0]]], [[["LAX", 0]]]),
    ("JFK", "LAX", [[["JFK", 0]]], [[["LAX", 0]]]),
])
def test_native_airport_arrays_use_one_rpc(monkeypatch, origin, destination, expected_origin, expected_destination):
    payload, result = _capture_search(monkeypatch, origin, destination, max_stops=0, airlines=["AA"])
    segment = payload[1][13][0]
    assert segment[0] == expected_origin
    assert segment[1] == expected_destination
    assert segment[3] == rpc.STOPS_NONSTOP
    assert segment[4] == ["AA"]
    assert segment[6] == "2026-10-26"
    assert result is not None


def test_airport_arrays_do_not_change_the_returned_segment_identity(monkeypatch):
    _, expected = _capture_search(monkeypatch, "JFK", "LAX")
    _, combined = _capture_search(monkeypatch, ["JFK", "LGA"], ["LAX", "SFO"])
    assert combined.best == expected.best and combined.other == expected.other
    assert all(isinstance(segment.departure_airport_code, str)
               for itinerary in [*combined.best, *combined.other] for segment in itinerary.segments)


def test_roundtrip_airport_arrays_reverse_without_losing_alternatives(monkeypatch):
    origins, destinations = ["JFK", "LGA"], ["LAX", "SFO"]
    original = deepcopy((origins, destinations))
    payload, _ = _capture_search(monkeypatch, origins, destinations, return_date="2026-11-02")
    outbound, inbound = payload[1][13]
    assert payload[1][2] == 1
    assert inbound[0] == outbound[1] and inbound[1] == outbound[0]
    assert inbound[6] == "2026-11-02"
    assert (origins, destinations) == original


@pytest.mark.parametrize("value", [[], ["JFK", "JFK"], ["jfk"], ["TOOLONG"], ["JFK", None], ("JFK", "LGA"), None, 1])
def test_invalid_alternatives_are_rejected_before_transport(monkeypatch, value):
    monkeypatch.setattr(rpc, "_http_post", lambda *_args, **_kwargs: pytest.fail("invalid alternatives dispatched"))
    with pytest.raises(ValueError):
        rpc.search_raw(value, "LAX", "2026-10-26")
    with pytest.raises(ValueError):
        rpc.search_raw("JFK", value, "2026-10-26")

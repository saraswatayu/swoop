"""Google's captured UI filter must survive every trip request and replay."""
import base64
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote, parse_qs

import pytest
import swoop
import swoop._selection as selection
import swoop.rpc as rpc
from swoop import flights_pb2 as PB
from swoop._deals import _build_deals_payload
from swoop.builders import SearchLeg, TFSData, _PBPassengers
from swoop.decoder import BookingOption
from tests.factories import make_simple_itinerary, make_raw_result


LEGS = [
    {"origin": "JFK", "destination": "LAX", "date": "2026-11-15"},
    {"origin": "LAX", "destination": "JFK", "date": "2026-11-22"},
]


def unpack(encoded):
    return json.loads(json.loads(unquote(encoded))[1])


def test_request_filter_matches_captured_google_ui():
    fixture = json.loads((Path(__file__).parent / "fixtures/google-hide-separate-tickets-request.json").read_text())
    assert len(fixture["show"][1]) == 18
    assert fixture["hide"][1][18] == 1
    normal = rpc._build_filters_from_legs(LEGS)
    restricted = rpc._build_filters_from_legs(LEGS, exclude_separate_tickets=True)
    assert normal[1][18] is None
    assert restricted[1][18] == fixture["hide"][1][18]
    # Other filters, including wider discovery, stay independent.
    restricted[1][18] = None
    assert restricted == normal


def test_search_page_filter_matches_google_tfs_field():
    fixture = json.loads((Path(__file__).parent / "fixtures/google-hide-separate-tickets-request.json").read_text())
    captured = PB.Info()
    captured.ParseFromString(base64.urlsafe_b64decode(fixture["hide_tfs"] + "=" * (-len(fixture["hide_tfs"]) % 4)))
    assert captured.exclude_separate_tickets
    query = TFSData.from_interface(flight_data=[SearchLeg(date="2026-11-15", from_airport="JFK", to_airport="LAX")],
        trip="one-way", seat="economy", passengers=_PBPassengers(), exclude_separate_tickets=True)
    actual = PB.Info()
    actual.ParseFromString(query.to_string())
    assert actual.exclude_separate_tickets == captured.exclude_separate_tickets
    assert PB.Info.DESCRIPTOR.fields_by_name["exclude_separate_tickets"].number == 17


def test_search_continuation_and_price_preserve_exclusion(monkeypatch):
    outbound = make_simple_itinerary(origin="JFK", destination="LAX", date="2026-11-15", airline="AA", flight_number="117", price=500, booking_token="out")
    inbound = make_simple_itinerary(origin="LAX", destination="JFK", date="2026-11-22", airline="AA", flight_number="118", price=600, booking_token="in")
    calls = []

    def provider(legs, **kwargs):
        calls.append(kwargs)
        # Unrestricted requests deliberately cannot produce this market.
        assert kwargs["exclude_separate_tickets"] is True
        return make_raw_result(inbound if legs[0].get("selected_legs") else outbound)

    def booking(token, legs, **kwargs):
        assert kwargs["exclude_separate_tickets"] is True
        calls.append(kwargs)
        return [BookingOption(price=600, _cabin_bucket="economy")]

    monkeypatch.setattr(selection, "_search_from_legs", provider)
    monkeypatch.setattr(selection, "get_trip_booking_results", booking)
    result = swoop.search("JFK", "LAX", "2026-11-15", return_date="2026-11-22", exclude_separate_tickets=True)
    prefix = result.results[0]
    assert selection.decode_trip_selector(prefix.selector)["exclude_separate_tickets"] is True
    complete = swoop.search_next_leg(prefix.selector).results[0]
    assert complete.is_resolved
    assert selection.decode_trip_selector(complete.selector)["exclude_separate_tickets"] is True
    quote = swoop.price_selector(complete.selector)
    assert quote is not None and not quote.is_estimate
    assert len(calls) == 6


@pytest.mark.parametrize("legacy", [False, True])
def test_legacy_and_unrestricted_selectors_keep_their_original_policy(legacy):
    itinerary = make_simple_itinerary(origin="JFK", destination="LAX", date="2026-11-15")
    selector = selection.encode_trip_selector(request_legs=LEGS[:1], itineraries=[itinerary], cabin="economy", include_basic_economy=False)
    if legacy:
        encoded = selector[len(selection.SELECTOR_PREFIX):]
        data = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        data.pop("exclude_separate_tickets")
        selector = selection.SELECTOR_PREFIX + selection._encode_payload(data)
    assert selection.decode_trip_selector(selector)["exclude_separate_tickets"] is False


def test_selector_rejects_non_boolean_exclusion():
    data = {"v": 1, "exclude_separate_tickets": "true"}
    with pytest.raises(ValueError, match="separate-ticket filter"):
        selection.decode_trip_selector(selection.SELECTOR_PREFIX + selection._encode_payload(data))


@pytest.mark.parametrize("trip", [False, True])
def test_exact_booking_lookup_sends_exclusion(monkeypatch, trip):
    captured = []
    monkeypatch.setattr(rpc, "_http_post", lambda url, *, content, **kwargs: captured.append(content) or SimpleNamespace(text=""))
    monkeypatch.setattr(rpc, "_parse_booking_rpc_response", lambda *args, **kwargs: [])
    if trip:
        rpc.get_trip_booking_results("token", LEGS, exclude_separate_tickets=True)
    else:
        rpc.get_booking_results("token", origin="JFK", destination="LAX", date="2026-11-15",
            selected_legs=[["JFK", "2026-11-15", "LAX", None, "AA", "117"]], exclude_separate_tickets=True)
    encoded = parse_qs(captured[0].decode())["f.req"][0]
    inner = json.loads(json.loads(encoded)[1])
    assert inner[1][18] == 1


def test_deals_payload_excludes_separate_and_self_transfer():
    assert unpack(_build_deals_payload("JFK"))[1][18] is None
    assert unpack(_build_deals_payload("JFK", exclude_separate_tickets=True))[1][18] == 1

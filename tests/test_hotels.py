"""Tests for the Google Travel Hotels feature."""

from __future__ import annotations

import json
import urllib.parse

from swoop._hotels import (
    HOTEL_RESULTS_RPC,
    HOTEL_REVIEWS_RPC,
    UNIVERSAL_SEARCH_RPC,
    _build_hotel_detail_payload,
    _build_hotels_results_payload,
    _build_reviews_payload,
    _build_universal_search_payload,
    _encode_travel_f_req,
    _extract_context_from_universal,
    _parse_batchexecute_response,
    parse_hotel_prices_payload,
    parse_hotel_reviews_payload,
    parse_hotels_payload,
)


def _batchexecute(rpc_id: str, inner: list[object]) -> str:
    payload = [["wrb.fr", rpc_id, json.dumps(inner), None, None, [], "generic"]]
    line = json.dumps(payload)
    return f")]}}'\n\n{len(line)}\n{line}\n"


def _price(label: str, amount: int) -> list[object]:
    return [label, None, float(amount), None, amount]


def _raw_hotel() -> list[object]:
    record: list[object] = [None] * 24
    record[1] = "HI New York City Hostel"
    location: list[object] = [None] * 17
    location[0] = [40.798663, -73.966588]
    location[1] = [[["891 Amsterdam Ave, New York, NY 10025, United States"]]]
    location[16] = "3.5 mi away"
    record[2] = location
    record[3] = ["3-star hotel", 3]
    record[5] = [None, [[None, ["https://lh3.example/hotel.jpg", 192, 287]]]]
    price_info: list[object] = [None] * 9
    price_info[1] = _price("$65", 65)
    price_info[8] = _price("$130", 130)
    record[6] = [None, None, price_info, "LWMQBd"]
    record[7] = [[4.4, 4584]]
    record[9] = "0x89c2f6246837073b:0xc9dcfc4023c86664"
    record[13] = ["https://lh3.example/thumb.jpg", 150, 92]
    record[18] = "ChgI5MyhnoKIv-7JARoLL2cvMXdrN3J0MmIQAQ"
    record[22] = "5960741009900747244"
    record[23] = [1, None, None, None, "ChIJOwc3aCT2wokRZGbII0D83Mk"]
    return record


def _context() -> list[object]:
    context: list[object] = [None] * 19
    context[3] = "USD"
    context[4] = [[2026, 6, 1], [2026, 6, 3], 2, 1]
    context[16] = [[40.59, -74.21], [40.83, -73.80]]
    context[18] = ["/m/02_286", "New York", "0x89c24fa5d33f083b:0xc80b8f06e177fe62"]
    return context


def _universal_payload() -> list[object]:
    root: list[object] = [None] * 8
    root[1] = "New York"
    root[2] = [None, None, None, None, None, "ChgI5MyhnoKIv-7JARoLL2cvMXdrN3J0MmIQAQ", 13]
    root[5] = ["/m/02_286", None, None, None, None, "0x89c24fa5d33f083b:0xc80b8f06e177fe62"]
    root[7] = {
        "404340221": [_context()],
        "441552390": _raw_hotel(),
    }
    return [None, root, None]


def test_encode_travel_f_req_roundtrips():
    encoded = _encode_travel_f_req(UNIVERSAL_SEARCH_RPC, ["New York"])
    outer = json.loads(urllib.parse.unquote(encoded))
    assert outer[0][0][0] == UNIVERSAL_SEARCH_RPC
    assert json.loads(outer[0][0][1]) == ["New York"]
    assert outer[0][0][3] == "generic"


def test_parse_batchexecute_response_extracts_inner_payload():
    inner = _universal_payload()
    assert _parse_batchexecute_response(_batchexecute(UNIVERSAL_SEARCH_RPC, inner), UNIVERSAL_SEARCH_RPC) == inner


def test_build_universal_search_payload_includes_dates_currency_and_occupancy():
    payload = _build_universal_search_payload(
        "New York",
        check_in="2026-06-01",
        check_out="2026-06-03",
        adults=2,
        child_ages=[12],
        rooms=1,
        currency="USD",
    )
    assert payload[0] == "New York"
    assert payload[1][1] == [[[3], [3], [2, 12]], 1]
    assert payload[1][2][1][1] == [[2026, 6, 1], [2026, 6, 3], 2]
    assert payload[1][4][0][6] == "USD"


def test_extract_context_and_build_results_payload():
    context = _extract_context_from_universal(_universal_payload())
    assert context["destination_name"] == "New York"
    assert context["place_id"] == "/m/02_286"
    assert context["hotel_token"] == "ChgI5MyhnoKIv-7JARoLL2cvMXdrN3J0MmIQAQ"

    payload = _build_hotels_results_payload(
        context,
        check_in="2026-06-01",
        check_out="2026-06-03",
        adults=2,
        rooms=1,
        currency="USD",
    )
    assert payload is not None
    assert payload[1][3] == "USD"
    assert payload[1][4] == [[2026, 6, 1], [2026, 6, 3], 2, 1]
    assert payload[1][18][1] == "New York"


def test_parse_hotels_payload_extracts_hotel_cards():
    payload = [None, None, None, None, None, [None] * 10]
    payload[5][9] = [[1, {"179305178": _raw_hotel()}]]

    result = parse_hotels_payload(payload, query="New York", currency="USD", destination_name="New York")

    assert result.query == "New York"
    assert result.destination_name == "New York"
    assert len(result.hotels) == 1
    hotel = result.hotels[0]
    assert hotel.name == "HI New York City Hostel"
    assert hotel.price == 65
    assert hotel.total_price == 130
    assert hotel.rating == 4.4
    assert hotel.review_count == 4584
    assert hotel.hotel_class == 3
    assert hotel.latitude == 40.798663
    assert hotel.longitude == -73.966588
    assert hotel.address == "891 Amsterdam Ave, New York, NY 10025, United States"
    assert hotel.booking_token == "ChgI5MyhnoKIv-7JARoLL2cvMXdrN3J0MmIQAQ"
    assert hotel.place_id == "ChIJOwc3aCT2wokRZGbII0D83Mk"


def test_build_hotel_detail_payload_uses_token_and_entity_filter():
    context = _extract_context_from_universal(_universal_payload())
    payload = _build_hotel_detail_payload(
        context,
        "hotel-token",
        check_in="2026-06-01",
        check_out="2026-06-03",
        hotel_entity_id="0xhotel",
    )
    assert payload is not None
    assert payload[3] == "hotel-token"
    assert payload[4][13] == ["0xhotel"]
    assert payload[6] == 2


def test_parse_hotel_prices_payload_extracts_providers():
    nightly = _price("$73", 73)
    total = _price("$147", 147)
    provider_row: list[object] = [None] * 19
    provider_row[0] = ["Hostelworld", 100372930, "/aclk?sa=l", ["//www.gstatic.com/icon.png"]]
    provider_row[7] = [["Basic 10 Bed Male Dorm", None, [[None, [1], [False], None, nightly, total, None, None, True]]]]
    price_container: list[object] = [None] * 14
    price_container[4] = nightly
    price_container[5] = total
    provider_row[12] = price_container
    detail = [None, [None, None, None, "USD"], [None, _price("$65", 65), [provider_row]]]

    hotel = parse_hotel_prices_payload(detail)

    assert hotel.price == 65
    assert len(hotel.providers) == 1
    provider = hotel.providers[0]
    assert provider.name == "Hostelworld"
    assert provider.provider_id == "100372930"
    assert provider.url == "https://www.google.com/aclk?sa=l"
    assert provider.logo_url == "https://www.gstatic.com/icon.png"
    assert provider.room_name == "Basic 10 Bed Male Dorm"
    assert provider.price == 73
    assert provider.total_price == 147
    assert provider.is_free_cancellation is True


def test_parse_hotel_reviews_payload_extracts_reviews():
    payload = [
        [
            [
                [
                    ["Tripadvisor", None, ["https://www.gstatic.com/tripadvisor.png", 24, 24], 2, 100532569],
                    [
                        [
                            ["CJ2122", "https://www.tripadvisor.com/review", ["avatar", 40, 40]],
                            "7 months ago",
                            [4, 5],
                            [[[1, "Great stay. "], [1, "Clean rooms."]]],
                        ]
                    ],
                ]
            ]
        ]
    ]

    result = parse_hotel_reviews_payload(payload, hotel_token="hotel-token")

    assert result.hotel_token == "hotel-token"
    assert len(result.reviews) == 1
    review = result.reviews[0]
    assert review.source == "Tripadvisor"
    assert review.author == "CJ2122"
    assert review.url == "https://www.tripadvisor.com/review"
    assert review.rating == 4
    assert review.max_rating == 5
    assert review.relative_date == "7 months ago"
    assert review.text == "Great stay. Clean rooms."


def test_build_reviews_payload_places_hotel_token_at_captured_index():
    payload = _build_reviews_payload("hotel-token")
    assert payload[8] == "hotel-token"
    assert payload[-1] == [[]]
    assert HOTEL_RESULTS_RPC == "M0CRd"
    assert HOTEL_REVIEWS_RPC == "ocp93e"

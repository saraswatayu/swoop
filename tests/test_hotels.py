"""Tests for the Google Travel Hotels feature."""

from __future__ import annotations

import json
import urllib.parse

from swoop._hotels import (
    HOTEL_RESULTS_RPC,
    HOTEL_REVIEWS_RPC,
    UNIVERSAL_SEARCH_RPC,
    _build_hotel_detail_payload,
    _build_filtered_universal_search_payload,
    _build_hotels_results_payload,
    _build_reviews_payload,
    _build_universal_search_payload,
    _encode_travel_f_req,
    _extract_context_from_universal,
    _extract_af_init_data,
    _filter_and_sort_hotels,
    _merge_seed_hotel,
    _parse_batchexecute_response,
    _parse_hotels_page_html,
    parse_hotel_prices_payload,
    parse_hotel_reviews_payload,
    parse_hotels_payload,
    fetch_hotels,
)
from swoop.models import Hotel


def _batchexecute(rpc_id: str, inner: list[object]) -> str:
    payload = [["wrb.fr", rpc_id, json.dumps(inner), None, None, [], "generic"]]
    line = json.dumps(payload)
    return f")]}}'\n\n{len(line)}\n{line}\n"


def _af_init_data(key: str, payload: list[object]) -> str:
    return (
        "<script>AF_initDataCallback({key: "
        f"{key!r}, hash: '1', data:{json.dumps(payload)}, sideChannel: {{}}}});</script>"
    )


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


def _raw_hotel_without_token(
    name: str,
    entity_id: str,
    *,
    nightly: int = 65,
    total: int = 130,
    rating: float = 4.4,
    hotel_class: int = 3,
) -> list[object]:
    record = _raw_hotel()
    record[1] = name
    record[9] = entity_id
    record[18] = None
    record[22] = entity_id
    record[3] = [f"{hotel_class}-star hotel", hotel_class]
    record[6][2][1] = _price(f"${nightly}", nightly)
    record[6][2][8] = _price(f"${total}", total)
    record[7] = [[rating, 1000]]
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


def _broad_universal_payload() -> list[object]:
    root: list[object] = [None] * 8
    root[1] = "New York"
    root[5] = ["/m/02_286", None, None, None, None, "0x89c24fa5d33f083b:0xc80b8f06e177fe62"]
    root[7] = {
        "404340221": [_context()],
        "hotel_a": _raw_hotel_without_token(
            "HI New York City Hostel",
            "0x89c2f6246837073b:0xc9dcfc4023c86664",
        ),
        "hotel_b": _raw_hotel_without_token(
            "Second Test Hotel",
            "0x89c2f6246837073b:0x0000000000000002",
            nightly=120,
            total=240,
            rating=3.8,
            hotel_class=4,
        ),
    }
    return [None, root, None]


def _filtered_class_payload() -> list[object]:
    root: list[object] = [None] * 8
    root[1] = "New York"
    root[5] = ["/m/02_286", None, None, None, None, "0x89c24fa5d33f083b:0xc80b8f06e177fe62"]
    root[7] = {
        "404340221": [_context()],
        "hotel_b": _raw_hotel_without_token(
            "Second Test Hotel",
            "0x89c2f6246837073b:0x0000000000000002",
            nightly=120,
            total=240,
            rating=3.8,
            hotel_class=4,
        ),
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


def test_build_filtered_universal_search_payload_uses_captured_filter_slots():
    context = _extract_context_from_universal(_broad_universal_payload())

    payload = _build_filtered_universal_search_payload(
        "New York",
        context,
        check_in="2026-06-01",
        check_out="2026-06-03",
        adults=2,
        rooms=1,
        currency="USD",
        max_price=150,
        min_rating=4,
        min_hotel_class=4,
        property_types=["hostels", "motels"],
        has_pool=True,
        free_cancellation=True,
        special_offers=True,
        eco_certified=True,
    )

    assert payload is not None
    assert payload[1][2][0][1][0][0] == "/m/02_286"
    assert payload[1][2][1][1] == [[2026, 6, 1], [2026, 6, 3], 1]
    filter_block = payload[1][4]
    assert filter_block[0][0] == [6]
    assert filter_block[0][1] == [4, 5]
    assert filter_block[0][3] == 1
    assert filter_block[0][6] == "USD"
    assert filter_block[0][9] == 1
    assert filter_block[0][10] == [14, 16]
    assert filter_block[1] is None
    assert filter_block[2] == []
    assert filter_block[3] == [None, [None, 150], 1]
    assert filter_block[4] == 8
    assert filter_block[5] == 1
    assert payload[2] == [1, None, None, None, None, None, 13, None, 0]


def test_build_filtered_universal_search_payload_omits_price_block_when_unneeded():
    context = _extract_context_from_universal(_broad_universal_payload())

    payload = _build_filtered_universal_search_payload(
        "New York",
        context,
        check_in="2026-06-01",
        check_out="2026-06-03",
        currency="USD",
        property_types=["resorts"],
        free_cancellation=True,
        eco_certified=True,
    )

    assert payload is not None
    assert payload[1][4] == [
        [None, None, None, 1, None, None, "USD", None, None, 1, [17]],
        None,
        [],
    ]


def test_build_filtered_universal_search_payload_uses_captured_sort_slots():
    context = _extract_context_from_universal(_broad_universal_payload())

    price_payload = _build_filtered_universal_search_payload(
        "New York",
        context,
        check_in="2026-06-01",
        check_out="2026-06-03",
        currency="USD",
        sort_by="price",
    )
    rating_payload = _build_filtered_universal_search_payload(
        "New York",
        context,
        check_in="2026-06-01",
        check_out="2026-06-03",
        currency="USD",
        sort_by="rating",
    )

    assert price_payload is not None
    assert rating_payload is not None
    assert price_payload[1][4][0][4] == 3
    assert rating_payload[1][4][0][4] == 8


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


def test_parse_hotels_payload_attaches_selected_hotel_token_to_single_result():
    result = parse_hotels_payload(_universal_payload(), query="HI New York City Hostel", currency="USD")

    assert len(result.hotels) == 1
    assert result.hotels[0].booking_token == "ChgI5MyhnoKIv-7JARoLL2cvMXdrN3J0MmIQAQ"


def test_extract_af_init_data_extracts_server_rendered_payloads():
    html = _af_init_data("ds:0", _broad_universal_payload())

    blocks = _extract_af_init_data(html)

    assert blocks == [("ds:0", _broad_universal_payload())]


def test_parse_hotels_page_html_requires_matching_dates_and_currency():
    html = _af_init_data("ds:0", _broad_universal_payload())

    result = _parse_hotels_page_html(
        html,
        query="New York",
        check_in="2026-06-01",
        check_out="2026-06-03",
        currency="USD",
    )
    stale_result = _parse_hotels_page_html(
        html,
        query="New York",
        check_in="2026-06-02",
        check_out="2026-06-04",
        currency="USD",
    )

    assert result is not None
    assert result.is_complete is False
    assert [hotel.name for hotel in result.hotels] == [
        "HI New York City Hostel",
        "Second Test Hotel",
    ]
    assert stale_result is None


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


def test_merge_seed_hotel_preserves_detail_prices_and_fills_identity():
    seed = _raw_hotel()
    seed_hotel = parse_hotels_payload([None, seed], currency="USD").hotels[0]
    detail = Hotel(hotel_id="", name="", price=147, currency="USD")
    merged = _merge_seed_hotel(
        detail,
        seed_hotel,
        hotel_token="ChgI5MyhnoKIv-7JARoLL2cvMXdrN3J0MmIQAQ",
    )

    assert merged.hotel_id == "5960741009900747244"
    assert merged.name == "HI New York City Hostel"
    assert merged.price == 147
    assert merged.total_price == 130
    assert merged.rating == 4.4
    assert merged.booking_token == "ChgI5MyhnoKIv-7JARoLL2cvMXdrN3J0MmIQAQ"


def test_fetch_hotels_single_selected_hotel_returns_complete_without_results_rpc(monkeypatch):
    class Response:
        def __init__(self, text: str):
            self.status_code = 200
            self.text = text

    class Client:
        def __init__(self):
            self.posts = []

        def get(self, *args, **kwargs):
            return Response('"cfb2h":"bl-test","FdrFJe":"sid-test"')

        def post(self, url, *, content, headers, timeout):
            self.posts.append((url, content))
            return Response(_batchexecute(UNIVERSAL_SEARCH_RPC, _universal_payload()))

    client = Client()
    monkeypatch.setattr("swoop._hotels._get_client", lambda *args: client)

    result = fetch_hotels(
        "HI New York City Hostel",
        check_in="2026-06-01",
        check_out="2026-06-03",
    )

    assert result.is_complete is True
    assert len(result.hotels) == 1
    assert result.hotels[0].booking_token == "ChgI5MyhnoKIv-7JARoLL2cvMXdrN3J0MmIQAQ"
    assert len(client.posts) == 1


def test_fetch_hotels_can_enrich_broad_results_with_booking_tokens(monkeypatch):
    class Response:
        def __init__(self, text: str):
            self.status_code = 200
            self.text = text

    class Client:
        def __init__(self):
            self.queries = []

        def get(self, *args, **kwargs):
            return Response('"cfb2h":"bl-test","FdrFJe":"sid-test"')

        def post(self, url, *, content, headers, timeout):
            parsed = urllib.parse.parse_qs(content.decode())
            outer = json.loads(urllib.parse.unquote(parsed["f.req"][0]))
            rpc_id = outer[0][0][0]
            payload = json.loads(outer[0][0][1])
            if rpc_id == UNIVERSAL_SEARCH_RPC:
                self.queries.append(payload[0])
                if payload[0] == "HI New York City Hostel":
                    return Response(_batchexecute(UNIVERSAL_SEARCH_RPC, _universal_payload()))
                return Response(_batchexecute(UNIVERSAL_SEARCH_RPC, _broad_universal_payload()))
            return Response(_batchexecute(HOTEL_RESULTS_RPC, [None]))

    client = Client()
    monkeypatch.setattr("swoop._hotels._get_client", lambda *args: client)

    result = fetch_hotels(
        "New York",
        check_in="2026-06-01",
        check_out="2026-06-03",
        include_booking_tokens=True,
        token_enrichment_limit=1,
    )

    assert len(result.hotels) == 2
    assert result.hotels[0].booking_token == "ChgI5MyhnoKIv-7JARoLL2cvMXdrN3J0MmIQAQ"
    assert result.hotels[1].booking_token is None
    assert client.queries == ["New York", "HI New York City Hostel"]


def test_fetch_hotels_uses_captured_server_filter_payload(monkeypatch):
    class Response:
        def __init__(self, text: str):
            self.status_code = 200
            self.text = text

    class Client:
        def __init__(self):
            self.payloads = []

        def get(self, *args, **kwargs):
            return Response('"cfb2h":"bl-test","FdrFJe":"sid-test"')

        def post(self, url, *, content, headers, timeout):
            parsed = urllib.parse.parse_qs(content.decode())
            outer = json.loads(urllib.parse.unquote(parsed["f.req"][0]))
            rpc_id = outer[0][0][0]
            payload = json.loads(outer[0][0][1])
            self.payloads.append((rpc_id, payload))
            if (
                rpc_id == UNIVERSAL_SEARCH_RPC
                and len(payload) > 2
                and isinstance(payload[2], list)
                and payload[2]
                and payload[2][0] == 1
            ):
                return Response(_batchexecute(UNIVERSAL_SEARCH_RPC, _filtered_class_payload()))
            if rpc_id == UNIVERSAL_SEARCH_RPC:
                return Response(_batchexecute(UNIVERSAL_SEARCH_RPC, _broad_universal_payload()))
            return Response("not-json")

    client = Client()
    monkeypatch.setattr("swoop._hotels._get_client", lambda *args: client)

    result = fetch_hotels(
        "New York",
        check_in="2026-06-01",
        check_out="2026-06-03",
        min_hotel_class=4,
    )

    assert [hotel.name for hotel in result.hotels] == ["Second Test Hotel"]
    filtered_payload = client.payloads[1][1]
    assert filtered_payload[1][4][0][1] == [4, 5]
    assert filtered_payload[2][0] == 1


def test_fetch_hotels_returns_server_only_filtered_results_without_unfiltered_fallback(monkeypatch):
    class Response:
        def __init__(self, text: str):
            self.status_code = 200
            self.text = text

    class Client:
        def __init__(self):
            self.payloads = []

        def get(self, *args, **kwargs):
            return Response('"cfb2h":"bl-test","FdrFJe":"sid-test"')

        def post(self, url, *, content, headers, timeout):
            parsed = urllib.parse.parse_qs(content.decode())
            outer = json.loads(urllib.parse.unquote(parsed["f.req"][0]))
            rpc_id = outer[0][0][0]
            payload = json.loads(outer[0][0][1])
            self.payloads.append((rpc_id, payload))
            if (
                rpc_id == UNIVERSAL_SEARCH_RPC
                and len(payload) > 2
                and isinstance(payload[2], list)
                and payload[2]
                and payload[2][0] == 1
            ):
                return Response(_batchexecute(UNIVERSAL_SEARCH_RPC, _filtered_class_payload()))
            if rpc_id == UNIVERSAL_SEARCH_RPC:
                return Response(_batchexecute(UNIVERSAL_SEARCH_RPC, _broad_universal_payload()))
            raise AssertionError("server-only hotel filters must not request unfiltered results")

    client = Client()
    monkeypatch.setattr("swoop._hotels._get_client", lambda *args: client)

    result = fetch_hotels(
        "New York",
        check_in="2026-06-01",
        check_out="2026-06-03",
        property_types=["hostels"],
        has_pool=True,
    )

    assert [hotel.name for hotel in result.hotels] == ["Second Test Hotel"]
    assert [rpc_id for rpc_id, _ in client.payloads] == [
        UNIVERSAL_SEARCH_RPC,
        UNIVERSAL_SEARCH_RPC,
    ]
    filtered_payload = client.payloads[1][1]
    assert filtered_payload[1][4][0][0] == [6]
    assert filtered_payload[1][4][0][10] == [14]


def test_filter_and_sort_hotels_applies_client_side_controls():
    result = parse_hotels_payload(_broad_universal_payload(), query="New York", currency="USD")

    filtered = _filter_and_sort_hotels(
        result,
        sort_by="total-price",
        min_rating=3.5,
        min_hotel_class=3,
        max_total_price=200,
    )

    assert [hotel.name for hotel in filtered.hotels] == ["HI New York City Hostel"]


def test_filter_and_sort_hotels_can_require_booking_tokens_and_sort_rating():
    result = parse_hotels_payload(_broad_universal_payload(), query="New York", currency="USD")
    result.hotels[1].booking_token = "second-token"

    filtered = _filter_and_sort_hotels(
        result,
        sort_by="rating",
        require_booking_token=True,
    )

    assert [hotel.name for hotel in filtered.hotels] == ["Second Test Hotel"]


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

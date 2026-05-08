"""Google Travel Hotels endpoint client."""

from __future__ import annotations

import json
import logging
import random
import re
import time
import urllib.parse
from typing import Any, Optional

from .exceptions import SwoopHTTPError, SwoopParseError, SwoopRateLimitError
from .models import (
    Hotel,
    HotelProvider,
    HotelReview,
    HotelReviewsResult,
    HotelSearchResult,
    TransportConfig,
)
from .rpc import _apply_country, _get_client

logger = logging.getLogger(__name__)

HOTELS_PAGE_URL = "https://www.google.com/travel/search"
HOTELS_BATCH_URL = "https://www.google.com/_/TravelFrontendUi/data/batchexecute"

UNIVERSAL_SEARCH_RPC = "AtySUc"
HOTEL_RESULTS_RPC = "M0CRd"
HOTEL_REVIEWS_RPC = "ocp93e"

HOTEL_SORT_DEFAULT = "default"
HOTEL_SORT_PRICE = "price"
HOTEL_SORT_TOTAL_PRICE = "total-price"
HOTEL_SORT_RATING = "rating"
HOTEL_SORT_MOST_REVIEWED = "most-reviewed"
HOTEL_SORT_CLASS = "class"
HOTEL_SORT_NAME = "name"

HOTEL_SORT_VALUES = {
    HOTEL_SORT_DEFAULT,
    HOTEL_SORT_PRICE,
    HOTEL_SORT_TOTAL_PRICE,
    HOTEL_SORT_RATING,
    HOTEL_SORT_MOST_REVIEWED,
    HOTEL_SORT_CLASS,
    HOTEL_SORT_NAME,
}

HOTEL_FILTER_MIN_RATING_4 = 8
HOTEL_SORT_CODE_PRICE = 3
HOTEL_SORT_CODE_RATING = 8
HOTEL_SORT_CODE_MOST_REVIEWED = 13
HOTEL_AMENITY_POOL = 6
HOTEL_RATING_FILTER_CODES = (
    (4.5, 9),
    (4.0, 8),
    (3.5, 7),
)
HOTEL_AMENITY_CODES = {
    "free_parking": 1,
    "parking": 1,
    "indoor_pool": 4,
    "outdoor_pool": 5,
    "pool": HOTEL_AMENITY_POOL,
    "fitness_center": 7,
    "restaurant": 8,
    "free_breakfast": 9,
    "spa": 10,
    "beach_access": 11,
    "kid_friendly": 12,
    "bar": 15,
    "pet_friendly": 19,
    "room_service": 22,
    "free_wifi": 35,
    "air_conditioned": 40,
    "all_inclusive_available": 52,
    "wheelchair_accessible": 53,
    "ev_charger": 61,
}
HOTEL_AMENITY_VALUES = frozenset(HOTEL_AMENITY_CODES)
HOTEL_PROPERTY_TYPE_CODES = {
    "beach_hotels": 12,
    "boutique_hotels": 13,
    "hostels": 14,
    "inns": 15,
    "motels": 16,
    "resorts": 17,
    "spa_hotels": 18,
    "bed_and_breakfasts": 19,
    "other": 20,
    "apartment_hotels": 21,
}
HOTEL_PROPERTY_TYPE_VALUES = frozenset(HOTEL_PROPERTY_TYPE_CODES)
HOTEL_BRAND_FILTERS = {
    "accor_live_limitless": [[33, [8, 84]]],
    "best_western_international": [[18, [155, 104, 105, 254, 255, 107]]],
    "choice_hotels": [[20, [63, 112, 27, 113, 82, 78, 23]]],
    "four_seasons": [[289]],
    "hilton_honors": [[28, [7, 151, 81, 88, 115, 71, 95, 54, 36, 77, 295, 285, 286, 41]]],
    "hyatt": [[37, [116, 412, 288, 119, 120, 121, 122, 349, 118, 346, 262]]],
    "ihcl": [[202, [203]]],
    "ihg_hotels_resorts": [[17, [125, 52, 42, 282, 64, 56, 87, 2, 127, 298]]],
    "knights_inn": [[89]],
    "marriott_bonvoy": [[46, [128, 60, 59, 86, 153, 256, 134, 58, 135, 26, 72, 61, 129, 131, 75, 3, 12, 83, 136, 143, 40, 137, 39]]],
    "melia_hotels_international": [[174, [179]]],
    "nh_hotel_group": [[169, [171]]],
    "omni_hotels_resorts": [[311]],
    "oyo": [[353, [99]]],
    "radisson_hotel_group": [[80, [25]]],
    "red_roof_inn": [[345, [399]]],
    "riu_hotels_resorts": [[163, [164]]],
    "sonesta_hotels": [[424, [426, 428, 425]]],
    "starwood_hotels": [[493, [494]]],
    "westgate_resorts": [[312]],
    "wyndham_hotels_resorts": [[53, [30, 19, 11, 49, 50, 93, 284, 16, 65, 68, 150, 141]]],
}
HOTEL_BRAND_VALUES = frozenset(HOTEL_BRAND_FILTERS)


def _safe_get(data: Any, path: list[Any], default: Any = None) -> Any:
    current = data
    for key in path:
        try:
            if isinstance(key, int) and isinstance(current, list):
                current = current[key]
            elif isinstance(key, str) and isinstance(current, dict):
                current = current[key]
            else:
                return default
        except (IndexError, KeyError, TypeError):
            return default
    return current


def _parse_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _parse_float(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _date_triplet(value: str) -> list[int]:
    parts = value.split("-")
    if len(parts) != 3:
        raise ValueError("date must be YYYY-MM-DD")
    try:
        return [int(parts[0]), int(parts[1]), int(parts[2])]
    except ValueError as exc:
        raise ValueError("date must be YYYY-MM-DD") from exc


def _normalize_url(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value:
        return None
    if value.startswith("//"):
        return f"https:{value}"
    if value.startswith("/"):
        return f"https://www.google.com{value}"
    return value


def _price_value(block: Any) -> Optional[int]:
    if not isinstance(block, list):
        return None
    label = _safe_get(block, [0])
    if not isinstance(label, str):
        return None
    for idx in (4, 2, 1):
        value = _safe_get(block, [idx])
        parsed = _parse_int(value)
        if parsed is not None:
            return parsed
    match = re.search(r"[\d,]+", label)
    if match:
        return int(match.group(0).replace(",", ""))
    return None


def _first_image_url(value: Any) -> Optional[str]:
    if isinstance(value, str) and (
        value.startswith("http://")
        or value.startswith("https://")
        or value.startswith("//")
    ):
        return _normalize_url(value)
    if isinstance(value, list):
        for item in value:
            result = _first_image_url(item)
            if result:
                return result
    if isinstance(value, dict):
        for item in value.values():
            result = _first_image_url(item)
            if result:
                return result
    return None


def _encode_travel_f_req(
    rpc_id: str,
    payload: list[Any],
    *,
    request_id: str = "generic",
) -> str:
    payload_json = json.dumps(payload, separators=(",", ":"))
    wrapped = [[[rpc_id, payload_json, None, request_id]]]
    return urllib.parse.quote(json.dumps(wrapped, separators=(",", ":")))


def _iter_wrb_entries(value: Any) -> list[list[Any]]:
    entries: list[list[Any]] = []
    if isinstance(value, list):
        if value and value[0] == "wrb.fr":
            entries.append(value)
        else:
            for item in value:
                entries.extend(_iter_wrb_entries(item))
    return entries


def _parse_batchexecute_response(text: str, rpc_id: Optional[str] = None) -> list[Any]:
    """Parse a TravelFrontendUi batchexecute response inner payload."""
    stripped = text[4:] if text.startswith(")]}'") else text
    parse_errors: list[json.JSONDecodeError] = []

    for line in stripped.splitlines():
        line = line.strip()
        if not line or line.isdigit():
            continue
        try:
            chunk = json.loads(line)
        except json.JSONDecodeError as exc:
            parse_errors.append(exc)
            continue
        for entry in _iter_wrb_entries(chunk):
            if rpc_id is not None and _safe_get(entry, [1]) != rpc_id:
                continue
            inner = _safe_get(entry, [2])
            if not isinstance(inner, str):
                continue
            try:
                payload = json.loads(inner)
            except json.JSONDecodeError as exc:
                raise SwoopParseError(
                    f"Failed to parse inner {rpc_id or 'Travel'} response JSON: {exc}"
                ) from exc
            if not isinstance(payload, list):
                raise SwoopParseError("Travel inner payload is not a list")
            return payload

    if parse_errors:
        raise SwoopParseError(f"Failed to parse Travel response JSON: {parse_errors[-1]}")
    raise SwoopParseError("Travel response missing inner payload")


def _extract_browser_params(page_html: str) -> dict[str, str]:
    params: dict[str, str] = {}
    bl_match = re.search(r'"cfb2h":"([^"]+)"', page_html)
    if bl_match:
        params["bl"] = bl_match.group(1)
    fsid_match = re.search(r'"FdrFJe":"([^"]+)"', page_html)
    if fsid_match:
        params["f.sid"] = fsid_match.group(1)
    return params


def _extract_af_init_data(page_html: str) -> list[tuple[str, list[Any]]]:
    """Extract JSON payloads embedded in AF_initDataCallback blocks."""
    blocks: list[tuple[str, list[Any]]] = []
    pattern = re.compile(
        r"AF_initDataCallback\(\{key:\s*['\"]([^'\"]+)['\"].*?\bdata\s*:",
        re.DOTALL,
    )
    for match in pattern.finditer(page_html):
        index = match.end()
        while index < len(page_html) and page_html[index].isspace():
            index += 1
        if index >= len(page_html) or page_html[index] not in "[{":
            continue

        start = index
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(page_html)):
            char = page_html[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char in "[{":
                depth += 1
            elif char in "]}":
                depth -= 1
                if depth == 0:
                    try:
                        payload = json.loads(page_html[start:index + 1])
                    except json.JSONDecodeError:
                        logger.debug("Failed to parse AF_initData block %s", match.group(1), exc_info=True)
                        break
                    if isinstance(payload, list):
                        blocks.append((match.group(1), payload))
                    break
    return blocks


def _page_payload_matches_request(
    data: list[Any],
    *,
    check_in: str,
    check_out: str,
    currency: str,
) -> bool:
    context = _extract_context_from_universal(data)
    context_currency = context.get("currency")
    if isinstance(context_currency, str) and context_currency != currency:
        return False

    m0crd_context = context.get("m0crd_context")
    if not isinstance(m0crd_context, list):
        return False

    dates = _safe_get(m0crd_context, [4])
    return (
        _safe_get(dates, [0]) == _date_triplet(check_in)
        and _safe_get(dates, [1]) == _date_triplet(check_out)
    )


def _parse_hotels_page_html(
    page_html: str,
    *,
    query: str,
    check_in: str,
    check_out: str,
    currency: str,
) -> Optional[HotelSearchResult]:
    """Parse server-rendered Google Hotels cards when they match the request."""
    best: Optional[HotelSearchResult] = None
    for _, data in _extract_af_init_data(page_html):
        if not _page_payload_matches_request(
            data,
            check_in=check_in,
            check_out=check_out,
            currency=currency,
        ):
            continue
        result = parse_hotels_payload(
            data,
            query=query,
            currency=currency,
            is_complete=False,
        )
        if best is None or len(result.hotels) > len(best.hotels):
            best = result
    return best


def _prefer_page_result(
    primary: HotelSearchResult,
    page_result: Optional[HotelSearchResult],
) -> HotelSearchResult:
    if (
        page_result is not None
        and not primary.is_complete
        and len(page_result.hotels) > len(primary.hotels)
    ):
        return page_result
    return primary


def _travel_rpc_url(
    rpc_id: str,
    *,
    source_path: str = "/travel/search",
    browser_params: Optional[dict[str, str]] = None,
    transport: TransportConfig = TransportConfig(),
) -> str:
    params = {
        "rpcids": rpc_id,
        "source-path": source_path,
        "hl": "en-US",
        "rt": "c",
    }
    if browser_params:
        params.update(browser_params)
    url = f"{HOTELS_BATCH_URL}?{urllib.parse.urlencode(params)}"
    return _apply_country(url, transport.country)


def _post_travel_rpc(
    client: Any,
    rpc_id: str,
    payload: list[Any],
    *,
    page_url: str,
    browser_params: Optional[dict[str, str]] = None,
    transport: TransportConfig = TransportConfig(),
) -> list[Any]:
    url = _travel_rpc_url(rpc_id, browser_params=browser_params, transport=transport)
    body = f"f.req={_encode_travel_f_req(rpc_id, payload)}".encode()
    headers = {
        "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
        "x-same-domain": "1",
        "referer": page_url,
    }

    for attempt in range(1 + transport.retries):
        res = client.post(url, content=body, headers=headers, timeout=transport.timeout)
        if res.status_code == 200:
            return _parse_batchexecute_response(res.text, rpc_id=rpc_id)
        if res.status_code == 429 and attempt < transport.retries:
            delay = (2 ** attempt) + random.uniform(0, 1)
            logger.info(
                "HTTP 429 from Hotels RPC, retrying in %.1fs (attempt %d/%d)",
                delay,
                attempt + 1,
                transport.retries,
            )
            time.sleep(delay)
            continue
        if res.status_code == 429:
            raise SwoopRateLimitError()
        raise SwoopHTTPError(res.status_code)

    raise SwoopParseError("Hotels RPC did not return a response")


def _build_occupancy(adults: int, child_ages: Optional[list[int]], rooms: int) -> list[Any]:
    guests: list[Any] = [[3] for _ in range(adults)]
    for age in child_ages or []:
        guests.append([2, age])
    return [guests, rooms]


def _build_universal_search_payload(
    query: str,
    *,
    check_in: str,
    check_out: str,
    adults: int = 2,
    child_ages: Optional[list[int]] = None,
    rooms: int = 1,
    currency: str = "USD",
) -> list[Any]:
    """Build an AtySUc payload for hotel destination context and seeds."""
    return [
        query,
        [
            1,
            _build_occupancy(adults, child_ages, rooms),
            [
                [None, None, []],
                [
                    None,
                    [_date_triplet(check_in), _date_triplet(check_out), adults],
                    None,
                    None,
                    None,
                    [rooms],
                ],
            ],
            None,
            [[None, None, None, None, None, None, currency]],
        ],
        [],
        [],
    ]


def _build_filtered_universal_search_payload(
    query: str,
    context: dict[str, Any],
    *,
    check_in: str,
    check_out: str,
    adults: int = 2,
    child_ages: Optional[list[int]] = None,
    rooms: int = 1,
    currency: str = "USD",
    sort_by: str = HOTEL_SORT_DEFAULT,
    max_price: Optional[int] = None,
    min_rating: Optional[float] = None,
    min_hotel_class: Optional[int] = None,
    hotel_classes: Optional[list[int]] = None,
    amenities: Optional[list[str]] = None,
    property_types: Optional[list[str]] = None,
    brands: Optional[list[str]] = None,
    has_pool: bool = False,
    free_cancellation: bool = False,
    special_offers: bool = False,
    eco_certified: bool = False,
) -> Optional[list[Any]]:
    place_id = context.get("place_id")
    destination_name = context.get("destination_name")
    entity_id = context.get("entity_id")
    if not isinstance(place_id, str):
        return None

    def set_slot(items: list[Any], index: int, value: Any) -> None:
        while len(items) <= index:
            items.append(None)
        items[index] = value

    currency_filter: list[Any] = [None, None, None, None, None, None, currency]
    amenity_codes = [HOTEL_AMENITY_CODES[value] for value in amenities or []]
    if has_pool:
        amenity_codes.append(HOTEL_AMENITY_POOL)
    if amenity_codes:
        currency_filter[0] = list(dict.fromkeys(amenity_codes))
    if hotel_classes:
        currency_filter[1] = list(dict.fromkeys(hotel_classes))
    elif min_hotel_class is not None and min_hotel_class >= 4:
        currency_filter[1] = list(range(min_hotel_class, 6))
    if free_cancellation:
        currency_filter[3] = 1
    if sort_by in {HOTEL_SORT_PRICE, HOTEL_SORT_TOTAL_PRICE}:
        currency_filter[4] = HOTEL_SORT_CODE_PRICE
    elif sort_by == HOTEL_SORT_RATING:
        currency_filter[4] = HOTEL_SORT_CODE_RATING
    elif sort_by == HOTEL_SORT_MOST_REVIEWED:
        currency_filter[4] = HOTEL_SORT_CODE_MOST_REVIEWED
    if brands:
        brand_filters: list[Any] = []
        for brand in brands:
            brand_filters.extend(HOTEL_BRAND_FILTERS[brand])
        set_slot(currency_filter, 7, brand_filters)
    if eco_certified:
        set_slot(currency_filter, 9, 1)
    if property_types:
        set_slot(currency_filter, 10, [HOTEL_PROPERTY_TYPE_CODES[value] for value in property_types])

    price_filter: list[Any] = [None, None, 1]
    if max_price is not None:
        price_filter[1] = [None, max_price]

    filter_block: list[Any] = [currency_filter, None, []]
    if (
        sort_by in {
            HOTEL_SORT_PRICE,
            HOTEL_SORT_TOTAL_PRICE,
            HOTEL_SORT_RATING,
            HOTEL_SORT_MOST_REVIEWED,
        }
        or max_price is not None
        or has_pool
        or special_offers
    ):
        filter_block.append(price_filter)
    rating_filter_code = _hotel_rating_filter_code(min_rating)
    if rating_filter_code is not None:
        set_slot(filter_block, 4, rating_filter_code)
    if special_offers:
        set_slot(filter_block, 5, 1)

    destination = [place_id, None, None, None, None, entity_id, destination_name]
    return [
        query,
        [
            1,
            _build_occupancy(adults, child_ages, 0),
            [
                [None, [destination], []],
                [
                    None,
                    [_date_triplet(check_in), _date_triplet(check_out), rooms],
                    None,
                    None,
                    None,
                    [None, 0],
                ],
            ],
            None,
            filter_block,
        ],
        [1, None, None, None, None, None, 13, None, 0],
    ]


def _should_use_server_hotel_controls(
    *,
    sort_by: str = HOTEL_SORT_DEFAULT,
    max_price: Optional[int] = None,
    min_rating: Optional[float] = None,
    min_hotel_class: Optional[int] = None,
    hotel_classes: Optional[list[int]] = None,
    amenities: Optional[list[str]] = None,
    property_types: Optional[list[str]] = None,
    brands: Optional[list[str]] = None,
    has_pool: bool = False,
    free_cancellation: bool = False,
    special_offers: bool = False,
    eco_certified: bool = False,
) -> bool:
    return (
        sort_by in {HOTEL_SORT_PRICE, HOTEL_SORT_TOTAL_PRICE, HOTEL_SORT_RATING}
        or sort_by == HOTEL_SORT_MOST_REVIEWED
        or max_price is not None
        or _hotel_rating_filter_code(min_rating) is not None
        or (min_hotel_class is not None and min_hotel_class >= 4)
        or _has_server_only_hotel_controls(
            hotel_classes=hotel_classes,
            amenities=amenities,
            property_types=property_types,
            brands=brands,
            has_pool=has_pool,
            free_cancellation=free_cancellation,
            special_offers=special_offers,
            eco_certified=eco_certified,
        )
    )


def _has_server_only_hotel_controls(
    *,
    hotel_classes: Optional[list[int]] = None,
    amenities: Optional[list[str]] = None,
    property_types: Optional[list[str]] = None,
    brands: Optional[list[str]] = None,
    has_pool: bool = False,
    free_cancellation: bool = False,
    special_offers: bool = False,
    eco_certified: bool = False,
) -> bool:
    return bool(
        hotel_classes
        or amenities
        or property_types
        or brands
        or has_pool
        or free_cancellation
        or special_offers
        or eco_certified
    )


def _hotel_rating_filter_code(min_rating: Optional[float]) -> Optional[int]:
    if min_rating is None:
        return None
    for threshold, code in HOTEL_RATING_FILTER_CODES:
        if min_rating >= threshold:
            return code
    return None


def _extract_context_from_universal(data: list[Any]) -> dict[str, Any]:
    context: dict[str, Any] = {}
    root = _safe_get(data, [1])
    if not isinstance(root, list):
        return context

    name = _safe_get(root, [1])
    if isinstance(name, str):
        context["destination_name"] = name

    token = _safe_get(root, [2, 5])
    if isinstance(token, str):
        context["hotel_token"] = token

    place_id = _safe_get(root, [5, 0])
    entity_id = _safe_get(root, [5, 5])
    if isinstance(place_id, str):
        context["place_id"] = place_id
    if isinstance(entity_id, str):
        context["entity_id"] = entity_id

    context_block = _extract_m0crd_context(data)
    if context_block is not None:
        context["m0crd_context"] = context_block
        currency = _safe_get(context_block, [3])
        bounds = _safe_get(context_block, [16])
        dest = _safe_get(context_block, [18])
        if isinstance(currency, str):
            context["currency"] = currency
        if isinstance(bounds, list):
            context["bounds"] = bounds
        if isinstance(dest, list):
            if isinstance(_safe_get(dest, [0]), str):
                context["place_id"] = _safe_get(dest, [0])
            if isinstance(_safe_get(dest, [1]), str):
                context["destination_name"] = _safe_get(dest, [1])
            if isinstance(_safe_get(dest, [2]), str):
                context["entity_id"] = _safe_get(dest, [2])

    return context


def _extract_m0crd_context(value: Any) -> Optional[list[Any]]:
    if isinstance(value, dict):
        for item in value.values():
            result = _extract_m0crd_context(item)
            if result is not None:
                return result
    if isinstance(value, list):
        if (
            len(value) > 18
            and isinstance(_safe_get(value, [3]), str)
            and isinstance(_safe_get(value, [4]), list)
            and isinstance(_safe_get(value, [18]), list)
        ):
            return value
        for item in value:
            result = _extract_m0crd_context(item)
            if result is not None:
                return result
    return None


def _build_hotels_results_payload(
    context: dict[str, Any],
    *,
    check_in: str,
    check_out: str,
    adults: int = 2,
    child_ages: Optional[list[int]] = None,
    rooms: int = 1,
    currency: str = "USD",
) -> Optional[list[Any]]:
    base = context.get("m0crd_context")
    if isinstance(base, list):
        filters = list(base)
    else:
        bounds = context.get("bounds")
        place_id = context.get("place_id")
        entity_id = context.get("entity_id")
        destination_name = context.get("destination_name")
        if not isinstance(bounds, list) or not isinstance(place_id, str):
            return None
        filters: list[Any] = [None] * 19
        filters[0] = [300, 0]
        filters[3] = currency
        filters[16] = bounds
        filters[18] = [place_id, destination_name, entity_id]

    while len(filters) <= 18:
        filters.append(None)
    filters[3] = context.get("currency") if isinstance(context.get("currency"), str) else currency
    filters[4] = [_date_triplet(check_in), _date_triplet(check_out), adults, rooms]
    if child_ages:
        while len(filters) <= 13:
            filters.append(None)
        filters[13] = [3, [child_ages], rooms]
    return [None, filters, [1, None, 1, 1]]


def _build_hotel_detail_payload(
    context: dict[str, Any],
    hotel_token: str,
    *,
    check_in: str,
    check_out: str,
    adults: int = 2,
    rooms: int = 1,
    currency: str = "USD",
    hotel_entity_id: Optional[str] = None,
) -> Optional[list[Any]]:
    base = context.get("m0crd_context")
    if isinstance(base, list):
        filters = list(base)
    else:
        place_id = context.get("place_id")
        destination_name = context.get("destination_name")
        entity_id = context.get("entity_id")
        if not isinstance(place_id, str):
            return None
        filters: list[Any] = [None] * 19
        filters[3] = currency
        filters[18] = [place_id, destination_name, entity_id]

    while len(filters) <= 18:
        filters.append(None)
    filters[3] = context.get("currency") if isinstance(context.get("currency"), str) else currency
    filters[4] = [_date_triplet(check_in), _date_triplet(check_out), adults, rooms]
    filters[16] = None

    hotel_filter: list[Any] = [None] * 14
    hotel_filter[6] = 1
    hotel_filter[8] = 2
    if hotel_entity_id:
        hotel_filter[13] = [hotel_entity_id]
    return [None, filters, [1, None, 1], hotel_token, hotel_filter, 1, 2]


def _looks_like_hotel_record(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) > 10
        and isinstance(_safe_get(value, [1]), str)
        and (
            isinstance(_safe_get(value, [2, 0]), list)
            or isinstance(_safe_get(value, [9]), str)
            or isinstance(_safe_get(value, [18]), str)
        )
    )


def _extract_hotel_records(value: Any) -> list[list[Any]]:
    records: list[list[Any]] = []
    if _looks_like_hotel_record(value):
        return [value]
    if isinstance(value, dict):
        for item in value.values():
            records.extend(_extract_hotel_records(item))
    elif isinstance(value, list):
        for item in value:
            records.extend(_extract_hotel_records(item))
    return records


def _hotel_id(raw: list[Any]) -> str:
    for path in ([22], [18], [9], [23, 4], [1]):
        value = _safe_get(raw, path)
        if isinstance(value, (str, int)):
            return str(value)
    return ""


def _parse_hotel_record(raw: list[Any], *, currency: Optional[str] = None) -> Optional[Hotel]:
    name = _safe_get(raw, [1])
    if not isinstance(name, str):
        return None

    coords = _safe_get(raw, [2, 0])
    latitude = _parse_float(_safe_get(coords, [0]))
    longitude = _parse_float(_safe_get(coords, [1]))
    price_block = _safe_get(raw, [6, 2, 1])
    total_price_block = None
    for path in ([6, 2, 9], [6, 2, 17], [6, 2, 8]):
        candidate = _safe_get(raw, path)
        if _price_value(candidate) is not None:
            total_price_block = candidate
            break
    rating = _parse_float(_safe_get(raw, [7, 0, 0]))
    review_count = _parse_int(_safe_get(raw, [7, 0, 1]))

    hotel_id = _hotel_id(raw)
    if not hotel_id:
        hotel_id = name

    return Hotel(
        hotel_id=hotel_id,
        name=name,
        price=_price_value(price_block),
        total_price=_price_value(total_price_block),
        currency=currency,
        rating=rating,
        review_count=review_count,
        hotel_class=_parse_int(_safe_get(raw, [3, 1])),
        hotel_class_label=_safe_get(raw, [3, 0]) if isinstance(_safe_get(raw, [3, 0]), str) else None,
        latitude=latitude,
        longitude=longitude,
        address=_safe_get(raw, [2, 1, 0, 0, 0]) if isinstance(_safe_get(raw, [2, 1, 0, 0, 0]), str) else None,
        image_url=_first_image_url(_safe_get(raw, [13])) or _first_image_url(_safe_get(raw, [5])),
        distance=_safe_get(raw, [2, 16]) if isinstance(_safe_get(raw, [2, 16]), str) else None,
        booking_token=_safe_get(raw, [18]) if isinstance(_safe_get(raw, [18]), str) else None,
        price_token=_safe_get(raw, [6, 3]) if isinstance(_safe_get(raw, [6, 3]), str) else None,
        place_id=_safe_get(raw, [23, 4]) if isinstance(_safe_get(raw, [23, 4]), str) else None,
        entity_id=_safe_get(raw, [9]) if isinstance(_safe_get(raw, [9]), str) else None,
    )


def parse_hotels_payload(
    data: list[Any],
    *,
    query: str = "",
    currency: Optional[str] = None,
    destination_name: Optional[str] = None,
    is_complete: bool = True,
) -> HotelSearchResult:
    """Parse an AtySUc or M0CRd payload into hotel results."""
    context = _extract_context_from_universal(data)
    if currency is None:
        currency = context.get("currency") if isinstance(context.get("currency"), str) else None
    if destination_name is None:
        destination_name = (
            context.get("destination_name")
            if isinstance(context.get("destination_name"), str)
            else None
        )

    hotels: list[Hotel] = []
    seen: set[str] = set()
    for raw in _extract_hotel_records(data):
        hotel = _parse_hotel_record(raw, currency=currency)
        if hotel is None:
            continue
        key = hotel.hotel_id or hotel.name
        if key in seen:
            continue
        seen.add(key)
        hotels.append(hotel)

    hotel_token = context.get("hotel_token")
    if isinstance(hotel_token, str):
        for hotel in hotels:
            if hotel.booking_token is not None:
                continue
            if len(hotels) == 1 or hotel.name == destination_name:
                hotel.booking_token = hotel_token

    return HotelSearchResult(
        hotels=hotels,
        query=query,
        destination_name=destination_name,
        currency=currency,
        is_complete=is_complete,
    )


def _hotel_price_for_filter(hotel: Hotel, *, use_total: bool = False) -> Optional[int]:
    if use_total:
        return hotel.total_price if hotel.total_price is not None else hotel.price
    return hotel.price if hotel.price is not None else hotel.total_price


def _hotel_sort_key(hotel: Hotel, sort_by: str) -> tuple[Any, ...]:
    if sort_by == HOTEL_SORT_PRICE:
        value = _hotel_price_for_filter(hotel)
        return (value is None, value if value is not None else 0, hotel.name.casefold())
    if sort_by == HOTEL_SORT_TOTAL_PRICE:
        value = _hotel_price_for_filter(hotel, use_total=True)
        return (value is None, value if value is not None else 0, hotel.name.casefold())
    if sort_by == HOTEL_SORT_RATING:
        value = hotel.rating
        return (value is None, -(value if value is not None else 0.0), hotel.name.casefold())
    if sort_by == HOTEL_SORT_MOST_REVIEWED:
        value = hotel.review_count
        return (value is None, -(value if value is not None else 0), hotel.name.casefold())
    if sort_by == HOTEL_SORT_CLASS:
        value = hotel.hotel_class
        return (value is None, -(value if value is not None else 0), hotel.name.casefold())
    if sort_by == HOTEL_SORT_NAME:
        return (hotel.name.casefold(),)
    return ()


def _filter_and_sort_hotels(
    result: HotelSearchResult,
    *,
    sort_by: str = HOTEL_SORT_DEFAULT,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    min_total_price: Optional[int] = None,
    max_total_price: Optional[int] = None,
    min_rating: Optional[float] = None,
    min_hotel_class: Optional[int] = None,
    require_booking_token: bool = False,
) -> HotelSearchResult:
    hotels = list(result.hotels)

    if min_price is not None:
        hotels = [
            hotel for hotel in hotels
            if (price := _hotel_price_for_filter(hotel)) is not None and price >= min_price
        ]
    if max_price is not None:
        hotels = [
            hotel for hotel in hotels
            if (price := _hotel_price_for_filter(hotel)) is not None and price <= max_price
        ]
    if min_total_price is not None:
        hotels = [
            hotel for hotel in hotels
            if (price := _hotel_price_for_filter(hotel, use_total=True)) is not None
            and price >= min_total_price
        ]
    if max_total_price is not None:
        hotels = [
            hotel for hotel in hotels
            if (price := _hotel_price_for_filter(hotel, use_total=True)) is not None
            and price <= max_total_price
        ]
    if min_rating is not None:
        hotels = [hotel for hotel in hotels if hotel.rating is not None and hotel.rating >= min_rating]
    if min_hotel_class is not None:
        hotels = [
            hotel for hotel in hotels
            if hotel.hotel_class is not None and hotel.hotel_class >= min_hotel_class
        ]
    if require_booking_token:
        hotels = [hotel for hotel in hotels if hotel.booking_token]

    if sort_by != HOTEL_SORT_DEFAULT:
        hotels.sort(key=lambda hotel: _hotel_sort_key(hotel, sort_by))

    result.hotels = hotels
    return result


def _provider_price_container(row: list[Any]) -> Any:
    for idx in (12, 13):
        value = _safe_get(row, [idx])
        if isinstance(value, list) and (
            _price_value(_safe_get(value, [4])) is not None
            or _price_value(_safe_get(value, [5])) is not None
        ):
            return value
    return None


def _parse_provider_row(row: list[Any], *, currency: Optional[str] = None) -> Optional[HotelProvider]:
    info = _safe_get(row, [0])
    name = _safe_get(info, [0])
    if not isinstance(name, str):
        return None

    room = _safe_get(row, [7, 0])
    price_container = _provider_price_container(row)
    nightly = _safe_get(price_container, [4])
    total = _safe_get(price_container, [5])
    if isinstance(room, list):
        room_nightly = _safe_get(room, [2, 0, 4])
        room_total = _safe_get(room, [2, 0, 5])
        nightly = room_nightly if _price_value(room_nightly) is not None else nightly
        total = room_total if _price_value(room_total) is not None else total

    provider_id = _safe_get(info, [1])
    room_name = _safe_get(room, [0])
    free_cancellation = _safe_get(room, [2, 0, 8])
    return HotelProvider(
        name=name,
        price=_price_value(nightly),
        total_price=_price_value(total),
        currency=currency,
        url=_normalize_url(_safe_get(info, [2])),
        logo_url=_first_image_url(_safe_get(info, [3])),
        provider_id=str(provider_id) if provider_id is not None else None,
        room_name=room_name if isinstance(room_name, str) else None,
        is_free_cancellation=free_cancellation if isinstance(free_cancellation, bool) else None,
    )


def parse_hotel_prices_payload(data: list[Any], *, currency: Optional[str] = None) -> Hotel:
    """Parse a hotel detail/pricing M0CRd payload into one Hotel with providers."""
    currency = currency or (_safe_get(data, [1, 3]) if isinstance(_safe_get(data, [1, 3]), str) else None)
    raw_hotel = _safe_get(data, [2])
    hotel = _parse_hotel_record(raw_hotel, currency=currency) if isinstance(raw_hotel, list) else None
    if hotel is None:
        hotel = Hotel(
            hotel_id="",
            name="",
            price=_price_value(_safe_get(data, [2, 1])),
            currency=currency,
        )

    providers: list[HotelProvider] = []
    raw_providers = _safe_get(data, [2, 2])
    if isinstance(raw_providers, list):
        for row in raw_providers:
            if not isinstance(row, list):
                continue
            provider = _parse_provider_row(row, currency=currency)
            if provider is not None:
                providers.append(provider)
    hotel.providers = providers
    return hotel


def _merge_seed_hotel(
    hotel: Hotel,
    seed_hotel: Optional[Hotel],
    *,
    hotel_token: str,
) -> Hotel:
    if not hotel.hotel_id:
        hotel.hotel_id = seed_hotel.hotel_id if seed_hotel is not None else hotel_token
    if seed_hotel is not None:
        for field_name in (
            "name",
            "total_price",
            "rating",
            "review_count",
            "hotel_class",
            "hotel_class_label",
            "latitude",
            "longitude",
            "address",
            "image_url",
            "distance",
            "place_id",
            "entity_id",
        ):
            if getattr(hotel, field_name) is None or getattr(hotel, field_name) == "":
                setattr(hotel, field_name, getattr(seed_hotel, field_name))
    hotel.booking_token = hotel_token
    return hotel


def _same_hotel(left: Hotel, right: Hotel) -> bool:
    for field_name in ("entity_id", "place_id", "hotel_id"):
        left_value = getattr(left, field_name)
        right_value = getattr(right, field_name)
        if left_value and right_value and left_value == right_value:
            return True
    return bool(left.name and right.name and left.name.casefold() == right.name.casefold())


def _enrich_booking_tokens(
    client: Any,
    result: HotelSearchResult,
    *,
    check_in: str,
    check_out: str,
    adults: int = 2,
    child_ages: Optional[list[int]] = None,
    rooms: int = 1,
    currency: str = "USD",
    page_url: str,
    browser_params: Optional[dict[str, str]] = None,
    transport: TransportConfig = TransportConfig(),
    limit: Optional[int] = None,
) -> HotelSearchResult:
    remaining = len(result.hotels) if limit is None else max(0, limit)
    if remaining == 0:
        return result

    for hotel in result.hotels:
        if hotel.booking_token:
            continue
        if not hotel.name:
            continue
        if remaining <= 0:
            break

        remaining -= 1
        try:
            exact_data = _post_travel_rpc(
                client,
                UNIVERSAL_SEARCH_RPC,
                _build_universal_search_payload(
                    hotel.name,
                    check_in=check_in,
                    check_out=check_out,
                    adults=adults,
                    child_ages=child_ages,
                    rooms=rooms,
                    currency=currency,
                ),
                page_url=page_url,
                browser_params=browser_params,
                transport=transport,
            )
        except (SwoopHTTPError, SwoopParseError, SwoopRateLimitError):
            logger.debug("Hotel token enrichment failed for %s", hotel.name, exc_info=True)
            continue

        context = _extract_context_from_universal(exact_data)
        hotel_token = context.get("hotel_token")
        if not isinstance(hotel_token, str):
            continue

        exact_result = parse_hotels_payload(
            exact_data,
            query=hotel.name,
            currency=currency,
            is_complete=False,
        )
        if not exact_result.hotels:
            hotel.booking_token = hotel_token
            continue

        for exact_hotel in exact_result.hotels:
            if _same_hotel(hotel, exact_hotel):
                _merge_seed_hotel(hotel, exact_hotel, hotel_token=hotel_token)
                break
        else:
            if len(exact_result.hotels) == 1:
                _merge_seed_hotel(hotel, exact_result.hotels[0], hotel_token=hotel_token)

    return result


def _review_text(value: Any) -> Optional[str]:
    parts: list[str] = []

    def visit(node: Any) -> None:
        if isinstance(node, list):
            if len(node) >= 2 and node[0] == 1 and isinstance(node[1], str):
                parts.append(node[1])
                return
            for item in node:
                visit(item)

    visit(value)
    text = "".join(parts).strip()
    return text or None


def _is_review_row(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) >= 4
        and isinstance(_safe_get(value, [0]), list)
        and isinstance(_safe_get(value, [1]), str)
        and isinstance(_safe_get(value, [2]), list)
    )


def parse_hotel_reviews_payload(
    data: list[Any],
    *,
    hotel_token: Optional[str] = None,
) -> HotelReviewsResult:
    """Parse an ocp93e hotel reviews payload."""
    groups = _safe_get(data, [0, 0])
    reviews: list[HotelReview] = []
    if isinstance(groups, list):
        for group in groups:
            if not isinstance(group, list):
                continue
            source = _safe_get(group, [0, 0])
            candidate_reviews = _safe_get(group, [1])
            if not isinstance(source, str) or not isinstance(candidate_reviews, list):
                continue
            if _is_review_row(candidate_reviews):
                raw_reviews = [candidate_reviews]
            else:
                raw_reviews = [
                    item for item in candidate_reviews
                    if _is_review_row(item)
                ]
            for raw in raw_reviews:
                rating = _safe_get(raw, [2])
                author = _safe_get(raw, [0, 0])
                url = _safe_get(raw, [0, 1])
                reviews.append(
                    HotelReview(
                        source=source,
                        author=author if isinstance(author, str) else None,
                        rating=_parse_float(_safe_get(rating, [0])),
                        max_rating=_parse_float(_safe_get(rating, [1])),
                        relative_date=_safe_get(raw, [1]) if isinstance(_safe_get(raw, [1]), str) else None,
                        text=_review_text(_safe_get(raw, [3])),
                        url=url if isinstance(url, str) else None,
                    )
                )
    return HotelReviewsResult(reviews=reviews, hotel_token=hotel_token)


def _build_reviews_payload(hotel_token: str) -> list[Any]:
    return [None, None, None, None, None, None, None, None, hotel_token, None, None, [[]]]


def fetch_hotels(
    query: str,
    *,
    check_in: str,
    check_out: str,
    adults: int = 2,
    child_ages: Optional[list[int]] = None,
    rooms: int = 1,
    currency: str = "USD",
    sort_by: str = HOTEL_SORT_DEFAULT,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    min_total_price: Optional[int] = None,
    max_total_price: Optional[int] = None,
    min_rating: Optional[float] = None,
    min_hotel_class: Optional[int] = None,
    hotel_classes: Optional[list[int]] = None,
    amenities: Optional[list[str]] = None,
    property_types: Optional[list[str]] = None,
    brands: Optional[list[str]] = None,
    has_pool: bool = False,
    free_cancellation: bool = False,
    special_offers: bool = False,
    eco_certified: bool = False,
    require_booking_token: bool = False,
    include_booking_tokens: bool = False,
    token_enrichment_limit: Optional[int] = None,
    transport: TransportConfig = TransportConfig(),
) -> HotelSearchResult:
    """Fetch Google Travel hotel search results."""
    client = _get_client(transport.proxy, transport.impersonate)
    page_params = {
        "q": query,
        "check_in_date": check_in,
        "check_out_date": check_out,
        "adults": adults,
        "currency": currency,
    }
    page_url = _apply_country(
        f"{HOTELS_PAGE_URL}?{urllib.parse.urlencode(page_params)}",
        transport.country,
    )
    page_res = client.get(
        page_url,
        headers={"accept": "text/html", "accept-language": "en-US,en;q=0.9"},
        timeout=transport.timeout,
    )
    browser_params = _extract_browser_params(page_res.text)
    page_result = _parse_hotels_page_html(
        page_res.text,
        query=query,
        check_in=check_in,
        check_out=check_out,
        currency=currency,
    )

    universal_payload = _build_universal_search_payload(
        query,
        check_in=check_in,
        check_out=check_out,
        adults=adults,
        child_ages=child_ages,
        rooms=rooms,
        currency=currency,
    )
    universal_data = _post_travel_rpc(
        client,
        UNIVERSAL_SEARCH_RPC,
        universal_payload,
        page_url=page_url,
        browser_params=browser_params,
        transport=transport,
    )
    context = _extract_context_from_universal(universal_data)
    seed_result = parse_hotels_payload(
        universal_data,
        query=query,
        currency=currency,
        destination_name=context.get("destination_name") if isinstance(context.get("destination_name"), str) else None,
        is_complete=False,
    )
    server_only_controls = _has_server_only_hotel_controls(
        hotel_classes=hotel_classes,
        amenities=amenities,
        property_types=property_types,
        brands=brands,
        has_pool=has_pool,
        free_cancellation=free_cancellation,
        special_offers=special_offers,
        eco_certified=eco_certified,
    )
    if context.get("hotel_token") and len(seed_result.hotels) == 1 and not server_only_controls:
        seed_result.is_complete = True
        return _filter_and_sort_hotels(
            seed_result,
            sort_by=sort_by,
            min_price=min_price,
            max_price=max_price,
            min_total_price=min_total_price,
            max_total_price=max_total_price,
            min_rating=min_rating,
            min_hotel_class=min_hotel_class,
            require_booking_token=require_booking_token,
        )

    if _should_use_server_hotel_controls(
        sort_by=sort_by,
        max_price=max_price,
        min_rating=min_rating,
        min_hotel_class=min_hotel_class,
        hotel_classes=hotel_classes,
        amenities=amenities,
        property_types=property_types,
        brands=brands,
        has_pool=has_pool,
        free_cancellation=free_cancellation,
        special_offers=special_offers,
        eco_certified=eco_certified,
    ):
        filtered_payload = _build_filtered_universal_search_payload(
            query,
            context,
            check_in=check_in,
            check_out=check_out,
            adults=adults,
            child_ages=child_ages,
            rooms=rooms,
            currency=currency,
            sort_by=sort_by,
            max_price=max_price,
            min_rating=min_rating,
            min_hotel_class=min_hotel_class,
            hotel_classes=hotel_classes,
            amenities=amenities,
            property_types=property_types,
            brands=brands,
            has_pool=has_pool,
            free_cancellation=free_cancellation,
            special_offers=special_offers,
            eco_certified=eco_certified,
        )
        filtered_result: Optional[HotelSearchResult] = None
        if filtered_payload is not None:
            try:
                filtered_data = _post_travel_rpc(
                    client,
                    UNIVERSAL_SEARCH_RPC,
                    filtered_payload,
                    page_url=page_url,
                    browser_params=browser_params,
                    transport=transport,
                )
            except (SwoopHTTPError, SwoopParseError, SwoopRateLimitError):
                logger.debug("Hotel filtered search RPC failed", exc_info=True)
            else:
                filtered_result = parse_hotels_payload(
                    filtered_data,
                    query=query,
                    currency=currency,
                    destination_name=context.get("destination_name")
                    if isinstance(context.get("destination_name"), str)
                    else None,
                    is_complete=False,
                )
                if filtered_result.hotels:
                    seed_result = filtered_result
        if server_only_controls:
            if filtered_result is None or not filtered_result.hotels:
                seed_result = HotelSearchResult(
                    hotels=[],
                    query=query,
                    destination_name=context.get("destination_name")
                    if isinstance(context.get("destination_name"), str)
                    else None,
                    currency=currency,
                    is_complete=False,
                )
            if include_booking_tokens:
                seed_result = _enrich_booking_tokens(
                    client,
                    seed_result,
                    check_in=check_in,
                    check_out=check_out,
                    adults=adults,
                    child_ages=child_ages,
                    rooms=rooms,
                    currency=currency,
                    page_url=page_url,
                    browser_params=browser_params,
                    transport=transport,
                    limit=token_enrichment_limit,
                )
            return _filter_and_sort_hotels(
                seed_result,
                sort_by=sort_by,
                min_price=min_price,
                max_price=max_price,
                min_total_price=min_total_price,
                max_total_price=max_total_price,
                min_rating=min_rating,
                min_hotel_class=min_hotel_class,
                require_booking_token=require_booking_token,
            )

    results_payload = _build_hotels_results_payload(
        context,
        check_in=check_in,
        check_out=check_out,
        adults=adults,
        child_ages=child_ages,
        rooms=rooms,
        currency=currency,
    )
    if results_payload is None:
        seed_result = _prefer_page_result(seed_result, page_result)
        return _filter_and_sort_hotels(
            seed_result,
            sort_by=sort_by,
            min_price=min_price,
            max_price=max_price,
            min_total_price=min_total_price,
            max_total_price=max_total_price,
            min_rating=min_rating,
            min_hotel_class=min_hotel_class,
            require_booking_token=require_booking_token,
        )

    try:
        results_data = _post_travel_rpc(
            client,
            HOTEL_RESULTS_RPC,
            results_payload,
            page_url=page_url,
            browser_params=browser_params,
            transport=transport,
        )
    except SwoopParseError:
        logger.debug("Hotel results RPC did not return parseable results", exc_info=True)
        seed_result = _prefer_page_result(seed_result, page_result)
        if include_booking_tokens:
            seed_result = _enrich_booking_tokens(
                client,
                seed_result,
                check_in=check_in,
                check_out=check_out,
                adults=adults,
                child_ages=child_ages,
                rooms=rooms,
                currency=currency,
                page_url=page_url,
                browser_params=browser_params,
                transport=transport,
                limit=token_enrichment_limit,
            )
        return _filter_and_sort_hotels(
            seed_result,
            sort_by=sort_by,
            min_price=min_price,
            max_price=max_price,
            min_total_price=min_total_price,
            max_total_price=max_total_price,
            min_rating=min_rating,
            min_hotel_class=min_hotel_class,
            require_booking_token=require_booking_token,
        )

    result = parse_hotels_payload(
        results_data,
        query=query,
        currency=currency,
        destination_name=context.get("destination_name") if isinstance(context.get("destination_name"), str) else None,
        is_complete=True,
    )
    final_result = result if result.hotels else seed_result
    final_result = _prefer_page_result(final_result, page_result)
    if include_booking_tokens:
        final_result = _enrich_booking_tokens(
            client,
            final_result,
            check_in=check_in,
            check_out=check_out,
            adults=adults,
            child_ages=child_ages,
            rooms=rooms,
            currency=currency,
            page_url=page_url,
            browser_params=browser_params,
            transport=transport,
            limit=token_enrichment_limit,
        )
    return _filter_and_sort_hotels(
        final_result,
        sort_by=sort_by,
        min_price=min_price,
        max_price=max_price,
        min_total_price=min_total_price,
        max_total_price=max_total_price,
        min_rating=min_rating,
        min_hotel_class=min_hotel_class,
        require_booking_token=require_booking_token,
    )


def fetch_hotel_prices(
    hotel_token: str,
    *,
    query: str = "",
    check_in: str,
    check_out: str,
    adults: int = 2,
    child_ages: Optional[list[int]] = None,
    rooms: int = 1,
    currency: str = "USD",
    transport: TransportConfig = TransportConfig(),
) -> Hotel:
    """Fetch provider prices for a hotel token from Google Travel."""
    client = _get_client(transport.proxy, transport.impersonate)
    page_url = _apply_country(
        f"{HOTELS_PAGE_URL}?{urllib.parse.urlencode({'q': query or hotel_token})}",
        transport.country,
    )
    page_res = client.get(
        page_url,
        headers={"accept": "text/html", "accept-language": "en-US,en;q=0.9"},
        timeout=transport.timeout,
    )
    browser_params = _extract_browser_params(page_res.text)
    universal_data = _post_travel_rpc(
        client,
        UNIVERSAL_SEARCH_RPC,
        _build_universal_search_payload(
            query or hotel_token,
            check_in=check_in,
            check_out=check_out,
            adults=adults,
            child_ages=child_ages,
            rooms=rooms,
            currency=currency,
        ),
        page_url=page_url,
        browser_params=browser_params,
        transport=transport,
    )
    context = _extract_context_from_universal(universal_data)
    seed_result = parse_hotels_payload(universal_data, query=query, currency=currency, is_complete=False)

    seed_hotel = None
    hotel_entity_id = None
    for hotel in seed_result.hotels:
        if hotel.booking_token == hotel_token or hotel.hotel_id == hotel_token:
            seed_hotel = hotel
            hotel_entity_id = hotel.entity_id
            break

    detail_payload = _build_hotel_detail_payload(
        context,
        hotel_token,
        check_in=check_in,
        check_out=check_out,
        adults=adults,
        rooms=rooms,
        currency=currency,
        hotel_entity_id=hotel_entity_id,
    )
    if detail_payload is None:
        return Hotel(hotel_id=hotel_token, name="", currency=currency)

    data = _post_travel_rpc(
        client,
        HOTEL_RESULTS_RPC,
        detail_payload,
        page_url=page_url,
        browser_params=browser_params,
        transport=transport,
    )
    hotel = parse_hotel_prices_payload(data, currency=currency)
    return _merge_seed_hotel(hotel, seed_hotel, hotel_token=hotel_token)


def fetch_hotel_reviews(
    hotel_token: str,
    *,
    transport: TransportConfig = TransportConfig(),
) -> HotelReviewsResult:
    """Fetch reviews for a Google Travel hotel token."""
    client = _get_client(transport.proxy, transport.impersonate)
    page_url = _apply_country(HOTELS_PAGE_URL, transport.country)
    page_res = client.get(
        page_url,
        headers={"accept": "text/html", "accept-language": "en-US,en;q=0.9"},
        timeout=transport.timeout,
    )
    data = _post_travel_rpc(
        client,
        HOTEL_REVIEWS_RPC,
        _build_reviews_payload(hotel_token),
        page_url=page_url,
        browser_params=_extract_browser_params(page_res.text),
        transport=transport,
    )
    return parse_hotel_reviews_payload(data, hotel_token=hotel_token)

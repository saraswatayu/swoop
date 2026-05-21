"""Booking option parsing for Google Flights GetBookingResults responses.

Extracts fare options (brand, price, fare family) from the nested protobuf/JSON
response structure returned by GetBookingResults. Separated from rpc.py for
cohesion — this module handles response parsing, not request building.
"""

import base64
import json
import logging
import urllib.parse
from typing import Any

from .builders import CABIN_CLASS_MAP, ItinerarySummary
from .decoder import BookingOption, _safe_get

logger = logging.getLogger(__name__)


def _looks_like_price_block(value: Any) -> bool:
    """True when value looks like booking option price block."""
    if not (isinstance(value, list) and len(value) >= 2):
        return False
    summary = value[0]
    token = value[1]
    if not (isinstance(summary, list) and len(summary) >= 2):
        return False
    return isinstance(summary[1], (int, float)) and isinstance(token, str)


def _extract_price_block(option: list[Any]) -> list[Any] | None:
    """Extract price block from option list."""
    direct = _safe_get(option, [7], None)
    if _looks_like_price_block(direct):
        return direct

    for value in option:
        if _looks_like_price_block(value):
            return value
    return None


def _looks_like_brand_block(value: Any) -> bool:
    """True when value looks like booking option brand block."""
    if not (isinstance(value, list) and len(value) >= 4):
        return False

    brand_tuple = _safe_get(value, [0], None)
    if not (isinstance(brand_tuple, list) and len(brand_tuple) >= 2 and isinstance(brand_tuple[1], str)):
        return False

    label = _safe_get(value, [3], None)
    return isinstance(label, str)


def _extract_brand_block(option: list[Any]) -> list[Any] | None:
    """Extract brand block from option list."""
    direct = _safe_get(option, [21], None)
    if _looks_like_brand_block(direct):
        return direct

    for value in option:
        if _looks_like_brand_block(value):
            return value
    return None


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    """Read protobuf varint from byte buffer."""
    result = 0
    shift = 0
    while pos < len(data):
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7
        if shift > 70:
            raise ValueError("protobuf varint too long")
    raise ValueError("truncated protobuf varint")


def _skip_wire_value(data: bytes, pos: int, wire_type: int) -> int:
    """Skip one protobuf value for the given wire type."""
    if wire_type == 0:
        _, pos = _read_varint(data, pos)
        return pos
    if wire_type == 1:
        return pos + 8
    if wire_type == 2:
        length, pos = _read_varint(data, pos)
        return pos + length
    if wire_type == 5:
        return pos + 4
    raise ValueError(f"unsupported wire type: {wire_type}")


def _extract_option_index_and_token_price_raw(price_token_b64: str) -> tuple[int | None, int | None]:
    """Decode booking price token and return (option_index, price_raw)."""
    if not price_token_b64:
        return None, None

    try:
        summary = ItinerarySummary.from_b64(price_token_b64)
    except Exception:
        return None, None

    option_index = None
    flights_field = str(summary.flights or "")
    if flights_field.startswith("options:"):
        try:
            option_index = int(flights_field.split(":", 1)[1])
        except (TypeError, ValueError, IndexError):
            option_index = None

    price_raw = int(summary.price or 0) if summary else None
    return option_index, price_raw


def _extract_display_price_raw_from_context(context_token_b64: str) -> int | None:
    """Decode option context token and extract raw display price (field 3.1)."""
    if not context_token_b64:
        return None

    try:
        raw = base64.b64decode(context_token_b64)
    except Exception:
        return None

    try:
        pos = 0
        while pos < len(raw):
            tag, pos = _read_varint(raw, pos)
            field = tag >> 3
            wire_type = tag & 7

            if field == 3 and wire_type == 2:
                length, pos = _read_varint(raw, pos)
                nested = raw[pos:pos + length]
                pos += length

                nested_pos = 0
                while nested_pos < len(nested):
                    nested_tag, nested_pos = _read_varint(nested, nested_pos)
                    nested_field = nested_tag >> 3
                    nested_wire_type = nested_tag & 7

                    if nested_field == 1 and nested_wire_type == 0:
                        raw_price, nested_pos = _read_varint(nested, nested_pos)
                        return raw_price

                    nested_pos = _skip_wire_value(nested, nested_pos, nested_wire_type)
            else:
                pos = _skip_wire_value(raw, pos, wire_type)
    except Exception:
        return None

    return None


def _extract_context_tokens(option: list[Any]) -> tuple[str, str]:
    """Extract context protobuf tokens from option[19] JSON payload."""
    context_raw = _safe_get(option, [19], "")
    if not isinstance(context_raw, str):
        return "", ""

    try:
        context = json.loads(context_raw)
    except json.JSONDecodeError:
        return "", ""

    token0 = context[0] if isinstance(context, list) and len(context) > 0 and isinstance(context[0], str) else ""
    token1 = (
        context[1][0]
        if isinstance(context, list)
        and len(context) > 1
        and isinstance(context[1], list)
        and len(context[1]) > 0
        and isinstance(context[1][0], str)
        else ""
    )
    return token0, token1


def _extract_segment_identity_from_context(context_segment_token_b64: str) -> dict[str, str]:
    """Decode context segment token and extract identity fields from message 1.*."""
    if not context_segment_token_b64:
        return {}

    try:
        raw = base64.b64decode(context_segment_token_b64)
    except Exception:
        return {}

    fields: dict[str, str] = {}
    try:
        pos = 0
        while pos < len(raw):
            tag, pos = _read_varint(raw, pos)
            field = tag >> 3
            wire_type = tag & 7

            # Context token 1 uses field 1 as a nested segment descriptor message.
            if field != 1 or wire_type != 2:
                pos = _skip_wire_value(raw, pos, wire_type)
                continue

            length, pos = _read_varint(raw, pos)
            nested = raw[pos:pos + length]
            pos += length

            nested_pos = 0
            while nested_pos < len(nested):
                nested_tag, nested_pos = _read_varint(nested, nested_pos)
                nested_field = nested_tag >> 3
                nested_wire_type = nested_tag & 7

                if nested_wire_type != 2:
                    nested_pos = _skip_wire_value(nested, nested_pos, nested_wire_type)
                    continue

                text_len, nested_pos = _read_varint(nested, nested_pos)
                chunk = nested[nested_pos:nested_pos + text_len]
                nested_pos += text_len

                try:
                    text_value = chunk.decode("utf-8")
                except Exception:
                    continue
                if not text_value:
                    continue

                if nested_field == 1:
                    fields["context_origin_iata"] = text_value
                elif nested_field == 2:
                    fields["context_departure_local_iso"] = text_value
                elif nested_field == 3:
                    fields["context_destination_iata"] = text_value
                elif nested_field == 4:
                    fields["context_arrival_local_iso"] = text_value
                elif nested_field == 5:
                    fields["context_carrier_code"] = text_value
                elif nested_field == 6:
                    fields["context_flight_number"] = text_value
                elif nested_field == 10:
                    fields["context_aircraft_code"] = text_value
    except Exception:
        return {}

    return fields


_GSTATIC_LOGO_BASE = "https://www.gstatic.com/flights/partner_logos/70px"


def _extract_seller(option: list[Any]) -> tuple[str, str, str, str, bool]:
    """Extract (seller_name, seller_code, booking_url, logo_url, is_airline_direct) from a booking option.

    opt[1]: [[seller_code, seller_name, logo_code_or_None, is_airline_bool], ...]
    opt[5]: [display_url, None, [clk_base_url, [["u", token], ["v", "1"]]]]

    Seller identity (opt[1]) and click URL (opt[5]) are extracted independently:
    a missing seller block does not block URL extraction, and vice versa.

    logo_url is ``{_GSTATIC_LOGO_BASE}/<logo_code>.png`` only when Google
    explicitly provides logo_code in opt[1][0][2]. When logo_code is absent
    logo_url is empty — callers wanting a best-effort airline-direct logo
    can construct one themselves via ``{_GSTATIC_LOGO_BASE}/{seller_code}.png``
    (works for IATA-style codes; will 404 for OTA seller codes).

    is_airline_direct is True when Google flags the seller as the operating
    carrier (booking goes directly to the airline rather than an OTA).
    """
    booking_url = _extract_booking_url(_safe_get(option, [5]))

    seller_entry = _safe_get(option, [1, 0])
    if not isinstance(seller_entry, list):
        return "", "", booking_url, "", False

    # Multi-seller opt[1] lists are not observed in our corpus but the wire
    # format permits them. Log so we can detect this if Google ever changes.
    opt1_list = _safe_get(option, [1])
    if isinstance(opt1_list, list) and len(opt1_list) > 1:
        logger.debug(
            "booking option has %d seller entries; using the first (%s)",
            len(opt1_list),
            seller_entry[0] if seller_entry else None,
        )

    seller_code = str(seller_entry[0]) if len(seller_entry) > 0 and seller_entry[0] else ""
    seller_name = str(seller_entry[1]) if len(seller_entry) > 1 and seller_entry[1] else ""
    logo_code = str(seller_entry[2]) if len(seller_entry) > 2 and seller_entry[2] else ""
    is_airline_direct = bool(seller_entry[3]) if len(seller_entry) > 3 else False

    logo_url = (
        f"{_GSTATIC_LOGO_BASE}/{urllib.parse.quote(logo_code, safe='')}.png"
        if logo_code else ""
    )
    return seller_name, seller_code, booking_url, logo_url, is_airline_direct


def _extract_booking_url(url_block: Any) -> str:
    """Reconstruct the clk redirect URL from opt[5]. Empty string when the shape is unexpected."""
    if not (isinstance(url_block, list) and len(url_block) >= 3):
        return ""
    clk_block = url_block[2]
    if not (isinstance(clk_block, list) and len(clk_block) >= 2):
        return ""
    clk_base = clk_block[0] if isinstance(clk_block[0], str) else ""
    params = clk_block[1] if isinstance(clk_block[1], list) else []
    kv_pairs = [
        (str(p[0]), str(p[1]))
        for p in params
        if isinstance(p, list) and len(p) >= 2 and p[0] and p[1]
    ]
    if not (clk_base and kv_pairs):
        return ""
    sep = "&" if "?" in clk_base else "?"
    return clk_base + sep + urllib.parse.urlencode(kv_pairs)


def _normalize_attribute_vector(value: Any) -> list[Any]:
    """Normalize brand attribute vector to scalar-only values for diagnostics."""
    if not isinstance(value, list):
        return []
    normalized: list[Any] = []
    for item in value[:32]:
        if item is None or isinstance(item, (bool, int, float, str)):
            normalized.append(item)
        elif isinstance(item, list):
            normalized.append("list")
        elif isinstance(item, dict):
            normalized.append("dict")
        else:
            normalized.append(type(item).__name__)
    return normalized


def _classify_fare_family(brand_code: str, brand_label: str, *, is_basic: bool) -> str:
    """Classify fare-family bucket from booking-option brand strings."""
    haystack = f"{brand_code} {brand_label}".upper()
    if is_basic:
        return "basic"
    if any(token in haystack for token in ("FIRST", "BUSINESS", "PREMIUM")):
        return "premium"
    if "DELTA MAIN CLASSIC" in haystack:
        return "standard"
    if "MAIN CABIN" in haystack:
        return "standard"
    if "ECONOMY FULLY REFUNDABLE" in haystack or haystack.strip() == "ECONOMY":
        return "standard"
    if " ECONOMY " in f" {haystack} " and "PLUS" not in haystack:
        return "standard"
    if any(token in haystack for token in ("PLUS", "SELECT", "COMFORT")):
        return "enhanced"
    return "unknown"


_CABIN_NUM_TO_BUCKET: dict[int, str] = {v: k for k, v in CABIN_CLASS_MAP.items()}


def _cabin_bucket_from_brand_block(brand_block: list) -> str:
    """Extract cabin bucket from the numeric cabin class field in brand_block[6][0][0].

    Google encodes a per-option cabin class using the same Seat enum as the
    request filter (1=economy, 2=premium-economy, 3=business, 4=first).
    This field is present on every observed booking option — including
    codeshare and OTA options that carry no brand text.
    """
    cabin_num = _safe_get(brand_block, [6, 0, 0])
    if isinstance(cabin_num, int) and cabin_num in _CABIN_NUM_TO_BUCKET:
        return _CABIN_NUM_TO_BUCKET[cabin_num]
    return "unknown"



def _infer_rebookability_signal(fare_family: str, *, is_basic: bool) -> str:
    """Infer user-facing rebookability signal for observability."""
    if is_basic or fare_family == "basic":
        return "restricted"
    if fare_family == "standard":
        return "standard_rebookable"
    if fare_family in ("enhanced", "premium"):
        return "upgraded_rebookable"
    return "unknown"


def parse_booking_payload(text: str) -> list[list[Any]]:
    """Extract raw booking-option lists from a GetBookingResults response text."""
    stripped = text.lstrip(")]}'")
    if not stripped.strip():
        return []

    try:
        outer = json.loads(stripped)
    except json.JSONDecodeError:
        return []

    if not isinstance(outer, list):
        return []

    for frame in outer:
        if not (isinstance(frame, list) and len(frame) >= 3):
            continue
        if frame[0] != "wrb.fr" or not isinstance(frame[2], str):
            continue
        try:
            payload = json.loads(frame[2])
        except (json.JSONDecodeError, TypeError):
            continue
        options_raw = _safe_get(payload, [1, 0], [])
        if isinstance(options_raw, list) and options_raw:
            return [opt for opt in options_raw if isinstance(opt, list)]

    return []


def _parse_booking_rpc_response(
    text: str,
    *,
    registry_version: str | None = None,
    required_keys: tuple[str, ...] | None = None,
) -> list[BookingOption]:
    """Parse GetBookingResults response into fare options.

    Each option returns a :class:`BookingOption` with:
      - price (USD integer)
      - brand_label (user-facing)
      - brand_code (internal normalized code)

    Args:
        registry_version: If provided, set as ``registry_version`` on each option.
        required_keys: If provided, warns when any key is missing from parsed options.
    """
    options_raw = parse_booking_payload(text)
    if not options_raw:
        return []

    dropped_missing_price = 0
    dropped_missing_brand = 0
    dropped_invalid_price = 0
    options: list[BookingOption] = []

    for option in options_raw:
        price_block = _extract_price_block(option)
        if not price_block:
            dropped_missing_price += 1
            continue

        price = _safe_get(price_block, [0, 1])
        if not isinstance(price, (int, float)):
            dropped_invalid_price += 1
            continue

        brand_block = _extract_brand_block(option)
        if not brand_block:
            # OTA/codeshare options have option[21] with null brand fields
            # but still carry cabin class at [6][0][0]. Use it directly.
            raw_21 = _safe_get(option, [21])
            if isinstance(raw_21, list):
                brand_block = raw_21
            else:
                dropped_missing_brand += 1
                continue

        brand_label = str(_safe_get(brand_block, [3], "") or "")
        brand_code = str(_safe_get(brand_block, [0, 1], "") or "")

        price_token = str(_safe_get(price_block, [1], "") or "")
        option_index, token_price_raw = _extract_option_index_and_token_price_raw(price_token)

        context_token0, context_token1 = _extract_context_tokens(option)
        display_price_raw = _extract_display_price_raw_from_context(context_token0)
        segment_identity = _extract_segment_identity_from_context(context_token1)
        price_delta_raw = (
            int(display_price_raw - token_price_raw)
            if isinstance(display_price_raw, int) and isinstance(token_price_raw, int)
            else None
        )

        flag_primary = _safe_get(brand_block, [2], None) is True
        flag_secondary = _safe_get(brand_block, [16], None) is True
        flag_tail = _safe_get(option, [24], None) is True
        is_basic_by_flags = flag_primary and flag_secondary and flag_tail
        is_basic_by_text = "BASIC" in f"{brand_label} {brand_code}".upper()
        is_basic = is_basic_by_flags or is_basic_by_text
        fare_family = _classify_fare_family(brand_code, brand_label, is_basic=is_basic)
        cabin_bucket = _cabin_bucket_from_brand_block(brand_block)
        rebookability_signal = _infer_rebookability_signal(fare_family, is_basic=is_basic)
        attribute_vector = _normalize_attribute_vector(_safe_get(brand_block, [1], []))
        seller_name, seller_code, booking_url, logo_url, is_airline_direct = _extract_seller(option)

        parsed_option = BookingOption(
            price=int(round(price)),
            brand_label=brand_label,
            brand_code=brand_code,
            is_basic=is_basic,
            fare_family=fare_family,
            rebookability_signal=rebookability_signal,
            seller_name=seller_name,
            seller_code=seller_code,
            booking_url=booking_url,
            logo_url=logo_url,
            is_airline_direct=is_airline_direct,
            _is_basic_by_flags=is_basic_by_flags,
            _is_basic_by_text=is_basic_by_text,
            _option_index=option_index,
            _token_price_raw=token_price_raw,
            _display_price_raw=display_price_raw,
            _price_delta_raw=price_delta_raw,
            _context_segment_token=context_token1,
            _context_origin_iata=segment_identity.get("context_origin_iata"),
            _context_destination_iata=segment_identity.get("context_destination_iata"),
            _context_departure_local_iso=segment_identity.get("context_departure_local_iso"),
            _context_arrival_local_iso=segment_identity.get("context_arrival_local_iso"),
            _context_carrier_code=segment_identity.get("context_carrier_code"),
            _context_flight_number=segment_identity.get("context_flight_number"),
            _context_aircraft_code=segment_identity.get("context_aircraft_code"),
            _cabin_bucket=cabin_bucket,
            _brand_attribute_vector=attribute_vector,
            _registry_version=registry_version,
        )
        options.append(parsed_option)

    if options and required_keys is not None:
        missing_required = sorted({key for option in options for key in required_keys if not hasattr(option, key)})
        if missing_required:
            logger.warning("Booking options parser missing required fields: %s", ",".join(missing_required))

    if dropped_missing_price or dropped_missing_brand or dropped_invalid_price:
        log = logger.warning if not options else logger.debug
        log(
            "Booking options parser dropped options (missing_price=%s, missing_brand=%s, invalid_price=%s)",
            dropped_missing_price,
            dropped_missing_brand,
            dropped_invalid_price,
        )

    return options

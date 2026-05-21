"""Validate brand_block[6][0][0] as cabin class across economy + business searches."""

import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from swoop import CabinClass, Passengers, TransportConfig
from swoop.rpc import search_raw, _http_post, _build_filters_from_legs, _build_booking_f_req, _build_selected_legs, _normalize_rpc_leg, BOOKING_RPC_URL, SORT_DEPARTURE_TIME
from swoop._booking import parse_booking_payload, _extract_brand_block, _extract_price_block, _safe_get

SEAT_NAMES = {1: "ECONOMY", 2: "PREMIUM_ECONOMY", 3: "BUSINESS", 4: "FIRST"}


def fetch_raw_booking_options(itinerary, cabin):
    booking_token = itinerary.booking_token
    origin = itinerary.departure_airport_code
    destination = itinerary.arrival_airport_code
    dep = itinerary.departure_date
    date = f"{dep[0]:04d}-{dep[1]:02d}-{dep[2]:02d}"
    selected_legs = _build_selected_legs(itinerary)

    legs = [_normalize_rpc_leg(origin, destination, date)]
    filters = _build_filters_from_legs(
        legs, cabin=cabin,
        passengers=Passengers(),
        sort=SORT_DEPARTURE_TIME,
    )
    filter_block = filters[1]
    encoded_body = _build_booking_f_req(booking_token, filter_block, selected_legs)
    if not encoded_body:
        return []

    res = _http_post(
        BOOKING_RPC_URL,
        content=f"f.req={encoded_body}".encode(),
        transport=TransportConfig(),
    )
    return parse_booking_payload(res.text)


def analyze_options(raw_options, label):
    print(f"\n{'='*70}")
    print(f"  {label}")
    print(f"{'='*70}")

    for i, option in enumerate(raw_options):
        brand_block = _safe_get(option, [21])
        brand_label = ""
        brand_code = ""
        if brand_block:
            bb0 = _safe_get(brand_block, [0])
            if isinstance(bb0, list):
                brand_code = _safe_get(bb0, [1], "")
            brand_label = _safe_get(brand_block, [3], "") or ""

        price = _safe_get(option, [7, 0, 1], 0)

        # The key field
        cabin_num = _safe_get(brand_block, [6, 0, 0]) if brand_block else None
        cabin_name = SEAT_NAMES.get(cabin_num, f"?({cabin_num})") if cabin_num is not None else "MISSING"

        # Also check brand_block[13] and brand_block[15]
        bb13 = _safe_get(brand_block, [13]) if brand_block else None
        bb15 = _safe_get(brand_block, [15]) if brand_block else None

        print(f"  Option {i}: ${price:>6}  cabin_field={cabin_num} ({cabin_name:16s})  "
              f"brand={brand_label or '(none)':25s}  bb[13]={bb13}  bb[15]={json.dumps(bb15)}")


def main():
    tests: list[tuple[str, str, str, CabinClass]] = [
        ("JFK", "LHR", "2026-06-15", "economy"),
        ("JFK", "LHR", "2026-06-15", "business"),
        ("JFK", "LHR", "2026-06-15", "first"),
        ("SFO", "NRT", "2026-06-15", "business"),
    ]

    for origin, dest, date, cabin in tests:
        print(f"\n\nSearching {origin}->{dest} cabin={cabin}...")
        result = search_raw(origin, dest, date, cabin=cabin)
        if result is None:
            print("  No result")
            continue
        all_itins = (result.best or []) + (result.other or [])

        if not all_itins:
            print("  No itineraries found")
            continue

        # Take first itinerary
        itin = all_itins[0]
        print(f"  Itinerary: {itin.segments[0].airline}{itin.segments[0].flight_number} ${itin.price}")

        raw_options = fetch_raw_booking_options(itin, cabin)
        if not raw_options:
            print("  No booking options")
            continue

        analyze_options(raw_options, f"{origin}->{dest} cabin={cabin} ({itin.segments[0].airline}{itin.segments[0].flight_number})")


if __name__ == "__main__":
    main()

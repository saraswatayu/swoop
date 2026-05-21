"""Dump raw booking option arrays from a live business-class price check.

Inspects every index in the raw option arrays to find structural cabin
class indicators beyond brand name text matching.
"""

import json
import sys
import os
from typing import cast

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch
from swoop import CabinClass, Passengers, TransportConfig
from swoop.rpc import search_raw, _http_post, _build_filters_from_legs, _build_booking_f_req, _build_selected_legs, _normalize_rpc_leg, BOOKING_RPC_URL, SORT_DEPARTURE_TIME
from swoop._booking import parse_booking_payload, _extract_brand_block, _extract_price_block, _safe_get


def dump_raw_booking_options(itinerary, *, cabin: CabinClass = "business"):
    """Make a GetBookingResults call and return (raw_options, response_text)."""
    booking_token = itinerary.booking_token
    origin = itinerary.departure_airport_code
    destination = itinerary.arrival_airport_code
    dep = itinerary.departure_date
    date = f"{dep[0]:04d}-{dep[1]:02d}-{dep[2]:02d}"
    selected_legs = _build_selected_legs(itinerary)

    legs = [_normalize_rpc_leg(origin, destination, date)]
    filters = _build_filters_from_legs(
        legs,
        cabin=cabin,
        passengers=Passengers(adults=1, children=0, infants_in_seat=0, infants_on_lap=0),
        sort=SORT_DEPARTURE_TIME,
    )
    filter_block = filters[1]
    encoded_body = _build_booking_f_req(booking_token, filter_block, selected_legs)
    if not encoded_body:
        print("ERROR: Failed to build booking request")
        return None, None

    res = _http_post(
        BOOKING_RPC_URL,
        content=f"f.req={encoded_body}".encode(),
        transport=TransportConfig(timeout=90, retries=2),
    )

    raw_options = parse_booking_payload(res.text)
    return raw_options, res.text


def summarize_value(val, max_len=120):
    """Summarize a value for display."""
    if val is None:
        return "null"
    if isinstance(val, bool):
        return str(val)
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, str):
        if len(val) > max_len:
            return f"str({len(val)} chars): {val[:max_len]}..."
        return f"str: {val!r}"
    if isinstance(val, list):
        if not val:
            return "[]"
        flat = json.dumps(val)
        if len(flat) > max_len:
            return f"list({len(val)} items): {flat[:max_len]}..."
        return f"list({len(val)}): {flat}"
    return repr(val)[:max_len]


def main():
    # Search for business class flights — try international route for variety
    routes = [
        ("JFK", "LHR", "2026-06-15", "business"),
        ("SFO", "NRT", "2026-06-15", "business"),
    ]

    for origin, dest, date, cabin in routes:
        print(f"\n{'='*80}")
        print(f"SEARCHING: {origin} → {dest} on {date}, cabin={cabin}")
        print(f"{'='*80}")

        result = search_raw(origin, dest, date, cabin=cast(CabinClass, cabin))
        if result is None:
            print("  No result returned from search_raw")
            continue
        all_itins = (result.best or []) + (result.other or [])

        if not all_itins:
            print("  No itineraries found")
            continue

        print(f"  Found {len(all_itins)} itineraries")

        # Also dump segment[13] (seat type) from the search results
        for i, itin in enumerate(all_itins[:3]):
            print(f"\n  --- Itinerary {i}: {itin.departure_airport_code}->{itin.arrival_airport_code} ${itin.price} ---")
            for j, seg in enumerate(itin.segments):
                print(f"    Segment {j}: {seg.airline}{seg.flight_number} {seg.departure_airport_code}->{seg.arrival_airport_code}")

        # Pick first itinerary for booking options dump
        itin = all_itins[0]
        print(f"\n  Fetching booking options for itinerary 0: ${itin.price}")

        raw_options, response_text = dump_raw_booking_options(itin, cabin=cast(CabinClass, cabin))
        if not raw_options:
            print("  No raw booking options returned")
            continue

        print(f"\n  Got {len(raw_options)} raw booking options")
        print(f"  Option array lengths: {[len(opt) for opt in raw_options]}")

        for i, option in enumerate(raw_options):
            brand_block = _extract_brand_block(option)
            price_block = _extract_price_block(option)
            brand_label = str(_safe_get(brand_block, [3], "")) if brand_block else ""
            brand_code = str(_safe_get(brand_block, [0, 1], "")) if brand_block else ""
            price = _safe_get(price_block, [0, 1], 0) if price_block else 0

            print(f"\n  === OPTION {i}: {brand_label} ({brand_code}) ${price} ===")
            print(f"  Array length: {len(option)}")
            for j, val in enumerate(option):
                known = ""
                if j == 7:
                    known = " ← PRICE BLOCK"
                elif j == 19:
                    known = " ← CONTEXT TOKENS"
                elif j == 21:
                    known = " ← BRAND BLOCK"
                elif j == 24:
                    known = " ← BASIC FLAG"
                print(f"    [{j:2d}]{known}: {summarize_value(val)}")

        # Save full response for offline analysis
        dump_path = f"/tmp/booking_dump_{origin}_{dest}_{cabin}.json"
        with open(dump_path, "w") as f:
            json.dump(raw_options, f, indent=2)
        print(f"\n  Raw options saved to {dump_path}")

        # Only do first route
        break


if __name__ == "__main__":
    main()

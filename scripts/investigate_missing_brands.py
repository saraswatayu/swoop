"""Investigate booking options dropped for 'missing brand' on international routes."""

import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from swoop import CabinClass, Passengers, TransportConfig
from swoop.rpc import (
    search_raw, _http_post, _build_filters_from_legs, _build_booking_f_req,
    _build_selected_legs, _normalize_rpc_leg, BOOKING_RPC_URL, SORT_DEPARTURE_TIME,
)
from swoop._booking import (
    parse_booking_payload, _looks_like_brand_block, _extract_brand_block,
    _looks_like_price_block, _extract_price_block, _safe_get,
)


def fetch_raw(itinerary, cabin: CabinClass):
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
    encoded_body = _build_booking_f_req(booking_token, filters[1], selected_legs)
    if not encoded_body:
        return []
    res = _http_post(
        BOOKING_RPC_URL,
        content=f"f.req={encoded_body}".encode(),
        transport=TransportConfig(),
    )
    return parse_booking_payload(res.text)


def main():
    # Routes where we saw many missing brands
    routes: list[tuple[str, str, str, CabinClass]] = [
        ("JFK", "LHR", "2026-06-20", "business"),
        ("SFO", "NRT", "2026-06-20", "business"),
    ]

    for origin, dest, date, cabin in routes:
        print(f"\n{'='*70}")
        print(f"{origin}->{dest} cabin={cabin}")
        print(f"{'='*70}")

        result = search_raw(origin, dest, date, cabin=cabin)
        if not result:
            continue

        all_itins = (result.best or []) + (result.other or [])
        # Just try first itinerary
        itin = all_itins[0]
        seg0 = itin.segments[0]
        print(f"Itinerary: {seg0.airline}{seg0.flight_number}")

        raw_options = fetch_raw(itin, cabin)
        print(f"Total raw options: {len(raw_options)}")

        has_brand = 0
        no_brand = 0

        for i, option in enumerate(raw_options):
            price_block = _extract_price_block(option)
            brand_block = _extract_brand_block(option)
            has_price = price_block is not None
            has_brand_b = brand_block is not None
            price = _safe_get(price_block, [0, 1], 0) if price_block else 0

            if has_brand_b:
                has_brand += 1
                continue

            no_brand += 1
            print(f"\n  --- Option {i}: NO BRAND BLOCK (has_price={has_price}, ${price}) ---")
            print(f"  Array length: {len(option)}")

            # Show which indices have data
            for j, val in enumerate(option):
                if val is not None:
                    val_str = json.dumps(val) if not isinstance(val, str) else f"str({len(val)})"
                    if len(val_str) > 100:
                        val_str = val_str[:100] + "..."
                    print(f"    [{j:2d}]: {val_str}")

            # Check index 21 specifically — why did brand detection fail?
            idx21 = option[21] if len(option) > 21 else None
            if idx21 is not None:
                print(f"\n  option[21] detailed:")
                print(f"    type: {type(idx21).__name__}")
                print(f"    is list: {isinstance(idx21, list)}")
                if isinstance(idx21, list):
                    print(f"    length: {len(idx21)}")
                    for k, v in enumerate(idx21[:5]):
                        print(f"    [{k}]: {json.dumps(v)[:80]}")
                    print(f"    _looks_like_brand_block: {_looks_like_brand_block(idx21)}")

            # Also scan all indices for anything that looks like a brand block
            for j, val in enumerate(option):
                if j == 21:
                    continue
                if isinstance(val, list) and _looks_like_brand_block(val):
                    print(f"\n  FOUND brand-like block at [{j}]!")

            # Check for cabin class field even without brand block
            # Maybe option[21][6][0][0] is accessible even when brand detection fails
            cabin_field = _safe_get(option, [21, 6, 0, 0]) if len(option) > 21 else None
            print(f"\n  option[21][6][0][0] (cabin): {cabin_field}")

        print(f"\n  Summary: {has_brand} with brand, {no_brand} without brand")


if __name__ == "__main__":
    main()

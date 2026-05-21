"""Sweep many itineraries across routes/cabins to check if brand_block[6][0][0] is ever missing."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from swoop import CabinClass, Passengers, TransportConfig
from swoop.rpc import search_raw, _http_post, _build_filters_from_legs, _build_booking_f_req, _build_selected_legs, _normalize_rpc_leg, BOOKING_RPC_URL, SORT_DEPARTURE_TIME
from swoop._booking import parse_booking_payload, _extract_brand_block, _extract_price_block, _safe_get

SEAT_NAMES = {1: "ECON", 2: "PREM", 3: "BIZ", 4: "FIRST"}


def fetch_raw_booking_options(itinerary, cabin: CabinClass):
    booking_token = itinerary.booking_token
    origin = itinerary.departure_airport_code
    destination = itinerary.arrival_airport_code
    dep = itinerary.departure_date
    date = f"{dep[0]:04d}-{dep[1]:02d}-{dep[2]:02d}"
    selected_legs = _build_selected_legs(itinerary)

    legs = [_normalize_rpc_leg(origin, destination, date)]
    filters = _build_filters_from_legs(
        legs, cabin=cabin,
        passengers=Passengers(adults=1, children=0, infants_in_seat=0, infants_on_lap=0),
        sort=SORT_DEPARTURE_TIME,
    )
    filter_block = filters[1]
    encoded_body = _build_booking_f_req(booking_token, filter_block, selected_legs)
    if not encoded_body:
        return []

    res = _http_post(
        BOOKING_RPC_URL,
        content=f"f.req={encoded_body}".encode(),
        transport=TransportConfig(timeout=90, retries=2),
    )
    return parse_booking_payload(res.text)


def main():
    routes = [
        # Domestic US
        ("JFK", "LAX", "2026-06-20"),
        ("SFO", "ORD", "2026-06-20"),
        ("ATL", "DFW", "2026-06-20"),
        # Transatlantic
        ("JFK", "LHR", "2026-06-20"),
        ("JFK", "CDG", "2026-06-20"),
        ("JFK", "FRA", "2026-06-20"),
        # Transpacific
        ("SFO", "NRT", "2026-06-20"),
        ("LAX", "ICN", "2026-06-20"),
        ("SFO", "SIN", "2026-06-20"),
        # Middle East / other
        ("JFK", "DXB", "2026-06-20"),
        ("LAX", "SYD", "2026-06-20"),
    ]

    cabins: list[CabinClass] = ["economy", "business"]

    total_options = 0
    missing_count = 0
    present_count = 0
    missing_examples = []

    for origin, dest, date in routes:
        for cabin in cabins:
            print(f"\n{origin}->{dest} cabin={cabin}...", end=" ", flush=True)
            try:
                result = search_raw(origin, dest, date, cabin=cabin)
            except Exception as e:
                print(f"search failed: {e}")
                continue

            if result is None:
                print("null result")
                continue
            all_itins = (result.best or []) + (result.other or [])
            if not all_itins:
                print("no itineraries")
                continue

            # Check up to 3 itineraries per route/cabin combo
            for itin_idx, itin in enumerate(all_itins[:3]):
                seg0 = itin.segments[0] if itin.segments else None
                flight_id = f"{seg0.airline}{seg0.flight_number}" if seg0 else "?"

                try:
                    raw_options = fetch_raw_booking_options(itin, cabin)
                except Exception as e:
                    print(f"[{flight_id}: booking failed]", end=" ", flush=True)
                    continue

                if not raw_options:
                    print(f"[{flight_id}: no opts]", end=" ", flush=True)
                    continue

                for opt_idx, option in enumerate(raw_options):
                    total_options += 1
                    brand_block = _safe_get(option, [21])
                    cabin_num = _safe_get(brand_block, [6, 0, 0]) if brand_block else None

                    brand_label = ""
                    if brand_block:
                        bb0 = _safe_get(brand_block, [0])
                        if isinstance(bb0, list):
                            brand_label = _safe_get(bb0, [1], "") or ""
                        brand_label = brand_label or (_safe_get(brand_block, [3], "") or "")

                    price = _safe_get(option, [7, 0, 1], 0)

                    if cabin_num is None:
                        missing_count += 1
                        missing_examples.append({
                            "route": f"{origin}->{dest}",
                            "cabin": cabin,
                            "flight": flight_id,
                            "brand": brand_label,
                            "price": price,
                            "brand_block_exists": brand_block is not None,
                            "bb6": _safe_get(brand_block, [6]) if brand_block else None,
                        })
                    else:
                        present_count += 1

                print(f"[{flight_id}: {len(raw_options)} opts]", end=" ", flush=True)

            print()

    print(f"\n\n{'='*70}")
    print(f"RESULTS")
    print(f"{'='*70}")
    print(f"Total booking options inspected: {total_options}")
    print(f"  cabin field PRESENT: {present_count}")
    print(f"  cabin field MISSING: {missing_count}")

    if missing_examples:
        print(f"\nMISSING EXAMPLES:")
        for ex in missing_examples:
            print(f"  {ex['route']} {ex['cabin']} {ex['flight']} "
                  f"brand={ex['brand']!r} ${ex['price']} "
                  f"bb_exists={ex['brand_block_exists']} bb6={ex['bb6']}")
    else:
        print(f"\nNo missing examples found — field is 100% reliable across all samples.")


if __name__ == "__main__":
    main()

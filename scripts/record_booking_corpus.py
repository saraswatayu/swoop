"""Record booking corpus fixtures across diverse airlines, routes, and cabin classes."""

import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from swoop.rpc import search_raw
from swoop._booking import parse_booking_payload, _parse_booking_rpc_response, _extract_brand_block, _safe_get

from _booking_helper import fetch_booking_results

_CABIN_NUM_TO_BUCKET = {1: "economy", 2: "premium-economy", 3: "business", 4: "first"}


def fetch_booking_response_text(itinerary, cabin):
    _, response_text = fetch_booking_results(itinerary, cabin)
    return response_text


def record(origin, dest, date, cabin, target_airlines, fixture_name, fixture_id, min_options=2):
    print(f"\n{'='*60}")
    print(f"Recording: {fixture_id} ({origin}->{dest} {cabin})")
    print(f"{'='*60}")

    result = search_raw(origin, dest, date, cabin=cabin)
    if result is None:
        print("  No results")
        return None

    all_itins = (result.best or []) + (result.other or [])
    print(f"  {len(all_itins)} itineraries")

    for itin in all_itins:
        seg0 = itin.segments[0] if itin.segments else None
        if not seg0:
            continue
        if target_airlines and seg0.airline not in target_airlines:
            continue

        response_text = fetch_booking_response_text(itin, cabin)
        if not response_text:
            continue

        options = _parse_booking_rpc_response(response_text, registry_version="2026-03-18")
        if len(options) < min_options:
            continue

        raw = parse_booking_payload(response_text)
        cabin_buckets = []
        for opt in raw:
            bb = _extract_brand_block(opt)
            cn = _safe_get(bb, [6, 0, 0]) if bb else None
            bucket = _CABIN_NUM_TO_BUCKET.get(cn, "unknown") if isinstance(cn, int) else "unknown"
            cabin_buckets.append(bucket)

        all_have_cabin = "unknown" not in cabin_buckets

        if not all_have_cabin:
            continue

        path = f"tests/fixtures/corpus/{fixture_name}"
        with open(path, "w") as f:
            f.write(response_text)

        entry = {
            "id": fixture_id,
            "path": f"corpus/{fixture_name}",
            "registry_version": "2026-03-18",
            "expected": {
                "prices": [o.price for o in options],
                "brands": [o.brand_label for o in options],
                "cabin_buckets": cabin_buckets,
                "fare_families": [o.fare_family for o in options],
            }
        }

        print(f"  Recorded: {seg0.airline}{seg0.flight_number} ({len(options)} options)")
        print(f"    Brands: {[o.brand_label for o in options]}")
        print(f"    Cabins: {cabin_buckets}")
        return entry

    print("  No suitable itinerary found")
    return None


def main():
    scenarios = [
        # Transatlantic business — BA uses non-standard brand names
        ("JFK", "LHR", "2026-06-20", "business", ["BA"], "booking_ba_business_response.txt", "booking_ba_business_v1"),
        # Transatlantic business — VS with mixed cabin options
        ("JFK", "LHR", "2026-06-20", "business", ["VS"], "booking_vs_business_response.txt", "booking_vs_business_v1"),
        # Domestic economy — AA
        ("JFK", "LAX", "2026-06-20", "economy", ["AA"], "booking_aa_economy_response.txt", "booking_aa_economy_v1"),
        # Transpacific business
        ("SFO", "NRT", "2026-06-20", "business", ["NH", "JL", "UA"], "booking_transpacific_business_response.txt", "booking_transpacific_business_v1"),
        # First class
        ("JFK", "LHR", "2026-06-20", "first", ["BA"], "booking_ba_first_response.txt", "booking_ba_first_v1"),
        # Middle East business
        ("JFK", "DXB", "2026-06-20", "business", ["EK", "TK", "QR"], "booking_mideast_business_response.txt", "booking_mideast_business_v1"),
    ]

    entries = []
    for origin, dest, date, cabin, airlines, fname, fid in scenarios:
        entry = record(origin, dest, date, cabin, airlines, fname, fid)
        if entry:
            entries.append(entry)

    print(f"\n\n{'='*60}")
    print(f"RECORDED {len(entries)} NEW FIXTURES")
    print(f"{'='*60}")
    for entry in entries:
        print(json.dumps(entry, indent=2))
        print(",")


if __name__ == "__main__":
    main()

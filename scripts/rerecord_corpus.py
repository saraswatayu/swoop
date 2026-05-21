"""Re-record booking corpus fixtures from live API with current response structure."""

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


def record_fixture(origin, dest, date, cabin, target_airlines, fixture_name):
    """Search and record booking response that matches target airlines/mixed cabins."""
    print(f"\nSearching {origin}->{dest} cabin={cabin}...")
    result = search_raw(origin, dest, date, cabin=cabin)
    if result is None:
        print("  No results")
        return

    all_itins = (result.best or []) + (result.other or [])
    print(f"  Found {len(all_itins)} itineraries")

    for itin in all_itins:
        seg0 = itin.segments[0] if itin.segments else None
        if not seg0:
            continue
        airline = seg0.airline
        if target_airlines and airline not in target_airlines:
            continue

        print(f"  Trying {airline}{seg0.flight_number}...")
        response_text = fetch_booking_response_text(itin, cabin)
        if not response_text:
            continue

        options = _parse_booking_rpc_response(response_text, registry_version="2026-03-18")
        if len(options) < 2:
            print(f"    Only {len(options)} options, skipping")
            continue

        # Check the cabin field is present
        raw = parse_booking_payload(response_text)
        all_have_cabin = all(
            _safe_get(_extract_brand_block(opt), [6, 0, 0]) is not None
            for opt in raw if isinstance(opt, list) and _extract_brand_block(opt)
        )

        cabin_buckets = []
        for opt in raw:
            bb = _extract_brand_block(opt)
            cn = _safe_get(bb, [6, 0, 0]) if bb else None
            bucket = _CABIN_NUM_TO_BUCKET.get(cn, "unknown") if isinstance(cn, int) else "unknown"
            cabin_buckets.append(bucket)

        print(f"    {len(options)} options, cabin_field_present={all_have_cabin}")
        print(f"    Prices: {[o.price for o in options]}")
        print(f"    Brands: {[o.brand_label for o in options]}")
        print(f"    Cabin buckets: {cabin_buckets}")
        print(f"    Fare families: {[o.fare_family for o in options]}")

        if not all_have_cabin:
            print("    SKIPPING: missing cabin field")
            continue

        # Save
        path = f"tests/fixtures/corpus/{fixture_name}"
        with open(path, "w") as f:
            f.write(response_text)

        print(f"    SAVED to {path}")
        print(f"\n    Manifest entry:")
        manifest_entry = {
            "id": fixture_name.replace(".txt", "").replace("booking_", "booking_"),
            "path": f"corpus/{fixture_name}",
            "registry_version": "2026-03-18",
            "expected": {
                "prices": [o.price for o in options],
                "brands": [o.brand_label for o in options],
                "cabin_buckets": cabin_buckets,
                "fare_families": [o.fare_family for o in options],
            }
        }
        print(json.dumps(manifest_entry, indent=2))
        return manifest_entry

    print("  No suitable itinerary found")
    return None


def main():
    # Re-record Delta mixed (needs economy + premium economy + business options)
    delta_entry = record_fixture(
        "JFK", "LAX", "2026-06-20", "business",
        target_airlines=["DL", "AA"],
        fixture_name="booking_delta_mixed_response.txt",
    )

    # Re-record JetBlue (economy with multiple fare tiers)
    blue_entry = record_fixture(
        "JFK", "LAX", "2026-06-20", "economy",
        target_airlines=["B6"],
        fixture_name="booking_jfk_lax_blue_response.txt",
    )

    if delta_entry or blue_entry:
        print("\n\nUpdate contract_corpus_manifest.json booking entries with the above.")


if __name__ == "__main__":
    main()

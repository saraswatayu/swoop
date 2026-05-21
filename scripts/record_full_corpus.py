"""Record comprehensive corpus fixtures for both RPC endpoints.

Covers the full combination matrix:
- Shopping: one-way + roundtrip × economy/premium-economy/business/first × multiple countries
- Booking: economy/premium-economy/business/first × domestic/international × mixed-cabin scenarios
"""

import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import cast

from swoop import CabinClass, Passengers, TransportConfig
from swoop.rpc import (
    search_raw, _http_post, _build_filters_from_legs, _build_booking_f_req,
    _build_selected_legs, _normalize_rpc_leg, BOOKING_RPC_URL, SORT_DEPARTURE_TIME,
)
from swoop._booking import (
    parse_booking_payload, _parse_booking_rpc_response,
    _extract_brand_block, _safe_get,
)
from swoop.decoder import decode_result

_CABIN_NUM_TO_BUCKET = {1: "economy", 2: "premium-economy", 3: "business", 4: "first"}

FIXTURES_DIR = "tests/fixtures"
RESPONSES_DIR = f"{FIXTURES_DIR}/responses"
CORPUS_DIR = f"{FIXTURES_DIR}/corpus"
REGISTRY_VERSION = "2026-03-18"


# ──────────────────────────────────────────────────────────────
# Shopping helpers
# ──────────────────────────────────────────────────────────────

def record_shopping(origin, dest, date, cabin, country, fixture_name, fixture_id,
                    return_date=None):
    """Record a shopping response fixture."""
    trip_label = "roundtrip" if return_date else "oneway"
    print(f"\n  Shopping {origin}->{dest} {trip_label} cabin={cabin} country={country}...", end=" ", flush=True)

    try:
        result = search_raw(
            origin, dest, date,
            cabin=cast(CabinClass, cabin),
            return_date=return_date,
            transport=TransportConfig(country=country),
        )
    except Exception as e:
        print(f"FAILED: {e}")
        return None

    if result is None:
        print("null result")
        return None

    best = result.best or []
    other = result.other or []
    if not best and not other:
        print("no itineraries")
        return None

    first_section = "best" if best else "other"
    first_itin = best[0] if best else other[0]
    first_airline = first_itin.airline_code if first_itin.airline_code else "multi"

    # We need the raw data to save
    # Re-do the search to capture raw response
    from swoop.rpc import _search_from_legs, _normalize_rpc_leg
    legs = [_normalize_rpc_leg(origin, dest, date)]
    if return_date:
        legs.append(_normalize_rpc_leg(dest, origin, return_date))
    raw_result = _search_from_legs(legs, cabin=cast(CabinClass, cabin), transport=TransportConfig(country=country))
    if raw_result is None:
        print("raw search failed")
        return None

    # Save the raw data as JSON (same format as existing shopping fixtures)
    path = f"{RESPONSES_DIR}/{fixture_name}"
    with open(path, "w") as f:
        json.dump(raw_result._raw, f)

    # Build expected values
    expected = {
        "best_count": len(best),
        "other_count": len(other),
        "first_section": first_section,
        "first_airline_code": first_airline,
        "first_origin": first_itin.departure_airport_code,
        "first_destination": first_itin.arrival_airport_code,
        "first_price": first_itin.price,
        "first_segments": len(first_itin.segments),
    }
    if first_itin.currency:
        expected["currency"] = first_itin.currency

    entry = {
        "id": fixture_id,
        "path": f"responses/{fixture_name}",
        "expected": expected,
    }

    print(f"OK ({len(best)}+{len(other)} itins, ${first_itin.price} {first_itin.currency or 'USD'})")
    return entry


# ──────────────────────────────────────────────────────────────
# Booking helpers
# ──────────────────────────────────────────────────────────────

def fetch_booking_response_text(itinerary, cabin):
    booking_token = itinerary.booking_token
    origin = itinerary.departure_airport_code
    destination = itinerary.arrival_airport_code
    dep = itinerary.departure_date
    date = f"{dep[0]:04d}-{dep[1]:02d}-{dep[2]:02d}"
    selected_legs = _build_selected_legs(itinerary)

    legs = [_normalize_rpc_leg(origin, destination, date)]
    filters = _build_filters_from_legs(
        legs, cabin=cast(CabinClass, cabin),
        passengers=Passengers(adults=1, children=0, infants_in_seat=0, infants_on_lap=0),
        sort=SORT_DEPARTURE_TIME,
    )
    filter_block = filters[1]
    encoded_body = _build_booking_f_req(booking_token, filter_block, selected_legs)
    if not encoded_body:
        return None

    res = _http_post(
        BOOKING_RPC_URL,
        content=f"f.req={encoded_body}".encode(),
        transport=TransportConfig(timeout=90, retries=2),
    )
    return res.text


def record_booking(origin, dest, date, cabin, fixture_name, fixture_id,
                   target_airlines=None, min_options=2, require_mixed_cabins=False):
    """Record a booking response fixture."""
    print(f"\n  Booking {origin}->{dest} cabin={cabin} airlines={target_airlines}...", end=" ", flush=True)

    try:
        result = search_raw(origin, dest, date, cabin=cast(CabinClass, cabin))
    except Exception as e:
        print(f"FAILED: {e}")
        return None

    if result is None:
        print("null result")
        return None

    all_itins = (result.best or []) + (result.other or [])
    if not all_itins:
        print("no itineraries")
        return None

    for itin in all_itins:
        seg0 = itin.segments[0] if itin.segments else None
        if not seg0:
            continue
        if target_airlines and seg0.airline not in target_airlines:
            continue

        try:
            response_text = fetch_booking_response_text(itin, cabin)
        except Exception:
            continue
        if not response_text:
            continue

        options = _parse_booking_rpc_response(response_text, registry_version=REGISTRY_VERSION)
        if len(options) < min_options:
            continue

        raw = parse_booking_payload(response_text)
        cabin_buckets = []
        for opt in raw:
            bb = _extract_brand_block(opt)
            cn = _safe_get(bb, [6, 0, 0]) if bb else None
            bucket = _CABIN_NUM_TO_BUCKET.get(cn, "unknown") if isinstance(cn, int) else "unknown"
            cabin_buckets.append(bucket)

        if "unknown" in cabin_buckets:
            continue

        if require_mixed_cabins:
            unique_buckets = set(cabin_buckets)
            if len(unique_buckets) < 2:
                continue

        path = f"{CORPUS_DIR}/{fixture_name}"
        with open(path, "w") as f:
            f.write(response_text)

        entry = {
            "id": fixture_id,
            "path": f"corpus/{fixture_name}",
            "registry_version": REGISTRY_VERSION,
            "expected": {
                "prices": [o.price for o in options],
                "brands": [o.brand_label for o in options],
                "cabin_buckets": cabin_buckets,
                "fare_families": [o.fare_family for o in options],
            }
        }

        mixed_label = " MIXED" if len(set(cabin_buckets)) > 1 else ""
        print(f"OK ({seg0.airline}{seg0.flight_number}, {len(options)} opts, buckets={list(set(cabin_buckets))}{mixed_label})")
        return entry

    print("no suitable itinerary")
    return None


# ──────────────────────────────────────────────────────────────
# Main matrix
# ──────────────────────────────────────────────────────────────

def main():
    shopping_entries = []
    booking_entries = []

    # ── Shopping: cabin class × trip type ──

    print("\n" + "=" * 60)
    print("SHOPPING: CABIN CLASS × TRIP TYPE")
    print("=" * 60)

    shopping_cabin_matrix = [
        # (origin, dest, date, cabin, country, return_date, fixture_name, fixture_id)
        # Premium economy
        ("JFK", "LHR", "2026-06-20", "premium-economy", None, None,
         "shopping_oneway_premium_economy.json", "shopping_premium_economy_oneway_v1"),
        ("JFK", "LHR", "2026-06-20", "premium-economy", None, "2026-06-27",
         "shopping_roundtrip_premium_economy.json", "shopping_premium_economy_roundtrip_v1"),
        # Business
        ("JFK", "LHR", "2026-06-20", "business", None, None,
         "shopping_oneway_business.json", "shopping_business_oneway_v1"),
        ("JFK", "LHR", "2026-06-20", "business", None, "2026-06-27",
         "shopping_roundtrip_business.json", "shopping_business_roundtrip_v1"),
        # First
        ("JFK", "LHR", "2026-06-20", "first", None, None,
         "shopping_oneway_first.json", "shopping_first_oneway_v1"),
        ("JFK", "LHR", "2026-06-20", "first", None, "2026-06-27",
         "shopping_roundtrip_first.json", "shopping_first_roundtrip_v1"),
        # Economy roundtrip international (gap — existing roundtrips are domestic only)
        ("JFK", "LHR", "2026-06-20", "economy", None, "2026-06-27",
         "shopping_roundtrip_transatlantic.json", "shopping_economy_roundtrip_transatlantic_v1"),
        # Economy roundtrip transpacific
        ("SFO", "NRT", "2026-06-20", "economy", None, "2026-06-27",
         "shopping_roundtrip_transpacific.json", "shopping_economy_roundtrip_transpacific_v1"),
    ]

    for origin, dest, date, cabin, country, return_date, fname, fid in shopping_cabin_matrix:
        entry = record_shopping(origin, dest, date, cabin, country, fname, fid,
                                return_date=return_date)
        if entry:
            shopping_entries.append(entry)

    # ── Booking: cabin class × route type ──

    print("\n" + "=" * 60)
    print("BOOKING: CABIN CLASS × ROUTE TYPE")
    print("=" * 60)

    booking_cabin_matrix = [
        # Premium economy — domestic
        ("JFK", "LAX", "2026-06-20", "premium-economy", "booking_premium_economy_domestic.txt",
         "booking_premium_economy_domestic_v1", ["DL", "AA", "UA"], False),
        # Premium economy — international
        ("JFK", "LHR", "2026-06-20", "premium-economy", "booking_premium_economy_intl.txt",
         "booking_premium_economy_intl_v1", None, False),
        # Business — domestic
        ("JFK", "LAX", "2026-06-20", "business", "booking_business_domestic.txt",
         "booking_business_domestic_v1", ["DL", "AA", "UA"], False),
        # Business — transatlantic
        ("JFK", "LHR", "2026-06-20", "business", "booking_business_transatlantic.txt",
         "booking_business_transatlantic_v1", None, False),
        # Business — transpacific
        ("LAX", "NRT", "2026-06-20", "business", "booking_business_transpacific.txt",
         "booking_business_transpacific_v1", None, False),
        # First — international
        ("JFK", "LHR", "2026-06-20", "first", "booking_first_intl.txt",
         "booking_first_intl_v1", None, False),
    ]

    for origin, dest, date, cabin, fname, fid, airlines, mixed in booking_cabin_matrix:
        entry = record_booking(origin, dest, date, cabin, fname, fid,
                               target_airlines=airlines, require_mixed_cabins=mixed)
        if entry:
            booking_entries.append(entry)

    # ── Booking: MIXED CABIN scenarios (the bug case!) ──

    print("\n" + "=" * 60)
    print("BOOKING: MIXED CABIN SCENARIOS")
    print("=" * 60)

    mixed_cabin_matrix = [
        # Business search returning premium economy options
        ("JFK", "LHR", "2026-06-20", "business", "booking_mixed_business_premium.txt",
         "booking_mixed_business_premium_v1", None, True),
        # First search returning business options
        ("JFK", "LHR", "2026-06-20", "first", "booking_mixed_first_business.txt",
         "booking_mixed_first_business_v1", None, True),
        # Business domestic — mixed with economy
        ("JFK", "LAX", "2026-06-20", "business", "booking_mixed_business_economy_domestic.txt",
         "booking_mixed_business_economy_domestic_v1", None, True),
        # Business transpacific — mixed
        ("LAX", "NRT", "2026-06-20", "business", "booking_mixed_business_transpacific.txt",
         "booking_mixed_business_transpacific_v1", None, True),
    ]

    for origin, dest, date, cabin, fname, fid, airlines, mixed in mixed_cabin_matrix:
        entry = record_booking(origin, dest, date, cabin, fname, fid,
                               target_airlines=airlines, require_mixed_cabins=mixed)
        if entry:
            booking_entries.append(entry)

    # ── Summary ──

    print(f"\n\n{'='*60}")
    print(f"RECORDED: {len(shopping_entries)} shopping + {len(booking_entries)} booking")
    print(f"{'='*60}")

    print("\n--- NEW SHOPPING ENTRIES ---")
    for entry in shopping_entries:
        print(json.dumps(entry, indent=2) + ",")

    print("\n--- NEW BOOKING ENTRIES ---")
    for entry in booking_entries:
        print(json.dumps(entry, indent=2) + ",")


if __name__ == "__main__":
    main()

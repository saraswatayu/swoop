#!/usr/bin/env python3
"""Build cabin brand mapping from Google Flights booking data.

Optimized search-then-book pipeline: economy searches discover airlines,
then a single booking call per airline collects brands across ALL cabins.

~182 RPC calls in ~5 min, covering 100-150 airlines (vs. old brute-force
approach of 800 calls in 20 min for 13 airlines).

Usage:
    python scripts/build_cabin_mapping.py                    # full run
    python scripts/build_cabin_mapping.py --resume           # resume from checkpoint
    python scripts/build_cabin_mapping.py --dry-run          # print route plan
    python scripts/build_cabin_mapping.py --delay 2.0        # slower rate limiting
    python scripts/build_cabin_mapping.py --tier 1           # only Tier 1 routes
    python scripts/build_cabin_mapping.py --airlines AA,DL   # only these airlines
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from statistics import median, mode

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SEAT_TYPE_TO_CABIN: dict[int, str] = {
    1: "economy",
    2: "economy",
    3: "premium-economy",
    4: "business",
    5: "business",
    6: "business",
    9: "business",
}

# Airlines known to offer premium economy
PREMIUM_ECONOMY_AIRLINES = {
    "AA", "DL", "UA", "BA", "AF", "LH", "KL", "SQ", "CX", "QF",
    "NH", "JL", "KE", "OZ", "TG", "BR", "CI", "QR", "EK", "EY",
    "TK", "VS", "AZ", "IB", "TP", "SK", "AY", "NZ", "LA", "AC",
}

# Airlines known to offer first class
FIRST_CLASS_AIRLINES = {
    "AA", "EK", "SQ", "LH", "NH", "JL", "QR", "BA", "CX", "EY",
    "TK", "KE", "AF", "GA",
}

# Tier 1: 25 mega-hub routes (~80 airlines expected)
TIER_1_ROUTES: list[tuple[str, str]] = [
    # US domestic
    ("JFK", "LAX"), ("JFK", "SFO"), ("ORD", "LAX"), ("ATL", "LAX"),
    ("DFW", "JFK"), ("SEA", "JFK"), ("DEN", "JFK"),
    # Transatlantic
    ("JFK", "LHR"), ("JFK", "CDG"), ("JFK", "FRA"), ("JFK", "AMS"),
    ("JFK", "MAD"), ("JFK", "FCO"), ("JFK", "IST"),
    # Gulf
    ("JFK", "DXB"), ("JFK", "DOH"), ("JFK", "AUH"),
    # Asia-Pacific
    ("LAX", "NRT"), ("LAX", "ICN"), ("LAX", "SIN"), ("SFO", "HKG"),
    ("LAX", "TPE"), ("SFO", "SYD"),
    # LatAm/Other
    ("MIA", "GRU"), ("YYZ", "LHR"),
]

# Tier 2: 15 regional fill routes (~20 more airlines)
TIER_2_ROUTES: list[tuple[str, str]] = [
    ("JFK", "OSL"), ("LAX", "AKL"), ("BOS", "KEF"), ("JFK", "LIS"),
    ("MIA", "BOG"), ("MIA", "PTY"), ("LAX", "MEX"), ("JFK", "HEL"),
    ("JFK", "DUB"), ("LAX", "BKK"), ("SFO", "MNL"), ("LAX", "HNL"),
    ("MSP", "PHX"), ("DFW", "CUN"), ("ORD", "HND"),
]

# Likely routes for targeted airline searches (Tier 3)
AIRLINE_ROUTE_HINTS: dict[str, tuple[str, str]] = {
    # US majors (usually found in Tier 1, but needed for --airlines)
    "AA": ("JFK", "LAX"),   # American
    "DL": ("JFK", "LAX"),   # Delta
    "UA": ("JFK", "SFO"),   # United
    "B6": ("JFK", "LAX"),   # JetBlue
    "WN": ("LAX", "LAS"),   # Southwest
    "NK": ("FLL", "LAX"),   # Spirit
    "F9": ("DEN", "LAX"),   # Frontier
    "G4": ("LAS", "LAX"),   # Allegiant
    "SY": ("MSP", "FLL"),   # Sun Country
    "HA": ("LAX", "HNL"),   # Hawaiian
    "AS": ("SEA", "LAX"),   # Alaska
    "WS": ("YYC", "YYZ"),   # WestJet
    "AC": ("YYZ", "LHR"),   # Air Canada
    "AM": ("MEX", "LAX"),   # Aeromexico
    "CM": ("PTY", "MIA"),   # Copa
    "AV": ("BOG", "MIA"),   # Avianca
    "LA": ("SCL", "MIA"),   # LATAM
    "AR": ("EZE", "MIA"),   # Aerolineas Argentinas
    "TP": ("LIS", "JFK"),   # TAP
    "AY": ("HEL", "JFK"),   # Finnair
    "SK": ("CPH", "JFK"),   # SAS
    "LO": ("WAW", "JFK"),   # LOT
    "OS": ("VIE", "JFK"),   # Austrian
    "LX": ("ZRH", "JFK"),   # Swiss
    "SN": ("BRU", "JFK"),   # Brussels Airlines
    "EI": ("DUB", "JFK"),   # Aer Lingus
    "IB": ("MAD", "JFK"),   # Iberia
    "AZ": ("FCO", "JFK"),   # ITA Airways
    "FI": ("KEF", "JFK"),   # Icelandair
    "DY": ("OSL", "JFK"),   # Norwegian
    "EW": ("FRA", "JFK"),   # Eurowings
    "GA": ("CGK", "NRT"),   # Garuda
    "MH": ("KUL", "NRT"),   # Malaysia Airlines
    "PR": ("MNL", "LAX"),   # Philippine Airlines
    "VN": ("SGN", "NRT"),   # Vietnam Airlines
    "AI": ("DEL", "JFK"),   # Air India
    "UL": ("CMB", "NRT"),   # SriLankan
    "ET": ("ADD", "JFK"),   # Ethiopian
    "SA": ("JNB", "JFK"),   # South African
    "MS": ("CAI", "JFK"),   # EgyptAir
    "RJ": ("AMM", "JFK"),   # Royal Jordanian
    "SV": ("JED", "JFK"),   # Saudia
    "WY": ("MCT", "JFK"),   # Oman Air
    "GF": ("BAH", "LHR"),   # Gulf Air
    "NZ": ("AKL", "LAX"),   # Air New Zealand
}

CHECKPOINT_PATH = Path(__file__).parent / ".cabin_mapping_checkpoint.jsonl"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "swoop" / "cabin_brands.json"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class BrandObservation:
    airline_iata: str
    brand_code: str
    brand_label: str
    option_price: int
    base_price: int
    is_basic: bool
    seat_types: list[int | None]
    route: str
    cabin_searched: str = "economy"  # always economy in the new pipeline
    price_ratio: float = 0.0


# ---------------------------------------------------------------------------
# Search date helper
# ---------------------------------------------------------------------------


def _search_date() -> str:
    """Return a search date ~30 days from now (weekday)."""
    target = date.today() + timedelta(days=30)
    while target.weekday() >= 5:
        target += timedelta(days=1)
    return target.isoformat()


# ---------------------------------------------------------------------------
# Discovery + immediate booking
# ---------------------------------------------------------------------------


def _book_airline(
    itin,
    airline_code: str,
    route: str,
    *,
    delay: float,
) -> list[BrandObservation]:
    """Book a single itinerary and return brand observations."""
    from swoop.rpc import get_booking_results

    time.sleep(delay)

    base_price = itin.price or 0
    seat_types = [f.seat_type for f in itin.flights]

    try:
        options = get_booking_results(itin, cabin="economy")
    except Exception as exc:
        logger.debug("Booking failed for %s on %s: %s", airline_code, route, exc)
        return []

    observations: list[BrandObservation] = []
    for opt in options:
        price = opt.price
        brand_code = opt.brand_code
        brand_label = opt.brand_label
        is_basic = opt.is_basic
        ratio = price / base_price if base_price > 0 else 0.0

        observations.append(BrandObservation(
            airline_iata=airline_code,
            brand_code=brand_code,
            brand_label=brand_label,
            option_price=price,
            base_price=base_price,
            is_basic=is_basic,
            seat_types=seat_types,
            route=route,
            price_ratio=round(ratio, 4),
        ))

    return observations


def _discover_and_book_route(
    origin: str,
    dest: str,
    search_dt: str,
    seen_airlines: set[str],
    all_observations: list[BrandObservation],
    *,
    delay: float,
    airlines_filter: list[str] | None = None,
) -> int:
    """Search one route, then immediately book each new airline discovered.

    Returns number of new airlines booked.
    """
    from swoop.rpc import search_raw

    route = f"{origin}-{dest}"
    new_booked = 0

    try:
        result = search_raw(
            origin, dest, search_dt,
            cabin="economy",
            airlines=airlines_filter,
        )
    except Exception as exc:
        logger.warning("Search failed for %s: %s", route, exc)
        return 0

    if result is None:
        return 0

    itineraries = [*result.best, *result.other]
    if not itineraries:
        return 0

    # Group itineraries by airline, keep first with booking token
    airline_itins: dict[str, object] = {}
    for itin in itineraries:
        code = itin.airline_code
        if (
            code
            and code not in seen_airlines
            and code not in airline_itins
            and itin.booking_token
        ):
            airline_itins[code] = itin

    for airline_code, itin in airline_itins.items():
        if airline_code in seen_airlines:
            continue

        logger.info("  Booking %s (from %s)...", airline_code, route)
        obs = _book_airline(itin, airline_code, route, delay=delay)

        if obs:
            _save_checkpoint(obs, airline_code, route)
            all_observations.extend(obs)
            seen_airlines.add(airline_code)
            new_booked += 1
            logger.info("    → %d brands for %s", len(obs), airline_code)
        else:
            # Retry with fresh search filtered to this airline
            logger.debug("    Empty booking for %s, retrying with airline filter", airline_code)
            obs = _retry_booking(origin, dest, search_dt, airline_code, route, delay=delay)
            if obs:
                _save_checkpoint(obs, airline_code, route)
                all_observations.extend(obs)
                seen_airlines.add(airline_code)
                new_booked += 1
                logger.info("    → %d brands for %s (retry)", len(obs), airline_code)
            else:
                logger.info("    → no brands for %s (skipped)", airline_code)
                seen_airlines.add(airline_code)  # don't retry again

    return new_booked


def _retry_booking(
    origin: str,
    dest: str,
    search_dt: str,
    airline_code: str,
    route: str,
    *,
    delay: float,
) -> list[BrandObservation]:
    """Re-search with airline filter and try booking again."""
    from swoop.rpc import search_raw

    time.sleep(delay)

    try:
        fresh = search_raw(
            origin, dest, search_dt,
            cabin="economy",
            airlines=[airline_code],
        )
    except Exception:
        return []

    if fresh is None:
        return []

    for itin in [*fresh.best, *fresh.other]:
        if itin.booking_token:
            return _book_airline(itin, airline_code, route, delay=delay)

    return []


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _cabin_from_brand_name(brand_code: str) -> str | None:
    """Infer cabin from brand name patterns. Returns None if ambiguous.

    Mirrors the pattern-matching layer in _booking._classify_cabin_bucket
    to correct misclassifications from price-ratio-only analysis.
    Checked highest-cabin-first so "PREMIUM ECONOMY" matches before "ECONOMY".
    """
    h = brand_code.upper().strip()
    if not h:
        return None

    # Named business suites BEFORE generic "SUITE" → first
    if any(t in h for t in (
        "CLUB SUITE", "QSUITE", "Q SUITE", "PRESTIGE SUITE",
        "SKY SUITE", "THE ROOM",
    )):
        return "business"
    if any(t in h for t in ("FIRST", "LA PREMIERE", "SUITE")):
        return "first"
    if any(t in h for t in (
        "BUSINESS", "DELTA ONE", "POLARIS", "UPPER CLASS", "MINT",
        "CLUB WORLD", "PRESTIGE",
    )):
        return "business"
    if any(t in h for t in (
        "PREMIUM ECONOMY", "PREMIUM SELECT", "PREMIUM PLUS",
        "PREM ECON", "WORLD TRAVELLER PLUS",
    )):
        return "premium-economy"
    # Economy brands — prevents seat_type override from misclassifying
    if any(t in h for t in (
        "BASIC", "MAIN CABIN", "MAIN CLASSIC", "ECONOMY", "COMFORT",
        "COACH", "STANDARD", "SAVER", "VALUE", "BLUE", "EVEN MORE",
    )):
        return "economy"
    return None


def classify_brand(
    observations: list[BrandObservation],
    brand_code: str = "",
) -> tuple[str, float]:
    """Classify a brand from its observations. Returns (cabin, confidence).

    Uses a 3-signal approach:
    1. Brand name pattern matching (strongest — unambiguous cabin keywords)
    2. seat_type consensus from near-base observations
    3. Price ratio closest to 1.0 relative to searched cabin
    """
    if not observations:
        return "unknown", 0.0

    # Signal 1 (strongest): brand name indicates cabin unambiguously
    name_cabin = _cabin_from_brand_name(brand_code)
    if name_cabin is not None:
        return name_cabin, 0.95

    # Signal 2: Price ratio analysis
    cabin_ratios: dict[str, list[float]] = defaultdict(list)
    for obs in observations:
        cabin_ratios[obs.cabin_searched].append(obs.price_ratio)

    best_cabin: str | None = None
    best_distance = float("inf")
    for cabin, ratios in cabin_ratios.items():
        med = median(ratios)
        dist = abs(med - 1.0)
        if dist < best_distance:
            best_distance = dist
            best_cabin = cabin

    # Signal 3: seat_type consensus from near-base observations
    near_base = [o for o in observations if 0.8 <= o.price_ratio <= 1.3]
    seat_types = [st for o in near_base for st in o.seat_types if st is not None]
    if seat_types:
        try:
            seat_cabin = SEAT_TYPE_TO_CABIN.get(mode(seat_types))
        except Exception:
            seat_cabin = None
        if seat_cabin and seat_cabin != best_cabin:
            best_cabin = seat_cabin  # seat_type is a stronger signal

    confidence = 1.0 - min(best_distance, 1.0)
    return best_cabin or "unknown", round(confidence, 2)


# ---------------------------------------------------------------------------
# Checkpoint helpers
# ---------------------------------------------------------------------------


def _load_checkpoint() -> tuple[list[BrandObservation], set[str]]:
    """Load observations and completed airlines from checkpoint file."""
    if not CHECKPOINT_PATH.exists():
        return [], set()

    observations: list[BrandObservation] = []
    seen_airlines: set[str] = set()

    for line in CHECKPOINT_PATH.read_text().splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue

        if data.get("type") == "discovery":
            seen_airlines.add(data["airline"])
        elif data.get("type") == "brand":
            # Remove checkpoint metadata before creating BrandObservation
            brand_data = {k: v for k, v in data.items() if k != "type"}
            try:
                observations.append(BrandObservation(**brand_data))
            except TypeError:
                continue

    return observations, seen_airlines


def _save_checkpoint(
    observations: list[BrandObservation],
    airline_code: str,
    route: str,
) -> None:
    """Append observations + discovery record to checkpoint file."""
    with CHECKPOINT_PATH.open("a") as f:
        # Write discovery record first
        f.write(json.dumps({
            "type": "discovery",
            "airline": airline_code,
            "route": route,
            "brands_collected": len(observations),
        }) + "\n")
        # Write brand observations
        for obs in observations:
            record = asdict(obs)
            record["type"] = "brand"
            f.write(json.dumps(record) + "\n")


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def build_mapping(observations: list[BrandObservation]) -> dict:
    """Build the cabin_brands.json structure from observations."""
    by_brand: dict[str, list[BrandObservation]] = defaultdict(list)
    for obs in observations:
        key = obs.brand_code.upper().strip()
        if key:
            by_brand[key].append(obs)

    brands: dict[str, dict] = {}
    unmapped: list[str] = []

    for brand_key, obs_list in sorted(by_brand.items()):
        cabin, confidence = classify_brand(obs_list, brand_code=brand_key)
        airlines = sorted({o.airline_iata for o in obs_list if o.airline_iata})
        if cabin == "unknown":
            unmapped.append(brand_key)
        else:
            brands[brand_key] = {
                "cabin": cabin,
                "confidence": confidence,
                "observations": len(obs_list),
                "airlines": airlines,
            }

    return {
        "version": date.today().isoformat(),
        "brands": brands,
        "unmapped": sorted(unmapped),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build cabin brand mapping from Google Flights data",
    )
    parser.add_argument("--resume", action="store_true",
                        help="Resume from checkpoint")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print plan without making API calls")
    parser.add_argument("--delay", type=float, default=1.5,
                        help="Delay between RPC calls in seconds (default: 1.5)")
    parser.add_argument("--tier", type=int, default=None, choices=[1, 2, 3],
                        help="Only run specific tier (1, 2, or 3)")
    parser.add_argument("--airlines", type=str, default=None,
                        help="Comma-separated airlines to target (e.g., AA,DL)")
    parser.add_argument("--output", type=str, default=str(OUTPUT_PATH),
                        help=f"Output path (default: {OUTPUT_PATH})")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    # Determine which tiers to run
    run_tier_1 = args.tier is None or args.tier == 1
    run_tier_2 = args.tier is None or args.tier == 2
    run_tier_3 = args.tier is None or args.tier == 3

    routes_tier_1 = TIER_1_ROUTES if run_tier_1 else []
    routes_tier_2 = TIER_2_ROUTES if run_tier_2 else []

    # If --airlines specified, only run targeted searches (Tier 3)
    target_airlines: set[str] | None = None
    if args.airlines:
        target_airlines = {a.strip().upper() for a in args.airlines.split(",")}
        routes_tier_1 = []
        routes_tier_2 = []
        run_tier_3 = True

    search_dt = _search_date()

    # Estimate RPC budget
    tier_1_calls = len(routes_tier_1)
    tier_2_calls = len(routes_tier_2)
    # Booking calls estimated at ~3 airlines per route + 1 booking each
    est_booking = (tier_1_calls + tier_2_calls) * 3
    est_tier_3 = len(target_airlines) if target_airlines else 10
    est_total = tier_1_calls + tier_2_calls + est_booking + est_tier_3
    est_time_min = (est_total * args.delay) / 60

    logger.info("Plan: Tier 1=%d routes, Tier 2=%d routes, Tier 3=targeted",
                len(routes_tier_1), len(routes_tier_2))
    logger.info("Estimated RPC calls: ~%d, time: ~%.1f min (at %.1fs delay)",
                est_total, est_time_min, args.delay)
    logger.info("Search date: %s", search_dt)

    if args.dry_run:
        if routes_tier_1:
            print("\nTier 1 — Mega-hub routes:")
            for origin, dest in routes_tier_1:
                print(f"  {origin}-{dest}")
        if routes_tier_2:
            print("\nTier 2 — Regional fill routes:")
            for origin, dest in routes_tier_2:
                print(f"  {origin}-{dest}")
        if run_tier_3:
            print("\nTier 3 — Targeted airline searches:")
            if target_airlines:
                for a in sorted(target_airlines):
                    route = AIRLINE_ROUTE_HINTS.get(a, ("???", "???"))
                    print(f"  {a}: {route[0]}-{route[1]}")
            else:
                print("  (stragglers not found in Tiers 1-2)")
        logger.info("Dry run complete. No API calls made.")
        return

    # Load checkpoint
    all_observations: list[BrandObservation] = []
    seen_airlines: set[str] = set()

    if args.resume:
        all_observations, seen_airlines = _load_checkpoint()
        logger.info("Resumed: %d observations, %d airlines done (%s)",
                     len(all_observations), len(seen_airlines),
                     ", ".join(sorted(seen_airlines)))

    # --- Tier 1: Mega-hub discovery ---
    if routes_tier_1:
        logger.info("=== Tier 1: %d mega-hub routes ===", len(routes_tier_1))
        for i, (origin, dest) in enumerate(routes_tier_1, 1):
            logger.info("[T1 %d/%d] %s-%s (seen: %d airlines)",
                        i, len(routes_tier_1), origin, dest, len(seen_airlines))
            _discover_and_book_route(
                origin, dest, search_dt, seen_airlines, all_observations,
                delay=args.delay,
            )
            time.sleep(args.delay)

    # --- Tier 2: Regional fill ---
    if routes_tier_2:
        logger.info("=== Tier 2: %d regional routes ===", len(routes_tier_2))
        for i, (origin, dest) in enumerate(routes_tier_2, 1):
            logger.info("[T2 %d/%d] %s-%s (seen: %d airlines)",
                        i, len(routes_tier_2), origin, dest, len(seen_airlines))
            _discover_and_book_route(
                origin, dest, search_dt, seen_airlines, all_observations,
                delay=args.delay,
            )
            time.sleep(args.delay)

    # --- Tier 3: Targeted airline searches for stragglers ---
    if run_tier_3:
        if target_airlines:
            missing = target_airlines - seen_airlines
        else:
            missing = set(AIRLINE_ROUTE_HINTS.keys()) - seen_airlines

        if missing:
            logger.info("=== Tier 3: %d targeted airline searches ===", len(missing))
            for i, airline in enumerate(sorted(missing), 1):
                route_hint = AIRLINE_ROUTE_HINTS.get(airline, ("JFK", "LHR"))
                origin, dest = route_hint
                logger.info("[T3 %d/%d] %s via %s-%s",
                            i, len(missing), airline, origin, dest)

                _discover_and_book_route(
                    origin, dest, search_dt, seen_airlines, all_observations,
                    delay=args.delay,
                    airlines_filter=[airline],
                )
                time.sleep(args.delay)

    # Build and write output
    mapping = build_mapping(all_observations)
    output_path = Path(args.output)
    output_path.write_text(json.dumps(mapping, indent=2) + "\n")

    # Summary
    n_brands = len(mapping["brands"])
    n_unmapped = len(mapping["unmapped"])
    brand_airlines = set()
    for v in mapping["brands"].values():
        brand_airlines.update(v.get("airlines", []))

    logger.info("Done! %d brands, %d airlines, %d unmapped → %s",
                n_brands, len(brand_airlines), n_unmapped, output_path)


if __name__ == "__main__":
    main()

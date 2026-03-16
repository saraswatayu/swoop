#!/usr/bin/env python3
"""Build cabin brand mapping from Google Flights booking data.

Searches diverse routes, collects booking option brands, and classifies each
brand into a cabin class using price ratio + seat_type signals from shopping
results.

Usage:
    python scripts/build_cabin_mapping.py                          # full run
    python scripts/build_cabin_mapping.py --routes JFK-LAX,JFK-LHR # specific routes
    python scripts/build_cabin_mapping.py --cabins economy,business # specific cabins
    python scripts/build_cabin_mapping.py --resume                  # resume from checkpoint
    python scripts/build_cabin_mapping.py --dry-run                 # print plan, no API calls
    python scripts/build_cabin_mapping.py --delay 2.0               # slower rate limiting
    python scripts/build_cabin_mapping.py --max-itins 5             # more itineraries per search
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

CABINS = ["economy", "premium-economy", "business", "first"]

# Route matrix targeting 40+ airlines across all cabin classes
ROUTES: list[tuple[str, str]] = [
    # US Domestic (14)
    ("JFK", "LAX"), ("JFK", "SEA"), ("ORD", "DEN"), ("DFW", "MIA"),
    ("MSP", "PHX"), ("LAX", "HNL"), ("DEN", "LAS"), ("ATL", "FLL"),
    ("BOS", "SFO"), ("SEA", "SFO"), ("JFK", "ORD"), ("MCO", "EWR"),
    ("YYZ", "YVR"), ("LAX", "SFO"),
    # Transatlantic (10)
    ("JFK", "LHR"), ("EWR", "FRA"), ("JFK", "CDG"), ("JFK", "AMS"),
    ("ORD", "ZRH"), ("JFK", "MAD"), ("JFK", "DUB"), ("LAX", "CPH"),
    ("JFK", "HEL"), ("JFK", "LIS"),
    # Middle East (4)
    ("JFK", "DOH"), ("JFK", "DXB"), ("JFK", "AUH"), ("JFK", "IST"),
    # Asia-Pacific (12)
    ("LAX", "NRT"), ("LAX", "HKG"), ("LAX", "SIN"), ("LAX", "ICN"),
    ("LAX", "TPE"), ("SFO", "MNL"), ("LAX", "BKK"), ("SFO", "SYD"),
    ("LAX", "AKL"), ("ORD", "HND"), ("SFO", "NRT"), ("SFO", "ICN"),
    # Latin America (6)
    ("MIA", "GRU"), ("MIA", "BOG"), ("MIA", "PTY"), ("JFK", "MEX"),
    ("LAX", "LIM"), ("DFW", "CUN"),
    # Other (4)
    ("JFK", "OSL"), ("LAX", "MEL"), ("YYZ", "LHR"), ("BOS", "KEF"),
]

CHECKPOINT_PATH = Path(__file__).parent / ".cabin_mapping_checkpoint.jsonl"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "swoop" / "cabin_brands.json"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class BrandObservation:
    route: str
    cabin_searched: str
    airline_iata: str
    brand_code: str
    brand_label: str
    option_price: int
    base_price: int
    price_ratio: float
    seat_types: list[int | None]
    is_basic: bool


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------


def _search_date() -> str:
    """Return a search date ~30 days from now (weekday)."""
    target = date.today() + timedelta(days=30)
    # Avoid weekends for better price signals
    while target.weekday() >= 5:
        target += timedelta(days=1)
    return target.isoformat()


def _collect_observations_for_route(
    origin: str,
    dest: str,
    cabin: str,
    search_date: str,
    *,
    max_itins: int = 3,
    delay: float = 1.5,
) -> list[BrandObservation]:
    """Search one route+cabin, fetch booking options, return observations."""
    # Late imports to avoid requiring swoop installation for --dry-run
    from swoop.rpc import search_raw, get_trip_booking_results
    from swoop._selection import _build_selected_legs
    from swoop.exceptions import SwoopHTTPError, SwoopParseError, SwoopRateLimitError

    observations: list[BrandObservation] = []
    route_key = f"{origin}-{dest}"

    try:
        result = search_raw(origin, dest, search_date, cabin=cabin)
    except (SwoopHTTPError, SwoopRateLimitError) as exc:
        logger.warning("Search failed for %s/%s: %s", route_key, cabin, exc)
        return observations

    if result is None:
        return observations

    itineraries = [*result.best, *result.other]
    if not itineraries:
        return observations

    # Sort by price for consistent sampling
    priced = sorted(
        [it for it in itineraries if it.price is not None and it.price > 0],
        key=lambda it: it.price,
    )
    if not priced:
        return observations

    for itin in priced[:max_itins]:
        time.sleep(delay)

        token = itin.booking_token
        if not token:
            continue

        base_price = itin.price or 0
        seat_types = [f.seat_type for f in itin.flights]

        selected_legs = _build_selected_legs(itin)
        if not selected_legs:
            continue

        # Build minimal leg for booking request
        legs = [{
            "origin": origin,
            "destination": dest,
            "date": search_date,
            "selected_legs": selected_legs,
        }]

        try:
            booking_options = get_trip_booking_results(
                token, legs, cabin=cabin, adults=1,
            )
        except (SwoopHTTPError, SwoopParseError, SwoopRateLimitError) as exc:
            logger.debug("Booking failed for %s/%s itin: %s", route_key, cabin, exc)
            continue

        airline_iata = itin.airline_code or ""
        for opt in booking_options:
            price = opt.price if hasattr(opt, "price") else opt.get("price", 0)
            brand_code = opt.brand_code if hasattr(opt, "brand_code") else opt.get("brand_code", "")
            brand_label = opt.brand_label if hasattr(opt, "brand_label") else opt.get("brand_label", "")
            is_basic = opt.is_basic if hasattr(opt, "is_basic") else opt.get("is_basic", False)

            ratio = price / base_price if base_price > 0 else 0.0
            observations.append(BrandObservation(
                route=route_key,
                cabin_searched=cabin,
                airline_iata=airline_iata,
                brand_code=brand_code,
                brand_label=brand_label,
                option_price=price,
                base_price=base_price,
                price_ratio=round(ratio, 4),
                seat_types=seat_types,
                is_basic=is_basic,
            ))

    return observations


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify_brand(observations: list[BrandObservation]) -> tuple[str, float]:
    """Classify a brand from its observations. Returns (cabin, confidence)."""
    if not observations:
        return "unknown", 0.0

    # Signal 1: Which searched cabin gives price_ratio closest to 1.0?
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

    # Signal 2: seat_type consensus from near-base observations
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


def _load_checkpoint() -> list[BrandObservation]:
    """Load observations from checkpoint file."""
    if not CHECKPOINT_PATH.exists():
        return []
    observations: list[BrandObservation] = []
    for line in CHECKPOINT_PATH.read_text().splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            observations.append(BrandObservation(**data))
        except (json.JSONDecodeError, TypeError):
            continue
    return observations


def _save_checkpoint(observations: list[BrandObservation]) -> None:
    """Append observations to checkpoint file."""
    with CHECKPOINT_PATH.open("a") as f:
        for obs in observations:
            f.write(json.dumps(asdict(obs)) + "\n")


def _completed_route_cabins(observations: list[BrandObservation]) -> set[tuple[str, str]]:
    """Return set of (route, cabin) pairs already collected."""
    return {(obs.route, obs.cabin_searched) for obs in observations}


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
        cabin, confidence = classify_brand(obs_list)
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
    parser.add_argument("--routes", type=str, default=None,
                        help="Comma-separated routes (e.g., JFK-LAX,JFK-LHR)")
    parser.add_argument("--cabins", type=str, default=None,
                        help="Comma-separated cabins (e.g., economy,business)")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from checkpoint")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print plan without making API calls")
    parser.add_argument("--delay", type=float, default=1.5,
                        help="Delay between RPC calls in seconds (default: 1.5)")
    parser.add_argument("--max-itins", type=int, default=3,
                        help="Max itineraries to fetch booking for per search (default: 3)")
    parser.add_argument("--output", type=str, default=str(OUTPUT_PATH),
                        help=f"Output path (default: {OUTPUT_PATH})")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    # Parse routes
    if args.routes:
        routes = []
        for r in args.routes.split(","):
            parts = r.strip().split("-")
            if len(parts) == 2:
                routes.append((parts[0].upper(), parts[1].upper()))
            else:
                logger.error("Invalid route format: %s (expected ORIGIN-DEST)", r)
                sys.exit(1)
    else:
        routes = ROUTES

    # Parse cabins
    cabins = args.cabins.split(",") if args.cabins else CABINS

    search_dt = _search_date()
    total_pairs = len(routes) * len(cabins)
    est_calls = total_pairs * (1 + args.max_itins)
    est_time_min = (est_calls * args.delay) / 60

    logger.info("Plan: %d routes × %d cabins = %d pairs", len(routes), len(cabins), total_pairs)
    logger.info("Estimated RPC calls: ~%d, time: ~%.0f min (at %.1fs delay)",
                est_calls, est_time_min, args.delay)
    logger.info("Search date: %s", search_dt)

    if args.dry_run:
        for origin, dest in routes:
            for cabin in cabins:
                print(f"  {origin}-{dest} / {cabin}")
        logger.info("Dry run complete. No API calls made.")
        return

    # Load checkpoint
    all_observations: list[BrandObservation] = []
    if args.resume:
        all_observations = _load_checkpoint()
        logger.info("Resumed %d observations from checkpoint", len(all_observations))

    completed = _completed_route_cabins(all_observations)
    remaining = [
        (origin, dest, cabin)
        for origin, dest in routes
        for cabin in cabins
        if (f"{origin}-{dest}", cabin) not in completed
    ]
    logger.info("Remaining: %d / %d pairs", len(remaining), total_pairs)

    for i, (origin, dest, cabin) in enumerate(remaining, 1):
        route_key = f"{origin}-{dest}"
        logger.info("[%d/%d] %s / %s", i, len(remaining), route_key, cabin)

        new_obs = _collect_observations_for_route(
            origin, dest, cabin, search_dt,
            max_itins=args.max_itins,
            delay=args.delay,
        )

        if new_obs:
            _save_checkpoint(new_obs)
            all_observations.extend(new_obs)
            logger.info("  → %d observations collected", len(new_obs))
        else:
            logger.info("  → no observations")

        time.sleep(args.delay)

    # Build and write output
    mapping = build_mapping(all_observations)
    output_path = Path(args.output)
    output_path.write_text(json.dumps(mapping, indent=2) + "\n")
    logger.info("Wrote %d brands to %s (%d unmapped)",
                len(mapping["brands"]), output_path, len(mapping["unmapped"]))


if __name__ == "__main__":
    main()

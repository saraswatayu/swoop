#!/usr/bin/env python3
"""Watch a deals exploration query for new deals and price drops.

Run on a cron or systemd timer. Each run hits Google Flights, diffs the
returned deals against the previous run's cached snapshot, and prints
new deals + price changes. Exits early if nothing changed.

Usage:
    # One iteration, default cache (.swoop-deals-cache.json)
    python examples/deals_watcher.py JFK

    # Europe-only summer deals under $700, polled hourly
    python examples/deals_watcher.py JFK \\
        --region europe --max-price 700 \\
        --depart-window 2026-06-01,2026-08-31 \\
        --interval 3600

    # NYC area with per-origin (parallel) fetch
    python examples/deals_watcher.py JFK,LGA,EWR --per-origin

The cache file is single-watcher per the same convention as
price_drop_watcher.py: one cache path, one process. If you want to poll
several queries in parallel, set --cache to a distinct path per process.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Optional

import swoop

DEFAULT_CACHE_PATH = Path(".swoop-deals-cache.json")
DEFAULT_INTERVAL_SECONDS = 60 * 60  # 1 hour
RATE_LIMIT_BACKOFF_SECONDS = 60 * 15  # 15 min extra on 429


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("origin", help="Origin IATA, or comma-separated list (e.g. JFK,LGA,EWR).")
    p.add_argument("--cabin", default="economy", choices=["economy", "premium-economy", "business", "first"])
    p.add_argument("--max-stops", type=int, default=None)
    p.add_argument("--airline", action="append", help="Filter airline IATA (repeatable).")
    p.add_argument("--per-origin", action="store_true", help="Parallel calls per origin.")
    p.add_argument("--depart-window", default=None, help="YYYY-MM-DD,YYYY-MM-DD")
    p.add_argument("--trip-length", default=None, help="MIN-MAX nights (e.g. 5-10)")
    p.add_argument("--destination", action="append", help="Whitelist destination IATA (repeatable).")
    p.add_argument("--exclude-destination", action="append", help="Exclude destination IATA (repeatable).")
    p.add_argument("--region", default=None,
                   choices=["north-america", "caribbean", "latin-america", "europe",
                            "africa", "middle-east", "asia-pacific"])
    p.add_argument("--max-price", type=int, default=None)
    p.add_argument("--min-discount", type=int, default=None)
    p.add_argument("--cache", type=Path, default=DEFAULT_CACHE_PATH,
                   help="JSON cache file path.")
    p.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SECONDS,
                   help="Seconds between polls. 0 = once and exit.")
    p.add_argument("--once", action="store_true", help="Run a single iteration and exit.")
    return p.parse_args()


def _origin_arg(raw: str) -> str | list[str]:
    if "," in raw:
        return [code.strip().upper() for code in raw.split(",") if code.strip()]
    return raw.upper()


def _parse_depart_window(raw: Optional[str]) -> Optional[tuple[str, str]]:
    if not raw:
        return None
    from datetime import date as _date
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 2:
        raise SystemExit("--depart-window must be START,END (YYYY-MM-DD,YYYY-MM-DD)")
    try:
        start = _date.fromisoformat(parts[0])
        end = _date.fromisoformat(parts[1])
    except ValueError as exc:
        raise SystemExit(f"--depart-window dates must be ISO YYYY-MM-DD: {exc}")
    if start > end:
        raise SystemExit(f"--depart-window start ({parts[0]}) is after end ({parts[1]})")
    return (parts[0], parts[1])


def _parse_trip_length(raw: Optional[str]) -> Optional[tuple[int, int]]:
    if not raw:
        return None
    parts = raw.split("-")
    try:
        if len(parts) != 2:
            raise ValueError("must have exactly two parts")
        lo, hi = int(parts[0]), int(parts[1])
    except (IndexError, ValueError) as exc:
        raise SystemExit(f"--trip-length must be MIN-MAX (e.g. 5-10): {exc}")
    if not (0 <= lo <= hi <= 365):
        raise SystemExit(f"--trip-length needs 0 <= MIN <= MAX <= 365 (got {lo}-{hi})")
    return (lo, hi)


def fetch_once(args: argparse.Namespace) -> int:
    """One iteration: fetch, diff, report. Returns the exit code."""
    region = swoop.Region(args.region) if args.region else None
    try:
        result = swoop.deals(
            _origin_arg(args.origin),
            cabin=args.cabin,
            max_stops=args.max_stops,
            airlines=list(args.airline) if args.airline else None,
            per_origin=args.per_origin,
            depart_window=_parse_depart_window(args.depart_window),
            trip_length=_parse_trip_length(args.trip_length),
            destinations=list(args.destination) if args.destination else None,
            exclude_destinations=list(args.exclude_destination) if args.exclude_destination else None,
            region=region,
            max_price=args.max_price,
            min_discount_pct=args.min_discount,
        )
    except swoop.SwoopRateLimitError:
        print(f"[rate-limit] sleeping {RATE_LIMIT_BACKOFF_SECONDS}s extra")
        time.sleep(RATE_LIMIT_BACKOFF_SECONDS)
        return 0

    diff = swoop.watch_deals(result, cache_path=args.cache)

    if not diff.has_changes:
        print(f"[no change] {len(diff.unchanged)} deals unchanged")
        return 0

    if diff.new:
        print(f"\n[NEW] {len(diff.new)} new deal{'s' if len(diff.new) != 1 else ''}:")
        for d in diff.new:
            airline = ", ".join(d.airline_names) or ", ".join(d.airlines) or "—"
            discount = f" ({d.discount_pct}% off)" if d.discount_pct else ""
            currency = d.currency or ""
            print(f"  {d.origin}->{d.destination} {d.destination_city:<25s} "
                  f"{d.departure_date}/{d.return_date or '—'}  "
                  f"{currency}{d.price}{discount}  {airline}")

    if diff.price_changes:
        print(f"\n[PRICE] {len(diff.price_changes)} change{'s' if len(diff.price_changes) != 1 else ''}:")
        for change in diff.price_changes:
            d = change.current
            arrow = "↓" if change.delta < 0 else "↑"
            currency = d.currency or ""
            print(f"  {d.origin}->{d.destination} {d.destination_city:<25s} "
                  f"{currency}{change.prior.price} {arrow} {currency}{d.price} "
                  f"({change.delta_pct:+.0f}%)")

    if diff.gone:
        print(f"\n[GONE] {len(diff.gone)} deal{'s' if len(diff.gone) != 1 else ''} no longer offered")

    return 0


def main() -> int:
    args = parse_args()
    if args.once or args.interval == 0:
        return fetch_once(args)

    print(f"Polling every {args.interval}s. Cache: {args.cache}. Ctrl-C to stop.\n")
    while True:
        fetch_once(args)
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            return 0


if __name__ == "__main__":
    sys.exit(main())

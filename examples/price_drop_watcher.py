#!/usr/bin/env python3
"""Watch a specific flight for price drops over time.

This is the same pattern Perch uses in production to save users an
average of $247 per trip: pin a booked flight, poll its bookable fare
on a schedule, alert when the price drops below the last-seen value.

Usage:
    # One-way, single check (good for testing)
    python examples/price_drop_watcher.py JFK LAX 2026-06-15 DL2300 --once

    # One-way, poll every hour
    python examples/price_drop_watcher.py JFK LAX 2026-06-15 DL2300

    # Roundtrip
    python examples/price_drop_watcher.py JFK LAX 2026-06-15 DL2300 \\
        --return-date 2026-06-22 --return-flight DL2301

Cache lives at .swoop-watch-cache.json in the working directory, keyed
by route + flight + date so multiple watchers coexist.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import swoop

CACHE_PATH = Path(".swoop-watch-cache.json")
DEFAULT_INTERVAL_SECONDS = 60 * 60  # 1 hour
RATE_LIMIT_BACKOFF_SECONDS = 60 * 15  # 15 min extra on 429


def cache_key(args: argparse.Namespace) -> str:
    parts = [args.origin, args.destination, args.date, args.flight_number]
    if args.return_date and args.return_flight:
        parts += [args.return_date, args.return_flight]
    return "|".join(parts)


def load_cache() -> dict[str, int]:
    try:
        return json.loads(CACHE_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_cache(cache: dict[str, int]) -> None:
    CACHE_PATH.write_text(json.dumps(cache, indent=2))


def check_once(args: argparse.Namespace, cache: dict[str, int]) -> None:
    key = cache_key(args)
    last_seen = cache.get(key)

    result = swoop.check_price(
        args.flight_number,
        origin=args.origin,
        destination=args.destination,
        date=args.date,
        return_flight_number=args.return_flight,
        return_date=args.return_date,
    )

    if result is None:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] flight not found")
        return

    current = result.price
    currency = result.currency or "USD"
    label = result.fare_brand or ("basic economy" if result.is_basic_economy else "main")
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {current} {currency} ({label})")

    if last_seen is not None and current < last_seen:
        delta = last_seen - current
        print(f"  PRICE DROP: {last_seen} -> {current} {currency} (saved {delta})")

    if current != last_seen:
        cache[key] = current
        save_cache(cache)


def run_loop(args: argparse.Namespace) -> int:
    cache = load_cache()
    while True:
        try:
            check_once(args, cache)
        except swoop.SwoopRateLimitError:
            wait = RATE_LIMIT_BACKOFF_SECONDS
            print(f"  rate-limited, backing off {wait}s", file=sys.stderr)
            time.sleep(wait)
            continue
        except swoop.SwoopError as exc:
            print(f"  swoop error: {exc}", file=sys.stderr)
        except (OSError, ConnectionError) as exc:
            print(f"  network error: {exc}", file=sys.stderr)

        if args.once:
            return 0
        time.sleep(args.interval)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("origin", help="origin IATA, e.g. JFK")
    parser.add_argument("destination", help="destination IATA, e.g. LAX")
    parser.add_argument("date", help="departure date YYYY-MM-DD")
    parser.add_argument("flight_number", help="outbound flight, e.g. DL2300")
    parser.add_argument("--return-date", dest="return_date", help="return date YYYY-MM-DD")
    parser.add_argument("--return-flight", dest="return_flight", help="return flight, e.g. DL2301")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SECONDS,
                        help=f"seconds between checks (default {DEFAULT_INTERVAL_SECONDS})")
    parser.add_argument("--once", action="store_true", help="run a single check and exit")
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(run_loop(parse_args()))

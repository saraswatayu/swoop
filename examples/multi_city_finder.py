#!/usr/bin/env python3
"""Multi-city / open-jaw flight search using swoop.search_legs().

Pass two or more legs and get the top trip options across the entire
itinerary. Behind the scenes this runs a staged beam search across
Google's official multi-city RPC.

Usage:
    # Three-city trip
    python examples/multi_city_finder.py \\
        --leg JFK LAX 2026-06-15 \\
        --leg LAX SFO 2026-06-18 \\
        --leg SFO SEA 2026-06-21

    # Same trip, nonstop only, wider beam, longer budget
    python examples/multi_city_finder.py \\
        --leg JFK LAX 2026-06-15 \\
        --leg LAX SFO 2026-06-18 \\
        --leg SFO SEA 2026-06-21 \\
        --nonstop --beam-width 25 --time-budget 120 --max-results 20
"""
from __future__ import annotations

import argparse
import sys

import swoop


class LegAction(argparse.Action):
    """Collect repeated --leg ORIGIN DESTINATION DATE triples."""

    def __call__(self, parser, namespace, values, option_string=None):
        legs = getattr(namespace, self.dest, None) or []
        origin, destination, date = values
        legs.append((origin.upper(), destination.upper(), date))
        setattr(namespace, self.dest, legs)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--leg", nargs=3, metavar=("ORIGIN", "DEST", "DATE"),
                        action=LegAction, dest="legs", required=True,
                        help="repeatable: --leg JFK LAX 2026-06-15")
    parser.add_argument("--nonstop", action="store_true", help="restrict every leg to nonstop")
    parser.add_argument("--max-results", type=int, default=None,
                        help="target trip combinations the beam search returns")
    parser.add_argument("--beam-width", type=int, default=None,
                        help="candidate prefixes carried between stages")
    parser.add_argument("--time-budget", type=int, default=None,
                        help="seconds before the beam search stops exploring")
    parser.add_argument("--cabin", default="economy",
                        choices=["economy", "premium-economy", "business", "first"])
    args = parser.parse_args()
    if len(args.legs) < 2:
        parser.error("at least 2 legs are required for multi-city search")
    return args


def format_leg(leg: swoop.TripLeg) -> str:
    it = leg.itinerary
    if it is None:
        return f"  {leg.origin}->{leg.destination} on {leg.date} (no itinerary)"
    airlines = ", ".join(it.airline_names) if it.airline_names else "?"
    stops = "nonstop" if it.stop_count == 0 else f"{it.stop_count} stop{'s' if it.stop_count != 1 else ''}"
    duration_h = it.travel_time // 60 if it.travel_time else 0
    duration_m = it.travel_time % 60 if it.travel_time else 0
    return f"  {leg.origin}->{leg.destination} {leg.date}  {airlines}  {stops}  {duration_h}h{duration_m:02d}m"


def main() -> int:
    args = parse_args()

    max_stops = 0 if args.nonstop else None
    search_legs = [
        swoop.SearchLeg(from_airport=o, to_airport=d, date=date, max_stops=max_stops)
        for (o, d, date) in args.legs
    ]

    try:
        result = swoop.search_legs(
            search_legs,
            cabin=args.cabin,
            max_results=args.max_results,
            beam_width=args.beam_width,
            time_budget=args.time_budget,
        )
    except swoop.SwoopRateLimitError:
        print("rate-limited by Google. try again in a minute or pass a proxy via swoop.set_proxy().", file=sys.stderr)
        return 2
    except swoop.SwoopError as exc:
        print(f"swoop error: {exc}", file=sys.stderr)
        return 1
    except (OSError, ConnectionError) as exc:
        print(f"network error: {exc}", file=sys.stderr)
        return 1

    if not result.results:
        print("no trip options found. try relaxing --nonstop or widening --beam-width.")
        return 0

    currency = result.currency or "USD"
    print(f"top {min(5, len(result.results))} of {len(result.results)} trip options ({currency}):\n")
    for i, option in enumerate(result.results[:5], 1):
        price_str = f"{option.price} {currency}" if option.price is not None else "price n/a"
        print(f"#{i}  {price_str}")
        for leg in option.legs:
            print(format_leg(leg))
        print()

    if not result.is_complete:
        print("note: results incomplete (beam search hit its budget). raise --time-budget for more.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Inspect booking-option seller diversity with swoop.get_booking_results().

Google Flights sells a single itinerary through more than one channel: the
operating airline ("airline-direct") and, for many fares, a list of online
travel agencies (OTAs) such as Expedia, FlightHub, or CheapOair.
``get_booking_results()`` returns one ``BookingOption`` per channel, each with
its own ``seller_code`` / ``seller_name`` and ``booking_url``.

Whether OTAs show up is decided by Google, not swoop:

  * Premium cabins and most major US carriers (AA/DL/UA/B6 ...) are usually
    airline-direct only — you will see one seller, the airline, across
    several fare brands. That is expected, not a bug.
  * International economy on foreign carriers (PR, OZ, CI, BR, LO ...) often
    returns a wall of OTAs alongside the airline. That is where seller
    diversity shows up.

So if every option you get back shares one ``seller_code``, try an
international economy itinerary on a foreign carrier before concluding the
flight has no OTAs.

Usage:
    # Default: SFO->MNL economy ~90 days out (an OTA-rich route)
    python examples/booking_options.py

    # Any route / cabin; --index selects which itinerary (0 = top result)
    python examples/booking_options.py JFK DEL 2026-09-15 --cabin economy
    python examples/booking_options.py JFK LAX 2026-06-15 --cabin first --index 2
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

import swoop


def _default_date() -> str:
    return (date.today() + timedelta(days=90)).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("origin", nargs="?", default="SFO", help="origin IATA code (default SFO)")
    parser.add_argument("destination", nargs="?", default="MNL", help="destination IATA code (default MNL)")
    parser.add_argument("date", nargs="?", default=None, help="YYYY-MM-DD (default ~90 days out)")
    parser.add_argument(
        "--cabin",
        default="economy",
        choices=["economy", "premium-economy", "business", "first"],
        help="cabin class (default economy — where OTAs are most common)",
    )
    parser.add_argument("--index", type=int, default=0, help="which itinerary to price (0 = top result)")
    args = parser.parse_args()

    origin = args.origin.upper()
    destination = args.destination.upper()
    when = args.date or _default_date()

    try:
        result = swoop.search(origin, destination, when, cabin=args.cabin)
    except swoop.SwoopError as exc:
        print(f"search failed: {exc}", file=sys.stderr)
        return 1

    if not result.results:
        print(f"No itineraries for {origin}->{destination} on {when}.", file=sys.stderr)
        return 1

    if args.index >= len(result.results):
        print(f"--index {args.index} out of range ({len(result.results)} results).", file=sys.stderr)
        return 1

    trip = result.results[args.index]
    itinerary = trip.legs[0].itinerary

    try:
        options = swoop.get_booking_results(itinerary, cabin=args.cabin)
    except swoop.SwoopError as exc:
        print(f"booking lookup failed: {exc}", file=sys.stderr)
        return 1

    if not options:
        print("No booking options returned for this itinerary.", file=sys.stderr)
        return 1

    direct = [opt for opt in options if opt.is_airline_direct]
    otas = [opt for opt in options if not opt.is_airline_direct]

    print(f"\n{origin} -> {destination}  {when}  ({args.cabin})")
    print(f"Itinerary #{args.index}: {itinerary.airline_code} from ${trip.price}\n")

    def _row(opt: swoop.BookingOption) -> str:
        seller = opt.seller_name or opt.seller_code or "airline-direct"
        brand = opt.brand_label or opt.fare_family or "—"
        return f"  ${opt.price:<6} {seller:<22} {brand}"

    print(f"Airline-direct ({len(direct)}):")
    for opt in direct or [None]:
        print("  (none)" if opt is None else _row(opt))

    print(f"\nOnline travel agencies ({len(otas)}):")
    for opt in sorted(otas, key=lambda o: o.price) or [None]:
        print("  (none)" if opt is None else _row(opt))

    distinct = sorted({opt.seller_code for opt in options if opt.seller_code})
    print(f"\n{len(options)} options across {len(distinct)} distinct sellers: {', '.join(distinct)}")
    if len(distinct) <= 1:
        print(
            "Only one seller here — this itinerary is airline-direct. Try an "
            "international economy route on a foreign carrier (e.g. SFO MNL) "
            "to see the OTA list.",
        )

    cheapest = min(options, key=lambda o: o.price)
    if cheapest.booking_url:
        seller = cheapest.seller_name or cheapest.seller_code or "the airline"
        print(f"\nCheapest (${cheapest.price}) is via {seller}:\n  {cheapest.booking_url}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

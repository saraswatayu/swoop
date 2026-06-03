#!/usr/bin/env python3
"""Replicate the OTA-seller gating reported in issue #24.

housine35 reported that ``get_booking_results()`` (the unsigned RPC) returns the
full OTA list on some routes but only the airline on others, and claimed the
difference tracks the BotGuard ``X-Goog-BatchExecute-Bgr`` signature:

    SFO->MNL (Philippine Airlines)  unsigned ~15 sellers   (OTA-rich)
    LGA->ATL (Frontier F9)          unsigned 2  / signed 7
    TLS->ALG (Transavia TO)         unsigned 1  / signed 15

swoop has NO signature path, so this script can only exercise the *unsigned*
side. It searches each route, picks the itinerary on the target carrier, calls
``get_booking_results()``, and prints the distinct seller count + the raw
response size (housine35 measured ~18 KB with 2 rows on the gated Frontier case).

Run live:  python scripts/replicate_ota_gating.py
"""
from __future__ import annotations

from dataclasses import dataclass

import swoop
from swoop.rpc import (
    BOOKING_RPC_URL,
    SORT_DEPARTURE_TIME,
    CabinClass,
    TransportConfig,
    _build_booking_f_req,
    _build_filters_from_legs,
    _build_selected_legs,
    _http_post,
    _normalize_rpc_leg,
)
from swoop import Passengers


@dataclass
class Route:
    label: str
    origin: str
    destination: str
    date: str
    carrier: str  # expected operating carrier IATA (for itinerary selection)
    claim_unsigned: str
    claim_signed: str


ROUTES = [
    Route("Philippine Airlines (OTA-rich)", "SFO", "MNL", "2026-09-01", "PR", "~15", "(same)"),
    Route("Frontier (claimed gated)", "LGA", "ATL", "2026-07-15", "F9", "2", "7"),
    Route("Transavia (claimed gated)", "TLS", "ALG", "2026-06-19", "TO", "1", "15"),
]


def _raw_response_len(itin, cabin: CabinClass) -> int:
    """Reproduce the exact GetBookingResults POST and return raw byte length."""
    origin = itin.departure_airport_code
    destination = itin.arrival_airport_code
    dep = itin.departure_date
    date = f"{dep[0]:04d}-{dep[1]:02d}-{dep[2]:02d}"
    legs = [_normalize_rpc_leg(origin, destination, date)]
    filters = _build_filters_from_legs(legs, cabin=cabin, passengers=Passengers(), sort=SORT_DEPARTURE_TIME)
    encoded = _build_booking_f_req(itin.booking_token, filters[1], _build_selected_legs(itin))
    if not encoded:
        return -1
    res = _http_post(BOOKING_RPC_URL, content=f"f.req={encoded}".encode(), transport=TransportConfig())
    return len(res.text)


def _pick_itinerary(result, carrier: str):
    """First itinerary on the target carrier; fall back to the top result."""
    for trip in result.results:
        itin = trip.legs[0].itinerary
        if itin is not None and itin.airline_code == carrier:
            return trip, itin
    trip = result.results[0]
    itin = trip.legs[0].itinerary
    assert itin is not None, "top result has no itinerary"
    return trip, itin


def run(route: Route) -> dict:
    out = {"label": route.label, "route": f"{route.origin}->{route.destination}", "ok": False}
    try:
        result = swoop.search(route.origin, route.destination, route.date, cabin="economy")
    except swoop.SwoopError as exc:
        out["error"] = f"search failed: {exc}"
        return out
    if not result.results:
        out["error"] = "no itineraries"
        return out

    trip, itin = _pick_itinerary(result, route.carrier)
    out["picked_carrier"] = itin.airline_code
    out["picked_price"] = trip.price

    try:
        options = swoop.get_booking_results(itin, cabin="economy")
    except swoop.SwoopError as exc:
        out["error"] = f"booking lookup failed: {exc}"
        return out

    sellers = sorted({o.seller_code for o in options if o.seller_code})
    direct = [o for o in options if o.is_airline_direct]
    otas = [o for o in options if not o.is_airline_direct]
    out.update(
        ok=True,
        n_options=len(options),
        n_sellers=len(sellers),
        sellers=sellers,
        n_direct=len(direct),
        n_otas=len(otas),
        raw_len=_raw_response_len(itin, "economy"),
        cheapest=min((o.price for o in options), default=None),
    )
    return out


def main() -> int:
    print(f"swoop {swoop.__version__} — unsigned GetBookingResults replication (issue #24)\n")
    rows = []
    for route in ROUTES:
        print(f"--- {route.label}: {route.origin}->{route.destination} on {route.date} ---")
        r = run(route)
        rows.append((route, r))
        if not r["ok"]:
            print(f"  ERROR: {r['error']}\n")
            continue
        print(f"  picked itinerary: {r['picked_carrier']} @ ${r['picked_price']} "
              f"(wanted {route.carrier})")
        print(f"  options={r['n_options']}  distinct sellers={r['n_sellers']}  "
              f"(direct={r['n_direct']}, OTA={r['n_otas']})")
        print(f"  sellers: {', '.join(r['sellers'])}")
        print(f"  cheapest=${r['cheapest']}  raw response={r['raw_len']} bytes\n")

    print("=" * 72)
    print(f"{'route':<14}{'carrier':<9}{'sellers(unsigned)':<19}{'claim unsigned':<16}{'claim signed'}")
    print("-" * 72)
    for route, r in rows:
        n = r.get("n_sellers", "ERR")
        print(f"{r['route']:<14}{route.carrier:<9}{str(n):<19}{route.claim_unsigned:<16}{route.claim_signed}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Client-side filter pipeline applied to parsed ``ExploreDestination`` lists.

The Explore RPC returns the full geographic destination set with no server-side
narrowing, so discovery filters (destination whitelist/blacklist, region, trip
length) are applied here in memory. Mirrors :mod:`swoop._deals_filter` so the
two discovery endpoints filter the same way.
"""

from __future__ import annotations

from datetime import date as _date
from typing import Iterable, Optional

from ._regions import Region, region_for_iata
from .models import ExploreDestination


def _nights(d: ExploreDestination) -> Optional[int]:
    """Whole nights between departure and return, or ``None`` if either is missing/unparseable."""
    if not (d.departure_date and d.return_date):
        return None
    try:
        return (_date.fromisoformat(d.return_date) - _date.fromisoformat(d.departure_date)).days
    except ValueError:
        return None


def filter_explore(
    items: Iterable[ExploreDestination],
    *,
    destinations: Optional[list[str]] = None,
    exclude_destinations: Optional[list[str]] = None,
    region: Optional[Region] = None,
    trip_length: Optional[tuple[int, int]] = None,
) -> list[ExploreDestination]:
    """Apply client-side discovery filters to explore destinations.

    All filters AND together; a destination must satisfy every active filter to
    pass. Filters set to ``None`` are no-ops. ``trip_length`` drops destinations
    without a return date (one-way), so it is roundtrip-only.
    """
    include = {c.upper() for c in destinations} if destinations else None
    exclude = {c.upper() for c in exclude_destinations} if exclude_destinations else None
    out: list[ExploreDestination] = []
    for d in items:
        code = d.destination.upper() if d.destination else None
        if include is not None and (code is None or code not in include):
            continue
        if exclude is not None and code is not None and code in exclude:
            continue
        if region is not None and (not d.destination or region_for_iata(d.destination) != region):
            continue
        if trip_length is not None:
            n = _nights(d)
            lo, hi = trip_length
            if n is None or not (lo <= n <= hi):
                continue
        out.append(d)
    return out

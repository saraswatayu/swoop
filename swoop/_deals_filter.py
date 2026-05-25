"""Client-side filter pipeline applied to parsed Deal lists.

The upstream RPC honors only a subset of filters at the server side
(airlines, max_stops, cabin, passengers, include_basic_economy,
multi-origin set). Everything else is applied client-side after the 30
deals come back. Centralizing these here keeps deals() readable and
the test surface focused.
"""

from __future__ import annotations

from datetime import date as _date
from typing import Iterable, Optional

from ._regions import Region
from .models import Deal


def _date_in_window(
    d: str | None, window: tuple[str, str] | None
) -> bool:
    """True if d is within the inclusive [start, end] window.

    A malformed window (un-parseable date, start > end) raises
    ValueError up to the caller — previously it silently filtered every
    deal to False, masking the typo as a "no matches" result.
    A malformed deal date (unparseable d) still returns False; the deal
    is dropped, not the whole filter.
    """
    if not window:
        return True
    try:
        start = _date.fromisoformat(window[0])
        end = _date.fromisoformat(window[1])
    except (ValueError, TypeError) as exc:
        raise ValueError(
            f"depart_window must be (YYYY-MM-DD, YYYY-MM-DD); got {window!r}: {exc}"
        ) from exc
    if start > end:
        raise ValueError(
            f"depart_window start ({window[0]}) must be <= end ({window[1]})"
        )
    if not d:
        return False
    try:
        target = _date.fromisoformat(d)
    except ValueError:
        return False
    return start <= target <= end


def filter_deals(
    deals: Iterable[Deal],
    *,
    depart_window: Optional[tuple[str, str]] = None,
    trip_length: Optional[tuple[int, int]] = None,
    destinations: Optional[list[str]] = None,
    exclude_destinations: Optional[list[str]] = None,
    region: Optional[Region] = None,
    max_price: Optional[int] = None,
    min_discount_pct: Optional[int] = None,
) -> list[Deal]:
    """Apply client-side filters to a list of deals.

    All filters AND together. A deal must satisfy every active filter to
    pass. Filters with ``None`` are no-ops.
    """
    dests_include = {d.upper() for d in destinations} if destinations else None
    dests_exclude = {d.upper() for d in exclude_destinations} if exclude_destinations else None
    out: list[Deal] = []
    for deal in deals:
        if depart_window and not _date_in_window(deal.departure_date, depart_window):
            continue
        if trip_length is not None:
            if deal.trip_days is None:
                continue
            lo, hi = trip_length
            if not (lo <= deal.trip_days <= hi):
                continue
        if dests_include is not None and deal.destination.upper() not in dests_include:
            continue
        if dests_exclude is not None and deal.destination.upper() in dests_exclude:
            continue
        if region is not None and deal.destination_region != region:
            continue
        if max_price is not None and deal.price > max_price:
            continue
        if min_discount_pct is not None:
            if deal.discount_pct is None or deal.discount_pct < min_discount_pct:
                continue
        out.append(deal)
    return out

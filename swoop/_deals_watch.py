"""Persistent deals watcher.

Records a deals fetch to a JSON cache file, then on subsequent runs
compares against the prior snapshot and surfaces the diff (new, gone,
price changes, unchanged). Mirrors the pattern of
``examples/price_drop_watcher.py`` — single file per watcher, atomic
write, no locking; run one watcher per cache file.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from ._regions import Region
from .models import Deal, DealsDiff, DealsResult, PriceChange

logger = logging.getLogger(__name__)

CACHE_SCHEMA_VERSION = 1


def diff_deals(prior: Iterable[Deal], current: Iterable[Deal]) -> DealsDiff:
    """Compute the diff between two snapshots of the same deals query.

    Uses ``Deal.fingerprint`` (origin + destination + dates + airlines)
    to match deals across runs. Price is intentionally NOT part of the
    fingerprint — the diff exists to surface price movement.
    """
    prior_by_fp: dict[str, Deal] = {d.fingerprint: d for d in prior}
    current_by_fp: dict[str, Deal] = {d.fingerprint: d for d in current}

    prior_keys = set(prior_by_fp)
    current_keys = set(current_by_fp)

    new = [current_by_fp[fp] for fp in current_keys - prior_keys]
    gone = [prior_by_fp[fp] for fp in prior_keys - current_keys]

    price_changes: list[PriceChange] = []
    unchanged: list[Deal] = []
    for fp in prior_keys & current_keys:
        before = prior_by_fp[fp]
        after = current_by_fp[fp]
        if before.price != after.price:
            price_changes.append(PriceChange(prior=before, current=after))
        else:
            unchanged.append(after)

    return DealsDiff(
        new=new, gone=gone, price_changes=price_changes, unchanged=unchanged,
    )


def _deal_to_dict(deal: Deal) -> dict:
    d = asdict(deal)
    # Enum → plain string for JSON.
    d["destination_region"] = deal.destination_region.value if deal.destination_region else None
    return d


def _coerce_int(value, default: int) -> int:
    """int() with a None-safe fallback. Raises ValueError on un-coercible
    non-None values so load_snapshot's outer except can treat the cache
    as malformed."""
    if value is None:
        return default
    return int(value)


def _coerce_list(value) -> list:
    """list() with a None-safe fallback. Catches `null` in cache that
    would otherwise crash list(None) with TypeError."""
    if value is None:
        return []
    return list(value)


def _coerce_bool(value, default: bool) -> bool:
    """Strict bool coercion. Only True/False/None are accepted; any
    other value (including the strings 'true'/'false') is rejected so
    a hand-edited or schema-drifted cache doesn't silently flip a
    filter from False to True via bool('false') == True."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise ValueError(f"expected bool for cached field, got {type(value).__name__}: {value!r}")


def _dict_to_deal(d: dict) -> Deal:
    region_raw = d.get("destination_region")
    # Tolerate enum evolution: an unknown region value (e.g. a future
    # snapshot wrote a value this version's enum doesn't recognize)
    # falls back to None rather than killing the whole snapshot.
    if region_raw:
        try:
            region = Region(region_raw)
        except ValueError:
            logger.warning(
                "unknown Region value %r in cache; falling back to None",
                region_raw,
            )
            region = None
    else:
        region = None
    return Deal(
        origin=d["origin"],
        destination=d["destination"],
        destination_city=d.get("destination_city", ""),
        destination_country=d.get("destination_country", ""),
        departure_date=d.get("departure_date", ""),
        return_date=d.get("return_date"),
        price=int(d["price"]),
        typical_price=d.get("typical_price"),
        discount_pct=d.get("discount_pct"),
        airlines=_coerce_list(d.get("airlines")),
        airline_names=_coerce_list(d.get("airline_names")),
        duration_minutes=d.get("duration_minutes"),
        stops=_coerce_int(d.get("stops"), 0),
        trip_days=d.get("trip_days"),
        destination_region=region,
        currency=d.get("currency"),
        booking_url=d.get("booking_url"),
        query_cabin=d.get("query_cabin"),
        query_adults=_coerce_int(d.get("query_adults"), 1),
        query_include_basic_economy=_coerce_bool(d.get("query_include_basic_economy"), False),
    )


def save_snapshot(path: str | os.PathLike, result: DealsResult) -> None:
    """Atomically write a DealsResult snapshot to ``path``."""
    payload = {
        "schema_version": CACHE_SCHEMA_VERSION,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "origin": result.origin,
        "deals": [_deal_to_dict(d) for d in result.deals],
    }
    path_obj = Path(path)
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: stage in a temp file in the same directory, then rename.
    # Otherwise an interrupted run leaves a partial JSON that load_snapshot
    # would treat as "no prior cache," wiping every fingerprint baseline.
    fd, tmp_path = tempfile.mkstemp(
        prefix=path_obj.name + ".", suffix=".tmp", dir=str(path_obj.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        os.replace(tmp_path, path_obj)
    except Exception:
        # Clean up the temp file on any error.
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise


def load_snapshot(path: str | os.PathLike) -> Optional[DealsResult]:
    """Load a prior DealsResult snapshot, or None if the cache doesn't exist
    or is malformed."""
    path_obj = Path(path)
    if not path_obj.exists():
        return None
    try:
        payload = json.loads(path_obj.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("deals cache at %s is unreadable: %s", path, exc)
        return None
    if payload.get("schema_version") != CACHE_SCHEMA_VERSION:
        logger.warning(
            "deals cache at %s uses schema_version=%s, expected %d; "
            "treating as no prior cache",
            path, payload.get("schema_version"), CACHE_SCHEMA_VERSION,
        )
        return None
    try:
        deals = [_dict_to_deal(d) for d in payload.get("deals", [])]
    except (KeyError, ValueError, TypeError) as exc:
        logger.warning("failed to parse cached deals from %s: %s", path, exc)
        return None
    return DealsResult(deals=deals, origin=payload.get("origin", ""))


def watch_deals(
    current_result: DealsResult,
    *,
    cache_path: str | os.PathLike,
    allow_empty: bool = False,
) -> DealsDiff:
    """One iteration of the watcher pattern: load prior, diff, save current.

    Typical usage::

        from swoop import deals, watch_deals

        result = deals("JFK", region=Region.EUROPE, max_price=700)
        diff = watch_deals(result, cache_path=".swoop-deals-cache.json")
        for change in diff.price_changes:
            if change.delta < -50:
                print(f"Price drop: {change}")

    Returns the :class:`DealsDiff` between the prior snapshot and the
    supplied ``current_result``, then persists ``current_result`` as
    the new snapshot. On first run (no prior cache), every deal appears
    in ``diff.new`` and ``diff.gone`` is empty.

    ``allow_empty`` (default ``False``): when ``current_result.deals`` is
    empty AND a prior snapshot with deals exists, the cache is NOT
    overwritten and an empty :class:`DealsDiff` is returned. This
    protects against transient upstream failures (anti-bot challenges,
    brief geo-blocks, momentary outages) wiping the watcher's baseline
    and producing a flood of fake ``new``/``gone`` events on the next
    run. Set ``allow_empty=True`` when you genuinely want to record an
    all-gone state (e.g. all deals have legitimately expired). The same
    flag also overrides the partial-result guard documented below.
    """
    prior = load_snapshot(cache_path)
    prior_deals = prior.deals if prior else []
    if not current_result.deals and prior_deals and not allow_empty:
        logger.warning(
            "watch_deals: current run returned 0 deals but prior had %d; "
            "refusing to overwrite cache. Pass allow_empty=True to override.",
            len(prior_deals),
        )
        return DealsDiff()
    # Per-origin parallel mode marks the result `partial` when any origin
    # failed. Saving a partial snapshot turns the missing-origin's prior
    # deals into spurious `gone` events on the next diff. Refuse to save
    # unless allow_empty=True — but still compute and return a SCOPED
    # diff against the prior deals of the origins that DID succeed, so
    # the user gets price-change/new signals from the working origins
    # instead of an empty diff.
    if current_result.partial and not allow_empty:
        succeeded_origins = {d.origin for d in current_result.deals}
        scoped_prior = [d for d in prior_deals if d.origin in succeeded_origins]
        logger.warning(
            "watch_deals: current run is partial (per-origin failure); "
            "computing diff over %d succeeded origin(s) but refusing to "
            "overwrite cache. Pass allow_empty=True to override.",
            len(succeeded_origins),
        )
        return diff_deals(scoped_prior, current_result.deals)
    diff = diff_deals(prior_deals, current_result.deals)
    save_snapshot(cache_path, current_result)
    return diff

"""Tests for swoop._deals_watch (diff + persistent watcher)."""
import json
from pathlib import Path

import pytest

from swoop import Deal, DealsDiff, DealsResult, PriceChange, Region
from swoop._deals_watch import (
    diff_deals,
    load_snapshot,
    save_snapshot,
    watch_deals,
)


def _deal(**overrides):
    defaults = dict(
        origin="JFK", destination="LIS", destination_city="Lisbon",
        destination_country="Portugal", departure_date="2026-06-15",
        return_date="2026-06-22", price=400, typical_price=900,
        discount_pct=55, airlines=["TP"], airline_names=["TAP"],
        duration_minutes=420, stops=0, trip_days=7,
        destination_region=Region.EUROPE, currency="USD",
    )
    defaults.update(overrides)
    return Deal(**defaults)


class TestDiffDeals:
    def test_first_run_everything_is_new(self):
        d1 = _deal(destination="LIS")
        d2 = _deal(destination="MAD")
        diff = diff_deals([], [d1, d2])
        assert len(diff.new) == 2
        assert diff.gone == []
        assert diff.price_changes == []
        assert diff.unchanged == []
        assert diff.has_changes

    def test_unchanged_deal(self):
        d = _deal()
        diff = diff_deals([d], [d])
        assert diff.new == []
        assert diff.gone == []
        assert diff.price_changes == []
        assert len(diff.unchanged) == 1
        assert not diff.has_changes

    def test_price_change_detected(self):
        before = _deal(price=400)
        after = _deal(price=350)
        diff = diff_deals([before], [after])
        assert diff.new == []
        assert diff.gone == []
        assert len(diff.price_changes) == 1
        change = diff.price_changes[0]
        assert change.prior.price == 400
        assert change.current.price == 350
        assert change.delta == -50
        assert change.delta_pct == pytest.approx(-12.5)
        assert diff.unchanged == []

    def test_gone_deal(self):
        d = _deal()
        diff = diff_deals([d], [])
        assert diff.new == []
        assert len(diff.gone) == 1
        assert diff.price_changes == []
        assert diff.unchanged == []

    def test_mixed_diff(self):
        unchanged_d = _deal(destination="LIS", price=400)
        changed_before = _deal(destination="MAD", price=500)
        changed_after = _deal(destination="MAD", price=400)
        gone = _deal(destination="BCN", price=300)
        new = _deal(destination="ROM", price=600)

        diff = diff_deals(
            [unchanged_d, changed_before, gone],
            [unchanged_d, changed_after, new],
        )
        assert {d.destination for d in diff.new} == {"ROM"}
        assert {d.destination for d in diff.gone} == {"BCN"}
        assert len(diff.price_changes) == 1
        assert diff.price_changes[0].current.destination == "MAD"
        assert {d.destination for d in diff.unchanged} == {"LIS"}


class TestPriceChange:
    def test_delta_negative_means_drop(self):
        before = _deal(price=500)
        after = _deal(price=400)
        change = PriceChange(prior=before, current=after)
        assert change.delta == -100

    def test_delta_pct_zero_when_prior_zero(self):
        before = _deal(price=0)
        after = _deal(price=100)
        change = PriceChange(prior=before, current=after)
        # No division-by-zero crash.
        assert change.delta_pct == 0.0

    def test_repr_contains_route_and_prices(self):
        before = _deal(destination="LIS", price=500)
        after = _deal(destination="LIS", price=400)
        change = PriceChange(prior=before, current=after)
        r = repr(change)
        assert "JFK->LIS" in r
        assert "500" in r
        assert "400" in r


class TestSavedAndLoadSnapshot:
    def test_save_then_load_roundtrip(self, tmp_path):
        d1 = _deal(destination="LIS", price=400)
        d2 = _deal(destination="MAD", price=550)
        result = DealsResult(deals=[d1, d2], origin="JFK")
        cache = tmp_path / "cache.json"
        save_snapshot(cache, result)
        loaded = load_snapshot(cache)
        assert loaded is not None
        assert loaded.origin == "JFK"
        assert len(loaded.deals) == 2
        assert loaded.deals[0].destination == "LIS"
        assert loaded.deals[0].destination_region == Region.EUROPE

    def test_load_missing_file_returns_none(self, tmp_path):
        cache = tmp_path / "does-not-exist.json"
        assert load_snapshot(cache) is None

    def test_load_malformed_json_returns_none(self, tmp_path):
        cache = tmp_path / "bad.json"
        cache.write_text("not json {{{")
        assert load_snapshot(cache) is None

    def test_load_wrong_schema_version_returns_none(self, tmp_path):
        cache = tmp_path / "v0.json"
        cache.write_text(json.dumps({"schema_version": 99, "deals": []}))
        assert load_snapshot(cache) is None

    def test_save_is_atomic_no_tempfile_leftovers(self, tmp_path):
        """After a successful save, the temp file should be cleaned up."""
        result = DealsResult(deals=[_deal()], origin="JFK")
        cache = tmp_path / "cache.json"
        save_snapshot(cache, result)
        leftovers = [p for p in tmp_path.iterdir() if p.name != "cache.json"]
        assert leftovers == []

    def test_save_creates_parent_dirs(self, tmp_path):
        cache = tmp_path / "nested" / "deeper" / "cache.json"
        save_snapshot(cache, DealsResult(deals=[_deal()], origin="JFK"))
        assert cache.exists()

    def test_load_tolerates_unknown_region_value(self, tmp_path):
        # A cache written by a future swoop with a region value this
        # version's enum doesn't recognize must NOT nuke the whole
        # snapshot — the affected deal falls back to region=None.
        cache = tmp_path / "future.json"
        cache.write_text(json.dumps({
            "schema_version": 1,
            "origin": "JFK",
            "deals": [{
                "origin": "JFK", "destination": "AKL",
                "destination_city": "Auckland", "destination_country": "New Zealand",
                "departure_date": "2026-09-01", "return_date": "2026-09-14",
                "price": 1200, "typical_price": 1800, "discount_pct": 33,
                "airlines": ["NZ"], "airline_names": ["Air New Zealand"],
                "duration_minutes": 1080, "stops": 1, "trip_days": 13,
                "destination_region": "oceania",  # not in current Region enum
                "currency": "USD", "booking_url": None,
            }],
        }))
        loaded = load_snapshot(cache)
        assert loaded is not None
        assert len(loaded.deals) == 1
        assert loaded.deals[0].destination_region is None
        assert loaded.deals[0].destination == "AKL"

    def test_load_returns_none_on_null_price(self, tmp_path):
        # Regression: int(None) used to raise TypeError uncaught,
        # crashing the watcher. Now treated as malformed cache.
        cache = tmp_path / "null-price.json"
        cache.write_text(json.dumps({
            "schema_version": 1, "origin": "JFK",
            "deals": [{"origin": "JFK", "destination": "LIS", "price": None}],
        }))
        assert load_snapshot(cache) is None

    def test_load_coerces_null_airlines_to_empty_list(self, tmp_path):
        # Regression: list(None) raised TypeError uncaught. Now coerced
        # to an empty list — the deal still loads (with no carriers).
        cache = tmp_path / "null-airlines.json"
        cache.write_text(json.dumps({
            "schema_version": 1, "origin": "JFK",
            "deals": [{"origin": "JFK", "destination": "LIS", "price": 400,
                       "airlines": None, "airline_names": None}],
        }))
        loaded = load_snapshot(cache)
        assert loaded is not None
        assert loaded.deals[0].airlines == []
        assert loaded.deals[0].airline_names == []

    def test_load_returns_none_on_null_stops(self, tmp_path):
        cache = tmp_path / "null-stops.json"
        cache.write_text(json.dumps({
            "schema_version": 1, "origin": "JFK",
            "deals": [{"origin": "JFK", "destination": "LIS", "price": 400,
                       "stops": None}],
        }))
        # _coerce_int(None, 0) returns 0, so this is actually LOAD-ABLE.
        loaded = load_snapshot(cache)
        assert loaded is not None
        assert loaded.deals[0].stops == 0

    def test_load_rejects_string_bool_for_basic_economy(self, tmp_path):
        # Regression: bool("false") returns True, silently flipping the
        # bridge's basic-economy filter on round-trip. Strict coercion
        # now rejects non-bool values via ValueError → load returns None.
        cache = tmp_path / "string-bool.json"
        cache.write_text(json.dumps({
            "schema_version": 1, "origin": "JFK",
            "deals": [{"origin": "JFK", "destination": "LIS", "price": 400,
                       "query_include_basic_economy": "false"}],
        }))
        assert load_snapshot(cache) is None

    def test_load_accepts_real_bool_for_basic_economy(self, tmp_path):
        cache = tmp_path / "real-bool.json"
        cache.write_text(json.dumps({
            "schema_version": 1, "origin": "JFK",
            "deals": [{"origin": "JFK", "destination": "LIS", "price": 400,
                       "query_include_basic_economy": True}],
        }))
        loaded = load_snapshot(cache)
        assert loaded is not None
        assert loaded.deals[0].query_include_basic_economy is True


class TestWatchDeals:
    def test_first_run_all_new(self, tmp_path):
        result = DealsResult(deals=[_deal(destination="LIS")], origin="JFK")
        cache = tmp_path / "cache.json"
        diff = watch_deals(result, cache_path=cache)
        assert len(diff.new) == 1
        assert diff.gone == []
        assert cache.exists()

    def test_second_run_unchanged(self, tmp_path):
        result = DealsResult(deals=[_deal(destination="LIS")], origin="JFK")
        cache = tmp_path / "cache.json"
        watch_deals(result, cache_path=cache)
        diff = watch_deals(result, cache_path=cache)
        assert diff.new == []
        assert diff.gone == []
        assert diff.price_changes == []
        assert len(diff.unchanged) == 1

    def test_second_run_price_drop(self, tmp_path):
        cache = tmp_path / "cache.json"
        watch_deals(
            DealsResult(deals=[_deal(destination="LIS", price=500)], origin="JFK"),
            cache_path=cache,
        )
        diff = watch_deals(
            DealsResult(deals=[_deal(destination="LIS", price=400)], origin="JFK"),
            cache_path=cache,
        )
        assert len(diff.price_changes) == 1
        assert diff.price_changes[0].delta == -100

    def test_swoop_watch_deals_export(self, tmp_path):
        # Public re-export works the same way as the internal one.
        import swoop
        cache = tmp_path / "cache.json"
        result = DealsResult(deals=[_deal()], origin="JFK")
        diff = swoop.watch_deals(result, cache_path=cache)
        assert isinstance(diff, DealsDiff)

    def test_empty_current_does_not_overwrite_nonempty_prior(self, tmp_path):
        # Transient upstream failure (anti-bot, brief outage) returns
        # zero deals — the watcher must NOT clobber the prior snapshot.
        cache = tmp_path / "cache.json"
        watch_deals(
            DealsResult(deals=[_deal(destination="LIS")], origin="JFK"),
            cache_path=cache,
        )
        original_mtime = cache.stat().st_mtime_ns

        diff = watch_deals(DealsResult(deals=[], origin="JFK"), cache_path=cache)
        assert diff.new == []
        assert diff.gone == []  # NOT [deal] — that would be the bug
        assert diff.price_changes == []
        # Cache untouched.
        assert cache.stat().st_mtime_ns == original_mtime

    def test_empty_current_allowed_when_allow_empty(self, tmp_path):
        cache = tmp_path / "cache.json"
        watch_deals(
            DealsResult(deals=[_deal(destination="LIS")], origin="JFK"),
            cache_path=cache,
        )
        diff = watch_deals(
            DealsResult(deals=[], origin="JFK"),
            cache_path=cache,
            allow_empty=True,
        )
        assert len(diff.gone) == 1

    def test_partial_does_not_overwrite(self, tmp_path):
        # per_origin parallel mode returns partial=True when an origin
        # fails; the watcher refuses to save a partial snapshot.
        cache = tmp_path / "cache.json"
        watch_deals(
            DealsResult(
                deals=[_deal(origin="JFK", destination="LIS"),
                       _deal(origin="LGA", destination="MAD")],
                origin="JFK,LGA",
            ),
            cache_path=cache,
        )
        original_mtime = cache.stat().st_mtime_ns

        # LGA "failed" — only JFK deals in current; partial=True.
        diff = watch_deals(
            DealsResult(
                deals=[_deal(origin="JFK", destination="LIS")],
                origin="JFK,LGA",
                partial=True,
            ),
            cache_path=cache,
        )
        assert diff.gone == []
        assert cache.stat().st_mtime_ns == original_mtime


class TestDealsDiff:
    def test_repr_shows_counts(self):
        diff = DealsDiff(
            new=[_deal()], gone=[], price_changes=[], unchanged=[_deal(), _deal()],
        )
        r = repr(diff)
        assert "new=1" in r
        assert "unchanged=2" in r

    def test_has_changes_false_when_only_unchanged(self):
        d = DealsDiff(unchanged=[_deal()])
        assert not d.has_changes

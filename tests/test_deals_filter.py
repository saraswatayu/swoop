"""Tests for swoop._deals_filter.filter_deals."""
import pytest

from swoop import Deal
from swoop._deals_filter import filter_deals
from swoop._regions import Region


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


class TestDepartWindow:
    def test_inside_window_passes(self):
        d = _deal(departure_date="2026-06-15")
        assert filter_deals([d], depart_window=("2026-06-01", "2026-06-30")) == [d]

    def test_outside_window_drops(self):
        d = _deal(departure_date="2026-08-15")
        assert filter_deals([d], depart_window=("2026-06-01", "2026-06-30")) == []

    def test_on_boundary_passes(self):
        early = _deal(departure_date="2026-06-01")
        late = _deal(departure_date="2026-06-30", destination="MAD")
        result = filter_deals([early, late], depart_window=("2026-06-01", "2026-06-30"))
        assert len(result) == 2

    def test_missing_date_drops(self):
        d = _deal(departure_date="")
        assert filter_deals([d], depart_window=("2026-06-01", "2026-06-30")) == []

    def test_malformed_window_raises(self):
        # Regression: a typoed window used to silently filter every deal
        # to False, masking the caller error as "no matches."
        import pytest
        d = _deal(departure_date="2026-06-15")
        with pytest.raises(ValueError, match="depart_window"):
            filter_deals([d], depart_window=("garbage", "2026-06-30"))

    def test_swapped_window_raises(self):
        import pytest
        d = _deal(departure_date="2026-06-15")
        with pytest.raises(ValueError, match="must be <= end"):
            filter_deals([d], depart_window=("2026-08-01", "2026-06-01"))


class TestTripLength:
    def test_inside_range_passes(self):
        d = _deal(trip_days=7)
        assert filter_deals([d], trip_length=(5, 10)) == [d]

    def test_below_min_drops(self):
        d = _deal(trip_days=3)
        assert filter_deals([d], trip_length=(5, 10)) == []

    def test_above_max_drops(self):
        d = _deal(trip_days=15)
        assert filter_deals([d], trip_length=(5, 10)) == []

    def test_none_trip_days_drops(self):
        d = _deal(trip_days=None)
        assert filter_deals([d], trip_length=(5, 10)) == []


class TestDestinations:
    def test_whitelist_includes(self):
        lis = _deal(destination="LIS")
        mad = _deal(destination="MAD")
        result = filter_deals([lis, mad], destinations=["LIS"])
        assert result == [lis]

    def test_whitelist_case_insensitive(self):
        d = _deal(destination="LIS")
        assert filter_deals([d], destinations=["lis"]) == [d]

    def test_blacklist_excludes(self):
        lis = _deal(destination="LIS")
        mad = _deal(destination="MAD")
        result = filter_deals([lis, mad], exclude_destinations=["LIS"])
        assert result == [mad]

    def test_whitelist_and_blacklist_intersect(self):
        # Blacklist wins when both are set on the same code.
        lis = _deal(destination="LIS")
        result = filter_deals(
            [lis],
            destinations=["LIS", "MAD"],
            exclude_destinations=["LIS"],
        )
        assert result == []


class TestRegion:
    def test_matching_region_passes(self):
        d = _deal(destination_region=Region.EUROPE)
        assert filter_deals([d], region=Region.EUROPE) == [d]

    def test_mismatched_region_drops(self):
        d = _deal(destination_region=Region.ASIA_PACIFIC)
        assert filter_deals([d], region=Region.EUROPE) == []

    def test_none_region_drops_when_filter_set(self):
        # If airportsdata wasn't available, destination_region is None.
        # A region filter still drops these — explicit is better than silent.
        d = _deal(destination_region=None)
        assert filter_deals([d], region=Region.EUROPE) == []


class TestMaxPrice:
    def test_under_max_passes(self):
        d = _deal(price=300)
        assert filter_deals([d], max_price=500) == [d]

    def test_over_max_drops(self):
        d = _deal(price=700)
        assert filter_deals([d], max_price=500) == []

    def test_at_max_passes(self):
        d = _deal(price=500)
        assert filter_deals([d], max_price=500) == [d]


class TestMinDiscount:
    def test_above_min_passes(self):
        d = _deal(discount_pct=60)
        assert filter_deals([d], min_discount_pct=50) == [d]

    def test_below_min_drops(self):
        d = _deal(discount_pct=30)
        assert filter_deals([d], min_discount_pct=50) == []

    def test_none_discount_drops(self):
        d = _deal(discount_pct=None)
        assert filter_deals([d], min_discount_pct=50) == []


class TestNoFilters:
    def test_empty_returns_all(self):
        d1 = _deal()
        d2 = _deal(destination="MAD")
        assert filter_deals([d1, d2]) == [d1, d2]


class TestCombinedFilters:
    def test_all_active_filters_intersect(self):
        # The full "exploration" use case: summer Europe deals under $500
        # with 5-10 day trips and at least 50% off.
        match = _deal(
            destination="LIS", departure_date="2026-07-10",
            trip_days=7, price=400, discount_pct=55,
        )
        wrong_date = _deal(
            destination="MAD", departure_date="2026-11-10",
            trip_days=7, price=400, discount_pct=55,
        )
        too_expensive = _deal(
            destination="BCN", departure_date="2026-07-15",
            trip_days=7, price=900, discount_pct=55,
        )
        result = filter_deals(
            [match, wrong_date, too_expensive],
            depart_window=("2026-06-01", "2026-08-31"),
            trip_length=(5, 10),
            region=Region.EUROPE,
            max_price=500,
            min_discount_pct=50,
        )
        assert result == [match]

"""API surface stability tests.

These tests freeze the public API surface of swoop to catch accidental
additions, removals, or renames that would break downstream consumers.
"""

import inspect
from dataclasses import fields

import pytest

import swoop
from swoop import Deal, DealsResult, Passengers, PriceResult, ResolvedLeg, SearchLeg, SearchResult, SelectedLeg, TransportConfig, TripLeg, TripOption
from swoop.decoder import (
    BookingOption,
    CarbonEmissions,
    Codeshare,
    Segment,
    Itinerary,
    Layover,
    PriceRange,
    QualitySignals,
    RawSearchResult,
    AmenityFlags,
)
from swoop.exceptions import (
    SwoopError,
    SwoopHTTPError,
    SwoopParseError,
    SwoopRateLimitError,
)


class TestFrozenExports:
    """Verify swoop.__all__ is exactly the expected set."""

    EXPECTED_ALL = {
        # Functions
        "search",
        "search_legs",
        "check_price",
        "price_selector",
        "price_legs",
        "deals",
        "search_deal",
        "price_deal",
        "get_booking_results",
        "search_raw",
        "set_country",
        "set_proxy",
        "parse_flight_number",
        "itinerary_matches_flight",
        # Types
        "CabinClass",
        "Deal",
        "DealsResult",
        "Passengers",
        "TransportConfig",
        "PriceResult",
        "RawSearchResult",
        "SearchResult",
        "SearchLeg",
        "SelectedLeg",
        "ResolvedLeg",
        "TripLeg",
        "TripOption",
        "Itinerary",
        "Segment",
        "BookingOption",
        "Codeshare",
        "Layover",
        "CarbonEmissions",
        "PriceRange",
        "AmenityFlags",
        "QualitySignals",
        # Exceptions
        "SwoopError",
        "SwoopHTTPError",
        "SwoopParseError",
        "SwoopRateLimitError",
        # Constants
        "SORT_TOP",
        "SORT_CHEAPEST",
        "SORT_DEPARTURE_TIME",
        "SORT_ARRIVAL_TIME",
        "SORT_DURATION",
        "STOPS_ANY",
        "STOPS_NONSTOP",
        "STOPS_ONE_OR_FEWER",
        "STOPS_TWO_OR_FEWER",
    }

    def test_all_matches_expected(self):
        actual = set(swoop.__all__)
        assert actual == self.EXPECTED_ALL

    def test_no_unexpected_additions(self):
        actual = set(swoop.__all__)
        extra = actual - self.EXPECTED_ALL
        assert extra == set(), f"Unexpected exports added: {extra}"

    def test_no_unexpected_removals(self):
        actual = set(swoop.__all__)
        missing = self.EXPECTED_ALL - actual
        assert missing == set(), f"Exports removed: {missing}"

    def test_all_exports_importable(self):
        for name in swoop.__all__:
            assert hasattr(swoop, name), f"swoop.__all__ lists {name!r} but it's not importable"

    def test_dir_returns_sorted_all(self):
        """`dir(swoop)` is what tab completion shows; PEP 562 `__dir__`
        guarantees the perceived public surface equals ``__all__``."""
        assert dir(swoop) == sorted(swoop.__all__)

    def test_dir_excludes_known_internals(self):
        """The previous test would still pass if ``__dir__`` were
        accidentally removed (it would degrade to listing every name
        in ``swoop.__dict__``, which is a superset of ``__all__``, so
        the sorted-equality check would just fail with a long list and
        no signal about what regressed). Pin a handful of internal
        names that exist in the module dict but MUST stay out of
        ``dir()`` so tab completion doesn't surface them.

        These are accessible via ``swoop.X`` for legitimate internal
        callers (and were intentionally kept reachable when ``__dir__``
        replaced the older ``globals().pop`` approach), but they have
        no place on the perceived public surface.
        """
        known_internals = [
            # standard-library imports that escaped
            "logging",
            "logger",
            # private validators
            "validate_iata_code",
            "validate_date",
            "validate_search_params",
            # internal selection helpers
            "search_trip_options",
            "price_trip_selector",
            "build_request_legs_from_selected",
            # internal submodules
            "rpc",
            "decoder",
            "builders",
        ]
        # Sanity: every name in this allowlist must actually be reachable
        # via ``swoop.X``. If one disappears, the test should be updated,
        # not silently passed because the membership check became vacuous.
        for name in known_internals:
            assert hasattr(swoop, name), (
                f"{name!r} is in the internals allowlist but isn't reachable "
                "via swoop.X — remove it from the allowlist or restore the import."
            )
        visible = set(dir(swoop))
        exposed = [name for name in known_internals if name in visible]
        assert exposed == [], (
            f"dir(swoop) exposes internals: {exposed}. Tab completion now "
            "surfaces these to users. Either add them to swoop.__all__ if "
            "they're intentionally public, or fix __dir__ (PEP 562) so the "
            "perceived surface stays curated."
        )

    def test_submodules_still_importable(self):
        """Hiding submodule attributes must not break `from swoop.X import Y`."""
        from swoop.decoder import BookingOption as _BookingOption
        from swoop.builders import CabinClass as _CabinClass
        from swoop.rpc import search_raw as _search_raw
        from swoop.exceptions import SwoopError as _SwoopError
        from swoop.models import Passengers as _Passengers
        assert all([_BookingOption, _CabinClass, _search_raw, _SwoopError, _Passengers])


class TestFrozenDataclassFields:
    """Verify dataclass field names haven't changed."""

    @staticmethod
    def _field_names(cls):
        return {f.name for f in fields(cls)}

    def test_segment_fields(self):
        expected = {
            "airline", "airline_name", "flight_number", "operator",
            "codeshares", "aircraft",
            "departure_airport_code", "departure_airport_name",
            "arrival_airport_code", "arrival_airport_name",
            "departure_date", "arrival_date",
            "departure_time", "arrival_time",
            "travel_time", "seat_pitch_short", "legroom", "co2_grams",
            "overnight", "has_premium_ife", "amenities", "seat_type",
        }
        assert self._field_names(Segment) == expected

    def test_itinerary_fields(self):
        expected = {
            "airline_code", "airline_names", "segments", "layovers",
            "travel_time", "departure_airport_code", "arrival_airport_code",
            "departure_date", "arrival_date", "departure_time", "arrival_time",
            "price_info", "direct_price", "booking_token", "carbon_emissions",
            "stop_count", "is_budget_carrier", "quality_signals",
        }
        assert self._field_names(Itinerary) == expected

    def test_search_result_fields(self):
        expected = {"results", "price_range", "is_complete"}
        assert self._field_names(SearchResult) == expected

    def test_search_result_currency_property(self):
        """currency is a derived property, not a stored field."""
        sr = SearchResult()
        assert sr.currency is None
        sr_with = SearchResult(results=[TripOption(selector="x", currency="USD")])
        assert sr_with.currency == "USD"

    def test_raw_search_result_fields(self):
        expected = {"_raw", "best", "other", "price_range"}
        assert self._field_names(RawSearchResult) == expected

    def test_booking_option_fields(self):
        expected = {
            "price", "brand_label", "brand_code",
            "is_basic", "fare_family", "rebookability_signal",
            "seller_name", "seller_code", "booking_url", "logo_url",
            "is_airline_direct",
        }
        actual = {
            field.name
            for field in fields(BookingOption)
            if not field.name.startswith("_")
        }
        assert actual == expected

    def test_codeshare_fields(self):
        expected = {"airline_code", "flight_number", "airline_name"}
        assert self._field_names(Codeshare) == expected

    def test_layover_fields(self):
        expected = {
            "minutes", "departure_airport_code", "departure_airport_name",
            "departure_airport_city", "arrival_airport_code",
            "arrival_airport_name", "arrival_airport_city", "is_overnight",
        }
        assert self._field_names(Layover) == expected

    def test_carbon_emissions_fields(self):
        expected = {
            "this_flight_grams", "typical_for_route_grams",
            "difference_percent", "emissions_rating",
        }
        assert self._field_names(CarbonEmissions) == expected

    def test_amenity_flags_fields(self):
        expected = {
            "has_power", "has_live_tv", "has_on_demand_video",
            "has_stream_media", "wifi",
        }
        assert self._field_names(AmenityFlags) == expected

    def test_quality_signals_fields(self):
        expected = {"quality_tier", "bag_flags"}
        assert self._field_names(QualitySignals) == expected

    def test_price_range_fields(self):
        expected = {"low", "high"}
        assert self._field_names(PriceRange) == expected

    def test_price_result_fields(self):
        expected = {"price", "currency", "fare_brand", "is_basic_economy", "booking_options", "itinerary", "resolved_legs", "rpc_calls"}
        assert self._field_names(PriceResult) == expected

    def test_resolved_leg_fields(self):
        expected = {"flight_summary", "origin", "destination", "date", "itinerary", "selection"}
        assert self._field_names(ResolvedLeg) == expected

    def test_selected_leg_fields(self):
        expected = {"flight_number", "origin", "destination", "date"}
        assert self._field_names(SelectedLeg) == expected

    def test_trip_leg_fields(self):
        expected = {"origin", "destination", "date", "itinerary"}
        assert self._field_names(TripLeg) == expected

    def test_passengers_fields(self):
        expected = {"adults", "children", "infants_in_seat", "infants_on_lap"}
        assert self._field_names(Passengers) == expected

    def test_trip_option_fields(self):
        expected = {"selector", "price", "currency", "legs"}
        assert self._field_names(TripOption) == expected

    def test_deal_fields(self):
        expected = {
            "origin", "destination", "destination_city", "destination_country",
            "departure_date", "return_date", "price", "typical_price",
            "discount_pct", "airlines", "airline_names",
            "duration_minutes", "stops", "trip_days", "destination_region",
            "currency", "booking_url",
        }
        assert self._field_names(Deal) == expected

    def test_deals_result_fields(self):
        expected = {"deals", "origin"}
        assert self._field_names(DealsResult) == expected

    def test_deals_result_currency_property(self):
        dr = DealsResult()
        assert dr.currency is None


class TestSearchSignature:
    """Verify search() accepts the expected parameters."""

    def test_search_params(self):
        sig = inspect.signature(swoop.search)
        param_names = list(sig.parameters.keys())
        expected = [
            "origin", "destination", "date",
            "return_date", "cabin", "passengers",
            "max_stops", "sort",
            "airlines", "flight_number", "include_basic_economy",
            "earliest_departure", "latest_departure",
            "earliest_arrival", "latest_arrival",
            "return_earliest_departure", "return_latest_departure",
            "transport",
            "max_results", "beam_width", "time_budget",
        ]
        assert param_names == expected

    def test_search_raw_params(self):
        sig = inspect.signature(swoop.search_raw)
        param_names = list(sig.parameters.keys())
        expected = [
            "origin", "destination", "date",
            "cabin", "passengers",
            "sort", "max_stops", "airlines",
            "earliest_departure", "latest_departure",
            "earliest_arrival", "latest_arrival",
            "return_date", "return_earliest_departure", "return_latest_departure",
            "selected_outbound_legs",
            "transport",
            "exclude_basic_economy",
        ]
        assert param_names == expected

    def test_check_price_params(self):
        sig = inspect.signature(swoop.check_price)
        param_names = list(sig.parameters.keys())
        expected = [
            "flight_number", "origin", "destination", "date",
            "return_flight_number", "return_date",
            "cabin", "passengers",
            "max_stops", "include_basic_economy",
            "transport",
        ]
        assert param_names == expected

    def test_search_legs_params(self):
        sig = inspect.signature(swoop.search_legs)
        param_names = list(sig.parameters.keys())
        expected = [
            "legs", "cabin", "passengers",
            "sort",
            "include_basic_economy", "transport",
            "max_results", "beam_width", "time_budget",
        ]
        assert param_names == expected

    def test_price_legs_params(self):
        sig = inspect.signature(swoop.price_legs)
        param_names = list(sig.parameters.keys())
        expected = [
            "legs", "cabin", "passengers",
            "include_basic_economy", "transport",
        ]
        assert param_names == expected

    def test_price_selector_params(self):
        sig = inspect.signature(swoop.price_selector)
        param_names = list(sig.parameters.keys())
        expected = ["selector", "transport"]
        assert param_names == expected

    def test_deals_params(self):
        sig = inspect.signature(swoop.deals)
        param_names = list(sig.parameters.keys())
        expected = [
            "origin", "cabin", "max_stops", "airlines", "passengers",
            "include_basic_economy", "transport",
        ]
        assert param_names == expected


class TestFrozenDefaults:
    """Verify critical default values haven't drifted."""

    def test_transport_config_defaults(self):
        tc = TransportConfig()
        assert tc.timeout == 90
        assert tc.retries == 2
        assert tc.country is None
        assert tc.proxy is None
        assert tc.impersonate is None

    def test_transport_config_fields(self):
        from dataclasses import fields as dc_fields
        expected = {"timeout", "retries", "country", "proxy", "impersonate"}
        actual = {f.name for f in dc_fields(TransportConfig)}
        assert actual == expected


class TestItineraryPrice:
    """Verify the canonical ``price`` property on Itinerary."""

    def test_prefers_direct_price(self):
        from swoop.builders import ItinerarySummary
        itin = Itinerary(
            direct_price=299,
            price_info=ItinerarySummary(flights="", price=298.0, currency="USD"),
        )
        assert itin.price == 299

    def test_none_when_no_direct_price(self):
        from swoop.builders import ItinerarySummary
        itin = Itinerary(
            direct_price=None,
            price_info=ItinerarySummary(flights="", price=29870, currency="USD"),
        )
        assert itin.price is None  # protobuf price is not used

    def test_none_when_no_price(self):
        itin = Itinerary()
        assert itin.price is None


class TestExceptionHierarchy:
    """Verify exception class relationships are stable."""

    def test_swoop_error_is_base(self):
        assert issubclass(SwoopHTTPError, SwoopError)
        assert issubclass(SwoopParseError, SwoopError)
        assert issubclass(SwoopRateLimitError, SwoopError)

    def test_rate_limit_is_http_error(self):
        assert issubclass(SwoopRateLimitError, SwoopHTTPError)

    def test_http_error_has_status_code(self):
        err = SwoopHTTPError(503)
        assert err.status_code == 503

    def test_rate_limit_has_429(self):
        err = SwoopRateLimitError()
        assert err.status_code == 429

    def test_all_inherit_from_exception(self):
        for cls in (SwoopError, SwoopHTTPError, SwoopParseError, SwoopRateLimitError):
            assert issubclass(cls, Exception)


class TestConstants:
    """Verify sort and stop constants are stable."""

    def test_sort_constants(self):
        assert swoop.SORT_TOP == 1
        assert swoop.SORT_CHEAPEST == 2
        assert swoop.SORT_DEPARTURE_TIME == 3
        assert swoop.SORT_ARRIVAL_TIME == 4
        assert swoop.SORT_DURATION == 5

    def test_stop_constants(self):
        assert swoop.STOPS_ANY == 0
        assert swoop.STOPS_NONSTOP == 1
        assert swoop.STOPS_ONE_OR_FEWER == 2
        assert swoop.STOPS_TWO_OR_FEWER == 3

    def test_cabin_class_map_importable(self):
        """CABIN_CLASS_MAP is importable from builders (canonical) and rpc (re-export)."""
        from swoop.builders import CABIN_CLASS_MAP
        assert CABIN_CLASS_MAP == {
            "economy": 1,
            "premium-economy": 2,
            "business": 3,
            "first": 4,
        }

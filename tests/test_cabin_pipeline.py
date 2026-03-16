"""End-to-end cabin classify → filter pipeline tests.

Tests exercise the full seam: raw brand strings through _classify_cabin_bucket
→ BookingOption → _eligible_booking_options, proving that unknown-bucket brands
leak into wrong cabins.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from swoop._booking import _classify_cabin_bucket
from swoop._selection import _eligible_booking_options, price_selected_trip
from swoop.decoder import BookingOption, Flight, Itinerary

import swoop._selection as selection


def _option(price, brand_label, brand_code=None, *, is_basic=False):
    """Build BookingOption using REAL classification, not pre-set buckets."""
    code = brand_code or brand_label.upper()
    bucket = _classify_cabin_bucket(code, brand_label)
    return BookingOption(
        price=price,
        brand_label=brand_label,
        brand_code=code,
        is_basic=is_basic,
        _cabin_bucket=bucket,
    )


def _make_itinerary(
    *,
    origin: str = "JFK",
    destination: str = "LAX",
    date: str = "2026-04-15",
    airline: str = "DL",
    flight_number: str = "2300",
    price: int = 500,
    booking_token: str = "token",
) -> Itinerary:
    year, month, day = [int(part) for part in date.split("-")]
    flight = Flight(
        airline=airline,
        airline_name=airline,
        flight_number=flight_number,
        departure_airport_code=origin,
        arrival_airport_code=destination,
        departure_date=(year, month, day),
        arrival_date=(year, month, day),
        departure_time=(8, 0),
        arrival_time=(11, 15),
        travel_time=195,
    )
    return Itinerary(
        airline_code=airline,
        airline_names=[airline],
        flights=[flight],
        travel_time=195,
        departure_airport_code=origin,
        arrival_airport_code=destination,
        departure_date=(year, month, day),
        arrival_date=(year, month, day),
        departure_time=(8, 0),
        arrival_time=(11, 15),
        direct_price=price,
        booking_token=booking_token,
    )


class TestAlaskaBusinessMispricingRegression:
    """Alaska 'Main' brand classified as unknown → leaks into business cabin."""

    def test_alaska_main_classified_as_economy(self):
        assert _classify_cabin_bucket("", "Main") == "economy"

    def test_alaska_main_not_eligible_for_business(self):
        option = _option(485, "Main")
        result = _eligible_booking_options([option], True, cabin="business")
        assert result == []

    def test_alaska_business_uses_base_price_not_economy_fare(self, monkeypatch):
        """Full integration: Alaska 'Main' at $485 + base_price $1990 → must be $1990."""
        request_legs = [
            {"origin": "SEA", "destination": "JFK", "date": "2026-04-15"},
            {"origin": "JFK", "destination": "SEA", "date": "2026-04-20"},
        ]
        itineraries = [
            _make_itinerary(origin="SEA", destination="JFK", airline="AS",
                            flight_number="12", price=900, booking_token="out"),
            _make_itinerary(origin="JFK", destination="SEA", airline="AS",
                            flight_number="13", price=1990, booking_token="ret"),
        ]
        options = [_option(485, "Main")]
        monkeypatch.setattr(selection, "fetch_trip_booking_options",
                            lambda *_a, **_kw: options)

        result = price_selected_trip(
            request_legs, itineraries,
            cabin="business", include_basic_economy=False,
        )
        assert result is not None
        assert result.price == 1990
        assert result.fare_brand is None


class TestJetBlueBrandClassification:
    """JetBlue brands 'Blue', 'Blue Extra', 'Even More' classify as unknown."""

    def test_blue_classified_as_economy(self):
        assert _classify_cabin_bucket("BLUE", "Blue") == "economy"

    def test_blue_extra_classified_as_economy(self):
        assert _classify_cabin_bucket("BLUE EXTRA", "Blue Extra") == "economy"

    def test_even_more_classified_as_economy(self):
        assert _classify_cabin_bucket("EVEN MORE", "Even More") == "economy"

    def test_jetblue_economy_brands_not_eligible_for_business(self):
        brands = [
            _option(159, "Blue Basic", is_basic=True),
            _option(204, "Blue"),
            _option(239, "Blue Extra"),
            _option(383, "Even More"),
        ]
        result = _eligible_booking_options(brands, True, cabin="business")
        assert result == []


class TestSuiteDisambiguation:
    """Branded suites like 'Club Suite' and 'Qsuite' are business, not first."""

    def test_club_suite_is_business(self):
        assert _classify_cabin_bucket("CLUB SUITE", "Club Suite") == "business"

    def test_qsuite_is_business(self):
        assert _classify_cabin_bucket("QSUITE", "Qsuite") == "business"

    def test_sky_suite_is_business(self):
        assert _classify_cabin_bucket("SKY SUITE", "Sky Suite") == "business"

    def test_generic_suite_is_still_first(self):
        """Control: bare 'Suite' should still map to first."""
        assert _classify_cabin_bucket("SUITE", "Suite") == "first"


class TestCrossCabinLeakagePrevention:
    """Economy brands must never appear in non-economy cabin results."""

    @pytest.mark.parametrize("brand_label", [
        "Main", "Blue", "Blue Extra", "Even More", "Saver", "Value",
    ])
    @pytest.mark.parametrize("cabin", ["business", "first", "premium-economy"])
    def test_economy_brand_not_eligible_for_non_economy_cabin(self, brand_label, cabin):
        option = _option(300, brand_label)
        result = _eligible_booking_options([option], True, cabin=cabin)
        assert result == []


class TestCabinBucketPropertyTests:
    """Property-based tests for _classify_cabin_bucket."""

    @given(text=st.text(max_size=50))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_classify_cabin_bucket_never_crashes(self, text):
        result = _classify_cabin_bucket(text, text)
        assert result in ("economy", "premium-economy", "business", "first", "unknown")

    @given(brand_label=st.sampled_from(["Main", "Blue", "Blue Extra", "Even More", "Saver", "Value"]))
    @settings(max_examples=50)
    def test_known_economy_brands_never_leak_to_business(self, brand_label):
        option = _option(300, brand_label)
        result = _eligible_booking_options([option], True, cabin="business")
        assert result == []

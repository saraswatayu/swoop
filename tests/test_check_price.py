"""Tests for swoop.check_price() — targeted price lookup."""

from __future__ import annotations

import inspect
from unittest.mock import patch

import pytest

import swoop
from swoop import check_price, PriceResult
from swoop.decoder import BookingOption, Flight, Itinerary, SearchResult
from swoop.exceptions import SwoopRateLimitError

from tests.factories import (
    encode_rpc_outer,
    make_flight_segment,
    make_full_response,
    make_itinerary_element,
)


class TestPriceResultDataclass:
    """Test PriceResult creation and attributes."""

    def test_basic_creation(self):
        result = PriceResult(price=342)
        assert result.price == 342
        assert result.fare_brand is None
        assert result.is_basic_economy is False
        assert result.booking_options == []
        assert result.itinerary is None
        assert result.rpc_calls == 0

    def test_full_creation(self):
        itin = Itinerary()
        opt = BookingOption(price=342, brand_label="Main Cabin")
        result = PriceResult(
            price=342,
            fare_brand="Main Cabin",
            is_basic_economy=False,
            booking_options=[opt],
            itinerary=itin,
            rpc_calls=3,
        )
        assert result.price == 342
        assert result.fare_brand == "Main Cabin"
        assert len(result.booking_options) == 1
        assert result.rpc_calls == 3


class TestCheckPriceOneWay:
    """Test check_price() for one-way flights."""

    def test_returns_price_on_match(self, fake_primp):
        """One-way check_price returns a PriceResult with the matched price."""
        seg = make_flight_segment(
            airline_code="DL", flight_number="2300",
            dep_airport="JFK", arr_airport="LAX",
        )
        itin = make_itinerary_element([seg])
        response_data = make_full_response(best_itins=[itin])
        fake_primp(200, encode_rpc_outer(response_data))

        result = check_price(
            "DL2300", origin="JFK", destination="LAX", date="2026-06-15",
        )
        assert result is not None
        assert isinstance(result, PriceResult)
        assert result.price > 0
        assert result.rpc_calls == 1

    def test_returns_none_when_no_results(self, fake_primp):
        """One-way check_price returns None when no flights found."""
        response_data = make_full_response(best_itins=[], other_itins=[])
        fake_primp(200, encode_rpc_outer(response_data))

        result = check_price(
            "DL2300", origin="JFK", destination="LAX", date="2026-06-15",
        )
        assert result is None

    def test_returns_none_when_flight_not_found(self, fake_primp):
        """One-way check_price returns None when flight number doesn't match."""
        seg = make_flight_segment(
            airline_code="UA", flight_number="1234",
            dep_airport="JFK", arr_airport="LAX",
        )
        itin = make_itinerary_element([seg])
        response_data = make_full_response(best_itins=[itin])
        fake_primp(200, encode_rpc_outer(response_data))

        result = check_price(
            "DL2300", origin="JFK", destination="LAX", date="2026-06-15",
        )
        assert result is None

    def test_validates_inputs(self):
        """check_price raises ValueError for invalid inputs."""
        with pytest.raises(ValueError, match="origin"):
            check_price("DL2300", origin="XX", destination="LAX", date="2026-06-15")

    def test_rate_limit_propagated(self, fake_primp):
        """Rate limit error from RPC is propagated."""
        fake_primp(429, "")
        with pytest.raises(SwoopRateLimitError):
            check_price(
                "DL2300", origin="JFK", destination="LAX", date="2026-06-15",
                retries=0,
            )


class TestCheckPriceRoundtrip:
    """Test check_price() for roundtrip flights."""

    def test_roundtrip_makes_multiple_rpc_calls(self):
        """Roundtrip check_price resolves two search stages plus exact booking."""
        outbound_itin = Itinerary(
            flights=[Flight(
                airline="DL", flight_number="2300",
                departure_airport_code="JFK", arrival_airport_code="LAX",
                departure_date=(2026, 6, 15), departure_time=(8, 30),
                arrival_date=(2026, 6, 15), arrival_time=(11, 45),
            )],
            direct_price=342,
            booking_token="outbound-token",
        )
        return_itin = Itinerary(
            flights=[Flight(
                airline="DL", flight_number="2301",
                departure_airport_code="LAX", arrival_airport_code="JFK",
                departure_date=(2026, 6, 22), departure_time=(14, 0),
                arrival_date=(2026, 6, 22), arrival_time=(22, 15),
            )],
            direct_price=684,
            booking_token="return-token",
        )

        outbound_result = SearchResult(_raw=[], best=[outbound_itin], other=[])
        return_result = SearchResult(_raw=[], best=[return_itin], other=[])
        booking_options = [
            BookingOption(price=684, brand_label="Main Cabin", is_basic=False, _cabin_bucket="economy"),
            BookingOption(price=580, brand_label="Basic Economy", is_basic=True, _cabin_bucket="economy"),
        ]

        call_count = 0

        def mock_search_from_legs(legs, **kwargs):
            nonlocal call_count
            call_count += 1
            if legs[0].get("selected_legs") is not None:
                return return_result
            return outbound_result

        def mock_fetch_trip_booking(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return booking_options

        with patch("swoop._selection._search_from_legs", side_effect=mock_search_from_legs), \
             patch("swoop._selection.fetch_trip_booking_options", side_effect=mock_fetch_trip_booking):
            result = check_price(
                "DL2300", origin="JFK", destination="LAX", date="2026-06-15",
                return_flight_number="DL2301", return_date="2026-06-22",
            )

        assert result is not None
        assert result.price == 684  # Main Cabin (non-basic)
        assert result.fare_brand == "Main Cabin"
        assert result.is_basic_economy is False
        assert result.rpc_calls == 3
        assert len(result.booking_options) == 2

    def test_roundtrip_return_expansion_has_no_airline_filter(self):
        """Return-stage search should use the return carrier, not the outbound one."""
        outbound_itin = Itinerary(
            flights=[Flight(
                airline="DL", flight_number="2300",
                departure_airport_code="JFK", arrival_airport_code="LAX",
                departure_date=(2026, 6, 15), departure_time=(8, 30),
                arrival_date=(2026, 6, 15), arrival_time=(11, 45),
            )],
            direct_price=342,
            booking_token="outbound-token",
        )
        return_itin = Itinerary(
            flights=[Flight(
                airline="UA", flight_number="456",
                departure_airport_code="LAX", arrival_airport_code="JFK",
                departure_date=(2026, 6, 22), departure_time=(14, 0),
                arrival_date=(2026, 6, 22), arrival_time=(22, 15),
            )],
            direct_price=700,
            booking_token="return-token",
        )
        outbound_result = SearchResult(_raw=[], best=[outbound_itin], other=[])
        return_result = SearchResult(_raw=[], best=[return_itin], other=[])

        search_calls = []

        def mock_search_from_legs(legs, **kwargs):
            search_calls.append(legs)
            if legs[0].get("selected_legs") is not None:
                return return_result
            return outbound_result

        with patch("swoop._selection._search_from_legs", side_effect=mock_search_from_legs), \
             patch("swoop._selection.fetch_trip_booking_options", return_value=[
                 BookingOption(price=700, brand_label="Main Cabin", is_basic=False, _cabin_bucket="economy"),
             ]):
            check_price(
                "DL2300", origin="JFK", destination="LAX", date="2026-06-15",
                return_flight_number="UA456", return_date="2026-06-22",
            )

        assert len(search_calls) == 2
        assert search_calls[1][1]["airlines"] == ["UA"]

    def test_roundtrip_include_basic_economy(self):
        """With include_basic_economy=True, basic fares are eligible."""
        outbound_itin = Itinerary(
            flights=[Flight(
                airline="DL", flight_number="2300",
                departure_airport_code="JFK", arrival_airport_code="LAX",
                departure_date=(2026, 6, 15), departure_time=(8, 30),
                arrival_date=(2026, 6, 15), arrival_time=(11, 45),
            )],
            direct_price=342,
            booking_token="outbound-token",
        )
        return_itin = Itinerary(
            flights=[Flight(
                airline="DL", flight_number="2301",
                departure_airport_code="LAX", arrival_airport_code="JFK",
                departure_date=(2026, 6, 22), departure_time=(14, 0),
                arrival_date=(2026, 6, 22), arrival_time=(22, 15),
            )],
            direct_price=684,
            booking_token="return-token",
        )

        outbound_result = SearchResult(_raw=[], best=[outbound_itin], other=[])
        return_result = SearchResult(_raw=[], best=[return_itin], other=[])
        booking_options = [
            BookingOption(price=684, brand_label="Main Cabin", is_basic=False, _cabin_bucket="economy"),
            BookingOption(price=580, brand_label="Basic Economy", is_basic=True, _cabin_bucket="economy"),
        ]

        def mock_search_from_legs(legs, **kwargs):
            if legs[0].get("selected_legs") is not None:
                return return_result
            return outbound_result

        with patch("swoop._selection._search_from_legs", side_effect=mock_search_from_legs), \
             patch("swoop._selection.fetch_trip_booking_options", return_value=booking_options):
            result = check_price(
                "DL2300", origin="JFK", destination="LAX", date="2026-06-15",
                return_flight_number="DL2301", return_date="2026-06-22",
                include_basic_economy=True,
            )

        assert result is not None
        assert result.price == 580
        assert result.is_basic_economy is True

    def test_roundtrip_business_does_not_downshift_to_economy(self):
        """Business pricing should ignore cheaper lower-cabin booking options."""
        outbound_itin = Itinerary(
            flights=[Flight(
                airline="DL", flight_number="2300",
                departure_airport_code="JFK", arrival_airport_code="LAX",
                departure_date=(2026, 6, 15), departure_time=(8, 30),
                arrival_date=(2026, 6, 15), arrival_time=(11, 45),
            )],
            direct_price=900,
            booking_token="outbound-token",
        )
        return_itin = Itinerary(
            flights=[Flight(
                airline="DL", flight_number="2301",
                departure_airport_code="LAX", arrival_airport_code="JFK",
                departure_date=(2026, 6, 22), departure_time=(14, 0),
                arrival_date=(2026, 6, 22), arrival_time=(22, 15),
            )],
            direct_price=1400,
            booking_token="return-token",
        )
        outbound_result = SearchResult(_raw=[], best=[outbound_itin], other=[])
        return_result = SearchResult(_raw=[], best=[return_itin], other=[])
        booking_options = [
            BookingOption(price=520, brand_label="Main Cabin", is_basic=False, _cabin_bucket="economy"),
            BookingOption(price=1450, brand_label="Delta One", is_basic=False, _cabin_bucket="business"),
        ]

        def mock_search_from_legs(legs, **kwargs):
            if legs[0].get("selected_legs") is not None:
                return return_result
            return outbound_result

        with patch("swoop._selection._search_from_legs", side_effect=mock_search_from_legs), \
             patch("swoop._selection.fetch_trip_booking_options", return_value=booking_options):
            result = check_price(
                "DL2300", origin="JFK", destination="LAX", date="2026-06-15",
                return_flight_number="DL2301", return_date="2026-06-22",
                cabin="business",
            )

        assert result is not None
        assert result.price == 1450
        assert result.fare_brand == "Delta One"

    def test_roundtrip_returns_none_when_outbound_not_found(self):
        """Returns None if outbound flight not found."""
        empty_result = SearchResult(_raw=[], best=[], other=[])

        with patch("swoop._selection._search_from_legs", return_value=empty_result):
            result = check_price(
                "DL2300", origin="JFK", destination="LAX", date="2026-06-15",
                return_date="2026-06-22",
            )

        assert result is None

    def test_roundtrip_falls_back_on_booking_failure(self):
        """Falls back to direct_price when GetBookingResults fails."""
        outbound_itin = Itinerary(
            flights=[Flight(
                airline="DL", flight_number="2300",
                departure_airport_code="JFK", arrival_airport_code="LAX",
                departure_date=(2026, 6, 15), departure_time=(8, 30),
                arrival_date=(2026, 6, 15), arrival_time=(11, 45),
            )],
            direct_price=342,
            booking_token="outbound-token",
        )
        return_itin = Itinerary(
            flights=[Flight(
                airline="DL", flight_number="2301",
                departure_airport_code="LAX", arrival_airport_code="JFK",
                departure_date=(2026, 6, 22), departure_time=(14, 0),
                arrival_date=(2026, 6, 22), arrival_time=(22, 15),
            )],
            direct_price=650,
            booking_token="return-token",
        )

        outbound_result = SearchResult(_raw=[], best=[outbound_itin], other=[])
        return_result = SearchResult(_raw=[], best=[return_itin], other=[])

        def mock_search_from_legs(legs, **kwargs):
            if legs[0].get("selected_legs") is not None:
                return return_result
            return outbound_result

        with patch("swoop._selection._search_from_legs", side_effect=mock_search_from_legs), \
             patch("swoop._selection.fetch_trip_booking_options", side_effect=SwoopRateLimitError()):
            result = check_price(
                "DL2300", origin="JFK", destination="LAX", date="2026-06-15",
                return_flight_number="DL2301", return_date="2026-06-22",
            )

        assert result is not None
        assert result.price == 650  # Fell back to direct_price


class TestCheckPriceUnknownCabinBucket:
    """Unknown cabin bucket must not be accepted for non-economy cabins."""

    def test_roundtrip_business_does_not_accept_unknown_cabin_bucket(self):
        outbound_itin = Itinerary(
            flights=[Flight(
                airline="AS", flight_number="12",
                departure_airport_code="SEA", arrival_airport_code="JFK",
                departure_date=(2026, 6, 15), departure_time=(8, 30),
                arrival_date=(2026, 6, 15), arrival_time=(16, 45),
            )],
            direct_price=900,
            booking_token="outbound-token",
        )
        return_itin = Itinerary(
            flights=[Flight(
                airline="AS", flight_number="13",
                departure_airport_code="JFK", arrival_airport_code="SEA",
                departure_date=(2026, 6, 22), departure_time=(14, 0),
                arrival_date=(2026, 6, 22), arrival_time=(17, 15),
            )],
            direct_price=1990,
            booking_token="return-token",
        )
        outbound_result = SearchResult(_raw=[], best=[outbound_itin], other=[])
        return_result = SearchResult(_raw=[], best=[return_itin], other=[])
        booking_options = [
            BookingOption(price=485, brand_label="Main", is_basic=False, _cabin_bucket="unknown"),
        ]

        def mock_search_from_legs(legs, **kwargs):
            if legs[0].get("selected_legs") is not None:
                return return_result
            return outbound_result

        with patch("swoop._selection._search_from_legs", side_effect=mock_search_from_legs), \
             patch("swoop._selection.fetch_trip_booking_options", return_value=booking_options):
            result = check_price(
                "AS12", origin="SEA", destination="JFK", date="2026-06-15",
                return_flight_number="AS13", return_date="2026-06-22",
                cabin="business",
            )

        assert result is not None
        assert result.price == 1990  # base_price, NOT the economy $485
        assert result.fare_brand is None


class TestCheckPriceRetryDefault:
    """check_price inherits the retries=2 default."""

    def test_default_retries_is_2(self):
        sig = inspect.signature(swoop.check_price)
        assert sig.parameters["retries"].default == 2

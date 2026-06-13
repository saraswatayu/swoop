"""Protobuf roundtrip tests.

Verify that ItinerarySummary and TFSData can encode and decode without
data loss. These tests protect against protobuf schema drift.
"""

import base64

import pytest

from swoop.builders import ItinerarySummary, TFSData, SearchLeg, _PBPassengers as Passengers
from swoop import flights_pb2 as PB


class TestItinerarySummaryRoundtrip:
    """ItinerarySummary b64 encode -> from_b64 -> verify.

    ItinerarySummary.price stores the raw protobuf value (no conversion).
    The authoritative price comes from Itinerary.direct_price.
    """

    def test_basic_roundtrip(self):
        pb = PB.ItinerarySummary()
        pb.flights = "test-flight-data"
        pb.price.price = 35000
        pb.price.currency = "USD"
        b64 = base64.b64encode(pb.SerializeToString()).decode()

        decoded = ItinerarySummary.from_b64(b64)
        assert decoded.flights == "test-flight-data"
        assert decoded.price == 35000  # raw protobuf value
        assert decoded.currency == "USD"

    def test_zero_price(self):
        pb = PB.ItinerarySummary()
        pb.flights = "zero"
        pb.price.price = 0
        pb.price.currency = "USD"
        b64 = base64.b64encode(pb.SerializeToString()).decode()

        decoded = ItinerarySummary.from_b64(b64)
        assert decoded.price == 0

    def test_high_price(self):
        pb = PB.ItinerarySummary()
        pb.flights = "expensive"
        pb.price.price = 999900
        pb.price.currency = "USD"
        b64 = base64.b64encode(pb.SerializeToString()).decode()

        decoded = ItinerarySummary.from_b64(b64)
        assert decoded.price == 999900  # raw protobuf value

    def test_non_usd_currency(self):
        pb = PB.ItinerarySummary()
        pb.flights = "intl"
        pb.price.price = 50000
        pb.price.currency = "EUR"
        b64 = base64.b64encode(pb.SerializeToString()).decode()

        decoded = ItinerarySummary.from_b64(b64)
        assert decoded.currency == "EUR"
        assert decoded.price == 50000

    def test_gbp_currency(self):
        pb = PB.ItinerarySummary()
        pb.flights = "gbp-flight"
        pb.price.price = 15000
        pb.price.currency = "GBP"
        b64 = base64.b64encode(pb.SerializeToString()).decode()

        decoded = ItinerarySummary.from_b64(b64)
        assert decoded.currency == "GBP"
        assert decoded.price == 15000

    def test_jpy_currency(self):
        pb = PB.ItinerarySummary()
        pb.flights = "jpy-flight"
        pb.price.price = 15000
        pb.price.currency = "JPY"
        b64 = base64.b64encode(pb.SerializeToString()).decode()

        decoded = ItinerarySummary.from_b64(b64)
        assert decoded.currency == "JPY"
        assert decoded.price == 15000

    def test_inr_currency(self):
        pb = PB.ItinerarySummary()
        pb.flights = "inr-flight"
        pb.price.price = 8500
        pb.price.currency = "INR"
        b64 = base64.b64encode(pb.SerializeToString()).decode()

        decoded = ItinerarySummary.from_b64(b64)
        assert decoded.currency == "INR"
        assert decoded.price == 8500

    def test_invalid_b64_returns_defaults(self):
        decoded = ItinerarySummary.from_b64("not-valid-base64!!!")
        assert decoded.flights == ""
        assert decoded.price == 0
        assert decoded.currency == ""

    def test_empty_string_returns_defaults(self):
        decoded = ItinerarySummary.from_b64("")
        assert decoded.price == 0

    def test_empty_protobuf_returns_defaults(self):
        b64 = base64.b64encode(b"").decode()
        decoded = ItinerarySummary.from_b64(b64)
        assert decoded.flights == ""
        assert decoded.price == 0


class TestTFSDataRoundtrip:
    """TFSData serialization roundtrip."""

    def test_one_way_roundtrip(self):
        leg = SearchLeg(
            date="2026-06-15",
            from_airport="JFK",
            to_airport="LAX",
        )
        tfs = TFSData.from_interface(
            flight_data=[leg],
            trip="one-way",
            passengers=Passengers(adults=1),
            seat="economy",
        )

        # Serialize
        serialized = tfs.to_string()
        assert isinstance(serialized, bytes)
        assert len(serialized) > 0

        # Parse back
        info = PB.Info()
        info.ParseFromString(serialized)
        assert len(info.data) == 1
        assert info.data[0].from_flight.airport == "JFK"
        assert info.data[0].to_flight.airport == "LAX"
        assert info.data[0].date == "2026-06-15"

    def test_roundtrip_two_legs(self):
        legs = [
            SearchLeg(date="2026-06-15", from_airport="JFK", to_airport="LAX"),
            SearchLeg(date="2026-06-22", from_airport="LAX", to_airport="JFK"),
        ]
        tfs = TFSData.from_interface(
            flight_data=legs,
            trip="round-trip",
            passengers=Passengers(adults=1),
            seat="economy",
        )

        info = PB.Info()
        info.ParseFromString(tfs.to_string())
        assert len(info.data) == 2
        assert info.data[0].from_flight.airport == "JFK"
        assert info.data[1].from_flight.airport == "LAX"

    def test_business_class(self):
        leg = SearchLeg(date="2026-06-15", from_airport="SFO", to_airport="NRT")
        tfs = TFSData.from_interface(
            flight_data=[leg],
            trip="one-way",
            passengers=Passengers(adults=1),
            seat="business",
        )

        info = PB.Info()
        info.ParseFromString(tfs.to_string())
        assert info.seat == PB.Seat.BUSINESS

    def test_multiple_passengers(self):
        leg = SearchLeg(date="2026-06-15", from_airport="JFK", to_airport="LAX")
        tfs = TFSData.from_interface(
            flight_data=[leg],
            trip="one-way",
            passengers=Passengers(adults=2, children=1),
            seat="economy",
        )

        info = PB.Info()
        info.ParseFromString(tfs.to_string())
        assert len(info.passengers) == 3

    def test_b64_encoding(self):
        leg = SearchLeg(date="2026-06-15", from_airport="JFK", to_airport="LAX")
        tfs = TFSData.from_interface(
            flight_data=[leg],
            trip="one-way",
            passengers=Passengers(adults=1),
            seat="economy",
        )

        b64 = tfs.as_b64()
        assert isinstance(b64, bytes)
        # Should be valid base64
        decoded = base64.b64decode(b64)
        assert len(decoded) > 0

    def test_airlines_filter(self):
        leg = SearchLeg(
            date="2026-06-15",
            from_airport="JFK",
            to_airport="LAX",
            airlines=["DL", "UA"],
        )
        tfs = TFSData.from_interface(
            flight_data=[leg],
            trip="one-way",
            passengers=Passengers(adults=1),
            seat="economy",
        )

        info = PB.Info()
        info.ParseFromString(tfs.to_string())
        assert "DL" in list(info.data[0].airlines)
        assert "UA" in list(info.data[0].airlines)

    def test_max_stops(self):
        leg = SearchLeg(
            date="2026-06-15",
            from_airport="JFK",
            to_airport="LAX",
            max_stops=0,
        )
        tfs = TFSData.from_interface(
            flight_data=[leg],
            trip="one-way",
            passengers=Passengers(adults=1),
            seat="economy",
            max_stops=0,
        )

        info = PB.Info()
        info.ParseFromString(tfs.to_string())
        assert info.data[0].max_stops == 0


class TestPassengersValidation:
    """Passengers validation edge cases."""

    def test_too_many_passengers_raises(self):
        with pytest.raises(ValueError, match="Too many passengers"):
            Passengers(adults=8, children=2)

    def test_too_many_infants_raises(self):
        with pytest.raises(ValueError, match="infant"):
            Passengers(adults=1, infants_on_lap=2)

    def test_max_passengers_ok(self):
        # 9 is the max
        p = Passengers(adults=9)
        assert len(p.pb) == 9

    def test_mixed_passengers(self):
        p = Passengers(adults=2, children=1, infants_on_lap=1)
        assert len(p.pb) == 4


class TestSearchLegValidation:
    """SearchLeg validation."""

    def test_invalid_airline_code_raises(self):
        with pytest.raises(ValueError, match="Invalid airline code"):
            SearchLeg(date="2026-06-15", from_airport="JFK", to_airport="LAX", airlines=["DELTA"])

    def test_alliance_accepted(self):
        leg = SearchLeg(
            date="2026-06-15",
            from_airport="JFK",
            to_airport="LAX",
            airlines=["STAR_ALLIANCE"],
        )
        assert leg.airlines == ["STAR_ALLIANCE"]

    def test_airport_uppercased(self):
        leg = SearchLeg(date="2026-06-15", from_airport="jfk", to_airport="lax")
        assert leg.from_airports == ["JFK"]
        assert leg.to_airports == ["LAX"]

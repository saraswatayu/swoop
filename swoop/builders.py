"""Typed protobuf payload builders for Google Flights RPC requests.

Constructs the serialized protobuf payloads used in the ?tfs= query parameter
and decodes ItinerarySummary responses. Uses string IATA codes directly
(no Airport enum dependency).
"""

import base64
from dataclasses import dataclass
from typing import List, Literal, Optional, Union

from . import flights_pb2 as PB

AIRLINE_ALLIANCES = ["SKYTEAM", "STAR_ALLIANCE", "ONEWORLD"]

CabinClass = Literal["economy", "premium-economy", "business", "first"]

# Cabin class mapping (matches Google Flights internal Seat enum values)
CABIN_CLASS_MAP: dict[CabinClass, int] = {
    "economy": 1,
    "premium-economy": 2,
    "business": 3,
    "first": 4,
}


class SearchLeg:
    """A search leg defining origin, destination, date, and optional filters.

    Each search has one leg (one-way) or two legs (roundtrip). This class
    builds the per-leg protobuf data used in the ``?tfs=`` query parameter.

    Args:
        date: Departure date (YYYY-MM-DD).
        from_airport: Origin IATA code or list of IATA codes.
        to_airport: Destination IATA code or list of IATA codes.
        max_stops: Maximum number of stops. None = any.
        airlines: Filter to these airline IATA codes.

    Attributes:
        from_airports: Origin airport(s) as a ``list[str]``. Always a list,
            even for single-airport legs. Read-only.
        to_airports: Same as ``from_airports`` for the destination.
    """

    __slots__ = ("date", "_from_airports", "_to_airports", "max_stops", "airlines")

    def __init__(
        self,
        *,
        date: str,
        from_airport: str | list[str],
        to_airport: str | list[str],
        max_stops: Optional[int] = None,
        airlines: Optional[List[str]] = None,
    ):
        self.date = date

        if isinstance(from_airport, str):
            if not from_airport:
                raise ValueError("from_airport must not be empty")
            self._from_airports: list[str] = [from_airport.upper()]
        elif isinstance(from_airport, list):
            if not from_airport:
                raise ValueError("from_airport list must not be empty")
            self._from_airports = [a.upper() for a in from_airport]
        else:
            raise TypeError(f"from_airport must be str or list[str], got {type(from_airport).__name__}")

        if isinstance(to_airport, str):
            if not to_airport:
                raise ValueError("to_airport must not be empty")
            self._to_airports: list[str] = [to_airport.upper()]
        elif isinstance(to_airport, list):
            if not to_airport:
                raise ValueError("to_airport list must not be empty")
            self._to_airports = [a.upper() for a in to_airport]
        else:
            raise TypeError(f"to_airport must be str or list[str], got {type(to_airport).__name__}")

        self.max_stops = max_stops

        if airlines is not None:
            self.airlines = []
            for airline in airlines:
                if not isinstance(airline, str):
                    raise TypeError(f"airline codes must be str, got {type(airline).__name__}")
                airline = airline.upper()
                if not (len(airline) == 2 or airline in AIRLINE_ALLIANCES):
                    raise ValueError(
                        f"Invalid airline code: {airline}. "
                        f"Must be 2-char IATA code or alliance: {AIRLINE_ALLIANCES}"
                    )
                self.airlines.append(airline)
        else:
            self.airlines = None

    @property
    def from_airports(self) -> list[str]:
        """Origin airport(s) as a list. Always a list, even for single-airport legs."""
        return self._from_airports

    @property
    def to_airports(self) -> list[str]:
        """Destination airport(s) as a list. Always a list, even for single-airport legs."""
        return self._to_airports

    def apply_to(self, info) -> None:
        """Write this leg into a protobuf Info message.

        The TFS/protobuf path only supports a single airport per leg.
        Multi-airport lists are supported by the RPC search path
        (_normalize_rpc_leg / _build_segment_from_leg), not here.
        Raises ValueError when called with more than one airport.
        """
        if len(self._from_airports) > 1 or len(self._to_airports) > 1:
            raise ValueError(
                "SearchLeg.apply_to() (TFS/protobuf path) supports only a single "
                "airport per leg. For multi-airport searches use swoop.search() or "
                "swoop.search_raw() directly."
            )
        data = info.data.add()
        data.date = self.date
        data.from_flight.airport = self._from_airports[0]
        data.to_flight.airport = self._to_airports[0]
        if self.max_stops is not None:
            data.max_stops = self.max_stops
        if self.airlines is not None:
            data.airlines.extend(self.airlines)


class _PBPassengers:
    def __init__(
        self,
        *,
        adults: int = 1,
        children: int = 0,
        infants_in_seat: int = 0,
        infants_on_lap: int = 0,
    ):
        total = adults + children + infants_in_seat + infants_on_lap
        if total > 9:
            raise ValueError(f"Too many passengers ({total} > 9 max)")
        if infants_on_lap > adults:
            raise ValueError(
                f"Need at least one adult per infant on lap "
                f"({infants_on_lap} infants but only {adults} adults)"
            )

        self.pb = []
        self.pb += [PB.Passenger.ADULT for _ in range(adults)]
        self.pb += [PB.Passenger.CHILD for _ in range(children)]
        self.pb += [PB.Passenger.INFANT_IN_SEAT for _ in range(infants_in_seat)]
        self.pb += [PB.Passenger.INFANT_ON_LAP for _ in range(infants_on_lap)]

    def apply_to(self, info) -> None:
        for p in self.pb:
            info.passengers.append(p)


class TFSData:
    """Builds the ``?tfs=`` query parameter for Google Flights URL.

    TFS (Travel Flight Search) is the parameter name Google Flights uses
    in its URL to encode the serialized protobuf search request.
    """

    def __init__(
        self,
        *,
        flight_data: List[SearchLeg],
        seat: int,
        trip: int,
        passengers: _PBPassengers,
        max_stops: Optional[int] = None,
        exclude_basic_economy: bool = False,
    ):
        self.flight_data = flight_data
        self.seat = seat
        self.trip = trip
        self.passengers = passengers
        self.max_stops = max_stops
        self.exclude_basic_economy = exclude_basic_economy

    def pb(self):
        info = PB.Info()
        info.seat = self.seat
        info.trip = self.trip
        self.passengers.apply_to(info)

        for fd in self.flight_data:
            fd.apply_to(info)

        if self.max_stops is not None:
            for flight in info.data:
                flight.max_stops = self.max_stops

        if self.exclude_basic_economy:
            info.exclude_basic_economy = True

        return info

    def to_string(self) -> bytes:
        return self.pb().SerializeToString()

    def as_b64(self) -> bytes:
        return base64.b64encode(self.to_string())

    @staticmethod
    def from_interface(
        *,
        flight_data: List[SearchLeg],
        trip: Literal["round-trip", "one-way", "multi-city"],
        passengers: _PBPassengers,
        seat: Literal["economy", "premium-economy", "business", "first"],
        max_stops: Optional[int] = None,
        exclude_basic_economy: bool = False,
    ) -> "TFSData":
        trip_t = {
            "round-trip": PB.Trip.ROUND_TRIP,
            "one-way": PB.Trip.ONE_WAY,
            "multi-city": PB.Trip.MULTI_CITY,
        }[trip]
        seat_t = {
            "economy": PB.Seat.ECONOMY,
            "premium-economy": PB.Seat.PREMIUM_ECONOMY,
            "business": PB.Seat.BUSINESS,
            "first": PB.Seat.FIRST,
        }[seat]
        return TFSData(
            flight_data=flight_data,
            seat=seat_t,
            trip=trip_t,
            passengers=passengers,
            max_stops=max_stops,
            exclude_basic_economy=exclude_basic_economy,
        )


@dataclass
class ItinerarySummary:
    """Decoded price data from a Google Flights itinerary summary token.

    The ``currency`` field is the 3-letter ISO 4217 code. The ``price``
    field stores the raw protobuf value — it is NOT used for display
    pricing. The authoritative price comes from ``Itinerary.direct_price``
    (the display integer at ``response[1][0][1]``).
    """

    flights: str
    price: float
    currency: str

    @classmethod
    def from_b64(cls, b64_string: str) -> "ItinerarySummary":
        try:
            raw = base64.b64decode(b64_string)
            pb = PB.ItinerarySummary()
            pb.ParseFromString(raw)
            return cls(pb.flights, pb.price.price, pb.price.currency)
        except Exception:
            return cls("", 0, "")  # empty currency = unknown (Itinerary.currency returns None)

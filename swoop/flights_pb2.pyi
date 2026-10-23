"""Minimal type stubs for the generated ``flights_pb2`` module.

The generated ``flights_pb2.py`` is excluded from pyright analysis
(see ``[tool.pyright].exclude`` in ``pyproject.toml``), but excluding
it also means call sites like ``swoop/builders.py`` had no type info
for ``PB.Passenger.ADULT``, ``PB.Info()`` etc. — a typo only surfaced
at runtime as ``AttributeError``.

Cover what ``swoop/builders.py`` actually touches. The stubs reflect
``swoop/flights.proto`` (5 messages, 3 enums); regenerating
``flights_pb2.py`` does not require touching this file unless the
schema changes.
"""

from typing import List

class Passenger:
    UNKNOWN_PASSENGER: int
    ADULT: int
    CHILD: int
    INFANT_IN_SEAT: int
    INFANT_ON_LAP: int

class Trip:
    UNKNOWN_TRIP: int
    ROUND_TRIP: int
    ONE_WAY: int
    MULTI_CITY: int

class Seat:
    UNKNOWN_SEAT: int
    ECONOMY: int
    PREMIUM_ECONOMY: int
    BUSINESS: int
    FIRST: int

class Airport:
    airport: str
    def __init__(self) -> None: ...

class FlightData:
    date: str
    from_flight: Airport
    to_flight: Airport
    max_stops: int
    airlines: List[str]
    max_duration: int
    connecting_airports: List[str]
    exclude_connecting_airports: List[str]
    min_layover_duration: int
    max_layover_duration: int
    def __init__(self) -> None: ...

class Baggage:
    carry_on_bags: int
    checked_bags: int
    def __init__(self) -> None: ...

class Info:
    data: List[FlightData]
    seat: int
    max_price: int
    baggage: Baggage
    passengers: List[int]
    trip: int
    exclude_basic_economy: bool
    def __init__(self) -> None: ...
    def SerializeToString(self) -> bytes: ...

class Price:
    price: int
    currency: str
    def __init__(self) -> None: ...

class ItinerarySummary:
    flights: str
    price: Price
    def __init__(self) -> None: ...
    def ParseFromString(self, data: bytes) -> None: ...

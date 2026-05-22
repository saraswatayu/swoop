"""Public result models for staged trip search and pricing."""

from dataclasses import dataclass, field
from typing import Optional

from .decoder import BookingOption, Itinerary, PriceRange, _flight_summary_repr


@dataclass
class Passengers:
    """Passenger counts for a flight search."""

    adults: int = 1
    children: int = 0
    infants_in_seat: int = 0
    infants_on_lap: int = 0


@dataclass
class TransportConfig:
    """HTTP transport configuration for API requests."""

    timeout: int = 90
    retries: int = 2
    country: Optional[str] = None
    proxy: Optional[str] = None
    impersonate: Optional[str] = None


@dataclass
class TripLeg:
    """A single requested leg paired with its resolved itinerary."""

    origin: str
    destination: str
    date: str
    itinerary: Optional[Itinerary] = None

    def __repr__(self) -> str:
        parts = [f"{self.origin}->{self.destination}", self.date]
        if self.itinerary and self.itinerary.segments:
            parts.append(_flight_summary_repr(self.itinerary.segments))
        return f"TripLeg({' '.join(parts)})"


@dataclass
class TripOption:
    """A complete trip option spanning one or more requested legs.

    ``price`` is in the currency's major unit (e.g. 250 for $250 USD,
    6725 for ₹6,725 INR). ``currency`` is the ISO 4217 code.
    """

    selector: str
    price: Optional[int] = None
    currency: Optional[str] = None
    legs: list[TripLeg] = field(default_factory=list)

    def __repr__(self) -> str:
        parts = []
        if self.price is not None:
            parts.append(f"price={self.price}")
        if len(self.legs) == 1:
            leg = self.legs[0]
            parts.append(f"{leg.origin}->{leg.destination}")
            if leg.itinerary and leg.itinerary.segments:
                parts.append(_flight_summary_repr(leg.itinerary.segments))
        elif self.legs:
            parts.append(f"{len(self.legs)} legs")
        return f"TripOption({' '.join(parts)})"


@dataclass
class SearchResult:
    """Public trip-level search result."""

    results: list[TripOption] = field(default_factory=list)
    price_range: Optional[PriceRange] = None
    is_complete: bool = True

    @property
    def currency(self) -> Optional[str]:
        """ISO 4217 currency code derived from the first result, or None."""
        return self.results[0].currency if self.results else None

    def __repr__(self) -> str:
        n = len(self.results)
        parts = [f"{n} result{'s' if n != 1 else ''}"]
        if self.price_range and (self.price_range.low is not None or self.price_range.high is not None):
            lo = self.price_range.low
            hi = self.price_range.high
            if lo is not None and hi is not None:
                parts.append(f"price_range={lo}-{hi}")
            elif lo is not None:
                parts.append(f"price_range={lo}-?")
            else:
                parts.append(f"price_range=?-{hi}")
        if not self.is_complete:
            parts.append("incomplete")
        return f"SearchResult({', '.join(parts)})"


@dataclass
class SelectedLeg:
    """A leg with a specific flight selection for pricing."""

    flight_number: str
    origin: str
    destination: str
    date: str


@dataclass
class ResolvedLeg:
    """A resolved leg in a price check result."""

    flight_summary: str
    origin: str
    destination: str
    date: str
    itinerary: Optional[Itinerary] = None
    selection: str = "explicit"

    def __repr__(self) -> str:
        parts = []
        if self.flight_summary:
            parts.append(self.flight_summary)
        parts.append(f"{self.origin}->{self.destination}")
        parts.append(self.date)
        return f"ResolvedLeg({' '.join(parts)})"


@dataclass
class Deal:
    """A single flight deal from Google Flights deals discovery.

    ``price`` and ``typical_price`` are in the currency's major unit
    (e.g. 342 for $342 USD).
    """

    origin: str
    destination: str
    destination_city: str
    destination_country: str
    departure_date: str
    return_date: Optional[str]
    price: int
    typical_price: Optional[int]
    discount_pct: Optional[int]
    airline_code: str
    airline_name: str
    duration_minutes: Optional[int]
    stops: int
    trip_days: Optional[int]
    currency: Optional[str] = None
    booking_url: Optional[str] = None

    def __repr__(self) -> str:
        parts = [f"{self.origin}->{self.destination}"]
        if self.destination_city:
            parts.append(self.destination_city)
        if self.price is not None:
            parts.append(f"price={self.price}")
        if self.discount_pct is not None:
            parts.append(f"{self.discount_pct}% off")
        return f"Deal({' '.join(parts)})"


@dataclass
class DealsResult:
    """Result of a deals discovery query."""

    deals: list[Deal] = field(default_factory=list)
    origin: str = ""

    @property
    def currency(self) -> Optional[str]:
        """ISO 4217 currency code derived from the first deal, or None."""
        return self.deals[0].currency if self.deals else None

    def __repr__(self) -> str:
        n = len(self.deals)
        parts = [f"{n} deal{'s' if n != 1 else ''}"]
        if self.origin:
            parts.append(f"from {self.origin}")
        return f"DealsResult({', '.join(parts)})"


@dataclass
class PriceResult:
    """Result of a targeted price check for a specific trip.

    ``price`` is in the currency's major unit (e.g. 342 for $342 USD,
    5540 for ¥5,540 JPY). ``currency`` is the ISO 4217 code.
    """

    price: int
    currency: Optional[str] = None
    fare_brand: Optional[str] = None
    is_basic_economy: bool = False
    booking_options: list[BookingOption] = field(default_factory=list)
    itinerary: Optional[Itinerary] = None
    resolved_legs: list[ResolvedLeg] = field(default_factory=list)
    rpc_calls: int = 0

    def __repr__(self) -> str:
        parts = [f"price={self.price}"]
        if self.fare_brand:
            parts.append(f"'{self.fare_brand}'")
        elif self.is_basic_economy:
            parts.append("basic_economy")
        detail = []
        if self.resolved_legs:
            detail.append(f"{len(self.resolved_legs)} leg{'s' if len(self.resolved_legs) != 1 else ''}")
        if self.booking_options:
            detail.append(f"{len(self.booking_options)} option{'s' if len(self.booking_options) != 1 else ''}")
        if detail:
            parts.append(", ".join(detail))
        return f"PriceResult({' '.join(parts)})"

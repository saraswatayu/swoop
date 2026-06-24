"""Public result models for staged trip search and pricing."""

import hashlib
from dataclasses import dataclass, field
from typing import Optional

from ._regions import Region
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

    ``airlines`` and ``airline_names`` are parallel lists — a deal can
    span multiple carriers on a roundtrip. The upstream surfaces a single
    primary carrier today; this list shape is forward-compatible.

    ``destination_region`` is derived from the destination IATA via the
    optional ``airportsdata`` dependency. When that dependency isn't
    installed, the field is ``None``.

    ``fingerprint`` is a stable identity hash useful for caching and
    diffing across runs. It excludes ``price`` so a deal that drops in
    price still hashes to the same id.
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
    airlines: list[str] = field(default_factory=list)
    airline_names: list[str] = field(default_factory=list)
    duration_minutes: Optional[int] = None
    stops: Optional[int] = None
    trip_days: Optional[int] = None
    destination_region: Optional[Region] = None
    currency: Optional[str] = None
    booking_url: Optional[str] = None
    # Query context: the deals() filters in effect when this Deal was
    # discovered. Persisted so search_deal()/price_deal() can resolve to
    # the SAME product (e.g. business class, basic economy) rather than
    # silently re-pricing as default economy / 1 adult / no basic.
    query_cabin: Optional[str] = None
    query_adults: int = 1
    query_include_basic_economy: bool = False

    @property
    def fingerprint(self) -> str:
        """Stable identity hash for caching and diff. Excludes price.

        Inputs are normalized (upper + strip on codes/dates, upper +
        strip on airlines) so cosmetic upstream drift (casing,
        whitespace) does not invent ``new``/``gone`` diff events for the
        same trip across runs.

        Includes the discovery query context (``query_cabin``,
        ``query_adults``, ``query_include_basic_economy``) — otherwise
        the same route discovered under different filters (e.g. business
        vs economy) collides on fingerprint and is diffed as a fake
        price_change instead of being separate identities. The cache
        schema_version was bumped to 2 when this changed; older v1
        snapshots are treated as no-prior-cache.
        """
        airlines = sorted(a.strip().upper() for a in self.airlines if a)
        parts = [
            self.origin.strip().upper(),
            self.destination.strip().upper(),
            self.departure_date.strip(),
            (self.return_date or "").strip(),
            ",".join(airlines),
            (self.query_cabin or "").strip().lower(),
            str(self.query_adults),
            "1" if self.query_include_basic_economy else "0",
        ]
        joined = "|".join(parts)
        return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:12]

    def to_search_kwargs(self) -> dict:
        """Convert this Deal into keyword arguments for :func:`swoop.search`.

        Useful when you want to see the specific itineraries that make up
        a deal — the deals endpoint surfaces the price and dates but not
        the actual flight options. Filter sentinel "*" (multi-airline)
        is dropped so the airlines list contains only real IATA codes.

        Example::

            from swoop import deals, search
            result = deals("JFK")
            top_deal = result.deals[0]
            itineraries = search(**top_deal.to_search_kwargs())
        """
        real_airlines = [a for a in self.airlines if a and a != "*"]
        kwargs: dict = {
            "origin": self.origin,
            "destination": self.destination,
            "date": self.departure_date,
        }
        if self.return_date:
            kwargs["return_date"] = self.return_date
        if real_airlines:
            kwargs["airlines"] = real_airlines
        # Carry the deals() query context forward so the bridge resolves
        # to the SAME product the user discovered (cabin/passengers/basic-
        # economy inclusion). Defaults match search()'s defaults so no-op
        # when the deal was discovered with the deals() defaults.
        if self.query_cabin:
            kwargs["cabin"] = self.query_cabin
        if self.query_adults != 1:
            from .models import Passengers
            kwargs["passengers"] = Passengers(adults=self.query_adults)
        if self.query_include_basic_economy:
            kwargs["include_basic_economy"] = True
        return kwargs

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
    """Result of a deals discovery query.

    ``partial`` is ``True`` when this result is missing data because one
    or more origins failed to fetch in ``per_origin=True`` mode. The
    watcher uses this to refuse to overwrite the cache on partial
    failures (otherwise the missing-origin's deals would be wrongly
    diff'd as ``gone`` and persisted as the new baseline).
    """

    deals: list[Deal] = field(default_factory=list)
    origin: str = ""
    partial: bool = False

    @property
    def currency(self) -> Optional[str]:
        """ISO 4217 currency code derived from the first deal, or None."""
        return self.deals[0].currency if self.deals else None

    def __repr__(self) -> str:
        n = len(self.deals)
        parts = [f"{n} deal{'s' if n != 1 else ''}"]
        if self.origin:
            parts.append(f"from {self.origin}")
        if self.partial:
            parts.append("(partial)")
        return f"DealsResult({', '.join(parts)})"


@dataclass
class PriceChange:
    """A price movement on a deal between two watcher runs.

    Not ``frozen``: the ``prior``/``current`` Deal fields are mutable
    dataclasses (Deal is not frozen because its lists are mutable), so
    ``frozen=True`` here would advertise hashability that doesn't
    actually work — ``hash(PriceChange(...))`` would raise
    ``TypeError: unhashable type: 'Deal'``.
    """

    prior: Deal
    current: Deal

    @property
    def delta(self) -> int:
        """Current price minus prior price. Negative = price drop."""
        return self.current.price - self.prior.price

    @property
    def delta_pct(self) -> float:
        """Percent change from prior to current price."""
        if self.prior.price == 0:
            return 0.0
        return (self.current.price - self.prior.price) / self.prior.price * 100.0

    def __repr__(self) -> str:
        sign = "+" if self.delta >= 0 else ""
        return (
            f"PriceChange({self.current.origin}->{self.current.destination} "
            f"{self.prior.price}->{self.current.price} ({sign}{self.delta_pct:.0f}%))"
        )


@dataclass
class DealsDiff:
    """Diff between two DealsResult runs of the same query.

    ``new`` are deals in the current run that weren't in the prior.
    ``gone`` are deals in the prior that aren't in the current.
    ``price_changes`` are deals with the same fingerprint but a different
    price. ``unchanged`` are deals with both the same fingerprint AND the
    same price.

    Not ``frozen``: every field is a mutable list, so ``frozen=True``
    would advertise hashability that doesn't work — ``hash(DealsDiff())``
    would raise ``TypeError: unhashable type: 'list'``.
    """

    new: list[Deal] = field(default_factory=list)
    gone: list[Deal] = field(default_factory=list)
    price_changes: list[PriceChange] = field(default_factory=list)
    unchanged: list[Deal] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.new or self.gone or self.price_changes)

    def __repr__(self) -> str:
        return (
            f"DealsDiff(new={len(self.new)}, gone={len(self.gone)}, "
            f"price_changes={len(self.price_changes)}, unchanged={len(self.unchanged)})"
        )


@dataclass
class PriceResult:
    """Result of a targeted price check for a specific trip.

    ``price`` is in the currency's major unit (e.g. 342 for $342 USD,
    5540 for ¥5,540 JPY). ``currency`` is the ISO 4217 code.

    ``is_estimate`` is ``True`` when ``price`` is the search-derived shopping
    price rather than a confirmed bookable fare — i.e. the booking lookup was
    skipped (no booking token) or degraded (a non-outage error), so no eligible
    booking option was found. ``False`` means the price came from an actual
    booking option. (An upstream outage during the lookup raises rather than
    returning an estimate.)
    """

    price: int
    currency: Optional[str] = None
    fare_brand: Optional[str] = None
    is_basic_economy: bool = False
    is_estimate: bool = False
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
        if self.is_estimate:
            parts.append("estimate")
        detail = []
        if self.resolved_legs:
            detail.append(f"{len(self.resolved_legs)} leg{'s' if len(self.resolved_legs) != 1 else ''}")
        if self.booking_options:
            detail.append(f"{len(self.booking_options)} option{'s' if len(self.booking_options) != 1 else ''}")
        if detail:
            parts.append(", ".join(detail))
        return f"PriceResult({' '.join(parts)})"


@dataclass
class ExploreDestination:
    """A single destination suggestion from Google Flights Explore.

    Explore is destination *discovery* ("where could I go?"), not pricing:
    the RPC returns no price (see ``docs/superpowers/specs``). ``departure_date``
    / ``return_date`` are Google's per-destination suggestions; ``return_date``
    is ``None`` for one-way queries. ``origin`` and the ``query_*`` fields are
    denormalized so :func:`swoop.price_explore` / :meth:`to_search_kwargs` are
    self-contained, mirroring :class:`Deal`.
    """

    origin: str
    destination: Optional[str]          # destination IATA; None when Google omits it
    destination_name: str               # city / region / landmark display name
    destination_country: str
    place_id: str                       # Google Knowledge Graph id, e.g. "/m/0d6lp"
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    departure_date: Optional[str] = None
    return_date: Optional[str] = None
    image_url: Optional[str] = None
    secondary_image_url: Optional[str] = None
    # Ground/drive time (minutes) from the origin metro for nearby drivable
    # suggestions; None for destinations Explore expects you to fly to. This is
    # NOT a flight duration — the Explore RPC does not return one.
    drive_minutes: Optional[int] = None
    # Discovery query context, denormalized so price_explore/to_search_kwargs
    # re-price the SAME product the user explored — not a default economy / 1
    # adult / any-stops search. Mirrors Deal's query_* fields.
    query_cabin: Optional[str] = None
    query_adults: int = 1
    query_children: int = 0
    query_infants_in_seat: int = 0
    query_infants_on_lap: int = 0
    query_max_stops: Optional[int] = None

    def to_search_kwargs(self) -> dict:
        """Convert this destination into keyword args for :func:`swoop.search`.

        Explore surfaces dates but not the actual itineraries; this rebuilds
        the search that produced this destination, carrying the discovery query
        context (cabin, full passenger party, max_stops) forward so pricing
        reflects the same product — e.g. a ``max_stops=0`` exploration prices a
        nonstop, not a cheaper connection. Raises ``ValueError`` when
        ``destination`` or ``departure_date`` is missing (Google occasionally
        omits either), since a search needs a concrete destination and date.
        """
        if not self.destination:
            raise ValueError("ExploreDestination has no destination airport to search")
        if not self.departure_date:
            raise ValueError("ExploreDestination has no departure date to search")
        kwargs: dict = {
            "origin": self.origin,
            "destination": self.destination,
            "date": self.departure_date,
        }
        if self.return_date:
            kwargs["return_date"] = self.return_date
        if self.query_cabin:
            kwargs["cabin"] = self.query_cabin
        if self.query_max_stops is not None:
            kwargs["max_stops"] = self.query_max_stops
        pax = Passengers(
            adults=self.query_adults,
            children=self.query_children,
            infants_in_seat=self.query_infants_in_seat,
            infants_on_lap=self.query_infants_on_lap,
        )
        if pax != Passengers():
            kwargs["passengers"] = pax
        return kwargs

    def __repr__(self) -> str:
        code = self.destination or "?"
        parts = [f"{code} {self.destination_name} from {self.origin}"]
        if self.departure_date:
            parts.append(self.departure_date)
        return f"ExploreDestination({', '.join(parts)})"


@dataclass
class ExploreResult:
    """Result of an :func:`swoop.explore` destination-discovery query."""

    destinations: list[ExploreDestination] = field(default_factory=list)
    origin: str = ""
    origin_name: Optional[str] = None
    origin_place_id: Optional[str] = None
    origin_latitude: Optional[float] = None
    origin_longitude: Optional[float] = None

    def __repr__(self) -> str:
        return f"ExploreResult({len(self.destinations)} destinations from {self.origin})"

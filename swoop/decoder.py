"""Hardened decoder for Google Flights nested-list data.

All assertions replaced with graceful error handling — individual itinerary
decode failures are skipped (not fatal), and missing/None fields get safe
defaults instead of crashing.

Index Reference (nested list paths from response root):
  [2, 0]       -> best flights list
  [3, 0]       -> other flights list

Per itinerary element:
  [0]          -> itinerary data
  [0][0]       -> airline code
  [0][1]       -> airline names list
  [0][2]       -> flights list (segments)
  [0][3]       -> departure airport
  [0][4..8]    -> dates/times (dep date, dep time, arr airport, arr date, arr time)
  [0][9]       -> travel time (minutes)
  [0][13]      -> layovers list
  [1]          -> itinerary summary (price data)

Per flight segment:
  [2]          -> operator
  [3], [4]     -> departure airport code, name
  [5], [6]     -> arrival airport code, name
  [8]          -> departure time (hour, min)
  [9]          -> premium IFE indicator (1 = yes)
  [10]         -> arrival time (hour, min)
  [11]         -> travel time (minutes)
  [12]         -> amenity flags (12-element array)
  [13]         -> seat type (1=avg, 2=below-avg, 3=above-avg, 4+=business)
  [14]         -> seat pitch
  [15]         -> codeshares list
  [17]         -> aircraft type
  [19]         -> overnight flag
  [20], [21]   -> departure date, arrival date (year, month, day)
  [22][0]      -> airline code
  [22][1]      -> flight number
  [22][3]      -> airline name
  [30]         -> legroom string
  [31]         -> CO2 grams per segment

Itinerary summary:
  [0]          -> [None, display_price]
  [1]          -> base64-encoded protobuf string
"""

import logging
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

from ._formatting import fmt_clock, fmt_duration
from .builders import ItinerarySummary
from .exceptions import SwoopUpstreamError

logger = logging.getLogger(__name__)


def _safe_get(data: Any, path: list[int], default: Any = None) -> Any:
    """Safely traverse a nested list structure. Returns default on any error."""
    try:
        it = data
        for index in path:
            if not isinstance(it, list) or index >= len(it):
                return default
            it = it[index]
        return it
    except (TypeError, IndexError, KeyError):
        return default


def detect_error_envelope(frame: Any) -> Optional[Tuple[int, Optional[str]]]:
    """Detect Google's structured ErrorResponse envelope in a ``wrb.fr`` frame.

    A success frame is ``["wrb.fr", null, "<json>"]`` with the result payload at
    index 2. When Google rejects the request it still answers HTTP 200 but
    replaces that payload with an error block, e.g.::

        ["wrb.fr", null, null, null, null,
         [13, null, [["type.googleapis.com/travel.frontend.flights.ErrorResponse", ...]]]]

    The leading int (``13`` here) is a gRPC status code (13 = INTERNAL). Returns
    ``(grpc_code, type_url)`` when such a block is present, else ``None``.

    Shared by every RPC parser (shopping, booking, deals, explore) so a Google
    outage surfaces as :class:`~swoop.exceptions.SwoopUpstreamError` on all
    endpoints instead of a silent empty result on some of them.

    ``bool`` values and gRPC code ``0`` (OK) are deliberately NOT treated as
    errors: ``bool`` is an ``int`` subclass, and ``0`` means success.
    """
    if not isinstance(frame, list):
        return None
    for element in frame:
        if not (isinstance(element, list) and len(element) >= 3):
            continue
        code = element[0]
        # A real gRPC error code is a plain, non-zero int. Exclude bool
        # (a subclass of int) and 0 (OK) so neither trips a false positive.
        if type(code) is not int or code == 0:
            continue
        type_url = _safe_get(element, [2, 0, 0])
        if isinstance(type_url, str) and type_url.endswith(".ErrorResponse"):
            return code, type_url
    return None


def raise_if_error_envelope(frames: list[Any], *, endpoint: str) -> None:
    """Raise :class:`~swoop.exceptions.SwoopUpstreamError` if any frame is an
    ErrorResponse envelope.

    Centralizes the detect -> log -> raise reaction shared by every RPC parser
    (shopping, booking, deals, explore) so a Google outage is reported
    identically on all endpoints. Non-streaming parsers pass a single-frame
    list. Returns normally when no frame carries an error envelope.
    """
    for frame in frames:
        error = detect_error_envelope(frame)
        if error is not None:
            grpc_code, type_url = error
            logger.warning(
                "%s returned an ErrorResponse (gRPC %s, %s)",
                endpoint, grpc_code, type_url,
            )
            raise SwoopUpstreamError(grpc_code, type_url=type_url)


def _safe_tuple(val: Any, length: int, defaults: list) -> tuple:
    """Convert a value to a tuple of fixed length with defaults for missing or None elements.

    Google Flights sometimes encodes a leading-zero hour by omitting it, sending
    e.g. ``[None, 5]`` for ``00:05``, so None inside the list is treated the
    same as a missing element.
    """
    seq: list = list(val) if isinstance(val, (list, tuple)) else []
    return tuple(
        seq[i] if i < len(seq) and seq[i] is not None else defaults[i]
        for i in range(length)
    )


def _safe_int(value: Any, default: int = 0) -> int:
    """Convert malformed scalar-like values to a safe non-negative integer.

    Negative values are treated as corrupt and silently replaced with *default*
    so that downstream fields like ``travel_time`` and ``minutes`` stay >= 0.
    """
    if isinstance(value, bool):
        return default
    try:
        if isinstance(value, (int, float, str)):
            coerced = int(value)
            return coerced if coerced >= 0 else default
    except (OverflowError, TypeError, ValueError):
        return default
    return default


# Re-export for backward compat (used by models.py and tests)
_fmt_clock = fmt_clock
_fmt_duration = fmt_duration


def _flight_summary_repr(segments: list) -> str:
    """Compact flight number summary for repr.

    - Nonstop: "DL 2300"
    - 2 segments same carrier: "DL 2300 / 5678"
    - 2 segments diff carrier: "DL 2300 / AA 200"
    - 3+ segments: "DL 2300 +2"
    - No segments: ""
    """
    if not segments:
        return ""
    first = segments[0]
    first_str = f"{first.airline} {first.flight_number}" if first.airline and first.flight_number else str(first.flight_number or "")
    if len(segments) == 1:
        return first_str
    if len(segments) == 2:
        second = segments[1]
        if first.airline and second.airline and first.airline == second.airline:
            return f"{first.airline} {first.flight_number} / {second.flight_number}"
        second_str = f"{second.airline} {second.flight_number}" if second.airline and second.flight_number else str(second.flight_number or "")
        return f"{first_str} / {second_str}"
    return f"{first_str} +{len(segments) - 1}"


@dataclass
class Codeshare:
    airline_code: str = ""
    flight_number: str = ""
    airline_name: str = ""


@dataclass
class AmenityFlags:
    """Amenity flags from segment[12]. Cabin-class specific."""
    has_power: bool = False         # [1] = true (in-seat power & USB)
    has_live_tv: bool = False       # [8] = true
    has_on_demand_video: bool = False  # [9] = true (seatback IFE)
    has_stream_media: bool = False  # [10] = true (wireless streaming)
    wifi: Optional[int] = None     # [11] = 2 (free) or 3 (free intl), None = no wifi


@dataclass
class QualitySignals:
    """Quality signals from itinerary[4]."""
    quality_tier: Optional[int] = None    # [4]: 1=standard, 3=budget
    bag_flags: Optional[List] = None      # [6]: [0,0]=no bags, [0,1]=personal, [null,1]=included


@dataclass
class Segment:
    airline: str = ""
    airline_name: str = ""
    flight_number: str = ""
    operator: str = ""
    codeshares: List[Codeshare] = field(default_factory=list)
    aircraft: str = ""
    departure_airport_code: str = ""
    departure_airport_name: str = ""
    arrival_airport_code: str = ""
    arrival_airport_name: str = ""
    departure_date: Tuple[int, int, int] = (0, 0, 0)
    arrival_date: Tuple[int, int, int] = (0, 0, 0)
    departure_time: Tuple[int, int] = (0, 0)
    arrival_time: Tuple[int, int] = (0, 0)
    travel_time: int = 0
    seat_pitch_short: str = ""
    legroom: str = ""  # Full legroom string, e.g., "28 inches"
    co2_grams: Optional[int] = None  # CO2 emissions in grams for this segment
    overnight: bool = False  # Whether flight crosses midnight
    has_premium_ife: bool = False  # segment[9]: premium IFE system
    amenities: Optional[AmenityFlags] = None  # segment[12]: cabin-class amenities
    seat_type: Optional[int] = None  # segment[13]: 1=avg, 2=below-avg, 3=above-avg, 4+=business

    def __repr__(self) -> str:
        parts = []
        if self.airline or self.flight_number:
            parts.append(f"{self.airline} {self.flight_number}".strip())
        dep = self.departure_airport_code
        arr = self.arrival_airport_code
        if dep or arr:
            parts.append(f"{dep}->{arr}")
        parts.append(f"{_fmt_clock(self.departure_time)}-{_fmt_clock(self.arrival_time)}")
        parts.append(_fmt_duration(self.travel_time))
        return f"Segment({' '.join(parts)})"


@dataclass
class Layover:
    minutes: int = 0
    departure_airport_code: str = ""
    departure_airport_name: str = ""
    departure_airport_city: str = ""
    arrival_airport_code: str = ""
    arrival_airport_name: str = ""
    arrival_airport_city: str = ""
    is_overnight: bool = False  # layover[3]: [1] when layover spans overnight

    def __repr__(self) -> str:
        parts = [_fmt_duration(self.minutes)]
        if self.departure_airport_code:
            parts.append(self.departure_airport_code)
        if self.is_overnight:
            parts.append("overnight")
        return f"Layover({' '.join(parts)})"


@dataclass
class CarbonEmissions:
    """Carbon emissions data for an itinerary."""
    this_flight_grams: Optional[int] = None  # CO2 for this specific itinerary
    typical_for_route_grams: Optional[int] = None  # Typical CO2 for this route
    difference_percent: Optional[int] = None  # % difference from typical (negative = less)
    emissions_rating: Optional[int] = None  # 1=below average (green), 3=above average (orange)


@dataclass
class Itinerary:
    airline_code: str = ""
    airline_names: List[str] = field(default_factory=list)
    segments: List[Segment] = field(default_factory=list)
    layovers: List[Layover] = field(default_factory=list)
    travel_time: int = 0
    departure_airport_code: str = ""
    arrival_airport_code: str = ""
    departure_date: Tuple[int, int, int] = (0, 0, 0)
    arrival_date: Tuple[int, int, int] = (0, 0, 0)
    departure_time: Tuple[int, int] = (0, 0)
    arrival_time: Tuple[int, int] = (0, 0)
    price_info: Optional[ItinerarySummary] = None
    direct_price: Optional[int] = None  # Integer price in response currency's major unit from root[1][0][1]
    booking_token: str = ""
    carbon_emissions: Optional[CarbonEmissions] = None
    stop_count: Optional[int] = None  # Number of stops
    is_budget_carrier: bool = False  # itinerary root[3]: budget carrier flag
    quality_signals: Optional[QualitySignals] = None  # itinerary root[4]

    @property
    def currency(self) -> Optional[str]:
        """ISO 4217 currency code from the itinerary's price info, or None."""
        if self.price_info is not None and self.price_info.currency:
            return self.price_info.currency
        return None

    @property
    def price(self) -> Optional[int]:
        """Price in the currency's major unit (e.g. 250 for $250, 6725 for ₹6,725)."""
        return self.direct_price

    def __repr__(self) -> str:
        parts = []
        summary = _flight_summary_repr(self.segments)
        if summary:
            parts.append(summary)
        dep = self.departure_airport_code
        arr = self.arrival_airport_code
        if dep or arr:
            parts.append(f"{dep}->{arr}")
        parts.append(f"{_fmt_clock(self.departure_time)}-{_fmt_clock(self.arrival_time)}")
        parts.append(_fmt_duration(self.travel_time))
        stops = self.stop_count if self.stop_count is not None else len(self.layovers)
        if stops == 0:
            parts.append("nonstop")
        else:
            parts.append(f"{stops} stop{'s' if stops > 1 else ''}")
        if self.direct_price is not None:
            parts.append(f"price={self.direct_price}")
        return f"Itinerary({' '.join(parts)})"


@dataclass
class PriceRange:
    """Price range from Google Flights price insights."""
    low: Optional[int] = None  # Lowest price seen
    high: Optional[int] = None  # Highest price seen


@dataclass
class PriceInsightPoint:
    """One day of the price-history series in Google Flights' insights panel."""
    timestamp_ms: int = 0  # Unix epoch milliseconds
    price: int = 0         # Fare in the response currency's major unit


@dataclass
class PriceInsights:
    """Google Flights' price-insights panel (``data[5]``).

    Prices are in the response currency's major unit. This block is ``null``
    from the shopping RPC and only rides the HTML/SSR page payload, so it is
    populated whenever a caller decodes such a payload and absent otherwise.

    Observed live 2026-10-11 (JFK->LAX 2026-11-15, economy/business/first):

    * The block is **per search cabin**: economy and business returned the same
      ~61 daily points but different values on every field, and premium economy
      returned no block at all on that route. Read it for the cabin you asked
      for, not as a route-wide statistic.
    * ``history`` is one point per day ending today (61 points over the trailing
      60 days), directly plottable.
    * ``typical_price`` tracks the **current cheapest fare**, not a historical
      average: it equalled both the page's cheapest row and the last history
      point (229 economy, 805 business).
    * ``lowest_seen`` / ``highest_seen`` are **not** the history's min/max; they
      reflect a longer tracking window than the graph (economy 90/270 against a
      178-249 series), so the band can sit outside the plotted range.

    ``price_level`` is Google's raw indicator, exposed uninterpreted: 1, 3, 4 and
    5 were all observed, and the scale and its breakpoints are not pinned, so do
    not read it as a stable enum. ``data[5][6]`` is the cabin code
    (1 economy / 3 business / 4 first observed) and is intentionally not exposed.
    """
    price_level: Optional[int] = None       # [0] raw level indicator (1/3/4/5 observed; scale unpinned)
    typical_price: Optional[int] = None     # [1][1] current cheapest fare (tracks the live best price)
    low_price: Optional[int] = None         # [2][1] usual/low fare for the route
    price_difference: Optional[int] = None  # [3][1] delta vs the usual band (negative = cheaper)
    lowest_seen: Optional[int] = None       # [4][1] lowest over the tracking window (may predate the 60-day graph)
    highest_seen: Optional[int] = None      # [5][1] highest over the tracking window (may predate the 60-day graph)
    destination_city: Optional[str] = None  # [12] destination city name
    history: List[PriceInsightPoint] = field(default_factory=list)  # [10][0] [[ts_ms, price], ...], one point/day


@dataclass
class RawSearchResult:
    _raw: list = field(default_factory=list)
    best: List[Itinerary] = field(default_factory=list)
    other: List[Itinerary] = field(default_factory=list)
    price_range: Optional[PriceRange] = None  # Price range from response
    price_insights: Optional[PriceInsights] = None  # data[5]; override when a payload carries it

    def __repr__(self) -> str:
        return f"RawSearchResult(best={len(self.best)}, other={len(self.other)})"


@dataclass
class BookingOption:
    """A single fare option from GetBookingResults.

    Seller fields (``seller_name``, ``seller_code``, ``booking_url``, ``logo_url``,
    ``is_airline_direct``) describe who is selling the fare and how to reach the
    booking page:

    * ``booking_url`` is a ``google.com/travel/clk/f`` redirect reconstructed
      from the RPC response. swoop only exposes it when the base URL is the
      expected ``https://www.google.com/travel/clk/f`` endpoint; malformed,
      reshaped, or non-Google bases produce an empty string.
    * ``logo_url`` points at ``gstatic.com/flights/partner_logos/70px/...`` only
      when Google explicitly provides ``logo_code``; empty otherwise. Consumers
      that want a best-effort airline logo can construct the URL themselves
      via ``{gstatic_base}/{seller_code}.png`` (works for IATA-style codes,
      404s for OTA codes).
    """
    price: int = 0
    brand_label: str = ""
    brand_code: str = ""
    is_basic: bool = False
    fare_family: str = ""
    rebookability_signal: str = ""
    seller_name: str = ""        # e.g. "Gotogate", "Qatar Airways"
    seller_code: str = ""        # e.g. "ETRAVELI_Gotogate", "QR"
    booking_url: str = ""
    logo_url: str = ""
    is_airline_direct: bool = False
    _is_basic_by_flags: bool = False
    _is_basic_by_text: bool = False
    _option_index: Optional[int] = None
    _token_price_raw: Optional[int] = None
    _display_price_raw: Optional[int] = None
    _price_delta_raw: Optional[int] = None
    _context_segment_token: str = ""
    _context_origin_iata: Optional[str] = None
    _context_destination_iata: Optional[str] = None
    _context_departure_local_iso: Optional[str] = None
    _context_arrival_local_iso: Optional[str] = None
    _context_carrier_code: Optional[str] = None
    _context_flight_number: Optional[str] = None
    _context_aircraft_code: Optional[str] = None
    _cabin_bucket: str = ""
    _brand_attribute_vector: List = field(default_factory=list)
    _registry_version: Optional[str] = None

    def __repr__(self) -> str:
        parts = [f"price={self.price}"]
        if self.seller_name:
            parts.append(f"seller={self.seller_name!r}")
        if self.brand_label:
            parts.append(f"{self.brand_label!r}")
        if self.is_basic:
            parts.append("basic")
        return f"BookingOption({' '.join(parts)})"



def _decode_codeshare(el: list) -> Codeshare:
    """Decode a single codeshare entry."""
    return Codeshare(
        airline_code=_safe_get(el, [0], "") or "",
        flight_number=str(_safe_get(el, [1], "") or ""),
        airline_name=str(_safe_get(el, [3], "") or ""),
    )


def _decode_amenities(el: list) -> Optional[AmenityFlags]:
    """Decode amenity flags from segment[12]. Cabin-class specific."""
    amenity_data = _safe_get(el, [12])
    if not isinstance(amenity_data, list) or len(amenity_data) == 0:
        return None
    wifi_raw = _safe_get(amenity_data, [11])
    return AmenityFlags(
        has_power=_safe_get(amenity_data, [1]) is True,
        has_live_tv=_safe_get(amenity_data, [8]) is True,
        has_on_demand_video=_safe_get(amenity_data, [9]) is True,
        has_stream_media=_safe_get(amenity_data, [10]) is True,
        wifi=wifi_raw if isinstance(wifi_raw, int) else None,
    )


def _decode_segment(el: list) -> Optional[Segment]:
    """Decode a single flight segment from nested list data."""
    try:
        # Codeshares at index 15
        codeshares_raw = _safe_get(el, [15])
        codeshares = []
        if isinstance(codeshares_raw, list):
            for cs in codeshares_raw:
                if isinstance(cs, list):
                    codeshares.append(_decode_codeshare(cs))

        # CO2 emissions at index 31 (grams)
        co2_raw = _safe_get(el, [31])
        co2_grams = co2_raw if isinstance(co2_raw, int) else None

        # Full legroom string at index 30, e.g. "28 inches"
        legroom = str(_safe_get(el, [30], "") or "")

        # Overnight flag at index 19
        overnight_raw = _safe_get(el, [19], False)
        overnight = overnight_raw is True

        # Premium IFE indicator at index 9 (1 = has premium IFE)
        premium_ife_raw = _safe_get(el, [9])
        has_premium_ife = premium_ife_raw == 1

        # Amenity flags at index 12
        amenities = _decode_amenities(el)

        # Seat type at index 13 (1=avg, 2=below-avg, 3=above-avg, 4+=business)
        seat_type_raw = _safe_get(el, [13])
        seat_type = seat_type_raw if isinstance(seat_type_raw, int) else None

        return Segment(
            operator=str(_safe_get(el, [2], "") or ""),
            departure_airport_code=str(_safe_get(el, [3], "") or ""),
            departure_airport_name=str(_safe_get(el, [4], "") or ""),
            arrival_airport_code=str(_safe_get(el, [6], "") or ""),
            arrival_airport_name=str(_safe_get(el, [5], "") or ""),
            departure_time=_safe_tuple(_safe_get(el, [8]), 2, [0, 0]),
            arrival_time=_safe_tuple(_safe_get(el, [10]), 2, [0, 0]),
            travel_time=_safe_int(_safe_get(el, [11], 0), 0),
            seat_pitch_short=str(_safe_get(el, [14], "") or ""),
            aircraft=str(_safe_get(el, [17], "") or ""),
            departure_date=_safe_tuple(_safe_get(el, [20]), 3, [0, 0, 0]),
            arrival_date=_safe_tuple(_safe_get(el, [21]), 3, [0, 0, 0]),
            airline=str(_safe_get(el, [22, 0], "") or ""),
            airline_name=str(_safe_get(el, [22, 3], "") or ""),
            flight_number=str(_safe_get(el, [22, 1], "") or ""),
            codeshares=codeshares,
            legroom=legroom,
            co2_grams=co2_grams,
            overnight=overnight,
            has_premium_ife=has_premium_ife,
            amenities=amenities,
            seat_type=seat_type,
        )
    except Exception as e:
        logger.warning("Failed to decode segment: %s", e)
        return None


def _decode_layover(el: list) -> Optional[Layover]:
    """Decode a single layover entry."""
    try:
        # Overnight flag at [3]: [1] when layover spans overnight, null otherwise
        is_overnight = _safe_get(el, [3, 0]) == 1

        return Layover(
            minutes=_safe_int(_safe_get(el, [0], 0), 0),
            departure_airport_code=str(_safe_get(el, [1], "") or ""),
            arrival_airport_code=str(_safe_get(el, [2], "") or ""),
            departure_airport_name=str(_safe_get(el, [4], "") or ""),
            departure_airport_city=str(_safe_get(el, [5], "") or ""),
            arrival_airport_name=str(_safe_get(el, [6], "") or ""),
            arrival_airport_city=str(_safe_get(el, [7], "") or ""),
            is_overnight=is_overnight,
        )
    except Exception:
        return None


def _decode_price_info(el: list) -> Optional[ItinerarySummary]:
    """Decode the itinerary summary (contains price).

    Structure: [[None, display_price], 'base64_encoded_protobuf']
    The b64 protobuf string is at el[1], NOT el[1][1] — this was a past
    bug where using path [1,1] returned None and all prices came back as $0.
    """
    try:
        b64_string = _safe_get(el, [1])
        if b64_string and isinstance(b64_string, str):
            return ItinerarySummary.from_b64(b64_string)
        return None
    except Exception:
        return None


def _decode_itinerary(el: list) -> Optional[Itinerary]:
    """Decode a single itinerary from nested list data.

    Returns None if the itinerary cannot be decoded (instead of crashing).
    """
    try:
        root = el
        itin_data = _safe_get(root, [0])
        if not isinstance(itin_data, list):
            return None

        # Segments at [0, 2]
        segments_raw = _safe_get(itin_data, [2])
        segments = []
        if isinstance(segments_raw, list):
            for f in segments_raw:
                if isinstance(f, list):
                    segment = _decode_segment(f)
                    if segment is not None:
                        segments.append(segment)

        # Layovers at [0, 13]
        layovers_raw = _safe_get(itin_data, [13])
        layovers = []
        if isinstance(layovers_raw, list):
            for lay in layovers_raw:
                if isinstance(lay, list):
                    layover = _decode_layover(lay)
                    if layover is not None:
                        layovers.append(layover)

        # Itinerary summary at [1]
        summary_data = _safe_get(root, [1])
        summary = None
        direct_price = None
        booking_token = ""
        if isinstance(summary_data, list):
            summary = _decode_price_info(summary_data)
            # Direct integer price at [1][0][1] — more accurate than protobuf
            # (protobuf price is consistently $1 less than the Chrome-displayed price)
            raw_direct = _safe_get(summary_data, [0, 1])
            if isinstance(raw_direct, int) and raw_direct > 0:
                direct_price = raw_direct
            token = _safe_get(summary_data, [1], "")
            if isinstance(token, str):
                booking_token = token

        # Carbon emissions data at itin_data[22]
        # [2] = emissions rating (1=below avg, 3=above avg)
        # [3] = % difference from typical (negative = less emissions)
        # [7] = this flight's CO2 in grams
        # [8] = typical CO2 for this route in grams
        carbon_data = _safe_get(itin_data, [22])
        carbon_emissions = None
        if isinstance(carbon_data, list) and len(carbon_data) > 8:
            this_flight = _safe_get(carbon_data, [7])
            typical = _safe_get(carbon_data, [8])
            diff_pct = _safe_get(carbon_data, [3])
            rating = _safe_get(carbon_data, [2])
            if any(isinstance(v, int) for v in (this_flight, typical, diff_pct)):
                carbon_emissions = CarbonEmissions(
                    this_flight_grams=this_flight if isinstance(this_flight, int) else None,
                    typical_for_route_grams=typical if isinstance(typical, int) else None,
                    difference_percent=diff_pct if isinstance(diff_pct, int) else None,
                    emissions_rating=rating if isinstance(rating, int) else None,
                )

        # Stop count: number of layovers = number of stops
        stop_count = len(layovers) if layovers else (len(segments) - 1 if segments else 0)

        # Budget carrier flag at root[3] (sibling of itin_data, NOT inside it)
        budget_raw = _safe_get(root, [3])
        is_budget_carrier = budget_raw is True

        # Quality signals at root[4]
        quality_signals = None
        qs_data = _safe_get(root, [4])
        if isinstance(qs_data, list) and len(qs_data) > 4:
            qt = _safe_get(qs_data, [4])
            bf = _safe_get(qs_data, [6])
            quality_signals = QualitySignals(
                quality_tier=qt if isinstance(qt, int) else None,
                bag_flags=list(bf) if isinstance(bf, list) else None,
            )

        return Itinerary(
            airline_code=str(_safe_get(itin_data, [0], "") or ""),
            airline_names=_safe_get(itin_data, [1], []) or [],
            segments=segments,
            layovers=layovers,
            departure_airport_code=str(_safe_get(itin_data, [3], "") or ""),
            departure_date=_safe_tuple(_safe_get(itin_data, [4]), 3, [0, 0, 0]),
            departure_time=_safe_tuple(_safe_get(itin_data, [5]), 2, [0, 0]),
            arrival_airport_code=str(_safe_get(itin_data, [6], "") or ""),
            arrival_date=_safe_tuple(_safe_get(itin_data, [7]), 3, [0, 0, 0]),
            arrival_time=_safe_tuple(_safe_get(itin_data, [8]), 2, [0, 0]),
            travel_time=_safe_int(_safe_get(itin_data, [9], 0), 0),
            price_info=summary,
            direct_price=direct_price,
            booking_token=booking_token,
            carbon_emissions=carbon_emissions,
            stop_count=stop_count,
            is_budget_carrier=is_budget_carrier,
            quality_signals=quality_signals,
        )
    except Exception as e:
        logger.warning("Failed to decode itinerary: %s", e)
        return None


def _decode_price_range(data: list) -> Optional[PriceRange]:
    """Extract price range from data[7][0] if available.

    Structure: data[7][0] = [[None, low_price], [None, high_price]]
    """
    try:
        range_data = _safe_get(data, [7, 0])
        if not isinstance(range_data, list) or len(range_data) < 2:
            return None
        low = _safe_get(range_data, [0, 1])
        high = _safe_get(range_data, [1, 1])
        if isinstance(low, (int, float)) or isinstance(high, (int, float)):
            return PriceRange(
                low=int(low) if isinstance(low, (int, float)) else None,
                high=int(high) if isinstance(high, (int, float)) else None,
            )
    except Exception:
        pass
    return None


def _decode_price_insights(data: list) -> Optional[PriceInsights]:
    """Extract Google Flights' price-insights block from ``data[5]``.

    Structure::

        [0]     price-level indicator (raw; 1/3/4/5 observed, scale unpinned)
        [1]     [null, current/cheapest fare]
        [2]     [null, usual low fare]
        [3]     [null, difference from the usual band]
        [4]     [null, lowest seen over the tracking window]
        [5]     [null, highest seen over the tracking window]
        [6]     cabin code (not exposed)
        [10][0] [[timestamp_ms, price], ...]  (~60-point daily history)
        [12]    destination city name

    Returns ``None`` when the block is absent (the shopping RPC sends null) or
    carries nothing usable. Tolerant of malformed input — never raises.
    """
    block = _safe_get(data, [5])
    if not isinstance(block, list):
        return None

    def _number(value: Any) -> Optional[int]:
        # bool is an int subclass; a flag is not a price.
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value)
        return None

    def _pair_price(index: int) -> Optional[int]:
        return _number(_safe_get(block, [index, 1]))

    history: List[PriceInsightPoint] = []
    raw_history = _safe_get(block, [10, 0])
    if isinstance(raw_history, list):
        for point in raw_history:
            ts = _number(_safe_get(point, [0]))
            price = _number(_safe_get(point, [1]))
            if ts is not None and price is not None:
                history.append(PriceInsightPoint(timestamp_ms=ts, price=price))

    city = _safe_get(block, [12])
    insights = PriceInsights(
        price_level=_number(_safe_get(block, [0])),
        typical_price=_pair_price(1),
        low_price=_pair_price(2),
        price_difference=_pair_price(3),
        lowest_seen=_pair_price(4),
        highest_seen=_pair_price(5),
        destination_city=city if isinstance(city, str) else None,
        history=history,
    )
    if (insights.price_level is None and insights.typical_price is None
            and insights.low_price is None and insights.price_difference is None
            and insights.lowest_seen is None and insights.highest_seen is None
            and insights.destination_city is None and not insights.history):
        return None
    return insights


def itinerary_matches_flight(
    itinerary: Itinerary, carrier: Optional[str], number: str
) -> bool:
    """Check whether *itinerary* contains a flight matching *carrier*/*number*.

    Checks each segment's operating flight (``flight.airline`` +
    ``flight.flight_number``) and its codeshares.  If *carrier* is ``None``,
    any carrier with the given number matches.

    Returns ``True`` on the first match across any segment.
    """
    for segment in itinerary.segments:
        # Operating flight
        if segment.flight_number == number:
            if carrier is None or segment.airline == carrier:
                return True
        # Codeshares
        for cs in segment.codeshares:
            if cs.flight_number == number:
                if carrier is None or cs.airline_code == carrier:
                    return True
    return False


def decode_result(data: list) -> RawSearchResult:
    """Decode the full Google Flights response data.

    Extracts best and other flight itineraries from the nested list structure.
    Gracefully handles missing or malformed data.
    """
    best = []
    other = []

    # Best flights at [2, 0]
    best_raw = _safe_get(data, [2, 0])
    if isinstance(best_raw, list):
        for el in best_raw:
            if isinstance(el, list):
                itin = _decode_itinerary(el)
                if itin is not None:
                    best.append(itin)

    # Other flights at [3, 0]
    other_raw = _safe_get(data, [3, 0])
    if isinstance(other_raw, list):
        for el in other_raw:
            if isinstance(el, list):
                itin = _decode_itinerary(el)
                if itin is not None:
                    other.append(itin)

    # Price range at [7, 0]
    price_range = _decode_price_range(data)

    # Price insights at [5] — present only when the payload carries it.
    price_insights = _decode_price_insights(data)

    return RawSearchResult(_raw=data, best=best, other=other, price_range=price_range,
                           price_insights=price_insights)

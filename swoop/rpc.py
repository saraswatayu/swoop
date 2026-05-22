"""Google Flights RPC endpoint client.

Uses the internal GetShoppingResults RPC endpoint that the Google Flights
web app uses when showing "more flights." Supports time-window filtering.

Based on reverse-engineering from punitarani/fli.
"""

from copy import deepcopy
import json
import logging
import urllib.parse
from typing import Any, Optional

from ._booking import (
    _classify_fare_family,
    _extract_brand_block,
    _extract_context_tokens,
    _extract_display_price_raw_from_context,
    _extract_option_index_and_token_price_raw,
    _extract_price_block,
    _extract_segment_identity_from_context,
    _infer_rebookability_signal,
    _looks_like_brand_block,
    _looks_like_price_block,
    _normalize_attribute_vector,
    _parse_booking_rpc_response,
    _read_varint,
    _skip_wire_value,
    parse_booking_payload,
)
from .builders import CABIN_CLASS_MAP, CabinClass
from .decoder import BookingOption, RawSearchResult, Itinerary, decode_result, _safe_get
from .exceptions import SwoopHTTPError, SwoopParseError, SwoopRateLimitError
from .models import Passengers, TransportConfig

logger = logging.getLogger(__name__)

SHOPPING_RPC_URL = (
    "https://www.google.com/_/FlightsFrontendUi/data/"
    "travel.frontend.flights.FlightsFrontendService/GetShoppingResults"
)
BOOKING_RPC_URL = (
    "https://www.google.com/_/FlightsFrontendUi/data/"
    "travel.frontend.flights.FlightsFrontendService/GetBookingResults"
)

# Sort order values
SORT_TOP = 1
SORT_CHEAPEST = 2
SORT_DEPARTURE_TIME = 3
SORT_ARRIVAL_TIME = 4
SORT_DURATION = 5

# Max stops values (different from the HTML approach)
STOPS_ANY = 0
STOPS_NONSTOP = 1
STOPS_ONE_OR_FEWER = 2
STOPS_TWO_OR_FEWER = 3


def _normalize_rpc_leg(
    origin: str,
    destination: str,
    date: str,
    *,
    max_stops: Optional[int] = None,
    airlines: Optional[list[str]] = None,
    earliest_departure: Optional[int] = None,
    latest_departure: Optional[int] = None,
    earliest_arrival: Optional[int] = None,
    latest_arrival: Optional[int] = None,
    selected_legs: Optional[list[list[Any]]] = None,
) -> dict[str, Any]:
    """Normalize a single search leg for generic request building."""
    return {
        "origin": origin,
        "destination": destination,
        "date": date,
        "max_stops": max_stops,
        "airlines": sorted(airlines) if airlines else None,
        "earliest_departure": earliest_departure,
        "latest_departure": latest_departure,
        "earliest_arrival": earliest_arrival,
        "latest_arrival": latest_arrival,
        "selected_legs": selected_legs,
    }


def _build_time_restrictions(
    earliest_departure: Optional[int],
    latest_departure: Optional[int],
    earliest_arrival: Optional[int],
    latest_arrival: Optional[int],
) -> Optional[list[Any]]:
    """Build the RPC time restrictions payload for a single leg."""
    if any(v is not None for v in [
        earliest_departure,
        latest_departure,
        earliest_arrival,
        latest_arrival,
    ]):
        return [
            earliest_departure,
            latest_departure,
            earliest_arrival,
            latest_arrival,
        ]
    return None


def _trip_type_from_legs(legs: list[dict[str, Any]]) -> int:
    """Map a normalized leg list to Google Flights' trip type value."""
    if len(legs) <= 1:
        return 2  # one-way
    if len(legs) == 2:
        return 1  # roundtrip/open-jaw
    return 3  # multi-city


def _build_segment_from_leg(leg: dict[str, Any]) -> list[Any]:
    """Build a single RPC segment entry from a normalized leg."""
    max_stops = leg.get("max_stops")
    if max_stops is None:
        stops_val = STOPS_ANY
    else:
        stops_val = max_stops + 1

    return [
        [[[leg["origin"], 0]]],       # departure airport
        [[[leg["destination"], 0]]],  # arrival airport
        _build_time_restrictions(
            leg.get("earliest_departure"),
            leg.get("latest_departure"),
            leg.get("earliest_arrival"),
            leg.get("latest_arrival"),
        ),
        stops_val,                    # max stops
        leg.get("airlines"),          # airlines filter
        None,                         # placeholder
        leg["date"],                  # travel date (YYYY-MM-DD)
        None,                         # max duration
        leg.get("selected_legs"),     # selected flight for expansion
        None,                         # layover airports
        None,                         # placeholder
        None,                         # placeholder
        None,                         # layover duration
        None,                         # emissions
        3,                            # constant
    ]


def _build_filters_from_legs(
    legs: list[dict[str, Any]],
    *,
    cabin: CabinClass = "economy",
    passengers: Passengers = Passengers(),
    sort: int = SORT_TOP,
    exclude_basic_economy: bool = False,
) -> list[Any]:
    """Build the shopping filters payload from normalized leg definitions."""
    seat_type = CABIN_CLASS_MAP.get(cabin, 1)
    segments = [_build_segment_from_leg(leg) for leg in legs]
    trip_type = _trip_type_from_legs(legs)

    filters = [
        [],                                          # empty array
        [
            None,                                    # [0] placeholder
            None,                                    # [1] placeholder
            trip_type,                               # [2] trip type
            None,                                    # [3] placeholder
            [],                                      # [4] empty array
            seat_type,                               # [5] seat type
            [passengers.adults, passengers.children, passengers.infants_in_seat, passengers.infants_on_lap],  # [6] passengers
            None,                                    # [7] price limit
            None,                                    # [8-12] placeholders
            None,
            None,
            None,
            None,
            segments,                                # [13] flight segments
            None,                                    # [14-16] placeholders
            None,
            None,
            1,                                       # [17] constant
            None,                                    # [18-27] placeholders
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            1 if exclude_basic_economy else None,    # [28] exclude basic economy
        ],
        sort,                                        # sort order
        0,                                           # constant
        0,                                           # constant
        2,                                           # constant
    ]
    return filters


def _encode_f_req_payload(payload: list[Any]) -> str:
    """Encode payload to the URL-escaped `f.req` value."""
    payload_json = json.dumps(payload, separators=(",", ":"))
    wrapped = json.dumps([None, payload_json], separators=(",", ":"))
    return urllib.parse.quote(wrapped)


def _search_from_legs(
    legs: list[dict[str, Any]],
    *,
    cabin: CabinClass = "economy",
    passengers: Passengers = Passengers(),
    sort: int = SORT_DEPARTURE_TIME,
    transport: TransportConfig = TransportConfig(),
    exclude_basic_economy: bool = False,
    retain_raw: bool = True,
) -> Optional[RawSearchResult]:
    """Search Google Flights from normalized leg definitions."""
    encoded_body = _encode_f_req_payload(
        _build_filters_from_legs(
            legs,
            cabin=cabin,
            passengers=passengers,
            sort=sort,
            exclude_basic_economy=exclude_basic_economy,
        )
    )

    res = _http_post(
        SHOPPING_RPC_URL,
        content=f"f.req={encoded_body}".encode(),
        transport=transport,
    )

    result = _parse_rpc_response(res.text)
    if isinstance(result, RawSearchResult):
        if not retain_raw:
            result._raw = []
        logger.info(
            "_search_from_legs found %d best + %d other itineraries",
            len(result.best), len(result.other),
        )
    return result


def _build_booking_f_req(
    booking_token: str,
    filter_block: list[Any],
    selected_legs: list[list[Any]],
) -> str:
    """Build the f.req body for GetBookingResults.

    The booking endpoint expects:
      [[None, booking_token], <filter_block>, None, 0]
    and requires selected legs at filter_block[13][0][8].
    """
    if not booking_token or not selected_legs:
        return ""

    payload_filter = deepcopy(filter_block)
    try:
        segments = payload_filter[13]
        if not isinstance(segments, list) or not segments or not isinstance(segments[0], list):
            return ""
        while len(segments[0]) <= 8:
            segments[0].append(None)
        segments[0][8] = selected_legs
    except (IndexError, TypeError):
        return ""

    inner = [[None, booking_token], payload_filter, None, 0]
    return _encode_f_req_payload(inner)


_MAX_CLIENTS = 32
_clients: dict[str, Any] = {}
_default_country: Optional[str] = None
_default_proxy: Optional[str] = None


def set_country(country: Optional[str]) -> None:
    """Set the default country for all subsequent requests.

    Controls the Google Flights point of sale, which determines currency
    and available fares.  Uses the ``gl=`` query parameter (ISO 3166-1
    alpha-2 country code).

    .. note::

        Not thread-safe.  If you need per-thread country control, pass
        ``country=`` explicitly to each call instead.

    Args:
        country: Two-letter country code (e.g. ``"US"``, ``"GB"``,
            ``"JP"``), or ``None`` to let Google auto-detect from IP.

    Example::

        import swoop
        swoop.set_country("US")  # prices in USD
    """
    global _default_country
    _default_country = country.upper() if country else None


def set_proxy(proxy: Optional[str]) -> None:
    """Set the default proxy for all subsequent requests.

    Useful for routing requests through different servers to use
    different source IPs (e.g. for rate-limit management).

    Supports HTTP, HTTPS, and SOCKS5 proxy URLs.

    .. note::

        Not thread-safe.  If you need per-thread proxy control, pass
        ``proxy=`` explicitly to each call instead.

    Args:
        proxy: Proxy URL (e.g. ``"socks5://user:pass@host:port"``,
            ``"http://host:port"``), or ``None`` to connect directly.

    Example::

        import swoop
        swoop.set_proxy("socks5://myserver:1080")
    """
    global _default_proxy
    if proxy != _default_proxy:
        # Only evict the old default-proxy client; leave per-call clients intact
        old_key = _default_proxy or ""
        _clients.pop(old_key, None)
        _default_proxy = proxy


def _get_client(proxy: Optional[str] = None, impersonate: Optional[str] = None) -> Any:
    """Return a Client for the given proxy + impersonate profile, with connection reuse.

    Maintains separate Client instances per (proxy, impersonate) so that different
    proxy routes and fingerprint profiles don't interfere with each other.
    """
    from primp import Client

    effective_proxy = proxy if proxy is not None else _default_proxy
    effective_impersonate = impersonate or "chrome"
    key = f"{effective_proxy or ''}|{effective_impersonate}"
    if key not in _clients:
        # Evict oldest entry if cache is full to prevent unbounded growth
        # when callers rotate through many proxy URLs or impersonate profiles.
        if len(_clients) >= _MAX_CLIENTS:
            _clients.pop(next(iter(_clients)))
        kwargs: dict[str, Any] = {"impersonate": effective_impersonate}
        if effective_proxy:
            kwargs["proxy"] = effective_proxy
        _clients[key] = Client(**kwargs)
    return _clients[key]


def _apply_country(url: str, country: Optional[str]) -> str:
    """Append ``?gl=XX`` to the URL if a country is set."""
    effective = country if country is not None else _default_country
    if effective:
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}gl={effective.upper()}"
    return url


def _http_post(
    url: str,
    content: bytes,
    *,
    transport: TransportConfig = TransportConfig(),
) -> Any:
    """POST with optional retry on 429 and timeout.

    Args:
        url: The URL to POST to.
        content: Request body bytes.
        transport: HTTP transport configuration.

    Returns:
        The response object from primp.

    Raises:
        SwoopRateLimitError: If 429 persists after all retries.
        SwoopHTTPError: If a non-200/non-429 response is received.
    """
    import random
    import time

    url = _apply_country(url, transport.country)
    client = _get_client(transport.proxy, transport.impersonate)
    headers = {"content-type": "application/x-www-form-urlencoded;charset=UTF-8"}

    for attempt in range(1 + transport.retries):
        res = client.post(url, content=content, headers=headers, timeout=transport.timeout)
        if res.status_code == 200:
            logger.debug("HTTP 200 from %s (%d bytes)", url.split("/")[-1], len(res.text))
            return res
        if res.status_code == 429 and attempt < transport.retries:
            delay = (2 ** attempt) + random.uniform(0, 1)
            logger.info("HTTP 429 from %s, retrying in %.1fs (attempt %d/%d)", url.split("/")[-1], delay, attempt + 1, transport.retries)
            time.sleep(delay)
            continue
        if res.status_code == 429:
            raise SwoopRateLimitError()
        raise SwoopHTTPError(res.status_code)


def search_raw(
    origin: str,
    destination: str,
    date: str,
    cabin: CabinClass = "economy",
    passengers: Passengers = Passengers(),
    sort: int = SORT_DEPARTURE_TIME,
    max_stops: Optional[int] = None,
    airlines: Optional[list[str]] = None,
    earliest_departure: Optional[int] = None,
    latest_departure: Optional[int] = None,
    earliest_arrival: Optional[int] = None,
    latest_arrival: Optional[int] = None,
    return_date: Optional[str] = None,
    return_earliest_departure: Optional[int] = None,
    return_latest_departure: Optional[int] = None,
    selected_outbound_legs: Optional[list[list[Any]]] = None,
    transport: TransportConfig = TransportConfig(),
    exclude_basic_economy: bool = False,
) -> Optional[RawSearchResult]:
    """Search Google Flights via RPC endpoint and return decoded results.

    Returns None if no data found. Raises on network errors.
    For roundtrip searches, provide return_date. The price in the result
    is the roundtrip total.

    Args:
        transport: HTTP transport configuration (default ``TransportConfig()``).
    """
    logger.debug(
        "search_raw %s->%s on %s (cabin=%s, adults=%d)",
        origin, destination, date, cabin, passengers.adults,
    )

    legs = [
        _normalize_rpc_leg(
            origin, destination, date,
            max_stops=max_stops, airlines=airlines,
            earliest_departure=earliest_departure,
            latest_departure=latest_departure,
            earliest_arrival=earliest_arrival,
            latest_arrival=latest_arrival,
            selected_legs=selected_outbound_legs if return_date else None,
        )
    ]
    if return_date:
        legs.append(
            _normalize_rpc_leg(
                destination, origin, return_date,
                max_stops=max_stops, airlines=airlines,
                earliest_departure=return_earliest_departure,
                latest_departure=return_latest_departure,
            )
        )

    return _search_from_legs(
        legs, cabin=cabin, passengers=passengers,
        sort=sort, transport=transport,
        exclude_basic_economy=exclude_basic_economy,
    )


def _build_selected_legs(itinerary: Itinerary) -> list[list[Any]]:
    """Build selected legs payload from an Itinerary object.

    Simpler version of the parent project's ``_build_selected_outbound_legs``
    — no airport normalization needed since decoder outputs clean codes.
    """
    selected: list[list[Any]] = []
    for segment in itinerary.segments or []:
        dep_date = getattr(segment, "departure_date", None) or (0, 0, 0)
        if not isinstance(dep_date, (list, tuple)) or len(dep_date) < 3:
            continue
        year, month, day = dep_date[0], dep_date[1], dep_date[2]
        if not year or not month or not day:
            continue
        selected.append([
            segment.departure_airport_code,
            f"{year:04d}-{month:02d}-{day:02d}",
            segment.arrival_airport_code,
            None,
            segment.airline,
            str(segment.flight_number or ""),
        ])
    return selected


def get_booking_results(
    itinerary_or_token: Itinerary | str,
    *,
    origin: str = "",
    destination: str = "",
    date: str = "",
    cabin: CabinClass = "economy",
    passengers: Passengers = Passengers(),
    max_stops: Optional[int] = None,
    airlines: Optional[list[str]] = None,
    earliest_departure: Optional[int] = None,
    latest_departure: Optional[int] = None,
    earliest_arrival: Optional[int] = None,
    latest_arrival: Optional[int] = None,
    selected_legs: Optional[list[list[Any]]] = None,
    registry_version: str | None = None,
    required_keys: tuple[str, ...] | None = None,
    transport: TransportConfig = TransportConfig(),
) -> list[BookingOption]:
    """Fetch fare options (brand + price) for a specific itinerary.

    The first argument can be either an :class:`Itinerary` object (extracts
    token, origin, destination, date, and legs automatically) or a plain
    booking token string (requires explicit keyword arguments).

    Args:
        itinerary_or_token: An Itinerary object or a booking token string.
        origin: Origin airport (required if passing a token string).
        destination: Destination airport (required if passing a token string).
        date: Departure date (required if passing a token string).
        selected_legs: Flight legs (required if passing a token string).
        registry_version: If provided, set as ``registry_version`` on each option.
        required_keys: If provided, warns when any key is missing from parsed options.
        transport: HTTP transport configuration (default ``TransportConfig()``).
    """
    if isinstance(itinerary_or_token, Itinerary):
        itin = itinerary_or_token
        booking_token = itin.booking_token
        if not origin:
            origin = itin.departure_airport_code
        if not destination:
            destination = itin.arrival_airport_code
        if not date:
            dep = itin.departure_date
            if dep and dep != (0, 0, 0):
                date = f"{dep[0]:04d}-{dep[1]:02d}-{dep[2]:02d}"
        if selected_legs is None:
            selected_legs = _build_selected_legs(itin)
    else:
        booking_token = itinerary_or_token

    if not booking_token or not selected_legs:
        return []

    logger.debug(
        "get_booking_results %s->%s on %s (%d legs)",
        origin, destination, date, len(selected_legs),
    )

    legs = [
        _normalize_rpc_leg(
            origin, destination, date,
            max_stops=max_stops, airlines=airlines,
            earliest_departure=earliest_departure,
            latest_departure=latest_departure,
            earliest_arrival=earliest_arrival,
            latest_arrival=latest_arrival,
        )
    ]
    filters = _build_filters_from_legs(
        legs, cabin=cabin, passengers=passengers,
        sort=SORT_DEPARTURE_TIME,
    )
    filter_block = filters[1]
    encoded_body = _build_booking_f_req(booking_token, filter_block, selected_legs)
    if not encoded_body:
        return []

    res = _http_post(
        BOOKING_RPC_URL,
        content=f"f.req={encoded_body}".encode(),
        transport=transport,
    )

    return _parse_booking_rpc_response(
        res.text,
        registry_version=registry_version,
        required_keys=required_keys,
    )


def get_trip_booking_results(
    booking_token: str,
    legs: list[dict[str, Any]],
    *,
    cabin: CabinClass = "economy",
    passengers: Passengers = Passengers(),
    transport: TransportConfig = TransportConfig(),
) -> list[BookingOption]:
    """Fetch booking options for an exact multi-leg trip selection."""
    if not booking_token or not legs:
        return []

    filters = _build_filters_from_legs(
        legs,
        cabin=cabin,
        passengers=passengers,
        sort=SORT_DEPARTURE_TIME,
    )
    inner = [[None, booking_token], filters[1], None, 0]
    encoded_body = _encode_f_req_payload(inner)

    res = _http_post(
        BOOKING_RPC_URL,
        content=f"f.req={encoded_body}".encode(),
        transport=transport,
    )

    return _parse_booking_rpc_response(res.text)


def _parse_rpc_response(text: str) -> Optional[RawSearchResult]:
    """Parse the RPC response.

    Response format: `)]}'` security prefix -> strip -> JSON parse
    -> extract [0][2] -> JSON parse again -> flight data.
    The inner structure matches what decode_result() expects.
    """
    # Strip security prefix
    stripped = text.lstrip(")]}'")
    if not stripped.strip():
        return None

    try:
        outer = json.loads(stripped)
    except json.JSONDecodeError as e:
        raise SwoopParseError(f"Failed to parse RPC response JSON: {e}") from e

    # Extract inner JSON string at [0][2]
    inner_json = None
    try:
        inner_json = outer[0][2]
    except (IndexError, TypeError):
        logger.warning("RPC response missing data at [0][2]")
        return None

    if not inner_json:
        return None

    # Inner value is a JSON string that needs to be parsed again
    try:
        data = json.loads(inner_json)
    except (json.JSONDecodeError, TypeError) as e:
        raise SwoopParseError(f"Failed to parse inner RPC response JSON: {e}") from e

    if data is None:
        return None

    return decode_result(data)

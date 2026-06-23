"""Google Flights deals endpoint client.

Fetches the best flight deals from a given origin airport via the
GetFlightDealsStreaming RPC endpoint. Requires a session established
by first loading the deals page (for cookies).
"""

import json
import logging
from datetime import date, timedelta
from typing import Any, Optional

from ._regions import region_for_iata
from .builders import CABIN_CLASS_MAP, CabinClass
from .decoder import _safe_get, detect_error_envelope
from .exceptions import SwoopParseError, SwoopUpstreamError
from .models import Deal, DealsResult, Passengers, TransportConfig
from .rpc import _apply_country, _encode_f_req_payload, _get_client, _post_with_retry

logger = logging.getLogger(__name__)

DEALS_PAGE_URL = "https://www.google.com/travel/flights/deals"

DEALS_RPC_URL = (
    "https://www.google.com/_/FlightsFrontendUi/data/"
    "travel.frontend.flights.FlightsFrontendService/GetFlightDealsStreaming"
)


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

def _establish_session(
    client: Any,
    *,
    transport: TransportConfig = TransportConfig(),
) -> None:
    """GET the deals page to establish session cookies on the client."""
    url = _apply_country(DEALS_PAGE_URL, transport.country)
    logger.debug("Establishing deals session via GET %s", url)
    client.get(
        url,
        headers={"accept": "text/html", "accept-language": "en-US,en;q=0.9"},
        timeout=transport.timeout,
    )


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------

def _build_deals_payload(
    origin: str | list[str],
    *,
    cabin: CabinClass = "economy",
    max_stops: Optional[int] = None,
    airlines: Optional[list[str]] = None,
    passengers: Passengers = Passengers(),
    include_basic_economy: bool = False,
) -> str:
    """Build and encode the deals request payload.

    Slot indexing on the inner array mirrors swoop.rpc._build_request — keep
    the two structures aligned so behavior parity is easy to maintain.
    """
    cabin_code = CABIN_CLASS_MAP.get(cabin, 1)

    if max_stops is None:
        stops_val = 0  # any
    else:
        stops_val = max_stops + 1  # 0 -> 1 (nonstop), 1 -> 2, etc.

    # Dates are required by the payload structure but ignored by the server
    # (probed: shifting date 90d ahead returns the same 30 deals byte-for-byte).
    # The server picks its own forward window (~4 months).
    tomorrow = date.today() + timedelta(days=1)
    date_out = tomorrow.isoformat()
    date_ret = (tomorrow + timedelta(days=7)).isoformat()

    # Multi-origin: accept either a single IATA string or a list. The RPC
    # accepts a list inside the airport_set structure (probed).
    if isinstance(origin, str):
        origin_codes = [origin]
    else:
        origin_codes = list(origin)
    origin_set = [[[code, 0] for code in origin_codes]]

    # Airlines filter: RPC slot 4 of each segment (probed). Pass sorted
    # list of IATA codes; the upstream treats it as OR-filter.
    airlines_slot = sorted(airlines) if airlines else None

    outbound_segment = [
        origin_set,        # [0] origin IATA(s)
        [],                # [1] destination: anywhere
        None,              # [2] time restrictions (probed: ignored)
        stops_val,         # [3] max stops
        airlines_slot,     # [4] airlines filter (probed: honored)
        None,              # [5] placeholder
        date_out,          # [6] travel date (probed: ignored)
    ]
    return_segment = [
        [],
        origin_set,
        None,
        stops_val,
        airlines_slot,
        None,
        date_ret,
    ]

    payload = [
        [],
        [
            None, None,                              # [0], [1]
            1,                                       # [2] trip type
            None, [],                                # [3], [4]
            cabin_code,                              # [5]
            [passengers.adults, passengers.children, # [6] passengers
             passengers.infants_in_seat, passengers.infants_on_lap],
            None, None, None, None, None, None,     # [7]-[12]
            [outbound_segment, return_segment],     # [13] segments
            None, None, None,                        # [14]-[16]
            1,                                       # [17] roundtrip flag
            None, None, None, None, None, None, None,  # [18]-[24]
            None, None, None,                                       # [25]-[27]
            None if include_basic_economy else 1,                   # [28] exclude basic economy (probed)
            None,                                                   # [29]
            [None, None, None, None, None, 1, None, None, 1],       # [30]
            3,                                                       # [31]
        ],
        "",    # query (empty = no AI search)
        "c1",  # session token
    ]
    return _encode_f_req_payload(payload)


# ---------------------------------------------------------------------------
# Streaming response parser
# ---------------------------------------------------------------------------

def _extract_deals_from_entries(entries: list[Any]) -> list[Any]:
    """Scan a list of wrb.fr entries for the one containing deals."""
    for entry in entries:
        inner_str = _safe_get(entry, [2])
        if not isinstance(inner_str, str) or len(inner_str) < 500:
            continue
        try:
            data = json.loads(inner_str)
        except json.JSONDecodeError:
            continue
        items = _safe_get(data, [3, 9])
        if isinstance(items, list) and len(items) > 0:
            return items
    return []


def _raise_if_deals_error_envelope(frames: list[Any]) -> None:
    """Raise :class:`SwoopUpstreamError` if any frame is an ErrorResponse.

    The deals parser otherwise treats a rejected request (null payload, error
    block at index 5) as zero deals — which can silently wipe the watcher's
    snapshot baseline. Surface it as an upstream error instead, matching the
    shopping path.
    """
    for frame in frames:
        error = detect_error_envelope(frame)
        if error is not None:
            logger.warning(
                "GetFlightDealsStreaming returned an ErrorResponse (gRPC %s, %s)",
                error[0], error[1],
            )
            raise SwoopUpstreamError(error[0], type_url=error[1])


def _parse_streaming_response(text: str) -> list[Any]:
    """Parse the deals streaming response and extract deal items.

    Handles two response formats:
    1. **Flat array** (primp): ``[["wrb.fr",...], ["wrb.fr",...], ...]``
       — multiple entries in one JSON array, deals in the largest entry.
    2. **Length-prefixed lines** (raw/browser): alternating length prefix
       and JSON line — each line is a separate ``[["wrb.fr",...]]`` chunk.
    """
    if text.startswith(")]}'"):
        text = text[4:]
    text = text.strip()

    if not text:
        return []

    # Detect anti-bot / consent / geo-block responses: Google returns HTTP
    # 200 with an HTML body in these cases. Without this guard the parser
    # silently treats the response as zero deals, which can wipe the
    # watcher's snapshot baseline.
    _head = text[:200].lower()
    if _head.startswith("<!doctype") or _head.startswith("<html") or "consent.google.com" in _head:
        raise SwoopParseError(
            "Google returned an HTML response (anti-bot challenge, "
            "consent wall, or geo-block) instead of JSON deals data."
        )

    # Try format 1: single JSON array with multiple entries
    try:
        outer = json.loads(text)
        if isinstance(outer, list) and len(outer) > 1:
            items = _extract_deals_from_entries(outer)
            if items:
                return items
            # No deals: distinguish a genuine empty result from a rejected
            # request before falling through. Each entry is a wrb.fr frame.
            _raise_if_deals_error_envelope(outer)
            return items
    except json.JSONDecodeError:
        pass

    # Format 2: length-prefixed lines
    deals: list[Any] = []
    frames: list[Any] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line or len(line) < 100:
            continue
        try:
            chunk = json.loads(line)
        except json.JSONDecodeError:
            continue
        # Each chunk is [["wrb.fr", null, "<inner JSON>", ...]]
        frame = _safe_get(chunk, [0])
        if isinstance(frame, list):
            frames.append(frame)
        inner_str = _safe_get(chunk, [0, 2])
        if not isinstance(inner_str, str) or len(inner_str) < 500:
            continue
        try:
            data = json.loads(inner_str)
        except json.JSONDecodeError:
            continue
        items = _safe_get(data, [3, 9])
        if isinstance(items, list) and len(items) > 0:
            deals = items
            break

    if not deals:
        _raise_if_deals_error_envelope(frames)
    return deals


# ---------------------------------------------------------------------------
# Deal item parser
# ---------------------------------------------------------------------------

def _format_date(raw: Any) -> Optional[str]:
    """Convert [year, month, day] to YYYY-MM-DD string."""
    if not isinstance(raw, list) or len(raw) < 3:
        return None
    try:
        return f"{raw[0]:04d}-{raw[1]:02d}-{raw[2]:02d}"
    except (TypeError, ValueError):
        return None


def _parse_deal(
    item: list[Any],
    currency: Optional[str] = None,
    *,
    query_cabin: Optional[str] = None,
    query_adults: int = 1,
    query_include_basic_economy: bool = False,
) -> Optional[Deal]:
    """Parse a single deal item from the response."""
    try:
        departure_date = _format_date(_safe_get(item, [1]))
        return_date = _format_date(_safe_get(item, [2]))
        price = _safe_get(item, [3, 0, 1])
        typical_price = _safe_get(item, [4, 0, 1])
        discount_pct = _safe_get(item, [5])
        booking_path = _safe_get(item, [6, 2])
        duration_minutes = _safe_get(item, [7])
        stops = _safe_get(item, [8])
        airline_code = _safe_get(item, [10])
        airline_name = _safe_get(item, [11])
        dest_info = _safe_get(item, [13])
        trip_days = _safe_get(item, [16])
        origin = _safe_get(item, [17])
        destination = _safe_get(item, [18])

        dest_city = dest_info[0] if isinstance(dest_info, list) and len(dest_info) > 0 else ""
        dest_country = dest_info[1] if isinstance(dest_info, list) and len(dest_info) > 1 else ""

        if price is None or destination is None:
            return None

        # Only accept absolute paths rooted at "/"; reject "//foo" (would
        # resolve as protocol-relative to www.google.com//foo, possibly
        # redirected by browsers to foo), absolute URLs, "javascript:",
        # and other shapes that should not appear in Google's response.
        booking_url = None
        if (
            isinstance(booking_path, str)
            and booking_path.startswith("/")
            and not booking_path.startswith("//")
        ):
            booking_url = "https://www.google.com" + booking_path

        # Upstream surfaces one primary carrier per deal today; list shape
        # is forward-compatible for multi-carrier disclosure later. The "*"
        # sentinel for "multiple airlines" is preserved as a single-element
        # list — callers can detect and handle it.
        airlines_list = [airline_code] if airline_code else []
        airline_names_list = [airline_name] if airline_name else []

        return Deal(
            origin=origin or "",
            destination=destination or "",
            destination_city=dest_city,
            destination_country=dest_country,
            departure_date=departure_date or "",
            return_date=return_date,
            price=int(price),
            typical_price=int(typical_price) if typical_price is not None else None,
            discount_pct=int(discount_pct) if discount_pct is not None else None,
            airlines=airlines_list,
            airline_names=airline_names_list,
            duration_minutes=int(duration_minutes) if duration_minutes is not None else None,
            # Keep stops=None when upstream doesn't report it. Mapping to
            # 0 would silently misrender as "Nonstop" — the user clicks
            # through expecting a nonstop and books a 2-stop itinerary.
            stops=int(stops) if stops is not None else None,
            trip_days=int(trip_days) if trip_days is not None else None,
            destination_region=region_for_iata(destination),
            currency=currency,
            booking_url=booking_url,
            query_cabin=query_cabin,
            query_adults=query_adults,
            query_include_basic_economy=query_include_basic_economy,
        )
    except (TypeError, IndexError, ValueError) as exc:
        logger.debug("Failed to parse deal item: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Currency header
# ---------------------------------------------------------------------------

_COUNTRY_CURRENCY: dict[str, str] = {
    "US": "USD", "GB": "GBP", "CA": "CAD", "AU": "AUD", "NZ": "NZD",
    "JP": "JPY", "KR": "KRW", "CN": "CNY", "IN": "INR", "SG": "SGD",
    "HK": "HKD", "TW": "TWD", "TH": "THB", "MY": "MYR", "PH": "PHP",
    "ID": "IDR", "VN": "VND", "MX": "MXN", "BR": "BRL", "AR": "ARS",
    "CL": "CLP", "CO": "COP", "PE": "PEN", "ZA": "ZAR", "AE": "AED",
    "SA": "SAR", "IL": "ILS", "TR": "TRY", "RU": "RUB", "UA": "UAH",
    "PL": "PLN", "CZ": "CZK", "HU": "HUF", "RO": "RON", "SE": "SEK",
    "NO": "NOK", "DK": "DKK", "CH": "CHF", "EG": "EGP", "NG": "NGN",
    "KE": "KES", "PA": "USD",
}

# EUR countries
for _cc in ("DE", "FR", "IT", "ES", "NL", "BE", "AT", "PT", "IE", "FI",
            "GR", "LT", "LV", "EE", "SK", "SI", "LU", "MT", "CY", "HR"):
    _COUNTRY_CURRENCY[_cc] = "EUR"


def _currency_header(country: Optional[str]) -> str:
    """Build the x-goog-ext-259736195-jspb header value."""
    cc = (country or "US").upper()
    curr = _COUNTRY_CURRENCY.get(cc, "USD")
    return json.dumps(["en", cc, curr, 1, None, [300], None, None, 7, []])


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def fetch_deals(
    origin: str | list[str],
    *,
    cabin: CabinClass = "economy",
    max_stops: Optional[int] = None,
    airlines: Optional[list[str]] = None,
    passengers: Passengers = Passengers(),
    include_basic_economy: bool = False,
    transport: TransportConfig = TransportConfig(),
    _client: Any = None,
) -> DealsResult:
    """Fetch flight deals from Google Flights.

    Establishes a session, builds the request, and parses the streaming
    response into a :class:`DealsResult`.

    ``_client`` (internal) lets a caller (notably
    ``_fetch_deals_per_origin``) inject a per-worker primp.Client so
    multiple parallel calls don't share one cookie jar. When ``None``,
    falls back to the shared, lock-protected ``_get_client`` cache.
    """
    client = _client if _client is not None else _get_client(
        transport.proxy, transport.impersonate,
    )

    # Step 1: Establish session cookies
    _establish_session(client, transport=transport)

    # Step 2: Build and send request
    encoded_payload = _build_deals_payload(
        origin, cabin=cabin, max_stops=max_stops, airlines=airlines,
        passengers=passengers,
        include_basic_economy=include_basic_economy,
    )
    url = _apply_country(DEALS_RPC_URL, transport.country)
    body = f"f.req={encoded_payload}".encode()

    headers = {
        "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
        "x-same-domain": "1",
        "x-goog-ext-259736195-jspb": _currency_header(transport.country),
        "referer": "https://www.google.com/travel/flights/deals",
    }

    logger.debug(
        "fetch_deals %s (cabin=%s, max_stops=%s)",
        origin, cabin, max_stops,
    )

    res = _post_with_retry(client, url, body, headers, transport=transport)

    # DealsResult.origin is a single string; collapse list to comma-joined
    # for multi-origin calls.
    origin_label = origin if isinstance(origin, str) else ",".join(origin)

    # Step 3: Parse streaming response
    raw_deals = _parse_streaming_response(res.text)
    if not raw_deals:
        logger.debug("No deals found in response")
        return DealsResult(deals=[], origin=origin_label)

    # Determine currency from country
    cc = (transport.country or "US").upper()
    currency = _COUNTRY_CURRENCY.get(cc, "USD")

    # Step 4: Parse individual deals
    deals: list[Deal] = []
    for item in raw_deals:
        deal = _parse_deal(
            item,
            currency=currency,
            query_cabin=cabin,
            query_adults=passengers.adults,
            query_include_basic_economy=include_basic_economy,
        )
        if deal is not None:
            deals.append(deal)

    logger.debug("Parsed %d deals from %s", len(deals), origin_label)
    return DealsResult(deals=deals, origin=origin_label)

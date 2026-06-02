"""Google Flights Explore endpoint client (``GetExploreDestinations``).

Explore is destination *discovery* ("where could I go from here?"). It returns
a list of suggested destinations with images, coordinates, and Google's own
suggested dates — one-way or roundtrip. It does NOT return prices: a live trace
confirmed the map's prices come from a separate client-side path, not this RPC
(see ``docs/superpowers/specs/2026-06-02-explore-endpoint-design.md``). Use
``swoop.price_explore`` to price a chosen destination.

Response row index map (verified live):
    [0]  place_id              [1]  [lat, lon]        [2]  display name
    [3]  image_url             [4]  country           [7]  secondary_image_url
    [11] departure_date        [12] return_date       [15] destination IATA
    [17] drive_minutes (ground/drive time for nearby drivable destinations;
         null for fly-to destinations — NOT a flight duration)
    origin metadata: ``inner[6][0]`` = [name, [lat, lon], place_id, IATA, ...]

The origin block is ``[[[code, flag]]]``. The IATA + flag ``0`` form returns a
region-scoped subset; a Google place_id origin + flag ``4`` returns the full
worldwide set (a future enhancement — see design spec D8).
"""
from __future__ import annotations

import json
import logging
import re
import threading
import urllib.parse
from typing import Any, Optional

from .builders import CABIN_CLASS_MAP, CabinClass
from .decoder import _safe_get
from .exceptions import SwoopParseError
from .models import ExploreDestination, ExploreResult, Passengers, TransportConfig
from .rpc import _apply_country, _encode_f_req_payload, _get_client, _post_with_retry

logger = logging.getLogger(__name__)

EXPLORE_PAGE_URL = "https://www.google.com/travel/explore"
EXPLORE_RPC_URL = (
    "https://www.google.com/_/FlightsFrontendUi/data/"
    "travel.frontend.flights.FlightsFrontendService/GetExploreDestinations"
)


def _origin_block(origin: str, flag: int = 0) -> list:
    """Build the nested origin structure ``[[[code, flag]]]``."""
    return [[[origin, flag]]]


def _build_explore_payload(
    origin: str,
    *,
    cabin: CabinClass = "economy",
    one_way: bool = False,
    max_stops: Optional[int] = None,
    passengers: Passengers = Passengers(),
    origin_flag: int = 0,
) -> list[Any]:
    """Build the ``GetExploreDestinations`` ``f.req`` payload."""
    cabin_code = CABIN_CLASS_MAP.get(cabin, 1)
    stops_val = 0 if max_stops is None else max_stops + 1
    ob = _origin_block(origin, origin_flag)
    trip_type = 2 if one_way else 1
    if one_way:
        segments = [[ob, [], None, stops_val]]
    else:
        segments = [[ob, [], None, stops_val], [[], ob, None, stops_val]]
    # Slot 6 mirrors rpc._build_request: [adults, children, infants_in_seat,
    # infants_on_lap]. Wire the real counts through so the explored set (and
    # the dates Google suggests) reflect the requested party size.
    pax = [
        passengers.adults, passengers.children,
        passengers.infants_in_seat, passengers.infants_on_lap,
    ]
    filters = [
        None, None, trip_type, None, [], cabin_code,
        pax,
        None, None, None, None, None, None,
        segments,
        None, None, None, 0,
    ]
    return [[], None, None, filters, None, 1, None, 0, None, 1, [1004, 833], 2]


def _encode_explore_f_req(payload: list[Any]) -> bytes:
    return f"f.req={_encode_f_req_payload(payload)}&".encode()


def _extract_inner(text: str) -> list[Any]:
    """Unwrap the RPC framing to the inner destination payload.

    Google batchexecute responses are chunked: a ``)]}'`` prefix, then repeated
    (length-prefix line, ``[[...]]`` data line) pairs. Scan every candidate line
    for a ``wrb.fr`` frame, skip unparseable ones, and prefer the chunk that
    actually carries destinations — so a reordered/empty leading chunk or a
    garbled first chunk doesn't yield empty or missing results.
    """
    stripped = text[4:].lstrip() if text.startswith(")]}'") else text
    lowered = stripped.lstrip().lower()
    if lowered.startswith("<!doctype") or lowered.startswith("<html") or "consent.google.com" in lowered:
        raise SwoopParseError("Explore RPC returned an HTML/consent page (likely blocked)")

    candidates: list[Any] = []
    for raw_line in stripped.splitlines():
        line = raw_line.strip()
        if not line.startswith("[["):
            continue
        try:
            outer = json.loads(line)
        except ValueError:
            continue
        for entry in outer if isinstance(outer, list) else []:
            if (isinstance(entry, list) and entry and entry[0] == "wrb.fr"
                    and len(entry) > 2 and isinstance(entry[2], str)):
                try:
                    candidates.append(json.loads(entry[2]))
                except ValueError:
                    continue

    # Prefer a chunk carrying a non-empty destination list; fall back to the
    # first valid frame so origin metadata still parses.
    for inner in candidates:
        rows = _safe_get(inner, [3, 0])
        if isinstance(rows, list) and rows:
            return inner
    if candidates:
        return candidates[0]
    raise SwoopParseError("Explore response missing inner payload")


def _float_pair(value: Any) -> tuple[Optional[float], Optional[float]]:
    if not isinstance(value, list) or len(value) < 2:
        return (None, None)
    try:
        return (float(value[0]), float(value[1]))
    except (TypeError, ValueError):
        return (None, None)


def _opt_str(value: Any) -> Optional[str]:
    return value if isinstance(value, str) else None


def _opt_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_destination(
    row: list[Any], *, origin: str, cabin: str, adults: int, one_way: bool = False
) -> Optional[ExploreDestination]:
    place_id = _safe_get(row, [0])
    name = _safe_get(row, [2])
    if not isinstance(place_id, str) or not isinstance(name, str):
        return None
    lat, lon = _float_pair(_safe_get(row, [1]))
    country = _safe_get(row, [4])
    return ExploreDestination(
        origin=origin,
        destination=_opt_str(_safe_get(row, [15])),
        destination_name=name,
        destination_country=country if isinstance(country, str) else "",
        place_id=place_id,
        latitude=lat,
        longitude=lon,
        departure_date=_opt_str(_safe_get(row, [11])),
        # Enforce the one-way contract here rather than trusting the server to
        # omit [12]: a one-way ExploreDestination must never carry a return
        # date, or price_explore would silently price a roundtrip.
        return_date=None if one_way else _opt_str(_safe_get(row, [12])),
        image_url=_opt_str(_safe_get(row, [3])),
        secondary_image_url=_opt_str(_safe_get(row, [7])),
        drive_minutes=_opt_int(_safe_get(row, [17])),
        query_cabin=cabin,
        query_adults=adults,
    )


def parse_explore_payload(
    inner: list[Any], *, origin: str, cabin: str = "economy", adults: int = 1,
    one_way: bool = False,
) -> ExploreResult:
    """Parse an Explore inner payload into public models."""
    raw = _safe_get(inner, [3, 0])
    destinations: list[ExploreDestination] = []
    if isinstance(raw, list):
        for row in raw:
            if isinstance(row, list):
                dest = _parse_destination(
                    row, origin=origin, cabin=cabin, adults=adults, one_way=one_way
                )
                if dest is not None:
                    destinations.append(dest)
    orow = _safe_get(inner, [6, 0])
    if isinstance(orow, list):
        olat, olon = _float_pair(_safe_get(orow, [1]))
        return ExploreResult(
            destinations=destinations,
            origin=_opt_str(_safe_get(orow, [3])) or origin,
            origin_name=_opt_str(_safe_get(orow, [0])),
            origin_place_id=_opt_str(_safe_get(orow, [2])),
            origin_latitude=olat,
            origin_longitude=olon,
        )
    return ExploreResult(destinations=destinations, origin=origin)


def _browser_params(page_html: str) -> dict[str, str]:
    """Extract the volatile ``bl`` / ``f.sid`` RPC session params from the page."""
    params: dict[str, str] = {}
    bl = re.search(r'"cfb2h":"([^"]+)"', page_html)
    if bl:
        params["bl"] = bl.group(1)
    fsid = re.search(r'"FdrFJe":"([^"]+)"', page_html)
    if fsid:
        params["f.sid"] = fsid.group(1)
    return params


# bl / f.sid are stable build/session identifiers, so cache them per
# (proxy, impersonate, country) to avoid a full page GET on every explore()
# call. Invalidated on a parse failure (a likely sign they went stale).
_browser_params_cache: dict[str, dict[str, str]] = {}
_browser_params_lock = threading.Lock()


def _get_browser_params(
    client: Any, page_url: str, *, key: str, transport: TransportConfig, force_refresh: bool = False
) -> dict[str, str]:
    """Return cached RPC session params, fetching the page on a miss/refresh."""
    if not force_refresh:
        with _browser_params_lock:
            cached = _browser_params_cache.get(key)
        if cached is not None:
            return cached
    page = client.get(
        page_url,
        headers={"accept": "text/html", "accept-language": "en-US,en;q=0.9"},
        timeout=transport.timeout,
    )
    params = _browser_params(page.text)
    with _browser_params_lock:
        _browser_params_cache[key] = params
    return params


def _explore_rpc_url(rpc_url: str, params: dict[str, str]) -> str:
    """Append the bl/f.sid session query to the RPC URL."""
    if not params:
        return rpc_url
    query = urllib.parse.urlencode({
        **params,
        "hl": "en-US",
        "soc-app": "162",
        "soc-platform": "1",
        "soc-device": "1",
        "rt": "c",
    })
    separator = "&" if "?" in rpc_url else "?"
    return f"{rpc_url}{separator}{query}"


def fetch_explore(
    origin: str,
    *,
    cabin: CabinClass = "economy",
    one_way: bool = False,
    max_stops: Optional[int] = None,
    passengers: Passengers = Passengers(),
    transport: TransportConfig = TransportConfig(),
) -> ExploreResult:
    """Fetch Explore destination suggestions from Google Flights."""
    client = _get_client(transport.proxy, transport.impersonate)
    page_url = _apply_country(EXPLORE_PAGE_URL, transport.country)
    rpc_base = _apply_country(EXPLORE_RPC_URL, transport.country)
    cache_key = f"{transport.proxy or ''}|{transport.impersonate or ''}|{transport.country or ''}"

    body = _encode_explore_f_req(
        _build_explore_payload(
            origin, cabin=cabin, one_way=one_way, max_stops=max_stops, passengers=passengers
        )
    )
    headers = {
        "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
        "x-same-domain": "1",
        "referer": page_url,
    }

    # Cached bl/f.sid can go stale; on a parse failure, drop the cache and
    # refetch the page once before giving up.
    for attempt in range(2):
        params = _get_browser_params(
            client, page_url, key=cache_key, transport=transport, force_refresh=(attempt == 1)
        )
        rpc_url = _explore_rpc_url(rpc_base, params)
        res = _post_with_retry(client, rpc_url, body, headers, transport=transport)
        try:
            inner = _extract_inner(res.text)
        except SwoopParseError:
            with _browser_params_lock:
                _browser_params_cache.pop(cache_key, None)
            if attempt == 0:
                continue
            raise
        return parse_explore_payload(
            inner, origin=origin, cabin=cabin, adults=passengers.adults, one_way=one_way
        )
    raise SwoopParseError("Explore response could not be parsed after a session refresh")

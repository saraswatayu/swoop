"""Google Flights Explore endpoint client."""

from __future__ import annotations

import json
import logging
import random
import re
import time
import urllib.parse
from typing import Any, Optional

from .builders import CABIN_CLASS_MAP, CabinClass
from .decoder import _safe_get
from .exceptions import SwoopHTTPError, SwoopParseError, SwoopRateLimitError
from .models import ExploreDestination, ExploreResult, Passengers, TransportConfig
from .rpc import _apply_country, _get_client

logger = logging.getLogger(__name__)

EXPLORE_PAGE_URL = "https://www.google.com/travel/explore"
EXPLORE_RPC_URL = (
    "https://www.google.com/_/FlightsFrontendUi/data/"
    "travel.frontend.flights.FlightsFrontendService/GetExploreDestinations"
)


def _build_explore_payload(
    origin: str,
    *,
    cabin: CabinClass = "economy",
    passengers: Passengers = Passengers(),
    max_stops: Optional[int] = None,
    viewport: tuple[int, int] = (1004, 833),
) -> list[Any]:
    """Build the Explore RPC payload for a flexible roundtrip destination query."""
    cabin_code = CABIN_CLASS_MAP.get(cabin, 1)
    stops_val = 0 if max_stops is None else max_stops + 1
    origin_block = [[[origin, 0]]]

    filters = [
        None,
        None,
        1,  # roundtrip
        None,
        [],
        cabin_code,
        [
            passengers.adults,
            passengers.children,
            passengers.infants_in_seat,
            passengers.infants_on_lap,
        ],
        None,
        None,
        None,
        None,
        None,
        None,
        [
            [
                origin_block,
                [],
                None,
                stops_val,
            ],
            [
                [],
                origin_block,
                None,
                stops_val,
            ],
        ],
        None,
        None,
        None,
        0,
    ]

    return [
        [],
        None,
        None,
        filters,
        None,
        1,
        None,
        0,
        None,
        1,
        list(viewport),
        2,
    ]


def _encode_explore_f_req(payload: list[Any]) -> bytes:
    payload_json = json.dumps(payload, separators=(",", ":"))
    wrapped = json.dumps([None, payload_json], separators=(",", ":"))
    return f"f.req={urllib.parse.quote(wrapped)}&".encode()


def _extract_json_line(text: str) -> str:
    stripped = text[4:].lstrip() if text.startswith(")]}'") else text
    for line in stripped.splitlines():
        line = line.strip()
        if line.startswith("[["):
            return line
    return stripped.strip()


def _parse_explore_response(text: str) -> list[Any]:
    """Parse the length-prefixed Explore RPC response."""
    try:
        outer = json.loads(_extract_json_line(text))
    except json.JSONDecodeError as exc:
        raise SwoopParseError(f"Failed to parse Explore response JSON: {exc}") from exc

    inner_str = None
    error_envelope = None
    if isinstance(outer, list):
        for entry in outer:
            if not isinstance(entry, list) or not entry:
                continue
            if entry[0] != "wrb.fr":
                continue
            if len(entry) > 2 and isinstance(entry[2], str):
                inner_str = entry[2]
                break
            if len(entry) > 5:
                error_envelope = entry[5]

    if not inner_str:
        if error_envelope is not None:
            raise SwoopParseError(f"Explore RPC returned error envelope: {error_envelope!r}")
        raise SwoopParseError("Explore response missing inner payload")

    try:
        inner = json.loads(inner_str)
    except json.JSONDecodeError as exc:
        raise SwoopParseError(f"Failed to parse inner Explore response JSON: {exc}") from exc
    if not isinstance(inner, list):
        raise SwoopParseError("Explore inner payload is not a list")
    return inner


def _parse_float_pair(value: Any) -> tuple[Optional[float], Optional[float]]:
    if not isinstance(value, list) or len(value) < 2:
        return (None, None)
    try:
        return (float(value[0]), float(value[1]))
    except (TypeError, ValueError):
        return (None, None)


def _parse_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_destination(row: list[Any]) -> Optional[ExploreDestination]:
    place_id = _safe_get(row, [0])
    name = _safe_get(row, [2])
    airport_code = _safe_get(row, [15])
    if not isinstance(place_id, str) or not isinstance(name, str):
        return None

    latitude, longitude = _parse_float_pair(_safe_get(row, [1]))
    country = _safe_get(row, [4])
    return ExploreDestination(
        place_id=place_id,
        name=name,
        country=country if isinstance(country, str) else "",
        latitude=latitude,
        longitude=longitude,
        airport_code=airport_code if isinstance(airport_code, str) else None,
        departure_date=_safe_get(row, [11]) if isinstance(_safe_get(row, [11]), str) else None,
        return_date=_safe_get(row, [12]) if isinstance(_safe_get(row, [12]), str) else None,
        image_url=_safe_get(row, [3]) if isinstance(_safe_get(row, [3]), str) else None,
        secondary_image_url=_safe_get(row, [7]) if isinstance(_safe_get(row, [7]), str) else None,
        distance=_safe_get(row, [8]) if isinstance(_safe_get(row, [8]), str) else None,
        duration_minutes=_parse_int(_safe_get(row, [17])),
        parent_place_id=_safe_get(row, [19]) if isinstance(_safe_get(row, [19]), str) else None,
    )


def _parse_origin(inner: list[Any], fallback: str) -> dict[str, Any]:
    row = _safe_get(inner, [6, 0])
    if not isinstance(row, list):
        return {"origin": fallback}
    latitude, longitude = _parse_float_pair(_safe_get(row, [1]))
    origin_name = _safe_get(row, [0])
    origin_place_id = _safe_get(row, [2])
    origin = _safe_get(row, [3])
    return {
        "origin": origin if isinstance(origin, str) else fallback,
        "origin_name": origin_name if isinstance(origin_name, str) else None,
        "origin_place_id": origin_place_id if isinstance(origin_place_id, str) else None,
        "origin_latitude": latitude,
        "origin_longitude": longitude,
    }


def parse_explore_payload(inner: list[Any], *, origin: str = "") -> ExploreResult:
    """Parse an Explore inner payload into public models."""
    raw_destinations = _safe_get(inner, [3, 0])
    destinations: list[ExploreDestination] = []
    if isinstance(raw_destinations, list):
        for row in raw_destinations:
            if not isinstance(row, list):
                continue
            destination = _parse_destination(row)
            if destination is not None:
                destinations.append(destination)

    return ExploreResult(
        destinations=destinations,
        **_parse_origin(inner, origin),
    )


def _extract_browser_params(page_html: str) -> dict[str, str]:
    """Extract volatile RPC query params from the Explore page HTML."""
    params: dict[str, str] = {}
    bl_match = re.search(r'"cfb2h":"([^"]+)"', page_html)
    if bl_match:
        params["bl"] = bl_match.group(1)
    fsid_match = re.search(r'"FdrFJe":"([^"]+)"', page_html)
    if fsid_match:
        params["f.sid"] = fsid_match.group(1)
    return params


def fetch_explore(
    origin: str,
    *,
    cabin: CabinClass = "economy",
    max_stops: Optional[int] = None,
    passengers: Passengers = Passengers(),
    transport: TransportConfig = TransportConfig(),
) -> ExploreResult:
    """Fetch Explore destination recommendations from Google Flights."""
    client = _get_client(transport.proxy, transport.impersonate)
    page_url = _apply_country(EXPLORE_PAGE_URL, transport.country)
    rpc_url = _apply_country(EXPLORE_RPC_URL, transport.country)

    page_res = client.get(
        page_url,
        headers={"accept": "text/html", "accept-language": "en-US,en;q=0.9"},
        timeout=transport.timeout,
    )
    params = _extract_browser_params(page_res.text)
    if params:
        query = urllib.parse.urlencode({
            **params,
            "hl": "en-US",
            "soc-app": "162",
            "soc-platform": "1",
            "soc-device": "1",
            "rt": "c",
        })
        separator = "&" if "?" in rpc_url else "?"
        rpc_url = f"{rpc_url}{separator}{query}"

    body = _encode_explore_f_req(
        _build_explore_payload(
            origin,
            cabin=cabin,
            passengers=passengers,
            max_stops=max_stops,
        )
    )
    headers = {
        "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
        "x-same-domain": "1",
        "referer": page_url,
    }

    for attempt in range(1 + transport.retries):
        res = client.post(rpc_url, content=body, headers=headers, timeout=transport.timeout)
        if res.status_code == 200:
            inner = _parse_explore_response(res.text)
            return parse_explore_payload(inner, origin=origin)
        if res.status_code == 429 and attempt < transport.retries:
            delay = (2 ** attempt) + random.uniform(0, 1)
            logger.info(
                "HTTP 429 from Explore RPC, retrying in %.1fs (attempt %d/%d)",
                delay,
                attempt + 1,
                transport.retries,
            )
            time.sleep(delay)
            continue
        if res.status_code == 429:
            raise SwoopRateLimitError()
        raise SwoopHTTPError(res.status_code)

    return ExploreResult(origin=origin)

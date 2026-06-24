"""Shared factory helpers for swoop test suite.

Centralised builders for Google Flights nested-list structures, decoded
dataclass instances, and common test doubles.  Import from here instead of
defining per-file helpers.
"""

from __future__ import annotations

import json
import urllib.parse

from swoop.decoder import Segment, Itinerary, RawSearchResult


# ---------------------------------------------------------------------------
# Google Flights raw nested-list builders (used by test_decoder, test_rpc)
# ---------------------------------------------------------------------------


def make_flight_segment(
    airline_code="DL",
    airline_name="Delta Air Lines",
    flight_number="2300",
    dep_airport="JFK",
    dep_name="John F. Kennedy International Airport",
    arr_airport="LAX",
    arr_name="Los Angeles International Airport",
    dep_date=(2026, 3, 15),
    arr_date=(2026, 3, 15),
    dep_time=(8, 30),
    arr_time=(11, 45),
    travel_time=315,
    codeshares=None,
    aircraft="Boeing 737-900",
    premium_ife=None,
    amenities=None,
    seat_type=None,
    overnight=None,
    legroom=None,
    co2_grams=None,
):
    """Build a flight segment nested list matching Google Flights structure."""
    # Indices: [0]=?, [1]=?, [2]=operator, [3]=dep_airport, [4]=dep_name,
    # [5]=arr_name, [6]=arr_airport, [7]=?, [8]=dep_time, [9]=premium_ife,
    # [10]=arr_time, [11]=travel_time, [12]=amenities, [13]=seat_type,
    # [14]=seat_pitch, [15]=codeshares, [16]=?, [17]=aircraft, [18]=?,
    # [19]=overnight, [20]=dep_date, [21]=arr_date,
    # [22]=[airline_code, flight_number, ?, airline_name],
    # ...[30]=legroom, [31]=co2_grams
    segment = [None] * 33
    segment[2] = f"Operated by {airline_name}"
    segment[3] = dep_airport
    segment[4] = dep_name
    segment[5] = arr_name
    segment[6] = arr_airport
    segment[8] = list(dep_time)
    segment[9] = premium_ife
    segment[10] = list(arr_time)
    segment[11] = travel_time
    segment[12] = amenities
    segment[13] = seat_type
    segment[14] = "32 in"
    segment[15] = codeshares or []
    segment[17] = aircraft
    segment[19] = overnight
    segment[20] = list(dep_date)
    segment[21] = list(arr_date)
    segment[22] = [airline_code, flight_number, None, airline_name]
    segment[30] = legroom
    segment[31] = co2_grams
    return segment


def make_codeshare(airline_code="AA", flight_number="4567", airline_name="American Airlines"):
    """Build a codeshare nested list."""
    return [airline_code, flight_number, None, airline_name]


def make_itinerary_element(
    flights_data,
    summary_data=None,
    airline_code="DL",
    travel_time=315,
    is_budget=False,
    quality_signals=None,
    layovers_data=None,
    carbon_data=None,
):
    """Build an itinerary element matching Google Flights structure.

    An itinerary element is an 11-element array:
    [itin_data, summary_data, None, is_budget, quality_signals, warnings, flag, [], token, cabin, flag]
    itin_data = [airline_code, airline_names, flights, dep_airport, dep_date, dep_time,
                 arr_airport, arr_date, arr_time, travel_time, ..., layovers(13), ..., carbon(22)]
    """
    itin_data = [None] * 23
    itin_data[0] = airline_code
    itin_data[1] = ["Delta Air Lines"]
    itin_data[2] = flights_data
    itin_data[3] = "JFK"
    itin_data[4] = [2026, 3, 15]
    itin_data[5] = [8, 30]
    itin_data[6] = "LAX"
    itin_data[7] = [2026, 3, 15]
    itin_data[8] = [11, 45]
    itin_data[9] = travel_time
    itin_data[13] = layovers_data if layovers_data is not None else []
    itin_data[22] = carbon_data

    # Default summary: [[None, price_cents], b64_string]
    # We can't easily create a valid b64 protobuf, so tests that need price
    # should mock ItinerarySummary.from_b64
    if summary_data is None:
        summary_data = [[None, 35000], None]  # no b64 string

    # Root: [itin_data, summary, None, is_budget, quality_signals, ...]
    root = [None] * 11
    root[0] = itin_data
    root[1] = summary_data
    root[3] = is_budget
    root[4] = quality_signals
    return root


def make_full_response(best_itins=None, other_itins=None):
    """Build a full response nested list matching Google Flights structure.

    Root: [?, ?, [best_flights_at_0, ...], [other_flights_at_0, ...], ...]
    """
    data = [None, None, None, None]
    data[2] = [best_itins or []]
    data[3] = [other_itins or []]
    return data


# ---------------------------------------------------------------------------
# RPC helpers (used by test_rpc)
# ---------------------------------------------------------------------------


def decode_f_req(encoded: str) -> list:
    """Decode f.req output -> inner filters structure."""
    decoded = urllib.parse.unquote(encoded)
    outer = json.loads(decoded)
    # outer = [None, json_string]
    return json.loads(outer[1])


def encode_rpc_outer(inner: object) -> str:
    """Wrap inner payload in Google Flights RPC response format."""
    return ")]}'" + json.dumps([["wrb.fr", None, json.dumps(inner)]])


_ERROR_TYPE_URL = "type.googleapis.com/travel.frontend.flights.ErrorResponse"


def make_error_frame(
    grpc_code: int = 13, type_url: str = _ERROR_TYPE_URL
) -> list:
    """A ``wrb.fr`` ErrorResponse envelope frame (HTTP 200, request rejected).

    Mirrors what Google returns when it rejects an RPC: a null result payload
    at index 2 and a ``[grpc_code, null, [[type_url, ...]]]`` block at index 5.
    """
    return ["wrb.fr", None, None, None, None, [grpc_code, None, [[type_url, [[None]]]]]]


def make_error_response(grpc_code: int = 13) -> str:
    """A full RPC response string carrying a single ErrorResponse envelope."""
    return ")]}'" + json.dumps([make_error_frame(grpc_code)])


def make_brand_block(code: str = "DELTA MAIN CLASSIC", label: str = "Delta Main Classic") -> list:
    """Build a brand block for booking options."""
    block = [None] * 22
    block[0] = ["DL", code]
    block[1] = []
    block[3] = label
    return block


def make_price_block(price: object = 250, token: str = "token") -> list:
    """Build a price block for booking options."""
    return [[None, price], token]


def make_booking_option(
    *,
    price: object | None = 250,
    brand_code: str | None = "DELTA MAIN CLASSIC",
    brand_label: str | None = "Delta Main Classic",
    seller_code: str | None = None,
    seller_name: str | None = None,
    logo_code: str | None = None,
    is_airline_direct: bool = False,
    booking_url_token: str | None = None,
) -> list:
    """Build a raw booking option list.

    Pass *seller_code* / *seller_name* to populate opt[1] (seller identity).
    Pass *is_airline_direct* to set the opt[1][0][3] boolean.
    Pass *booking_url_token* to populate opt[5] (redirect URL).
    """
    option = [None] * 25
    option[19] = json.dumps(["", [""]])
    if price is not None:
        option[7] = make_price_block(price)
    if brand_code is not None and brand_label is not None:
        option[21] = make_brand_block(brand_code, brand_label)
    if seller_code is not None or seller_name is not None:
        option[1] = [[seller_code or "", seller_name or "", logo_code, is_airline_direct]]
    if booking_url_token is not None:
        option[5] = [
            "www.example.com/...",
            None,
            ["https://www.google.com/travel/clk/f", [["u", booking_url_token], ["v", "1"]]],
        ]
    return option


# ---------------------------------------------------------------------------
# Decoded dataclass builders (used by test_flight_number)
# ---------------------------------------------------------------------------


def make_itinerary(*segments: Segment) -> Itinerary:
    """Build an Itinerary with the given segments (defaults for everything else)."""
    return Itinerary(segments=list(segments))


def make_simple_itinerary(
    *,
    origin: str = "JFK",
    destination: str = "LAX",
    date: str = "2026-06-15",
    airline: str = "DL",
    flight_number: str = "2300",
    price: int = 299,
    booking_token: str = "tok",
) -> Itinerary:
    """Build a complete single-flight Itinerary from simple parameters."""
    year, month, day = [int(p) for p in date.split("-")]
    segment = Segment(
        airline=airline,
        airline_name=airline,
        flight_number=flight_number,
        departure_airport_code=origin,
        arrival_airport_code=destination,
        departure_date=(year, month, day),
        arrival_date=(year, month, day),
        departure_time=(8, 0),
        arrival_time=(11, 15),
        travel_time=195,
    )
    return Itinerary(
        airline_code=airline,
        airline_names=[airline],
        segments=[segment],
        travel_time=195,
        departure_airport_code=origin,
        arrival_airport_code=destination,
        departure_date=(year, month, day),
        arrival_date=(year, month, day),
        departure_time=(8, 0),
        arrival_time=(11, 15),
        direct_price=price,
        booking_token=booking_token,
    )


def make_raw_result(*itineraries: Itinerary) -> RawSearchResult:
    """Build a RawSearchResult with itineraries in the 'best' bucket."""
    return RawSearchResult(_raw=[], best=list(itineraries), other=[])


def make_search_result(best=None, other=None) -> RawSearchResult:
    """Build a RawSearchResult with defaults."""
    return RawSearchResult(_raw=[], best=best or [], other=other or [])


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FakeHTTPResponse:
    """Minimal HTTP response for mocking primp."""

    def __init__(self, status_code: int = 200, text: str = ""):
        self.status_code = status_code
        self.text = text

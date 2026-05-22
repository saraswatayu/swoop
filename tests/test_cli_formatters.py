"""Direct formatter tests for user-visible search and pricing output."""

from __future__ import annotations

import json

from swoop import PriceResult, ResolvedLeg, SearchResult, TripLeg, TripOption
from swoop.cli import formatters
from swoop.decoder import BookingOption, Segment, Itinerary, Layover


def _make_itinerary(*, airline: str = "DL", number: str = "2300", route=None, layover_minutes=None) -> Itinerary:
    route = route or [("JFK", "LAX")]
    segments = []
    layovers = []
    for index, (origin, destination) in enumerate(route):
        segments.append(
            Segment(
                airline=airline,
                airline_name=airline,
                flight_number=str(int(number) + index),
                departure_airport_code=origin,
                arrival_airport_code=destination,
                departure_date=(2026, 4, 15),
                arrival_date=(2026, 4, 15),
                departure_time=(8 + (index * 3), 0),
                arrival_time=(10 + (index * 3), 15),
                travel_time=135,
            )
        )
    if layover_minutes is not None:
        layovers.append(
            Layover(
                minutes=layover_minutes,
                departure_airport_code=route[0][1],
                arrival_airport_code=route[0][1],
                is_overnight=False,
            )
        )
    return Itinerary(
        airline_code=airline,
        airline_names=[airline],
        segments=segments,
        layovers=layovers,
        travel_time=sum(seg.travel_time for seg in segments) + sum(lay.minutes for lay in layovers),
        departure_airport_code=route[0][0],
        arrival_airport_code=route[-1][1],
        departure_date=(2026, 4, 15),
        arrival_date=(2026, 4, 15),
        departure_time=segments[0].departure_time,
        arrival_time=segments[-1].arrival_time,
        direct_price=249,
        booking_token="token-1",
        stop_count=len(route) - 1,
    )


class TestFormatterHelpers:
    def test_flight_summary_variants(self):
        assert formatters._flight_summary(Itinerary()) == ""
        assert formatters._flight_summary(_make_itinerary(route=[("JFK", "LAX")])) == "DL 2300"
        assert formatters._flight_summary(
            _make_itinerary(route=[("JFK", "ORD"), ("ORD", "LAX")], layover_minutes=90)
        ) == "DL 2300 / 2301"

        mixed = _make_itinerary(route=[("JFK", "ORD"), ("ORD", "LAX")], layover_minutes=90)
        mixed.segments[1].airline = "UA"
        mixed.segments[1].flight_number = "401"
        assert formatters._flight_summary(mixed) == "DL 2300 / UA 401"

        assert formatters._flight_summary(
            _make_itinerary(route=[("JFK", "ORD"), ("ORD", "DEN"), ("DEN", "LAX")], layover_minutes=90)
        ) == "DL 2300 +2"

    def test_clock_and_date_tuple_helpers_reject_invalid_shapes(self):
        assert formatters._format_clock("08:30") is None
        assert formatters._format_clock([8]) == "08:00"  # hour-only defaults minute to 0
        assert formatters._format_clock([8, None]) == "08:00"  # None minute defaults to 0
        assert formatters._format_date_tuple("2026-04-15") is None
        assert formatters._format_date_tuple([2026, 4]) is None
        assert formatters._format_date_tuple([2026, 0, 15]) is None


class TestSearchFormatters:
    def test_format_search_table_handles_empty_result(self, capsys):
        formatters.format_search_table(
            SearchResult(results=[]),
            origin="JFK",
            destination="LAX",
            date="2026-04-15",
        )

        out = capsys.readouterr().out
        assert "No flights found" in out

    def test_format_search_table_renders_multi_leg_header_and_truncation(self, capsys):
        outbound = _make_itinerary(route=[("JFK", "LAX")])
        onward = _make_itinerary(route=[("LAX", "SFO")], number="1145")
        result = SearchResult(
            results=[
                TripOption(
                    selector="selector-1",
                    price=349,
                    legs=[
                        TripLeg(origin="JFK", destination="LAX", date="2026-04-15", itinerary=outbound),
                        TripLeg(origin="LAX", destination="SFO", date="2026-04-18", itinerary=onward),
                    ],
                )
            ],
            is_complete=False,
        )

        formatters.format_search_table(
            result,
            origin="JFK",
            destination="SFO",
            date="2026-04-15",
            legs=[("JFK", "LAX", "2026-04-15"), ("LAX", "SFO", "2026-04-18")],
            price_commands=["swoop price --selector 'selector-1'"],
        )

        out = capsys.readouterr().out
        assert "JFK -> LAX" in out
        assert "LAX -> SFO" in out
        assert "Results truncated" in out
        assert "swoop price --selector 'selector-1'" in out


class TestPriceFormatters:
    def test_format_price_table_renders_resolved_legs_and_booking_options(self, capsys):
        detailed = _make_itinerary(route=[("JFK", "ORD"), ("ORD", "LAX")], layover_minutes=95)
        result = PriceResult(
            price=684,
            fare_brand="Main Cabin",
            is_basic_economy=True,
            booking_options=[
                BookingOption(price=249, brand_label="Blue Basic", brand_code="BASIC", is_basic=True),
                BookingOption(price=289, brand_label="Blue Plus", brand_code="PLUS", is_basic=False),
            ],
            resolved_legs=[
                ResolvedLeg(
                    flight_summary="DL 2300",
                    origin="JFK",
                    destination="LAX",
                    date="2026-04-15",
                    itinerary=detailed,
                    selection="explicit",
                ),
                ResolvedLeg(
                    flight_summary="DL 2301",
                    origin="LAX",
                    destination="JFK",
                    date="2026-04-20",
                    itinerary=None,
                    selection="auto",
                ),
            ],
        )

        formatters.format_price_table(
            result,
            query_legs=[
                {"origin": "JFK", "destination": "LAX", "date": "2026-04-15"},
                {"origin": "LAX", "destination": "JFK", "date": "2026-04-20"},
            ],
        )

        out = capsys.readouterr().out
        assert "Outbound" in out
        assert "Return" in out
        assert "Layover" in out
        assert "Basic Economy" in out
        assert "Blue Basic" in out
        assert "Blue Plus" in out

    def test_format_price_json_includes_resolved_legs_and_itinerary(self, capsys):
        itinerary = _make_itinerary(route=[("JFK", "LAX")])
        result = PriceResult(
            price=342,
            fare_brand="Main Cabin",
            booking_options=[
                BookingOption(
                    price=342,
                    brand_label="Main Cabin",
                    brand_code="MAIN",
                    is_basic=False,
                    fare_family="standard",
                    rebookability_signal="standard_rebookable",
                    seller_name="Delta Air Lines",
                    seller_code="DL",
                    booking_url="https://www.google.com/travel/clk/f?u=tok&v=1",
                    logo_url="https://www.gstatic.com/flights/partner_logos/70px/DL.png",
                    is_airline_direct=True,
                )
            ],
            itinerary=itinerary,
            resolved_legs=[
                ResolvedLeg(
                    flight_summary="DL 2300",
                    origin="JFK",
                    destination="LAX",
                    date="2026-04-15",
                    itinerary=itinerary,
                    selection="explicit",
                )
            ],
        )

        formatters.format_price_json(
            result,
            query_legs=[{"origin": "JFK", "destination": "LAX", "date": "2026-04-15"}],
        )

        payload = json.loads(capsys.readouterr().out)
        assert payload["price"] == 342
        assert payload["itinerary"]["flight_summary"] == "DL 2300"
        assert payload["resolved_legs"][0]["selection"] == "explicit"
        assert payload["resolved_legs"][0]["itinerary"]["departure_airport_code"] == "JFK"
        assert payload["booking_options"] == [
            {
                "brand_label": "Main Cabin",
                "brand_code": "MAIN",
                "price": 342,
                "is_basic": False,
                "fare_family": "standard",
                "rebookability_signal": "standard_rebookable",
                "seller_name": "Delta Air Lines",
                "seller_code": "DL",
                "booking_url": "https://www.google.com/travel/clk/f?u=tok&v=1",
                "logo_url": "https://www.gstatic.com/flights/partner_logos/70px/DL.png",
                "is_airline_direct": True,
            }
        ]

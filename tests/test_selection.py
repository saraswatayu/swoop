"""Focused tests for staged selection and exact-trip pricing."""

from __future__ import annotations

import base64
import json

import pytest

import swoop._selection as selection
from swoop.decoder import BookingOption, Flight, Itinerary, RawSearchResult


def _make_itinerary(
    *,
    origin: str,
    destination: str,
    date: str,
    airline: str,
    flight_number: str,
    price: int,
    booking_token: str,
) -> Itinerary:
    year, month, day = [int(part) for part in date.split("-")]
    flight = Flight(
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
        flights=[flight],
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


def _raw_result(*itineraries: Itinerary) -> RawSearchResult:
    return RawSearchResult(_raw=[], best=list(itineraries), other=[])


def _booking_option(
    price: int,
    brand_label: str,
    cabin_bucket: str,
    *,
    is_basic: bool = False,
) -> BookingOption:
    return BookingOption(
        price=price,
        brand_label=brand_label,
        brand_code=brand_label.upper(),
        is_basic=is_basic,
        _cabin_bucket=cabin_bucket,
    )


class TestSelectorEncoding:
    def test_encode_decode_round_trip(self):
        request_legs = [
            {
                "origin": "JFK",
                "destination": "LAX",
                "date": "2026-04-15",
                "max_stops": 0,
                "airlines": ["DL"],
                "selected_legs": [["should-not-survive"]],
            },
            {
                "origin": "LAX",
                "destination": "SFO",
                "date": "2026-04-18",
                "earliest_departure": 9,
            },
        ]
        outbound = _make_itinerary(
            origin="JFK",
            destination="LAX",
            date="2026-04-15",
            airline="DL",
            flight_number="2300",
            price=249,
            booking_token="token-out",
        )
        onward = _make_itinerary(
            origin="LAX",
            destination="SFO",
            date="2026-04-18",
            airline="DL",
            flight_number="1145",
            price=329,
            booking_token="token-on",
        )

        selector_value = selection.encode_trip_selector(
            request_legs=request_legs,
            itineraries=[outbound, onward],
            cabin="business",
            adults=2,
            include_basic_economy=False,
        )
        payload = selection.decode_trip_selector(selector_value)

        assert payload["query_legs"] == [
            {
                "origin": "JFK",
                "destination": "LAX",
                "date": "2026-04-15",
                "max_stops": 0,
                "airlines": ["DL"],
            },
            {
                "origin": "LAX",
                "destination": "SFO",
                "date": "2026-04-18",
                "earliest_departure": 9,
            },
        ]
        assert payload["selected_legs"] == [
            selection._build_selected_legs(outbound),
            selection._build_selected_legs(onward),
        ]
        assert payload["cabin"] == "business"
        assert payload["adults"] == 2
        assert payload["include_basic_economy"] is False
        assert payload["booking_token_hint"] == "token-on"

    def test_decode_rejects_invalid_format(self):
        with pytest.raises(ValueError, match="invalid selector format"):
            selection.decode_trip_selector("not-a-selector")

    def test_decode_rejects_invalid_payload(self):
        with pytest.raises(ValueError, match="invalid selector payload"):
            selection.decode_trip_selector(f"{selection.SELECTOR_PREFIX}%%%")

    def test_decode_rejects_unsupported_version(self):
        encoded = base64.urlsafe_b64encode(json.dumps({"v": 99}).encode()).decode().rstrip("=")
        with pytest.raises(ValueError, match="unsupported selector version"):
            selection.decode_trip_selector(f"{selection.SELECTOR_PREFIX}{encoded}")


class TestStagedTripSearch:
    def test_search_trip_options_stages_selected_prefixes(self, monkeypatch):
        request_legs = [
            {"origin": "JFK", "destination": "LAX", "date": "2026-04-15"},
            {"origin": "LAX", "destination": "SFO", "date": "2026-04-18"},
        ]
        outbound = _make_itinerary(
            origin="JFK",
            destination="LAX",
            date="2026-04-15",
            airline="DL",
            flight_number="2300",
            price=249,
            booking_token="token-out",
        )
        onward = _make_itinerary(
            origin="LAX",
            destination="SFO",
            date="2026-04-18",
            airline="DL",
            flight_number="1145",
            price=329,
            booking_token="token-on",
        )
        calls: list[list[dict[str, object]]] = []

        def fake_search_from_legs(legs, **_kwargs):
            calls.append(legs)
            if len(calls) == 1:
                return _raw_result(outbound)
            return _raw_result(onward)

        monkeypatch.setattr(selection, "_search_from_legs", fake_search_from_legs)

        result = selection.search_trip_options(request_legs, cabin="economy")

        assert len(calls) == 2
        assert calls[1][0]["selected_legs"] == selection._build_selected_legs(outbound)
        assert "selected_legs" not in calls[1][1]
        assert len(result.results) == 1
        assert len(result.results[0].legs) == 2
        assert result.results[0].legs[0].itinerary is not None
        assert result.results[0].legs[0].itinerary.flights[0].flight_number == "2300"
        assert result.results[0].legs[0].itinerary.price is None
        assert result.results[0].legs[1].itinerary is not None
        assert result.results[0].legs[1].itinerary.flights[0].flight_number == "1145"
        assert result.results[0].legs[1].itinerary.price is None

    def test_search_trip_options_marks_incomplete_when_beam_width_truncates(self, monkeypatch):
        request_legs = [
            {"origin": "JFK", "destination": "LAX", "date": "2026-04-15"},
            {"origin": "LAX", "destination": "SFO", "date": "2026-04-18"},
        ]
        outbound_a = _make_itinerary(
            origin="JFK",
            destination="LAX",
            date="2026-04-15",
            airline="DL",
            flight_number="2300",
            price=249,
            booking_token="token-a",
        )
        outbound_b = _make_itinerary(
            origin="JFK",
            destination="LAX",
            date="2026-04-15",
            airline="UA",
            flight_number="1400",
            price=259,
            booking_token="token-b",
        )
        onward = _make_itinerary(
            origin="LAX",
            destination="SFO",
            date="2026-04-18",
            airline="DL",
            flight_number="1145",
            price=329,
            booking_token="token-on",
        )

        monkeypatch.setattr(selection, "BEAM_WIDTH", 1)
        monkeypatch.setattr(selection, "TARGET_RESULTS", 1)
        monkeypatch.setattr(
            selection,
            "_search_from_legs",
            lambda legs, **_kwargs: _raw_result(outbound_a, outbound_b)
            if legs[0].get("selected_legs") is None
            else _raw_result(onward),
        )

        result = selection.search_trip_options(request_legs, cabin="economy")

        assert result.is_complete is False
        assert len(result.results) == 1
        assert len(result.results[0].legs) == 2

    def test_search_trip_options_marks_incomplete_when_time_budget_is_hit(self, monkeypatch):
        request_legs = [
            {"origin": "JFK", "destination": "LAX", "date": "2026-04-15"},
            {"origin": "LAX", "destination": "SFO", "date": "2026-04-18"},
        ]
        outbound_a = _make_itinerary(
            origin="JFK",
            destination="LAX",
            date="2026-04-15",
            airline="DL",
            flight_number="2300",
            price=249,
            booking_token="token-a",
        )
        outbound_b = _make_itinerary(
            origin="JFK",
            destination="LAX",
            date="2026-04-15",
            airline="UA",
            flight_number="1400",
            price=259,
            booking_token="token-b",
        )
        onward = _make_itinerary(
            origin="LAX",
            destination="SFO",
            date="2026-04-18",
            airline="DL",
            flight_number="1145",
            price=329,
            booking_token="token-on",
        )
        ticks = iter([0.0, 0.0, 2.0])

        monkeypatch.setattr(selection, "TIME_BUDGET_SECONDS", 1)
        monkeypatch.setattr(selection.time, "monotonic", lambda: next(ticks))
        monkeypatch.setattr(
            selection,
            "_search_from_legs",
            lambda legs, **_kwargs: _raw_result(outbound_a, outbound_b)
            if legs[0].get("selected_legs") is None
            else _raw_result(onward),
        )

        result = selection.search_trip_options(request_legs, cabin="economy")

        assert result.is_complete is False
        assert len(result.results) == 1
        assert len(result.results[0].legs) == 2


class TestSelectorReplay:
    def test_resolve_trip_selector_raises_when_itinerary_disappears(self, monkeypatch):
        request_legs = [
            {"origin": "JFK", "destination": "LAX", "date": "2026-04-15"},
            {"origin": "LAX", "destination": "SFO", "date": "2026-04-18"},
        ]
        outbound = _make_itinerary(
            origin="JFK",
            destination="LAX",
            date="2026-04-15",
            airline="DL",
            flight_number="2300",
            price=249,
            booking_token="token-out",
        )
        expected_onward = _make_itinerary(
            origin="LAX",
            destination="SFO",
            date="2026-04-18",
            airline="DL",
            flight_number="1145",
            price=329,
            booking_token="token-on",
        )
        missing_onward = _make_itinerary(
            origin="LAX",
            destination="SFO",
            date="2026-04-18",
            airline="UA",
            flight_number="800",
            price=339,
            booking_token="token-miss",
        )
        selector_value = selection.encode_trip_selector(
            request_legs=request_legs,
            itineraries=[outbound, expected_onward],
            cabin="economy",
            adults=1,
            include_basic_economy=False,
        )

        def fake_search_from_legs(legs, **_kwargs):
            if legs[0].get("selected_legs") is None:
                return _raw_result(outbound)
            return _raw_result(missing_onward)

        monkeypatch.setattr(selection, "_search_from_legs", fake_search_from_legs)

        with pytest.raises(ValueError, match="selector itinerary no longer available"):
            selection.resolve_trip_selector(selector_value)

    def test_price_trip_selector_returns_none_for_invalid_selector(self):
        assert selection.price_trip_selector("bad-selector") is None


class TestSelectedTripResolution:
    def test_resolve_selected_trip_uses_auto_selection_when_flight_is_omitted(self, monkeypatch):
        request_legs = [{"origin": "JFK", "destination": "LAX", "date": "2026-04-15"}]
        outbound = _make_itinerary(
            origin="JFK",
            destination="LAX",
            date="2026-04-15",
            airline="DL",
            flight_number="2300",
            price=249,
            booking_token="token-out",
        )
        monkeypatch.setattr(selection, "_search_from_legs", lambda *_args, **_kwargs: _raw_result(outbound))

        resolved, selections, rpc_calls = selection.resolve_selected_trip(
            request_legs,
            [None],
            cabin="economy",
        )

        assert resolved == [outbound]
        assert selections == ["auto"]
        assert rpc_calls == 1

    def test_resolve_selected_trip_returns_empty_when_explicit_flight_is_missing(self, monkeypatch):
        request_legs = [{"origin": "JFK", "destination": "LAX", "date": "2026-04-15"}]
        outbound = _make_itinerary(
            origin="JFK",
            destination="LAX",
            date="2026-04-15",
            airline="DL",
            flight_number="2300",
            price=249,
            booking_token="token-out",
        )
        monkeypatch.setattr(selection, "_search_from_legs", lambda *_args, **_kwargs: _raw_result(outbound))

        resolved, selections, rpc_calls = selection.resolve_selected_trip(
            request_legs,
            ["UA999"],
            cabin="economy",
        )

        assert resolved == []
        assert selections == []
        assert rpc_calls == 1


class TestExactPricing:
    @pytest.mark.parametrize(
        ("cabin", "options", "expected_price", "expected_brand"),
        [
            (
                "business",
                [
                    _booking_option(319, "Main Cabin", "economy"),
                    _booking_option(689, "Delta One", "business"),
                ],
                689,
                "Delta One",
            ),
            (
                "premium-economy",
                [
                    _booking_option(319, "Main Cabin", "economy"),
                    _booking_option(459, "Premium Select", "premium-economy"),
                ],
                459,
                "Premium Select",
            ),
            (
                "first",
                [
                    _booking_option(689, "Delta One", "business"),
                    _booking_option(1299, "First", "first"),
                ],
                1299,
                "First",
            ),
        ],
    )
    def test_price_selected_trip_respects_requested_cabin(
        self,
        monkeypatch,
        cabin,
        options,
        expected_price,
        expected_brand,
    ):
        request_legs = [
            {"origin": "JFK", "destination": "LAX", "date": "2026-04-15"},
            {"origin": "LAX", "destination": "JFK", "date": "2026-04-20"},
        ]
        itineraries = [
            _make_itinerary(
                origin="JFK",
                destination="LAX",
                date="2026-04-15",
                airline="DL",
                flight_number="2300",
                price=449,
                booking_token="token-out",
            ),
            _make_itinerary(
                origin="LAX",
                destination="JFK",
                date="2026-04-20",
                airline="DL",
                flight_number="2301",
                price=799,
                booking_token="token-return",
            ),
        ]
        monkeypatch.setattr(selection, "fetch_trip_booking_options", lambda *_args, **_kwargs: options)

        result = selection.price_selected_trip(
            request_legs,
            itineraries,
            cabin=cabin,
            include_basic_economy=True,
        )

        assert result is not None
        assert result.price == expected_price
        assert result.fare_brand == expected_brand
        assert result.rpc_calls == 1

    @pytest.mark.parametrize(
        ("include_basic_economy", "expected_price", "expected_basic"),
        [
            (False, 249, False),
            (True, 199, True),
        ],
    )
    def test_price_selected_trip_only_uses_include_basic_for_economy(
        self,
        monkeypatch,
        include_basic_economy,
        expected_price,
        expected_basic,
    ):
        request_legs = [
            {"origin": "JFK", "destination": "LAX", "date": "2026-04-15"},
            {"origin": "LAX", "destination": "JFK", "date": "2026-04-20"},
        ]
        itineraries = [
            _make_itinerary(
                origin="JFK",
                destination="LAX",
                date="2026-04-15",
                airline="DL",
                flight_number="2300",
                price=449,
                booking_token="token-out",
            ),
            _make_itinerary(
                origin="LAX",
                destination="JFK",
                date="2026-04-20",
                airline="DL",
                flight_number="2301",
                price=299,
                booking_token="token-return",
            ),
        ]
        options = [
            _booking_option(199, "Delta Main Basic", "economy", is_basic=True),
            _booking_option(249, "Delta Main Classic", "economy"),
        ]
        monkeypatch.setattr(selection, "fetch_trip_booking_options", lambda *_args, **_kwargs: options)

        result = selection.price_selected_trip(
            request_legs,
            itineraries,
            cabin="economy",
            include_basic_economy=include_basic_economy,
        )

        assert result is not None
        assert result.price == expected_price
        assert result.is_basic_economy is expected_basic

    def test_price_selected_trip_falls_back_when_no_matching_cabin_option_exists(self, monkeypatch):
        request_legs = [
            {"origin": "JFK", "destination": "LAX", "date": "2026-04-15"},
            {"origin": "LAX", "destination": "JFK", "date": "2026-04-20"},
        ]
        itineraries = [
            _make_itinerary(
                origin="JFK",
                destination="LAX",
                date="2026-04-15",
                airline="DL",
                flight_number="2300",
                price=449,
                booking_token="token-out",
            ),
            _make_itinerary(
                origin="LAX",
                destination="JFK",
                date="2026-04-20",
                airline="DL",
                flight_number="2301",
                price=1199,
                booking_token="token-return",
            ),
        ]
        options = [_booking_option(319, "Main Cabin", "economy")]
        monkeypatch.setattr(selection, "fetch_trip_booking_options", lambda *_args, **_kwargs: options)

        result = selection.price_selected_trip(
            request_legs,
            itineraries,
            cabin="business",
            include_basic_economy=False,
        )

        assert result is not None
        # No business-cabin booking option exists, so the function falls back
        # to the last itinerary's direct_price rather than a booking option price.
        assert result.price == 1199
        assert result.fare_brand is None
        assert result.booking_options == options

    def test_eligible_returns_empty_for_unknown_on_business(self):
        options = [_booking_option(500, "Mystery Brand", "unknown")]
        result = selection._eligible_booking_options(options, True, cabin="business")
        assert result == []

    def test_eligible_returns_empty_for_unknown_on_first(self):
        options = [_booking_option(500, "Mystery Brand", "unknown")]
        result = selection._eligible_booking_options(options, True, cabin="first")
        assert result == []

    def test_eligible_returns_empty_for_unknown_on_premium_economy(self):
        options = [_booking_option(500, "Mystery Brand", "unknown")]
        result = selection._eligible_booking_options(options, True, cabin="premium-economy")
        assert result == []

    def test_eligible_still_includes_unknown_for_economy(self):
        options = [_booking_option(500, "Mystery Brand", "unknown")]
        result = selection._eligible_booking_options(options, True, cabin="economy")
        assert len(result) == 1

    def test_price_selected_trip_business_falls_back_with_unknown_only(self, monkeypatch):
        """Only unknown-bucket booking options → result must use base_price."""
        request_legs = [
            {"origin": "JFK", "destination": "LAX", "date": "2026-04-15"},
            {"origin": "LAX", "destination": "JFK", "date": "2026-04-20"},
        ]
        itineraries = [
            _make_itinerary(
                origin="JFK", destination="LAX", date="2026-04-15",
                airline="DL", flight_number="2300", price=449,
                booking_token="token-out",
            ),
            _make_itinerary(
                origin="LAX", destination="JFK", date="2026-04-20",
                airline="DL", flight_number="2301", price=1990,
                booking_token="token-return",
            ),
        ]
        options = [_booking_option(485, "Main", "unknown")]
        monkeypatch.setattr(selection, "fetch_trip_booking_options",
                            lambda *_a, **_kw: options)

        result = selection.price_selected_trip(
            request_legs, itineraries,
            cabin="business", include_basic_economy=False,
        )
        assert result is not None
        assert result.price == 1990
        assert result.fare_brand is None

    def test_price_selected_trip_does_not_treat_extra_legroom_economy_as_premium(self, monkeypatch):
        request_legs = [
            {"origin": "JFK", "destination": "LAX", "date": "2026-04-15"},
            {"origin": "LAX", "destination": "JFK", "date": "2026-04-20"},
        ]
        itineraries = [
            _make_itinerary(
                origin="JFK",
                destination="LAX",
                date="2026-04-15",
                airline="DL",
                flight_number="2300",
                price=449,
                booking_token="token-out",
            ),
            _make_itinerary(
                origin="LAX",
                destination="JFK",
                date="2026-04-20",
                airline="DL",
                flight_number="2301",
                price=1199,
                booking_token="token-return",
            ),
        ]
        options = [_booking_option(319, "Economy Plus", "economy")]
        monkeypatch.setattr(selection, "fetch_trip_booking_options", lambda *_args, **_kwargs: options)

        result = selection.price_selected_trip(
            request_legs,
            itineraries,
            cabin="premium-economy",
            include_basic_economy=False,
        )

        assert result is not None
        assert result.price == 1199
        assert result.fare_brand is None
        assert result.booking_options == options

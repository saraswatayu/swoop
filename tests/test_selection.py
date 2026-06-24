"""Focused tests for staged selection and exact-trip pricing."""

from __future__ import annotations

import base64
import json

import pytest

import swoop._selection as selection
from swoop.decoder import BookingOption, Itinerary, RawSearchResult
from swoop.exceptions import SwoopError, SwoopHTTPError, SwoopParseError, SwoopUpstreamError
from swoop.models import Passengers
from tests.factories import make_simple_itinerary as _make_itinerary, make_raw_result as _raw_result


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
            passengers=Passengers(adults=2),
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
        assert payload["passengers"].adults == 2
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
    def test_roundtrip_uses_fast_path(self, monkeypatch):
        """Roundtrip (2-leg) search uses single API call, no beam search."""
        request_legs = [
            {"origin": "JFK", "destination": "LAX", "date": "2026-04-15"},
            {"origin": "LAX", "destination": "JFK", "date": "2026-04-18"},
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
        calls: list[list[dict[str, object]]] = []

        def fake_search_from_legs(legs, **_kwargs):
            calls.append(legs)
            return _raw_result(outbound)

        monkeypatch.setattr(selection, "_search_from_legs", fake_search_from_legs)

        result = selection.search_trip_options(request_legs, cabin="economy")

        # Only 1 API call — no staged beam search
        assert len(calls) == 1
        assert len(result.results) == 1
        # Only outbound leg has itinerary detail
        assert len(result.results[0].legs) == 1
        assert result.results[0].legs[0].itinerary is not None
        assert result.results[0].legs[0].itinerary.segments[0].flight_number == "2300"
        assert result.is_complete is True

    def test_search_trip_options_stages_selected_prefixes(self, monkeypatch):
        """3+ leg search uses staged beam search."""
        request_legs = [
            {"origin": "JFK", "destination": "LAX", "date": "2026-04-15"},
            {"origin": "LAX", "destination": "SFO", "date": "2026-04-18"},
            {"origin": "SFO", "destination": "JFK", "date": "2026-04-20"},
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
        final = _make_itinerary(
            origin="SFO",
            destination="JFK",
            date="2026-04-20",
            airline="DL",
            flight_number="1200",
            price=399,
            booking_token="token-final",
        )
        calls: list[list[dict[str, object]]] = []

        def fake_search_from_legs(legs, **_kwargs):
            calls.append(legs)
            if len(calls) == 1:
                return _raw_result(outbound)
            if len(calls) == 2:
                return _raw_result(onward)
            return _raw_result(final)

        monkeypatch.setattr(selection, "_search_from_legs", fake_search_from_legs)

        result = selection.search_trip_options(request_legs, cabin="economy")

        assert len(calls) == 3
        assert calls[1][0]["selected_legs"] == selection._build_selected_legs(outbound)
        assert "selected_legs" not in calls[1][1]
        assert len(result.results) == 1
        assert len(result.results[0].legs) == 3

    def test_search_trip_options_marks_incomplete_when_beam_width_truncates(self, monkeypatch):
        request_legs = [
            {"origin": "JFK", "destination": "LAX", "date": "2026-04-15"},
            {"origin": "LAX", "destination": "SFO", "date": "2026-04-18"},
            {"origin": "SFO", "destination": "JFK", "date": "2026-04-20"},
        ]
        outbound_a = _make_itinerary(
            origin="JFK", destination="LAX", date="2026-04-15",
            airline="DL", flight_number="2300", price=249, booking_token="token-a",
        )
        outbound_b = _make_itinerary(
            origin="JFK", destination="LAX", date="2026-04-15",
            airline="UA", flight_number="1400", price=259, booking_token="token-b",
        )
        onward = _make_itinerary(
            origin="LAX", destination="SFO", date="2026-04-18",
            airline="DL", flight_number="1145", price=329, booking_token="token-on",
        )
        final = _make_itinerary(
            origin="SFO", destination="JFK", date="2026-04-20",
            airline="DL", flight_number="1200", price=399, booking_token="token-final",
        )

        monkeypatch.setattr(selection, "BEAM_WIDTH", 1)
        monkeypatch.setattr(selection, "TARGET_RESULTS", 1)

        def fake_search(legs, **_kwargs):
            if legs[0].get("selected_legs") is None:
                return _raw_result(outbound_a, outbound_b)
            if len(legs) >= 2 and legs[1].get("selected_legs") is not None:
                return _raw_result(final)
            return _raw_result(onward)

        monkeypatch.setattr(selection, "_search_from_legs", fake_search)

        result = selection.search_trip_options(request_legs, cabin="economy")

        assert result.is_complete is False
        assert len(result.results) == 1
        assert len(result.results[0].legs) == 3

    def test_search_trip_options_marks_incomplete_when_time_budget_is_hit(self, monkeypatch):
        request_legs = [
            {"origin": "JFK", "destination": "LAX", "date": "2026-04-15"},
            {"origin": "LAX", "destination": "SFO", "date": "2026-04-18"},
            {"origin": "SFO", "destination": "JFK", "date": "2026-04-20"},
        ]
        outbound_a = _make_itinerary(
            origin="JFK", destination="LAX", date="2026-04-15",
            airline="DL", flight_number="2300", price=249, booking_token="token-a",
        )
        outbound_b = _make_itinerary(
            origin="JFK", destination="LAX", date="2026-04-15",
            airline="UA", flight_number="1400", price=259, booking_token="token-b",
        )
        onward = _make_itinerary(
            origin="LAX", destination="SFO", date="2026-04-18",
            airline="DL", flight_number="1145", price=329, booking_token="token-on",
        )
        final = _make_itinerary(
            origin="SFO", destination="JFK", date="2026-04-20",
            airline="DL", flight_number="1200", price=399, booking_token="token-final",
        )
        call_count = 0

        def fake_monotonic():
            nonlocal call_count
            call_count += 1
            return 0.0 if call_count <= 2 else 2.0

        monkeypatch.setattr(selection, "TIME_BUDGET_SECONDS", 1)
        monkeypatch.setattr(selection.time, "monotonic", fake_monotonic)

        def fake_search(legs, **_kwargs):
            if legs[0].get("selected_legs") is None:
                return _raw_result(outbound_a, outbound_b)
            if len(legs) >= 2 and legs[1].get("selected_legs") is not None:
                return _raw_result(final)
            return _raw_result(onward)

        monkeypatch.setattr(selection, "_search_from_legs", fake_search)

        result = selection.search_trip_options(request_legs, cabin="economy")

        assert result.is_complete is False


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
            passengers=Passengers(adults=1),
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
    def test_price_selected_trip_fetches_booking_options_for_one_way(self, monkeypatch):
        request_legs = [
            {"origin": "JFK", "destination": "LAX", "date": "2026-04-15"},
        ]
        itinerary = _make_itinerary(
            origin="JFK",
            destination="LAX",
            date="2026-04-15",
            airline="DL",
            flight_number="2300",
            price=342,
            booking_token="token-out",
        )
        options = [
            BookingOption(
                price=319,
                brand_label="Main Cabin",
                brand_code="MAIN",
                seller_name="Delta Air Lines",
                booking_url="https://www.google.com/travel/clk/f?u=tok&v=1",
                _cabin_bucket="economy",
            )
        ]
        calls = []

        def fake_fetch_trip_booking(legs, itineraries, **kwargs):
            calls.append((legs, itineraries, kwargs))
            return options

        monkeypatch.setattr(selection, "fetch_trip_booking_options", fake_fetch_trip_booking)

        result = selection.price_selected_trip(
            request_legs,
            [itinerary],
            cabin="economy",
            include_basic_economy=False,
        )

        assert result is not None
        assert result.price == 319
        assert result.fare_brand == "Main Cabin"
        assert result.booking_options == options
        assert result.rpc_calls == 1
        assert calls[0][0] == request_legs
        assert calls[0][1] == [itinerary]

    def test_price_selected_trip_skips_booking_lookup_without_one_way_token(self, monkeypatch):
        request_legs = [
            {"origin": "JFK", "destination": "LAX", "date": "2026-04-15"},
        ]
        itinerary = _make_itinerary(
            origin="JFK",
            destination="LAX",
            date="2026-04-15",
            airline="DL",
            flight_number="2300",
            price=342,
            booking_token="",
        )
        calls = []

        def fake_fetch_trip_booking(*args, **kwargs):
            calls.append((args, kwargs))
            return [BookingOption(price=319, brand_label="Main Cabin", _cabin_bucket="economy")]

        monkeypatch.setattr(selection, "fetch_trip_booking_options", fake_fetch_trip_booking)

        result = selection.price_selected_trip(
            request_legs,
            [itinerary],
            cabin="economy",
            include_basic_economy=False,
        )

        assert result is not None
        assert result.price == 342
        assert result.booking_options == []
        assert result.rpc_calls == 0
        assert calls == []

    @pytest.mark.parametrize(
        "raised",
        [
            SwoopError("boom"),
            SwoopHTTPError(502),
            SwoopParseError("bad json"),
        ],
    )
    def test_price_selected_trip_falls_back_to_base_price_when_booking_rpc_raises(
        self, monkeypatch, raised
    ):
        """Non-upstream booking RPC failures fall back to base_price (an
        upstream outage propagates instead — see the next test)."""
        request_legs = [
            {"origin": "JFK", "destination": "LAX", "date": "2026-04-15"},
        ]
        itinerary = _make_itinerary(
            origin="JFK",
            destination="LAX",
            date="2026-04-15",
            airline="DL",
            flight_number="2300",
            price=342,
            booking_token="token-out",
        )

        def boom(*args, **kwargs):
            raise raised

        monkeypatch.setattr(selection, "fetch_trip_booking_options", boom)

        result = selection.price_selected_trip(
            request_legs,
            [itinerary],
            cabin="economy",
            include_basic_economy=False,
        )

        assert result is not None
        assert result.price == 342
        assert result.booking_options == []
        assert result.rpc_calls == 0

    def test_price_selected_trip_propagates_upstream_error(self, monkeypatch):
        """A Google outage during the bookable-price lookup surfaces as
        SwoopUpstreamError rather than silently degrading to the unverified
        search estimate (the price docstrings promise this)."""
        request_legs = [{"origin": "JFK", "destination": "LAX", "date": "2026-04-15"}]
        itinerary = _make_itinerary(
            origin="JFK", destination="LAX", date="2026-04-15",
            airline="DL", flight_number="2300", price=342, booking_token="token-out",
        )

        def boom(*args, **kwargs):
            raise SwoopUpstreamError(13)

        monkeypatch.setattr(selection, "fetch_trip_booking_options", boom)

        with pytest.raises(SwoopUpstreamError):
            selection.price_selected_trip(
                request_legs, [itinerary], cabin="economy", include_basic_economy=False
            )

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


class _StubLeg:
    """Minimal duck-typed leg for build_request_legs_from_selected."""

    def __init__(self, origin: str, destination: str, date: str) -> None:
        self.origin = origin
        self.destination = destination
        self.date = date


class TestBuildRequestLegsFromSelected:
    """Cover the carrier_filters branches in build_request_legs_from_selected.

    The function feeds airlines into the RPC request when the caller wants
    to pin the carrier per leg. None means "no airline pin," and silent
    misbehaviour here is hard to spot from higher-level tests (a missing
    filter just means Google returns more candidates than expected).
    """

    def test_none_filters_omits_airlines(self):
        legs = [_StubLeg("JFK", "LAX", "2026-06-15")]
        out = selection.build_request_legs_from_selected(legs)
        assert len(out) == 1
        assert out[0]["origin"] == "JFK"
        assert out[0]["destination"] == "LAX"
        assert out[0]["date"] == "2026-06-15"
        assert out[0]["airlines"] is None

    def test_empty_filters_list_treated_as_no_pin(self):
        """An empty carrier_filters list is falsy and must not raise
        IndexError on the first leg — it means "no pins anywhere."
        """
        legs = [_StubLeg("JFK", "LAX", "2026-06-15")]
        out = selection.build_request_legs_from_selected(legs, carrier_filters=[])
        assert out[0]["airlines"] is None

    def test_all_none_filters_omits_airlines_on_every_leg(self):
        legs = [
            _StubLeg("JFK", "LAX", "2026-06-15"),
            _StubLeg("LAX", "SFO", "2026-06-18"),
        ]
        out = selection.build_request_legs_from_selected(
            legs, carrier_filters=[None, None]
        )
        assert [leg["airlines"] for leg in out] == [None, None]

    def test_mixed_filters_apply_per_leg(self):
        legs = [
            _StubLeg("JFK", "LAX", "2026-06-15"),
            _StubLeg("LAX", "SFO", "2026-06-18"),
        ]
        out = selection.build_request_legs_from_selected(
            legs, carrier_filters=["DL", None]
        )
        assert out[0]["airlines"] == ["DL"]
        assert out[1]["airlines"] is None

    def test_length_mismatch_raises_indexerror(self):
        """Pinning the contract: a too-short filters list must raise
        IndexError rather than silently dropping pins. If we ever wrap
        this in a friendlier error, this test catches the API change.
        """
        legs = [
            _StubLeg("JFK", "LAX", "2026-06-15"),
            _StubLeg("LAX", "SFO", "2026-06-18"),
        ]
        with pytest.raises(IndexError):
            selection.build_request_legs_from_selected(legs, carrier_filters=["DL"])

"""Trip discovery must preserve exact bounds before selector-based pricing."""

from __future__ import annotations

from copy import deepcopy
import csv
import io
import json
from types import SimpleNamespace

import primp

import pytest
from click.testing import CliRunner

import swoop
import swoop._selection as selection
from swoop.models import Passengers, TransportConfig
from swoop.cli import main
from swoop.exceptions import SwoopUpstreamError
import swoop.rpc as rpc
from tests.factories import make_simple_itinerary, make_raw_result


def _stage_fixture(monkeypatch, *, return_origin="LHR", returns_per_outbound=2):
    legs = [
        {"origin": "JFK", "destination": "LHR", "date": "2030-04-15"},
        {"origin": return_origin, "destination": "JFK", "date": "2030-04-22"},
    ]
    outbounds = [
        make_simple_itinerary(
            origin="JFK", destination="LHR", date=legs[0]["date"],
            airline="BA", flight_number=str(101 + index), price=700 + index * 100,
            booking_token=f"outbound-{index}",
        )
        for index in range(2)
    ]
    returns = [
        [
            make_simple_itinerary(
                origin=return_origin, destination="JFK", date=legs[1]["date"],
                airline="BA", flight_number=str(201 + outbound_index * 10 + index),
                price=810 + outbound_index * 100 + index * 10,
                booking_token=f"return-{outbound_index}-{index}",
            )
            for index in range(returns_per_outbound)
        ]
        for outbound_index in range(2)
    ]
    calls = []

    def fake_search(request_legs, **kwargs):
        calls.append((deepcopy(request_legs), kwargs))
        selected = request_legs[0].get("selected_legs")
        if selected is None:
            return make_raw_result(*outbounds)
        index = next(
            index for index, outbound in enumerate(outbounds)
            if selection._build_selected_legs(outbound) == selected
        )
        return make_raw_result(*returns[index])

    monkeypatch.setattr(selection, "_search_from_legs", fake_search)
    return legs, outbounds, returns, calls


def test_fast_roundtrip_discovery_marks_unresolved_without_extra_rpc(monkeypatch):
    legs, _, _, calls = _stage_fixture(monkeypatch)
    result = selection.search_trip_options(legs)

    assert len(calls) == 1
    assert len(result.results) == 2
    assert all(not option.is_resolved for option in result.results)
    assert all(len(option.legs) == 1 for option in result.results)
    assert result.is_complete is True  # discovery coverage, not resolution


@pytest.mark.parametrize("return_origin", ["LHR", "CDG"])
def test_expanded_roundtrip_and_open_jaw_preserve_every_exact_combination(monkeypatch, return_origin):
    legs, outbounds, returns, calls = _stage_fixture(monkeypatch, return_origin=return_origin)
    passengers = Passengers(adults=2, children=1)
    result = selection.search_trip_options(
        legs, expand_legs=True, passengers=passengers, cabin="business",
        max_results=10, beam_width=10,
    )

    assert len(calls) == 3
    assert len(result.results) == 4
    assert result.is_complete is True
    assert all(option.is_resolved and len(option.legs) == 2 for option in result.results)
    combinations = set()
    for option in result.results:
        payload = selection.decode_trip_selector(option.selector)
        assert payload["query_legs"] == legs
        assert payload["passengers"] == passengers
        assert payload["cabin"] == "business"
        assert len(payload["selected_legs"]) == 2
        outbound_number = option.legs[0].itinerary.segments[0].flight_number
        return_number = option.legs[1].itinerary.segments[0].flight_number
        combinations.add((outbound_number, return_number))
        outbound_index = int(outbound_number) - 101
        expected = next(itin for itin in returns[outbound_index] if itin.segments[0].flight_number == return_number)
        assert option.price == expected.price  # final whole-trip price, not a sum
        assert payload["booking_token_hint"] == expected.booking_token
        assert option.legs[1].origin == return_origin
        assert option.legs[1].date == legs[1]["date"]

    assert combinations == {("101", "201"), ("101", "202"), ("102", "211"), ("102", "212")}
    assert calls[1][0][0]["selected_legs"] == selection._build_selected_legs(outbounds[0])
    assert calls[2][0][0]["selected_legs"] == selection._build_selected_legs(outbounds[1])
    assert "selected_legs" not in calls[1][0][1]


def test_expansion_allocates_beam_fairly_across_outbounds(monkeypatch):
    legs, _, _, calls = _stage_fixture(monkeypatch, returns_per_outbound=4)
    result = selection.search_trip_options(legs, expand_legs=True, beam_width=3, max_results=3)

    assert len(calls) == 3  # first outbound cannot fill beam and skip second
    assert len(result.results) == 3
    assert result.is_complete is False
    assert {option.legs[0].itinerary.segments[0].flight_number for option in result.results} == {"101", "102"}
    assert all(option.is_resolved for option in result.results)


def test_flight_number_filter_precedes_expansion_and_beam_truncation(monkeypatch):
    _, outbounds, _, calls = _stage_fixture(monkeypatch)
    result = swoop.search(
        "JFK", "LHR", "2030-04-15", return_date="2030-04-22",
        flight_number="BA102", expand_legs=True, beam_width=1, max_results=1,
    )

    assert len(calls) == 2
    assert len(result.results) == 1
    assert result.results[0].legs[0].itinerary.segments[0].flight_number == "102"
    assert calls[1][0][0]["selected_legs"] == selection._build_selected_legs(outbounds[1])


def test_incomplete_selector_rejected_before_rpc_and_never_chooses_return(monkeypatch):
    legs, outbounds, _, calls = _stage_fixture(monkeypatch)
    selector = selection.encode_trip_selector(
        request_legs=legs, itineraries=[outbounds[0]], cabin="economy", include_basic_economy=False,
    )

    with pytest.raises(ValueError, match="incomplete"):
        selection.resolve_trip_selector(selector)
    assert swoop.price_selector(selector) is None
    assert calls == []


def test_expanded_selector_replay_ignores_reordered_return_candidates(monkeypatch):
    legs, _, returns, _ = _stage_fixture(monkeypatch)
    option = selection.search_trip_options(legs, expand_legs=True).results[1]
    payload = selection.decode_trip_selector(option.selector)
    # Reordering Google's return list must not silently change the selection.
    for candidates in returns:
        candidates.reverse()
    _, replay_legs, resolved, calls = selection.resolve_trip_selector(option.selector)

    assert replay_legs == legs
    assert calls == 2
    assert [selection._build_selected_legs(itinerary) for itinerary in resolved] == payload["selected_legs"]
    assert resolved[-1].booking_token == payload["booking_token_hint"]


def test_expansion_budget_includes_first_search_and_emits_no_partial_trip(monkeypatch):
    legs, _, _, calls = _stage_fixture(monkeypatch)
    clock = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr(selection.time, "monotonic", lambda: next(clock))
    result = selection.search_trip_options(legs, expand_legs=True, time_budget=1)

    assert len(calls) == 1
    assert result.results == []
    assert result.is_complete is False


def test_expansion_caps_staged_timeout_to_remaining_budget(monkeypatch):
    legs, _, _, calls = _stage_fixture(monkeypatch)
    monkeypatch.setattr(selection.time, "monotonic", lambda: 0.0)
    original_transport = TransportConfig(timeout=90, retries=2, country="GB", proxy="http://proxy.invalid")
    selection.search_trip_options(
        legs, expand_legs=True, time_budget=5, transport=original_transport,
    )

    assert all(call[1]["transport"].timeout <= 5 for call in calls)
    assert all(call[1]["transport"].country == "GB" for call in calls)
    assert all(call[1]["transport"].proxy == "http://proxy.invalid" for call in calls)
    assert original_transport.timeout == 90


@pytest.mark.parametrize("fail_all", [False, True])
def test_expanded_stage_outage_never_becomes_complete_or_no_flights(monkeypatch, fail_all):
    legs, outbounds, returns, _ = _stage_fixture(monkeypatch)

    def fake_search(request_legs, **kwargs):
        selected = request_legs[0].get("selected_legs")
        if selected is None:
            return make_raw_result(*outbounds)
        if fail_all or selected == selection._build_selected_legs(outbounds[0]):
            raise SwoopUpstreamError(13)
        return make_raw_result(*returns[1])

    monkeypatch.setattr(selection, "_search_from_legs", fake_search)
    if fail_all:
        with pytest.raises(SwoopUpstreamError):
            selection.search_trip_options(legs, expand_legs=True)
    else:
        result = selection.search_trip_options(legs, expand_legs=True)
        assert result.is_complete is False
        assert len(result.results) == 2
        assert all(option.is_resolved for option in result.results)
        assert all(option.legs[0].itinerary.segments[0].flight_number == "102" for option in result.results)


def test_unknown_discovery_price_stays_missing(monkeypatch):
    legs, outbounds, _, _ = _stage_fixture(monkeypatch)
    outbounds[0].direct_price = None
    result = selection.search_trip_options(legs)
    assert result.results[0].price is None
    assert result.results[0].is_resolved is False


@pytest.mark.parametrize("expanded", [False, True])
def test_cli_json_discloses_trip_resolution(monkeypatch, expanded):
    _stage_fixture(monkeypatch)
    args = ["search", "JFK", "LHR", "2030-04-15", "-r", "2030-04-22", "-o", "json", "-q"]
    if expanded:
        args.append("--expand-legs")
    result = CliRunner().invoke(main, args)

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert all(option["is_resolved"] is expanded for option in data["results"])
    assert all(len(option["legs"]) == (2 if expanded else 1) for option in data["results"])


def test_cli_csv_discloses_unresolved_and_blocks_partial_fare_commands(monkeypatch):
    _stage_fixture(monkeypatch)
    route = ["search", "JFK", "LHR", "2030-04-15", "-r", "2030-04-22", "-q"]
    result = CliRunner().invoke(main, [*route, "-o", "csv"])
    assert result.exit_code == 0, result.output
    rows = list(csv.DictReader(io.StringIO(result.output)))
    assert rows and all(row["is_resolved"] == "False" for row in rows)

    partial = CliRunner().invoke(main, [*route, "--show-price-commands"])
    assert partial.exit_code == 2
    assert "--expand-legs" in partial.output

    exact = CliRunner().invoke(main, [*route, "--show-price-commands", "--expand-legs"])
    assert exact.exit_code == 0, exact.output
    assert "swoop price --selector" in exact.output


@pytest.mark.parametrize("fail_all", [False, True])
@pytest.mark.parametrize("phase", ["post", "body"])
def test_expansion_keeps_complete_choices_after_a_transport_timeout(monkeypatch, fail_all, phase):
    legs, outbounds, _, _ = _stage_fixture(monkeypatch)
    original_stage = selection._search_from_legs

    class LazyResponse:
        status_code = 200

        @property
        def text(self):
            raise primp.TimeoutError("lazy response body timeout")

    def timeout(*args, **kwargs):
        if phase == "post":
            raise primp.TimeoutError("proxy connect timeout")
        return LazyResponse()

    monkeypatch.setattr(rpc, "_get_client", lambda *args: SimpleNamespace(post=timeout))

    def fake_stage(request_legs, **kwargs):
        selected = request_legs[0].get("selected_legs")
        if selected is not None and (fail_all or selected == selection._build_selected_legs(outbounds[1])):
            return rpc._http_post(rpc.SHOPPING_RPC_URL, b"body", transport=TransportConfig(retries=0))
        return original_stage(request_legs, **kwargs)

    monkeypatch.setattr(selection, "_search_from_legs", fake_stage)
    if fail_all:
        with pytest.raises(swoop.SwoopTransportError, match="transport"):
            selection.search_trip_options(legs, expand_legs=True)
    else:
        result = selection.search_trip_options(legs, expand_legs=True)
        assert result.is_complete is False
        assert len(result.results) == 2
        assert all(option.is_resolved for option in result.results)
        assert all(option.legs[0].itinerary.segments[0].flight_number == "101" for option in result.results)

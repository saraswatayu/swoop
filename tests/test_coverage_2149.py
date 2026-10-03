"""Keep flights outside Google's shortened list selectable and priceable."""
import json

import pytest

import swoop
import swoop._selection as selection
from swoop.decoder import RawSearchResult, decode_result
from swoop.models import Passengers
from swoop.rpc import _build_filters_from_legs, _normalize_rpc_leg
from tests.factories import make_simple_itinerary


LEGS = [_normalize_rpc_leg("JFK", "LAX", "2026-11-15"),
        _normalize_rpc_leg("LAX", "JFK", "2026-11-22")]


def itinerary(number="117", *, returning=False):
    return make_simple_itinerary(
        origin="LAX" if returning else "JFK",
        destination="JFK" if returning else "LAX",
        date="2026-11-22" if returning else "2026-11-15",
        airline="AA", flight_number=number, price=500, booking_token="",
    )


def partial(*, show_all=True):
    return selection.encode_trip_selector(
        request_legs=LEGS, itineraries=[itinerary()], cabin="premium-economy",
        passengers=Passengers(adults=2, children=1), include_basic_economy=False,
        show_all_results=show_all,
    )


def test_wider_request_is_default_and_can_be_disabled():
    assert _build_filters_from_legs(LEGS)[3] == 1
    assert _build_filters_from_legs(LEGS, show_all_results=False)[3] == 0


def test_next_leg_keeps_every_return_and_original_context(monkeypatch):
    calls = []
    returns = [itinerary(str(100 + i), returning=True) for i in range(25)]
    def provider(legs, **kwargs):
        calls.append((legs, kwargs))
        return RawSearchResult(other=returns if legs[0].get("selected_legs") else [itinerary()])
    monkeypatch.setattr(selection, "_search_from_legs", provider)
    result = swoop.search_next_leg(partial())
    assert len(result.results) == 25
    assert result.is_complete
    assert result.rpc_calls == 2
    assert all(option.is_resolved and len(option.legs) == 2 for option in result.results)
    for legs, kwargs in calls:
        assert legs[1]["date"] == "2026-11-22"
        assert kwargs["show_all_results"] is True
        assert kwargs["cabin"] == "premium-economy"
        assert kwargs["passengers"] == Passengers(adults=2, children=1)
    assert calls[1][0][0]["selected_legs"] == selection._build_selected_legs(itinerary())
    assert all(option.legs[0].itinerary.segments[0].flight_number == "117" for option in result.results)


def test_selector_pricing_replays_wider_mode(monkeypatch):
    seen = []
    outbound, inbound = itinerary(), itinerary("118", returning=True)
    selector = selection.encode_trip_selector(request_legs=LEGS, itineraries=[outbound, inbound],
        cabin="economy", include_basic_economy=True, show_all_results=True)
    def provider(legs, **kwargs):
        seen.append(kwargs["show_all_results"])
        return RawSearchResult(other=[inbound if legs[0].get("selected_legs") else outbound])
    monkeypatch.setattr(selection, "_search_from_legs", provider)
    result = swoop.price_selector(selector)
    assert result is not None
    assert seen == [True, True]


@pytest.mark.parametrize("selector", ["broken", "swoop:sel:1:W10="])
def test_bad_continuation_does_no_provider_io(selector, monkeypatch):
    monkeypatch.setattr(selection, "_search_from_legs", lambda *a, **k: pytest.fail("provider called"))
    with pytest.raises(ValueError):
        swoop.search_next_leg(selector)


def test_completed_selector_cannot_select_another_bound(monkeypatch):
    selector = selection.encode_trip_selector(request_legs=LEGS, itineraries=[itinerary(), itinerary("118", returning=True)],
        cabin="economy", include_basic_economy=True)
    monkeypatch.setattr(selection, "_search_from_legs", lambda *a, **k: pytest.fail("provider called"))
    with pytest.raises(ValueError, match="unselected"):
        swoop.search_next_leg(selector)


def test_expansion_reports_both_caps_and_pending_outbounds(monkeypatch):
    def provider(legs, **kwargs):
        return RawSearchResult(other=[itinerary(str(i), returning=bool(legs[0].get("selected_legs"))) for i in range(20)])
    monkeypatch.setattr(selection, "_search_from_legs", provider)
    result = selection.search_trip_options(LEGS, expand_legs=True, beam_width=15, max_results=10)
    assert not result.is_complete
    assert set(result.truncation_reasons) == {"beam_limit", "result_limit"}
    assert result.unexpanded_prefixes == 5
    assert result.rpc_calls == 16


def test_limited_page_is_not_claimed_as_wider_coverage(monkeypatch):
    raw = RawSearchResult(other=[itinerary()], _result_scope="limited")
    monkeypatch.setattr(selection, "_search_from_legs", lambda *a, **k: raw)
    result = selection.search_trip_options(LEGS)
    assert result.result_scope == "limited"
    assert result.truncation_reasons == ["limited_transport"]
    assert not result.is_complete


def test_decoder_losses_are_reported(monkeypatch):
    raw = decode_result([None, None, None, [["malformed"]]])
    monkeypatch.setattr(selection, "_search_from_legs", lambda *a, **k: raw)
    result = selection.search_trip_options(LEGS)
    assert result.raw_result_count == 1
    assert result.decoded_result_count == 0
    assert result.truncation_reasons == ["parse_loss"]
    assert not result.is_complete


def test_old_selectors_preserve_their_original_reduced_mode():
    payload = selection.decode_trip_selector(partial())
    payload.pop("show_all_results")
    payload["passengers"] = {"adults": 2, "children": 1, "infants_in_seat": 0, "infants_on_lap": 0}
    old = selection.SELECTOR_PREFIX + selection._encode_payload(payload)
    assert selection.decode_trip_selector(old)["show_all_results"] is False


def test_next_cli_outputs_exact_bounds_and_coverage(monkeypatch):
    from click.testing import CliRunner
    from swoop.cli import main
    monkeypatch.setattr(selection, "_search_from_legs", lambda legs, **kwargs:
        RawSearchResult(other=[itinerary("118", returning=True) if legs[0].get("selected_legs") else itinerary()]))
    result = CliRunner().invoke(main, ["next", "--selector", partial()])
    assert result.exit_code == 0, result.output
    output = json.loads(result.output)
    assert output["query"]["legs"] == [{k: leg[k] for k in ("origin", "destination", "date")} for leg in LEGS]
    assert output["rpc_calls"] == 2
    assert output["results"][0]["is_resolved"]


def test_continuation_budget_is_shared_with_replay(monkeypatch):
    now, calls = [0.0], []
    monkeypatch.setattr(selection.time, "monotonic", lambda: now[0])
    def provider(legs, **kwargs):
        calls.append(kwargs["transport"].timeout)
        now[0] += 2
        return RawSearchResult(other=[itinerary("118", returning=True) if legs[0].get("selected_legs") else itinerary()])
    monkeypatch.setattr(selection, "_search_from_legs", provider)
    swoop.search_next_leg(partial(), time_budget=3)
    assert calls == [3, 1]


def test_three_bounds_continue_only_the_chosen_prefix(monkeypatch):
    legs = [LEGS[0], _normalize_rpc_leg("LAX", "SFO", "2026-11-18"),
            _normalize_rpc_leg("SFO", "JFK", "2026-11-22")]
    onward = make_simple_itinerary(origin="LAX", destination="SFO", date="2026-11-18", airline="AA", flight_number="119", booking_token="")
    final = make_simple_itinerary(origin="SFO", destination="JFK", date="2026-11-22", airline="AA", flight_number="120", booking_token="")
    def provider(query, **kwargs):
        return RawSearchResult(other=[final if query[1].get("selected_legs") else onward if query[0].get("selected_legs") else itinerary()])
    monkeypatch.setattr(selection, "_search_from_legs", provider)
    first = selection.search_trip_options(legs)
    assert first.rpc_calls == 1 and not first.results[0].is_resolved
    second = swoop.search_next_leg(first.results[0].selector)
    assert len(second.results[0].legs) == 2 and not second.results[0].is_resolved
    third = swoop.search_next_leg(second.results[0].selector)
    assert third.results[0].is_resolved and third.rpc_calls == 3


def test_recorded_wider_rows_decode_without_loss():
    from pathlib import Path
    payload = json.loads((Path(__file__).parent / "fixtures/coverage-2149-wider-rows.json").read_text())
    result = decode_result(payload)
    assert result._raw_result_count == 3
    assert len(result.best) + len(result.other) == 3
    assert any(segment.airline == "AA" and segment.flight_number == "117"
               for itinerary in [*result.best, *result.other] for segment in itinerary.segments)

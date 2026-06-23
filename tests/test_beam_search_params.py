"""Tests for configurable beam search parameters."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from click.testing import CliRunner

import swoop._selection as selection
from swoop.cli import main
from swoop.decoder import RawSearchResult
from tests.factories import make_simple_itinerary as _make_itinerary, make_raw_result as _raw_result


class TestSearchTripOptionsBeamParams:
    """search_trip_options() respects custom beam_width, time_budget, max_results."""

    def _setup_multi_leg(self, monkeypatch, *, num_outbound=3):
        """Set up a 3-leg search with controllable outbound candidates.

        Uses 3 legs so beam search is exercised (roundtrip 2-leg uses fast path).
        """
        request_legs = [
            {"origin": "JFK", "destination": "LAX", "date": "2026-04-15"},
            {"origin": "LAX", "destination": "SFO", "date": "2026-04-18"},
            {"origin": "SFO", "destination": "JFK", "date": "2026-04-20"},
        ]
        outbounds = [
            _make_itinerary(
                origin="JFK",
                destination="LAX",
                date="2026-04-15",
                airline="DL",
                flight_number=str(2300 + i),
                price=249 + i * 10,
                booking_token=f"token-out-{i}",
            )
            for i in range(num_outbound)
        ]
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

        def fake_search(legs, **_kwargs):
            if legs[0].get("selected_legs") is None:
                return _raw_result(*outbounds)
            if len(legs) >= 2 and legs[1].get("selected_legs") is not None:
                return _raw_result(final)
            return _raw_result(onward)

        monkeypatch.setattr(selection, "_search_from_legs", fake_search)
        return request_legs

    def test_custom_beam_width_limits_prefixes(self, monkeypatch):
        request_legs = self._setup_multi_leg(monkeypatch, num_outbound=5)

        result = selection.search_trip_options(
            request_legs, cabin="economy", beam_width=2,
        )

        # beam_width=2 limits to 2 prefixes, so at most 2 results
        assert len(result.results) <= 2
        assert result.is_complete is False

    def test_custom_max_results_limits_output(self, monkeypatch):
        request_legs = self._setup_multi_leg(monkeypatch, num_outbound=5)

        result = selection.search_trip_options(
            request_legs, cabin="economy", beam_width=5, max_results=2,
        )

        assert len(result.results) <= 2

    def test_custom_time_budget_triggers_early_stop(self, monkeypatch):
        request_legs = self._setup_multi_leg(monkeypatch, num_outbound=5)

        # Make time.monotonic advance past the budget on the second prefix.
        # With 3 legs, stages 1 and 2 each call monotonic per prefix.
        call_count = 0
        def fake_monotonic():
            nonlocal call_count
            call_count += 1
            # First few calls return 0, then jump past budget
            return 0.0 if call_count <= 3 else 100.0
        monkeypatch.setattr(selection.time, "monotonic", fake_monotonic)

        result = selection.search_trip_options(
            request_legs, cabin="economy", beam_width=5, time_budget=1,
        )

        assert result.is_complete is False

    def test_defaults_match_module_constants(self, monkeypatch):
        """When params are None, module constants are used."""
        request_legs = self._setup_multi_leg(monkeypatch, num_outbound=2)

        # With defaults (beam_width=15, max_results=10), 2 outbound fits fine
        result = selection.search_trip_options(request_legs, cabin="economy")

        assert len(result.results) == 2
        assert result.is_complete is True


class TestCLIBeamSearchFlags:
    """CLI flags --max-results, --beam-width, --time-budget are accepted."""

    @patch("swoop.cli.commands._run_search")
    def test_flags_passed_to_run_search(self, mock_search):
        from swoop import SearchResult
        mock_search.return_value = SearchResult(results=[])
        runner = CliRunner()
        result = runner.invoke(main, [
            "search", "JFK", "LAX", "2026-06-15",
            "--max-results", "5",
            "--beam-width", "3",
            "--time-budget", "30",
            "-q",
        ])
        assert result.exit_code in (0, 1)  # 1 = no results, but flags were accepted
        _, kwargs = mock_search.call_args
        assert kwargs["max_results"] == 5
        assert kwargs["beam_width"] == 3
        assert kwargs["time_budget"] == 30

    @patch("swoop.cli.commands._run_search_legs")
    def test_flags_passed_to_run_search_legs(self, mock_search_legs):
        from swoop import SearchResult
        mock_search_legs.return_value = SearchResult(results=[])
        runner = CliRunner()
        result = runner.invoke(main, [
            "search",
            "--leg", "JFK", "LAX", "2026-06-15",
            "--leg", "LAX", "SFO", "2026-06-18",
            "--max-results", "8",
            "--beam-width", "10",
            "--time-budget", "45",
            "-q",
        ])
        assert result.exit_code in (0, 1)
        _, kwargs = mock_search_legs.call_args
        assert kwargs["max_results"] == 8
        assert kwargs["beam_width"] == 10
        assert kwargs["time_budget"] == 45

    def test_help_shows_new_flags(self):
        runner = CliRunner()
        result = runner.invoke(main, ["search", "--help"])
        assert result.exit_code == 0
        assert "--max-results" in result.output
        assert "--beam-width" in result.output
        assert "--time-budget" in result.output


class TestBeamUpstreamErrorDegradesGracefully:
    """A transient upstream error on a later beam stage must not abort the whole
    multi-city search. The first pass returning None used to be absorbed as an
    empty stage; SwoopUpstreamError must degrade the same way (mark incomplete),
    not propagate out of search_trip_options."""

    def test_stage_upstream_error_marks_incomplete_not_raises(self, monkeypatch):
        from swoop.exceptions import SwoopUpstreamError
        from swoop.models import SearchResult
        from tests.factories import make_simple_itinerary as _make_itinerary, make_raw_result as _raw_result

        request_legs = [
            {"origin": "JFK", "destination": "LAX", "date": "2026-04-15"},
            {"origin": "LAX", "destination": "SFO", "date": "2026-04-18"},
            {"origin": "SFO", "destination": "JFK", "date": "2026-04-20"},
        ]
        outbounds = [
            _make_itinerary(
                origin="JFK", destination="LAX", date="2026-04-15",
                airline="DL", flight_number=str(2300 + i), price=249 + i * 10,
                booking_token=f"token-out-{i}",
            )
            for i in range(2)
        ]

        def fake_search(legs, **_kwargs):
            # First pass (no prefix selected) succeeds; any staged call rejects.
            if legs[0].get("selected_legs") is None:
                return _raw_result(*outbounds)
            raise SwoopUpstreamError(13)

        monkeypatch.setattr(selection, "_search_from_legs", fake_search)

        # Must not raise — the staged rejection is absorbed.
        result = selection.search_trip_options(request_legs, cabin="economy")
        assert isinstance(result, SearchResult)
        assert result.is_complete is False

    def test_first_pass_upstream_error_still_raises(self, monkeypatch):
        from swoop.exceptions import SwoopUpstreamError

        request_legs = [
            {"origin": "JFK", "destination": "LAX", "date": "2026-04-15"},
            {"origin": "LAX", "destination": "SFO", "date": "2026-04-18"},
            {"origin": "SFO", "destination": "JFK", "date": "2026-04-20"},
        ]

        def fake_search(legs, **_kwargs):
            raise SwoopUpstreamError(13)

        monkeypatch.setattr(selection, "_search_from_legs", fake_search)

        # A rejected first call means no results at all — surface it.
        with pytest.raises(SwoopUpstreamError):
            selection.search_trip_options(request_legs, cabin="economy")

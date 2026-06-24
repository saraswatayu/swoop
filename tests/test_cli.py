"""Tests for swoop CLI commands."""

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from swoop.cli import main
from swoop import PriceResult, SearchResult, TripLeg, TripOption
from swoop.cli.commands import search_cmd, price_cmd
from swoop.exceptions import SwoopUpstreamError
from swoop.cli.utils import format_time, format_duration, format_date_display, format_route, check_past_date, IATACodeType, DateType
from swoop.decoder import (
    BookingOption,
    CarbonEmissions,
    Segment,
    Itinerary,
    Layover,
    PriceRange,
)

from datetime import date as _date, timedelta as _timedelta

# Dates passed to the CLI below as the departure / return / second-leg value.
# Computed at import so they are ALWAYS in the future. A hardcoded date
# eventually goes stale: swoop then prints "Warning: <date> is in the past."
# to stderr, Click 8.3 folds stderr into result.output, and every test that
# parses the JSON/CSV/brief payload breaks on the warning line. The invariant
# that keeps real (stdout-only) output clean is pinned by
# TestPastDateWarningStreamSeparation.
_FUTURE = (_date.today() + _timedelta(days=30)).isoformat()
_RETURN = (_date.today() + _timedelta(days=37)).isoformat()
_LEG2 = (_date.today() + _timedelta(days=33)).isoformat()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_segment(**overrides) -> Segment:
    defaults = dict(
        airline="DL",
        airline_name="Delta Air Lines",
        flight_number="2300",
        departure_airport_code="JFK",
        arrival_airport_code="LAX",
        departure_time=(8, 30),
        arrival_time=(11, 45),
        departure_date=(2026, 6, 15),
        arrival_date=(2026, 6, 15),
        travel_time=315,
        aircraft="Boeing 737-900",
        legroom="32 inches",
    )
    defaults.update(overrides)
    return Segment(**defaults)


def _make_itinerary(**overrides) -> Itinerary:
    flight = _make_segment()
    defaults = dict(
        airline_code="DL",
        airline_names=["Delta Air Lines"],
        segments=[flight],
        layovers=[],
        travel_time=315,
        departure_airport_code="JFK",
        arrival_airport_code="LAX",
        departure_date=(2026, 6, 15),
        arrival_date=(2026, 6, 15),
        departure_time=(8, 30),
        arrival_time=(11, 45),
        direct_price=247,
        booking_token="token123",
        stop_count=0,
    )
    defaults.update(overrides)
    return Itinerary(**defaults)


def _make_connecting_itinerary() -> Itinerary:
    f1 = _make_segment(
        airline="UA", airline_name="United Airlines", flight_number="1234",
        departure_airport_code="JFK", arrival_airport_code="ORD",
        departure_time=(10, 15), arrival_time=(12, 20),
        travel_time=125,
    )
    f2 = _make_segment(
        airline="UA", airline_name="United Airlines", flight_number="5678",
        departure_airport_code="ORD", arrival_airport_code="LAX",
        departure_time=(14, 20), arrival_time=(15, 20),
        travel_time=180,
    )
    lay = Layover(
        minutes=120, departure_airport_code="ORD",
        departure_airport_name="O'Hare International Airport",
    )
    return Itinerary(
        airline_code="UA",
        airline_names=["United Airlines"],
        segments=[f1, f2],
        layovers=[lay],
        travel_time=485,
        departure_airport_code="JFK",
        arrival_airport_code="LAX",
        departure_date=(2026, 6, 15),
        arrival_date=(2026, 6, 15),
        departure_time=(10, 15),
        arrival_time=(15, 20),
        direct_price=183,
        booking_token="token456",
        stop_count=1,
    )


def _make_trip_option(itinerary: Itinerary, *, index: int, currency: str = "USD") -> TripOption:
    return TripOption(
        selector=f"selector-{index}",
        price=itinerary.price,
        currency=currency,
        legs=[
            TripLeg(
                origin=itinerary.departure_airport_code,
                destination=itinerary.arrival_airport_code,
                date="2026-06-15",
                itinerary=itinerary,
            )
        ],
    )


def _make_search_result(n: int = 3) -> SearchResult:
    options = [_make_trip_option(_make_itinerary(), index=1)]
    if n >= 2:
        options.append(_make_trip_option(_make_itinerary(
            airline_code="B6",
            airline_names=["JetBlue"],
            direct_price=219,
            departure_time=(9, 0),
            arrival_time=(12, 30),
            travel_time=330,
            segments=[_make_segment(
                airline="B6", airline_name="JetBlue", flight_number="524",
                departure_time=(9, 0), arrival_time=(12, 30), travel_time=330,
            )],
        ), index=2))
    if n >= 3:
        options.append(_make_trip_option(_make_connecting_itinerary(), index=3))
    return SearchResult(
        results=options,
        price_range=PriceRange(low=127, high=450),
        is_complete=True,
    )


def _make_booking_options() -> list[BookingOption]:
    return [
        BookingOption(price=219, brand_label="Blue Basic", brand_code="BASIC", is_basic=True, fare_family="basic"),
        BookingOption(price=249, brand_label="Blue", brand_code="STANDARD", is_basic=False, fare_family="standard"),
        BookingOption(price=289, brand_label="Blue Plus", brand_code="ENHANCED", is_basic=False, fare_family="enhanced"),
    ]


# ---------------------------------------------------------------------------
# Utils tests
# ---------------------------------------------------------------------------


class TestFormatTime:
    def test_morning(self):
        assert format_time(8, 30) == "8:30a"

    def test_afternoon(self):
        assert format_time(14, 0) == "2:00p"

    def test_midnight(self):
        assert format_time(0, 0) == "12:00a"

    def test_noon(self):
        assert format_time(12, 0) == "12:00p"

    def test_single_digit_minutes(self):
        assert format_time(9, 5) == "9:05a"


class TestFormatDuration:
    def test_hours_and_minutes(self):
        assert format_duration(315) == "5h 15m"

    def test_hours_only(self):
        assert format_duration(120) == "2h"

    def test_minutes_only(self):
        assert format_duration(45) == "45m"

    def test_zero(self):
        assert format_duration(0) == "0m"


class TestFormatDateDisplay:
    def test_valid_date(self):
        result = format_date_display("2026-06-15")
        assert "Jun" in result
        assert "2026" in result

    def test_invalid_date(self):
        assert format_date_display("bad") == "bad"


class TestFormatRoute:
    def test_direct(self):
        itin = _make_itinerary()
        assert format_route(itin) == "JFK -> LAX"

    def test_connecting(self):
        itin = _make_connecting_itinerary()
        assert format_route(itin) == "JFK -> ORD -> LAX"


class TestCheckPastDate:
    def test_future_date(self):
        assert check_past_date("2099-01-01") is None

    def test_past_date(self):
        result = check_past_date("2020-01-01")
        assert result is not None
        assert "past" in result.lower()


class TestIATACodeType:
    def test_uppercases(self):
        t = IATACodeType()
        assert t.convert("jfk", None, None) == "JFK"

    def test_rejects_invalid(self):
        t = IATACodeType()
        with pytest.raises(Exception):
            t.convert("XY", None, None)


class TestDateType:
    def test_valid(self):
        t = DateType()
        assert t.convert("2026-06-15", None, None) == "2026-06-15"

    def test_invalid(self):
        t = DateType()
        with pytest.raises(Exception):
            t.convert("2026-13-45", None, None)


# ---------------------------------------------------------------------------
# CLI group tests
# ---------------------------------------------------------------------------


class TestMainGroup:
    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "search" in result.output
        assert "price" in result.output
        assert "\n  book" not in result.output

    def test_no_subcommand_shows_help(self):
        runner = CliRunner()
        result = runner.invoke(main, [])
        assert result.exit_code == 0
        assert "search" in result.output

    def test_version(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0

    def test_version_matches_library(self):
        """CLI --version must report the same value as swoop.__version__."""
        import swoop
        runner = CliRunner()
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert swoop.__version__ in result.output


class TestResolveQuiet:
    """resolve_quiet auto-detects non-TTY stdout."""

    def test_explicit_quiet_flag_wins(self):
        from swoop.cli.utils import resolve_quiet
        import sys
        with patch.object(sys.stdout, "isatty", return_value=True):
            assert resolve_quiet(True) is True
        with patch.object(sys.stdout, "isatty", return_value=False):
            assert resolve_quiet(True) is True

    def test_tty_without_flag_stays_loud(self):
        from swoop.cli.utils import resolve_quiet
        import sys
        with patch.object(sys.stdout, "isatty", return_value=True):
            assert resolve_quiet(False) is False

    def test_non_tty_without_flag_auto_quiets(self):
        """The whole point: piping to jq shouldn't need a -q."""
        from swoop.cli.utils import resolve_quiet
        import sys
        with patch.object(sys.stdout, "isatty", return_value=False):
            assert resolve_quiet(False) is True


class TestVerboseFlag:
    """--verbose flag wires swoop.* loggers to stderr at DEBUG for the
    lifetime of the CLI invocation, then restores prior state."""

    @pytest.fixture(autouse=True)
    def _snapshot_swoop_logger(self):
        """Snapshot the swoop logger so a test's wiring can't leak.

        Replaces the previous teardown_method that hardcoded
        ``level=WARNING`` — that was itself a state leak (any pre-existing
        configuration above WARNING would be silently lowered) and made
        these tests order-dependent.
        """
        import logging
        log = logging.getLogger("swoop")
        saved_level = log.level
        saved_propagate = log.propagate
        saved_handlers = list(log.handlers)
        try:
            yield
        finally:
            log.handlers[:] = saved_handlers
            log.setLevel(saved_level)
            log.propagate = saved_propagate

    @patch("swoop.cli.commands._run_search")
    def test_verbose_attaches_and_restores_during_command(self, mock_search):
        """During the invocation the handler is attached and the level is
        DEBUG; after Click closes the context, prior state is restored."""
        import logging
        from swoop.cli.utils import _SwoopVerboseHandler

        observed = {}

        def _capture_state(*_args, **_kwargs):
            log = logging.getLogger("swoop")
            observed["level"] = log.level
            observed["propagate"] = log.propagate
            observed["has_handler"] = any(
                isinstance(h, _SwoopVerboseHandler) for h in log.handlers
            )
            return _make_search_result()

        mock_search.side_effect = _capture_state
        runner = CliRunner()
        result = runner.invoke(main, [
            "search", "JFK", "LAX", _FUTURE, "-q", "-o", "json", "--verbose",
        ])
        assert result.exit_code == 0
        # While the command was running.
        assert observed["level"] == logging.DEBUG
        assert observed["propagate"] is False
        assert observed["has_handler"] is True
        # After the command finished, Click closed the context and the
        # cleanup restored the prior swoop-logger state. Without this,
        # in-process callers (test harnesses, embedding wrappers) would
        # leak DEBUG and the handler into subsequent non-verbose runs.
        swoop_log = logging.getLogger("swoop")
        assert not any(
            isinstance(h, _SwoopVerboseHandler) for h in swoop_log.handlers
        )

    @patch("swoop.cli.commands._run_search")
    def test_verbose_short_flag(self, mock_search):
        """-v is equivalent to --verbose."""
        import logging
        observed_level = []

        def _capture(*_args, **_kwargs):
            observed_level.append(logging.getLogger("swoop").level)
            return _make_search_result()

        mock_search.side_effect = _capture
        runner = CliRunner()
        result = runner.invoke(main, [
            "search", "JFK", "LAX", _FUTURE, "-q", "-o", "json", "-v",
        ])
        assert result.exit_code == 0
        assert observed_level == [logging.DEBUG]

    @patch("swoop.cli.commands._run_search")
    def test_verbose_idempotent_within_invocation(self, mock_search):
        """Re-invoking with --verbose must not stack handlers even if
        the cleanup didn't fire between calls (e.g. exception path)."""
        import logging
        from swoop.cli.utils import _SwoopVerboseHandler, configure_verbose_logging

        mock_search.return_value = _make_search_result()
        runner = CliRunner()
        # Two back-to-back invocations: each scope must restore cleanly,
        # so the handler count between invocations stays at zero, then
        # rises to exactly one during the next invocation.
        for _ in range(3):
            runner.invoke(main, [
                "search", "JFK", "LAX", _FUTURE, "-q", "-o", "json", "-v",
            ])
            assert not any(
                isinstance(h, _SwoopVerboseHandler)
                for h in logging.getLogger("swoop").handlers
            )
        # Also exercise the bare function with no ctx: two calls
        # in the same scope must still produce only one handler.
        configure_verbose_logging(None, True)
        configure_verbose_logging(None, True)
        count = sum(
            1 for h in logging.getLogger("swoop").handlers
            if isinstance(h, _SwoopVerboseHandler)
        )
        assert count == 1, f"expected 1 verbose handler, got {count}"

    @patch("swoop.cli.commands._run_search")
    def test_no_verbose_leaves_logging_quiet(self, mock_search):
        """Without --verbose, no handler is added and the level is unchanged."""
        import logging
        from swoop.cli.utils import _SwoopVerboseHandler
        mock_search.return_value = _make_search_result()
        runner = CliRunner()
        prior_level = logging.getLogger("swoop").level
        result = runner.invoke(main, [
            "search", "JFK", "LAX", _FUTURE, "-q", "-o", "json",
        ])
        assert result.exit_code == 0
        swoop_log = logging.getLogger("swoop")
        assert not any(isinstance(h, _SwoopVerboseHandler) for h in swoop_log.handlers)
        assert swoop_log.level == prior_level

    def test_configure_verbose_logging_false_is_noop(self):
        """configure_verbose_logging(ctx, False) must not touch the logger."""
        import logging
        from swoop.cli.utils import _SwoopVerboseHandler, configure_verbose_logging
        log = logging.getLogger("swoop")
        prior_level = log.level
        prior_propagate = log.propagate
        configure_verbose_logging(None, False)
        assert log.level == prior_level
        assert log.propagate == prior_propagate
        assert not any(isinstance(h, _SwoopVerboseHandler) for h in log.handlers)


# ---------------------------------------------------------------------------
# Search command tests
# ---------------------------------------------------------------------------


class TestSearchCommand:
    @patch("swoop.cli.commands._run_search")
    def test_json_output(self, mock_search):
        mock_search.return_value = _make_search_result()
        runner = CliRunner()
        result = runner.invoke(main, [
            "search", "JFK", "LAX", _FUTURE, "-o", "json", "-q",
        ])
        assert result.exit_code == 0
        import json
        data = json.loads(result.output)
        assert data["query"]["origin"] == "JFK"
        assert data["price_source"] == "shopping"
        assert len(data["results"]) == 3
        assert data["results"][0]["price"] == 247
        assert data["results"][0]["selector"] == "selector-1"
        assert data["results"][0]["legs"][0]["itinerary"]["flight_summary"] == "DL 2300"

    @patch("swoop.cli.commands._run_search")
    def test_table_output(self, mock_search):
        mock_search.return_value = _make_search_result()
        runner = CliRunner()
        result = runner.invoke(main, [
            "search", "JFK", "LAX", _FUTURE, "-q",
        ])
        assert result.exit_code == 0
        assert "DL 2300" in result.output  # flight_summary
        assert "Nonstop" in result.output
        assert "Prices shown are shopping totals" in result.output
        assert "--show-price-commands" in result.output
        assert "swoop price --selector" in result.output
        assert "selector-1" not in result.output

    @patch("swoop.cli.commands._run_search")
    def test_csv_output(self, mock_search):
        mock_search.return_value = _make_search_result()
        runner = CliRunner()
        result = runner.invoke(main, [
            "search", "JFK", "LAX", _FUTURE, "-o", "csv", "-q",
        ])
        assert result.exit_code == 0
        lines = result.output.strip().split("\n")
        assert "index" in lines[0]  # header
        assert "selector" in lines[0]
        assert len(lines) == 4  # header + 3 results

    @patch("swoop.cli.commands._run_search")
    def test_brief_output(self, mock_search):
        mock_search.return_value = _make_search_result()
        runner = CliRunner()
        result = runner.invoke(main, [
            "search", "JFK", "LAX", _FUTURE, "-o", "brief", "-q",
        ])
        assert result.exit_code == 0
        lines = result.output.strip().split("\n")
        assert len(lines) >= 5
        assert "$247" in lines[0]
        assert "DL 2300" in lines[0]
        assert "Prices shown are shopping totals." in result.output
        assert "--show-price-commands" in result.output

    @patch("swoop.cli.commands._run_search")
    def test_limit(self, mock_search):
        mock_search.return_value = _make_search_result()
        runner = CliRunner()
        result = runner.invoke(main, [
            "search", "JFK", "LAX", _FUTURE, "-o", "json", "-q", "-l", "1",
        ])
        assert result.exit_code == 0
        import json
        data = json.loads(result.output)
        assert len(data["results"]) == 1

    @patch("swoop.cli.commands._run_search")
    def test_no_results(self, mock_search):
        mock_search.return_value = SearchResult(results=[])
        runner = CliRunner()
        result = runner.invoke(main, [
            "search", "JFK", "LAX", _FUTURE, "-q",
        ])
        assert result.exit_code == 1
        assert "No flights found" in result.stderr

    def test_bad_iata(self):
        runner = CliRunner()
        result = runner.invoke(main, [
            "search", "XY", "LAX", _FUTURE, "-q",
        ])
        assert result.exit_code == 2
        assert "not a valid IATA" in result.stderr

    def test_bad_date(self):
        runner = CliRunner()
        result = runner.invoke(main, [
            "search", "JFK", "LAX", "2026-13-45", "-q",
        ])
        assert result.exit_code == 2
        assert "not a valid date" in result.stderr

    @patch("swoop.cli.commands._run_search")
    def test_nonstop_flag(self, mock_search):
        mock_search.return_value = _make_search_result(1)
        runner = CliRunner()
        result = runner.invoke(main, [
            "search", "JFK", "LAX", _FUTURE, "--nonstop", "-o", "json", "-q",
        ])
        assert result.exit_code == 0
        # Verify nonstop was passed
        _, kwargs = mock_search.call_args
        assert kwargs["nonstop"] is True

    @patch("swoop.cli.commands._run_search")
    def test_roundtrip(self, mock_search):
        mock_search.return_value = _make_search_result(1)
        runner = CliRunner()
        result = runner.invoke(main, [
            "search", "JFK", "LAX", _FUTURE, "-r", _RETURN, "-o", "json", "-q",
        ])
        assert result.exit_code == 0
        _, kwargs = mock_search.call_args
        assert kwargs["return_date"] == _RETURN

    @patch("swoop.cli.commands._run_search")
    def test_rate_limit_error(self, mock_search):
        from swoop.exceptions import SwoopRateLimitError
        mock_search.side_effect = SwoopRateLimitError()
        runner = CliRunner()
        result = runner.invoke(main, [
            "search", "JFK", "LAX", _FUTURE, "-q",
        ])
        assert result.exit_code == 3
        assert "Rate limited" in result.stderr

    @patch("swoop.cli.commands._run_search")
    def test_http_error(self, mock_search):
        from swoop.exceptions import SwoopHTTPError
        mock_search.side_effect = SwoopHTTPError(500)
        runner = CliRunner()
        result = runner.invoke(main, [
            "search", "JFK", "LAX", _FUTURE, "-q",
        ])
        assert result.exit_code == 3
        assert "HTTP 500" in result.stderr

    @patch("swoop.cli.commands._run_search")
    def test_parse_error(self, mock_search):
        from swoop.exceptions import SwoopParseError
        mock_search.side_effect = SwoopParseError("bad")
        runner = CliRunner()
        result = runner.invoke(main, [
            "search", "JFK", "LAX", _FUTURE, "-q",
        ])
        assert result.exit_code == 4
        assert "Could not parse" in result.stderr

    @patch("swoop.cli.commands._run_search")
    def test_validation_error(self, mock_search):
        mock_search.side_effect = ValueError("origin must be valid")
        runner = CliRunner()
        result = runner.invoke(main, [
            "search", "JFK", "LAX", _FUTURE, "-q",
        ])
        assert result.exit_code == 2
        assert "origin must be valid" in result.stderr

    @patch("swoop.cli.commands._run_search")
    def test_airline_filter(self, mock_search):
        mock_search.return_value = _make_search_result(1)
        runner = CliRunner()
        result = runner.invoke(main, [
            "search", "JFK", "LAX", _FUTURE, "-a", "DL", "-a", "UA", "-o", "json", "-q",
        ])
        assert result.exit_code == 0
        _, kwargs = mock_search.call_args
        assert kwargs["airline"] == ("DL", "UA")

    @patch("swoop.cli.commands._run_search")
    def test_case_insensitive_iata(self, mock_search):
        mock_search.return_value = _make_search_result(1)
        runner = CliRunner()
        result = runner.invoke(main, [
            "search", "jfk", "lax", _FUTURE, "-o", "json", "-q",
        ])
        assert result.exit_code == 0
        # IATA should be uppercased
        args = mock_search.call_args[0]
        assert args[0] == "JFK"
        assert args[1] == "LAX"

    @patch("swoop.cli.commands._run_search")
    def test_roundtrip_labels_prices(self, mock_search):
        """Roundtrip search renders complete trip rows."""
        mock_search.return_value = _make_search_result(1)
        runner = CliRunner()
        result = runner.invoke(main, [
            "search", "JFK", "LAX", _FUTURE, "-r", _RETURN, "-q",
        ])
        assert result.exit_code == 0
        assert "JFK -> LAX" in result.output

    @patch("swoop.cli.commands._run_search")
    @patch("swoop.price_selector")
    def test_search_does_not_price_results(self, mock_price_selector, mock_search):
        mock_search.return_value = _make_search_result()
        runner = CliRunner()
        result = runner.invoke(main, [
            "search", "JFK", "LAX", _FUTURE, "-q",
        ])
        assert result.exit_code == 0
        mock_price_selector.assert_not_called()

    @patch("swoop.cli.commands._run_search")
    def test_show_price_commands(self, mock_search):
        mock_search.return_value = _make_search_result()
        runner = CliRunner()
        result = runner.invoke(main, [
            "search", "JFK", "LAX", _FUTURE, "--show-price-commands", "-q",
        ])
        assert result.exit_code == 0
        assert "Bookable fare commands for shown rows" in result.output
        assert "1. swoop price --selector 'selector-1'" in result.output
        assert "2. swoop price --selector 'selector-2'" in result.output

    @patch("swoop.cli.commands._run_search")
    def test_show_price_commands_respects_limit(self, mock_search):
        mock_search.return_value = _make_search_result()
        runner = CliRunner()
        result = runner.invoke(main, [
            "search", "JFK", "LAX", _FUTURE, "--show-price-commands", "-l", "1", "-q",
        ])
        assert result.exit_code == 0
        assert "1. swoop price --selector 'selector-1'" in result.output
        assert "2. swoop price --selector 'selector-2'" not in result.output

    def test_show_price_commands_rejects_json(self):
        runner = CliRunner()
        result = runner.invoke(main, [
            "search", "JFK", "LAX", _FUTURE, "--show-price-commands", "-o", "json", "-q",
        ])
        assert result.exit_code == 2
        assert "--show-price-commands is only supported with table or brief output" in result.stderr

    def test_show_price_commands_rejects_csv(self):
        runner = CliRunner()
        result = runner.invoke(main, [
            "search", "JFK", "LAX", _FUTURE, "--show-price-commands", "-o", "csv", "-q",
        ])
        assert result.exit_code == 2
        assert "--show-price-commands is only supported with table or brief output" in result.stderr

    @patch("swoop.cli.commands._run_search")
    def test_connecting_flight_table(self, mock_search):
        """Table output shows layover info for connecting flights."""
        mock_search.return_value = SearchResult(
            results=[_make_trip_option(_make_connecting_itinerary(), index=1)],
        )
        runner = CliRunner()
        result = runner.invoke(main, [
            "search", "JFK", "LAX", _FUTURE, "-q",
        ])
        assert result.exit_code == 0
        assert "stop" in result.output
        assert "ORD" in result.output

    @patch("swoop.cli.commands._run_search")
    def test_default_retries(self, mock_search):
        """CLI search passes retries=2 by default."""
        mock_search.return_value = _make_search_result()
        runner = CliRunner()
        result = runner.invoke(main, ["search", "JFK", "LAX", _FUTURE, "-q"])
        assert result.exit_code == 0
        _, kwargs = mock_search.call_args
        assert kwargs["retries"] == 2

    @patch("swoop.cli.commands._run_search_legs")
    def test_leg_search_mode(self, mock_search_legs):
        mock_search_legs.return_value = _make_search_result(1)
        runner = CliRunner()
        result = runner.invoke(main, [
            "search", "--leg", "JFK", "LAX", _FUTURE, "--leg", "LAX", "SFO", _LEG2, "-q",
        ])
        assert result.exit_code == 0
        mock_search_legs.assert_called_once()


# ---------------------------------------------------------------------------
# Price command tests
# ---------------------------------------------------------------------------


class TestPriceCommand:
    @patch("swoop.check_price")
    def test_price_shorthand_one_way(self, mock_check):
        mock_check.return_value = PriceResult(price=342, currency="USD", fare_brand="Main Cabin", rpc_calls=1)
        runner = CliRunner()
        result = runner.invoke(main, [
            "price", "JFK", "LAX", "--depart", _FUTURE, "DL2300", "-q",
        ])
        assert result.exit_code == 0
        assert "$342" in result.output
        mock_check.assert_called_once()
        args, kwargs = mock_check.call_args
        assert args == ("DL2300",)
        assert kwargs["origin"] == "JFK"
        assert kwargs["destination"] == "LAX"
        assert kwargs["date"] == _FUTURE
        assert kwargs["cabin"] == "economy"
        pax = kwargs["passengers"]
        assert pax.adults == 1
        assert pax.children == 0
        assert pax.infants_in_seat == 0
        assert pax.infants_on_lap == 0

    @patch("swoop.check_price")
    def test_price_json_output(self, mock_check):
        mock_check.return_value = PriceResult(price=342, fare_brand="Main Cabin", rpc_calls=1)
        runner = CliRunner()
        result = runner.invoke(main, [
            "price", "JFK", "LAX", "--depart", _FUTURE, "DL2300",
            "-o", "json", "-q",
        ])
        assert result.exit_code == 0
        import json
        data = json.loads(result.output)
        assert data["price"] == 342
        assert "rpc_calls" not in data

    @patch("swoop.check_price")
    def test_price_brief_output(self, mock_check):
        mock_check.return_value = PriceResult(price=342, currency="USD", fare_brand="Main Cabin", rpc_calls=1)
        runner = CliRunner()
        result = runner.invoke(main, [
            "price", "JFK", "LAX", "--depart", _FUTURE, "DL2300",
            "-o", "brief", "-q",
        ])
        assert result.exit_code == 0
        assert "$342" in result.output
        assert "1-leg" in result.output
        assert "RPC" not in result.output

    @patch("swoop.check_price")
    def test_price_csv_output_with_booking_options(self, mock_check):
        """csv emits one row per booking option with seller fields."""
        mock_check.return_value = PriceResult(
            price=342,
            currency="USD",
            fare_brand="Main Cabin",
            booking_options=[
                BookingOption(
                    price=342, brand_label="Main", brand_code="MAIN",
                    is_basic=False, fare_family="Main",
                    rebookability_signal="free-changes",
                    seller_name="Delta", seller_code="DL",
                    booking_url="https://example.test/book/dl",
                    logo_url="https://logos.test/dl.png",
                    is_airline_direct=True,
                ),
                BookingOption(
                    price=355, brand_label="Main", brand_code="MAIN",
                    is_basic=False, fare_family="Main",
                    rebookability_signal=None,
                    seller_name="Mytrip", seller_code="ETRAVELI_Mytrip",
                    booking_url="https://example.test/book/mytrip",
                    logo_url="",
                    is_airline_direct=False,
                ),
            ],
            rpc_calls=2,
        )
        runner = CliRunner()
        result = runner.invoke(main, [
            "price", "JFK", "LAX", "--depart", _FUTURE, "DL2300",
            "-o", "csv", "-q",
        ])
        assert result.exit_code == 0
        import csv as _csv
        import io as _io
        reader = _csv.reader(_io.StringIO(result.output))
        rows = list(reader)
        # Header + 2 booking options
        assert len(rows) == 3
        header = rows[0]
        assert header[0] == "price"
        assert "seller_name" in header
        assert "is_airline_direct" in header
        assert "logo_url" in header
        idx = {name: i for i, name in enumerate(header)}
        # Row 1: airline-direct Delta — booleans must be lowercase so
        # spreadsheet filters like is_airline_direct == TRUE match.
        assert rows[1][idx["price"]] == "342"
        assert rows[1][idx["seller_name"]] == "Delta"
        assert rows[1][idx["is_airline_direct"]] == "true"
        assert rows[1][idx["logo_url"]] == "https://logos.test/dl.png"
        # Row 2: OTA Mytrip
        assert rows[2][idx["price"]] == "355"
        assert rows[2][idx["seller_name"]] == "Mytrip"
        assert rows[2][idx["is_airline_direct"]] == "false"
        assert rows[2][idx["logo_url"]] == ""

    @patch("swoop.check_price")
    def test_price_csv_output_no_booking_options(self, mock_check):
        """csv falls back to a single row when booking_options is empty."""
        mock_check.return_value = PriceResult(
            price=342, currency="USD", fare_brand="Main Cabin", rpc_calls=1,
        )
        runner = CliRunner()
        result = runner.invoke(main, [
            "price", "JFK", "LAX", "--depart", _FUTURE, "DL2300",
            "-o", "csv", "-q",
        ])
        assert result.exit_code == 0
        import csv as _csv
        import io as _io
        rows = list(_csv.reader(_io.StringIO(result.output)))
        assert len(rows) == 2  # header + chosen fare
        # Header column count must match the fallback row column count.
        assert len(rows[0]) == len(rows[1])
        assert "logo_url" in rows[0]
        assert rows[1][rows[0].index("price")] == "342"

    @patch("swoop.check_price")
    def test_price_output_surfaces_is_estimate(self, mock_check):
        """An estimate (search-derived, no confirmed fare) must be visible in
        machine-readable output — JSON and CSV — so consumers can tell it apart
        from a confirmed bookable fare. The no-booking-options path is exactly
        the is_estimate=True case."""
        mock_check.return_value = PriceResult(
            price=342, currency="USD", is_estimate=True, rpc_calls=0,
        )
        runner = CliRunner()
        json_res = runner.invoke(main, [
            "price", "JFK", "LAX", "--depart", _FUTURE, "DL2300", "-o", "json", "-q",
        ])
        assert json_res.exit_code == 0
        import json
        assert json.loads(json_res.output)["is_estimate"] is True

        csv_res = runner.invoke(main, [
            "price", "JFK", "LAX", "--depart", _FUTURE, "DL2300", "-o", "csv", "-q",
        ])
        assert csv_res.exit_code == 0
        import csv as _csv
        import io as _io
        rows = list(_csv.reader(_io.StringIO(csv_res.output)))
        assert "is_estimate" in rows[0]
        assert rows[1][rows[0].index("is_estimate")] == "true"

    @patch("swoop.check_price")
    def test_price_csv_empty_currency_column_when_none(self, mock_check):
        """currency=None must serialize as an empty string, not 'None'."""
        mock_check.return_value = PriceResult(
            price=342, currency=None, fare_brand="Main Cabin", rpc_calls=1,
        )
        runner = CliRunner()
        result = runner.invoke(main, [
            "price", "JFK", "LAX", "--depart", _FUTURE, "DL2300",
            "-o", "csv", "-q",
        ])
        assert result.exit_code == 0
        import csv as _csv
        import io as _io
        rows = list(_csv.reader(_io.StringIO(result.output)))
        assert rows[1][rows[0].index("currency")] == ""

    @patch("swoop.check_price")
    def test_price_csv_round_trips_commas_quotes_newlines(self, mock_check):
        """csv.writer must quote commas, double-quotes, and newlines so
        the row round-trips through csv.reader unchanged."""
        from swoop.decoder import BookingOption
        mock_check.return_value = PriceResult(
            price=342, currency="USD",
            booking_options=[BookingOption(
                price=342,
                seller_name='Acme, Inc. "Travel"\nDivision',
                booking_url="https://x.test/a?q=1,2",
                is_airline_direct=False,
            )],
            rpc_calls=1,
        )
        runner = CliRunner()
        result = runner.invoke(main, [
            "price", "JFK", "LAX", "--depart", _FUTURE, "DL2300",
            "-o", "csv", "-q",
        ])
        assert result.exit_code == 0
        import csv as _csv
        import io as _io
        rows = list(_csv.reader(_io.StringIO(result.output)))
        idx = {name: i for i, name in enumerate(rows[0])}
        assert rows[1][idx["seller_name"]] == 'Acme, Inc. "Travel"\nDivision'
        assert rows[1][idx["booking_url"]] == "https://x.test/a?q=1,2"

    @patch("swoop.check_price")
    def test_price_csv_sanitizes_formula_prefixes(self, mock_check):
        """Excel/Sheets treat a cell starting with =,+,-,@,\\t,\\r as a
        formula. swoop passes Google's RPC strings through opaquely, so
        any such prefix must be neutralized with a leading single quote
        before the CSV reaches a spreadsheet that would auto-evaluate it.
        """
        from swoop.decoder import BookingOption
        mock_check.return_value = PriceResult(
            price=342, currency="USD",
            fare_brand="=cmd|'/c calc'!A1",
            booking_options=[
                BookingOption(price=342, seller_name="=HYPERLINK(\"https://evil\",\"click\")"),
                BookingOption(price=355, seller_name="+1234567890"),
                BookingOption(price=360, seller_name="-2+3"),
                BookingOption(price=370, seller_name="@SUM(A1:A10)"),
                BookingOption(price=380, seller_name="\tinjected"),
                BookingOption(price=390, seller_name="\rinjected"),
                BookingOption(price=400, seller_name="Delta Air Lines"),
            ],
            rpc_calls=1,
        )
        runner = CliRunner()
        result = runner.invoke(main, [
            "price", "JFK", "LAX", "--depart", _FUTURE, "DL2300",
            "-o", "csv", "-q",
        ])
        assert result.exit_code == 0
        import csv as _csv
        import io as _io
        rows = list(_csv.reader(_io.StringIO(result.output)))
        idx = {name: i for i, name in enumerate(rows[0])}
        sellers = [row[idx["seller_name"]] for row in rows[1:]]
        # Each dangerous prefix is neutralized with a leading single quote.
        assert sellers[0].startswith("'=HYPERLINK")
        assert sellers[1].startswith("'+1234567890")
        assert sellers[2].startswith("'-2+3")
        assert sellers[3].startswith("'@SUM")
        assert sellers[4].startswith("'\t")
        assert sellers[5].startswith("'\r")
        # Safe prefixes are passed through unmolested.
        assert sellers[6] == "Delta Air Lines"
        # fare_brand is also RPC-sourced and must be sanitized.
        assert rows[1][idx["fare_brand"]].startswith("'=cmd")

    @patch("swoop.check_price")
    def test_price_table_output_hides_rpc_call_count(self, mock_check):
        mock_check.return_value = PriceResult(price=342, fare_brand="Main Cabin", rpc_calls=1)
        runner = CliRunner()
        result = runner.invoke(main, [
            "price", "JFK", "LAX", "--depart", _FUTURE, "DL2300",
        ])
        assert result.exit_code == 0
        assert "RPC calls:" not in result.output

    @patch("swoop.check_price")
    def test_price_not_found(self, mock_check):
        mock_check.return_value = None
        runner = CliRunner()
        result = runner.invoke(main, [
            "price", "JFK", "LAX", "--depart", _FUTURE, "DL2300", "-q",
        ])
        assert result.exit_code == 1

    def test_price_missing_depart(self):
        runner = CliRunner()
        result = runner.invoke(main, ["price", "JFK", "LAX"])
        assert result.exit_code == 2
        assert "--depart is required" in result.stderr

    @patch("swoop.check_price")
    def test_price_shorthand_roundtrip(self, mock_check):
        mock_check.return_value = PriceResult(price=684, currency="USD", fare_brand="Main Cabin", rpc_calls=3)
        runner = CliRunner()
        result = runner.invoke(main, [
            "price", "JFK", "LAX",
            "--depart", _FUTURE, "DL2300",
            "--return", _RETURN, "DL2301",
            "-q",
        ])
        assert result.exit_code == 0
        mock_check.assert_called_once()
        args, kwargs = mock_check.call_args
        assert args == ("DL2300",)
        assert kwargs["origin"] == "JFK"
        assert kwargs["destination"] == "LAX"
        assert kwargs["date"] == _FUTURE
        assert kwargs["return_flight_number"] == "DL2301"
        assert kwargs["return_date"] == _RETURN
        assert kwargs["cabin"] == "economy"
        pax = kwargs["passengers"]
        assert pax.adults == 1
        assert pax.children == 0

    @patch("swoop.price_legs")
    def test_price_leg_syntax(self, mock_price_legs):
        """--leg repeated syntax works."""
        mock_price_legs.return_value = PriceResult(price=684, currency="USD", fare_brand="Main Cabin", rpc_calls=3)
        runner = CliRunner()
        result = runner.invoke(main, [
            "price",
            "--leg", "JFK", "LAX", _FUTURE, "DL2300",
            "--leg", "LAX", "JFK", _RETURN, "DL2301",
            "-q",
        ])
        assert result.exit_code == 0
        assert "$684" in result.output
        call_args = mock_price_legs.call_args
        assert len(call_args[0][0]) == 2
        assert call_args[0][0][1].flight_number == "DL2301"

    def test_price_shorthand_and_leg_error(self):
        """Shorthand + --leg is an error."""
        runner = CliRunner()
        result = runner.invoke(main, [
            "price", "JFK", "LAX", "--depart", _FUTURE, "DL2300",
            "--leg", "JFK", "LAX", _FUTURE, "DL2300",
        ])
        assert result.exit_code == 2
        assert "mutually exclusive" in result.stderr

    def test_price_return_requires_depart(self):
        runner = CliRunner()
        result = runner.invoke(main, [
            "price", "JFK", "LAX", "--return", _RETURN, "DL2301",
        ])
        assert result.exit_code == 2
        assert "--return requires --depart" in result.stderr

    def test_price_depart_requires_route_args(self):
        runner = CliRunner()
        result = runner.invoke(main, [
            "price", "--depart", _FUTURE, "DL2300",
        ])
        assert result.exit_code == 2
        assert "ORIGIN DESTINATION are required" in result.stderr

    def test_price_legacy_positional_fails_cleanly(self):
        runner = CliRunner()
        result = runner.invoke(main, [
            "price", "DL2300", "JFK", "LAX", _FUTURE,
        ])
        assert result.exit_code == 2
        assert "not a valid iata airport code" in result.stderr.lower()

    def test_price_legacy_return_flag_fails_cleanly(self):
        runner = CliRunner()
        result = runner.invoke(main, [
            "price", "JFK", "LAX", "--depart", _FUTURE, "DL2300", "--return-date", "2026-06-22",
        ])
        assert result.exit_code == 2
        stderr = result.stderr.lower()
        # Click's exact wording around unknown options has shifted between
        # versions (colon vs quoted, plus "did you mean" suggestions), so
        # assert on the contract — the dropped flag is named and rejected —
        # not the exact phrasing.
        assert "no such option" in stderr
        assert "--return-date" in stderr

    @patch("swoop.price_selector")
    def test_price_selector_mode(self, mock_price_selector):
        mock_price_selector.return_value = PriceResult(price=342, fare_brand="Main Cabin", rpc_calls=1)
        runner = CliRunner()
        result = runner.invoke(main, [
            "price", "--selector", "selector-1", "-q",
        ])
        assert result.exit_code == 0
        from swoop.models import TransportConfig
        mock_price_selector.assert_called_once_with("selector-1", transport=TransportConfig(timeout=90, retries=2, country=None, proxy=None))


# ---------------------------------------------------------------------------
# Currency display tests
# ---------------------------------------------------------------------------


class TestCurrencyDisplay:
    @patch("swoop.cli.commands._run_search")
    def test_gbp_table_output(self, mock_search):
        """GBP currency renders pound symbol in table output."""
        from swoop.builders import ItinerarySummary
        itin = _make_itinerary(
            direct_price=150,
            price_info=ItinerarySummary(flights="f", price=150.0, currency="GBP"),
        )
        option = TripOption(
            selector="sel-gbp",
            price=150,
            currency="GBP",
            legs=[TripLeg(origin="LHR", destination="CDG", date="2026-07-01", itinerary=itin)],
        )
        mock_search.return_value = SearchResult(results=[option])
        runner = CliRunner()
        result = runner.invoke(main, [
            "search", "LHR", "CDG", _FUTURE, "-q",
        ])
        assert result.exit_code == 0
        assert "\u00a3150" in result.output  # £150

    @patch("swoop.cli.commands._run_search")
    def test_gbp_brief_output(self, mock_search):
        """GBP currency renders pound symbol in brief output."""
        from swoop.builders import ItinerarySummary
        itin = _make_itinerary(
            direct_price=150,
            price_info=ItinerarySummary(flights="f", price=150.0, currency="GBP"),
        )
        option = TripOption(
            selector="sel-gbp",
            price=150,
            currency="GBP",
            legs=[TripLeg(origin="LHR", destination="CDG", date="2026-07-01", itinerary=itin)],
        )
        mock_search.return_value = SearchResult(results=[option])
        runner = CliRunner()
        result = runner.invoke(main, [
            "search", "LHR", "CDG", _FUTURE, "-o", "brief", "-q",
        ])
        assert result.exit_code == 0
        assert "\u00a3150" in result.output

    @patch("swoop.check_price")
    def test_price_table_gbp(self, mock_check):
        """Price table renders pound symbol for GBP."""
        mock_check.return_value = PriceResult(price=150, currency="GBP", fare_brand="Flex", rpc_calls=1)
        runner = CliRunner()
        result = runner.invoke(main, [
            "price", "LHR", "CDG", "--depart", _FUTURE, "BA304", "-q",
        ])
        assert result.exit_code == 0
        assert "\u00a3150" in result.output

    @patch("swoop.check_price")
    def test_price_brief_gbp(self, mock_check):
        """Price brief renders pound symbol for GBP."""
        mock_check.return_value = PriceResult(price=150, currency="GBP", fare_brand="Flex", rpc_calls=1)
        runner = CliRunner()
        result = runner.invoke(main, [
            "price", "LHR", "CDG", "--depart", _FUTURE, "BA304", "-o", "brief", "-q",
        ])
        assert result.exit_code == 0
        assert "\u00a3150" in result.output

    @patch("swoop.check_price")
    def test_price_json_includes_currency(self, mock_check):
        """Price JSON output includes currency field."""
        mock_check.return_value = PriceResult(price=150, currency="GBP", fare_brand="Flex", rpc_calls=1)
        runner = CliRunner()
        result = runner.invoke(main, [
            "price", "LHR", "CDG", "--depart", _FUTURE, "BA304", "-o", "json", "-q",
        ])
        assert result.exit_code == 0
        import json
        data = json.loads(result.output)
        assert data["price"] == 150
        assert data["currency"] == "GBP"

    @patch("swoop.cli.commands._run_search")
    def test_search_json_includes_currency(self, mock_search):
        """Search JSON output includes currency field."""
        from swoop.builders import ItinerarySummary
        itin = _make_itinerary(
            direct_price=150,
            price_info=ItinerarySummary(flights="f", price=150.0, currency="GBP"),
        )
        option = TripOption(
            selector="sel-gbp",
            price=150,
            currency="GBP",
            legs=[TripLeg(origin="LHR", destination="CDG", date="2026-07-01", itinerary=itin)],
        )
        mock_search.return_value = SearchResult(results=[option])
        runner = CliRunner()
        result = runner.invoke(main, [
            "search", "LHR", "CDG", _FUTURE, "-o", "json", "-q",
        ])
        assert result.exit_code == 0
        import json
        data = json.loads(result.output)
        assert data["currency"] == "GBP"
        assert data["results"][0]["price"] == 150
        assert data["results"][0]["currency"] == "GBP"

    @patch("swoop.cli.commands._run_search")
    def test_search_csv_includes_currency(self, mock_search):
        """Search CSV output includes currency column."""
        from swoop.builders import ItinerarySummary
        itin = _make_itinerary(
            direct_price=150,
            price_info=ItinerarySummary(flights="f", price=150.0, currency="GBP"),
        )
        option = TripOption(
            selector="sel-gbp",
            price=150,
            currency="GBP",
            legs=[TripLeg(origin="LHR", destination="CDG", date="2026-07-01", itinerary=itin)],
        )
        mock_search.return_value = SearchResult(results=[option])
        runner = CliRunner()
        result = runner.invoke(main, [
            "search", "LHR", "CDG", _FUTURE, "-o", "csv", "-q",
        ])
        assert result.exit_code == 0
        lines = result.output.strip().split("\n")
        assert "currency" in lines[0]
        assert "GBP" in lines[1]


# ---------------------------------------------------------------------------
# New flag tests
# ---------------------------------------------------------------------------


class TestNewFlags:
    @patch("swoop.cli.commands._run_search")
    def test_country_flag_search(self, mock_search):
        mock_search.return_value = _make_search_result(1)
        runner = CliRunner()
        result = runner.invoke(main, [
            "search", "JFK", "LAX", _FUTURE, "--country", "GB", "-o", "json", "-q",
        ])
        assert result.exit_code == 0
        _, kwargs = mock_search.call_args
        assert kwargs["country"] == "GB"

    @patch("swoop.cli.commands._run_search")
    def test_proxy_flag_search(self, mock_search):
        mock_search.return_value = _make_search_result(1)
        runner = CliRunner()
        result = runner.invoke(main, [
            "search", "JFK", "LAX", _FUTURE, "--proxy", "socks5://localhost:1080", "-o", "json", "-q",
        ])
        assert result.exit_code == 0
        _, kwargs = mock_search.call_args
        assert kwargs["proxy"] == "socks5://localhost:1080"

    @patch("swoop.cli.commands._run_search")
    def test_children_flag_search(self, mock_search):
        mock_search.return_value = _make_search_result(1)
        runner = CliRunner()
        result = runner.invoke(main, [
            "search", "JFK", "LAX", _FUTURE, "--children", "2", "-o", "json", "-q",
        ])
        assert result.exit_code == 0
        _, kwargs = mock_search.call_args
        assert kwargs["children"] == 2

    @patch("swoop.cli.commands._run_search")
    def test_infants_flags_search(self, mock_search):
        mock_search.return_value = _make_search_result(1)
        runner = CliRunner()
        result = runner.invoke(main, [
            "search", "JFK", "LAX", _FUTURE,
            "--infants-in-seat", "1", "--infants-on-lap", "1",
            "-o", "json", "-q",
        ])
        assert result.exit_code == 0
        _, kwargs = mock_search.call_args
        assert kwargs["infants_in_seat"] == 1
        assert kwargs["infants_on_lap"] == 1

    @patch("swoop.check_price")
    def test_country_flag_price(self, mock_check):
        mock_check.return_value = PriceResult(price=342, currency="GBP", fare_brand="Main Cabin", rpc_calls=1)
        runner = CliRunner()
        result = runner.invoke(main, [
            "price", "JFK", "LAX", "--depart", _FUTURE, "DL2300",
            "--country", "GB", "-q",
        ])
        assert result.exit_code == 0
        _, kwargs = mock_check.call_args
        assert kwargs["transport"].country == "GB"

    @patch("swoop.check_price")
    def test_children_flag_price(self, mock_check):
        mock_check.return_value = PriceResult(price=342, currency="USD", fare_brand="Main Cabin", rpc_calls=1)
        runner = CliRunner()
        result = runner.invoke(main, [
            "price", "JFK", "LAX", "--depart", _FUTURE, "DL2300",
            "--children", "1", "--infants-on-lap", "1", "-q",
        ])
        assert result.exit_code == 0
        _, kwargs = mock_check.call_args
        pax = kwargs["passengers"]
        assert pax.children == 1
        assert pax.infants_on_lap == 1

    @patch("swoop.price_selector")
    def test_country_proxy_with_selector(self, mock_ps):
        mock_ps.return_value = PriceResult(price=342, fare_brand="Main Cabin", rpc_calls=1)
        runner = CliRunner()
        result = runner.invoke(main, [
            "price", "--selector", "sel-1",
            "--country", "DE", "--proxy", "http://proxy:8080", "-q",
        ])
        assert result.exit_code == 0
        from swoop.models import TransportConfig
        mock_ps.assert_called_once_with(
            "sel-1", transport=TransportConfig(timeout=90, retries=2, country="DE", proxy="http://proxy:8080"),
        )

    def test_search_help_shows_new_flags(self):
        runner = CliRunner()
        result = runner.invoke(main, ["search", "--help"])
        assert result.exit_code == 0
        assert "--country" in result.output
        assert "--proxy" in result.output
        assert "--children" in result.output
        assert "--infants-in-seat" in result.output
        assert "--infants-on-lap" in result.output

    def test_price_help_shows_new_flags(self):
        runner = CliRunner()
        result = runner.invoke(main, ["price", "--help"])
        assert result.exit_code == 0
        assert "--country" in result.output
        assert "--proxy" in result.output
        assert "--children" in result.output
        assert "--infants-in-seat" in result.output
        assert "--infants-on-lap" in result.output

    def test_price_help_shows_selector_example(self):
        runner = CliRunner()
        result = runner.invoke(main, ["price", "--help"])
        assert result.exit_code == 0
        assert "Selector syntax" in result.output
        assert "swoop price --selector" in result.output


# ---------------------------------------------------------------------------
# Enriched output tests
# ---------------------------------------------------------------------------


class TestEnrichedOutput:
    @patch("swoop.cli.commands._run_search")
    def test_brief_shows_duration_and_stops(self, mock_search):
        mock_search.return_value = _make_search_result()
        runner = CliRunner()
        result = runner.invoke(main, [
            "search", "JFK", "LAX", _FUTURE, "-o", "brief", "-q",
        ])
        assert result.exit_code == 0
        lines = result.output.strip().split("\n")
        # First line should have duration and Nonstop
        assert "5h 15m" in lines[0]
        assert "Nonstop" in lines[0]

    @patch("swoop.cli.commands._run_search")
    def test_brief_shows_stops_for_connecting(self, mock_search):
        mock_search.return_value = SearchResult(
            results=[_make_trip_option(_make_connecting_itinerary(), index=1)],
        )
        runner = CliRunner()
        result = runner.invoke(main, [
            "search", "JFK", "LAX", _FUTURE, "-o", "brief", "-q",
        ])
        assert result.exit_code == 0
        assert "1 stop" in result.output

    @patch("swoop.cli.commands._run_search")
    def test_csv_has_new_columns(self, mock_search):
        mock_search.return_value = _make_search_result()
        runner = CliRunner()
        result = runner.invoke(main, [
            "search", "JFK", "LAX", _FUTURE, "-o", "csv", "-q",
        ])
        assert result.exit_code == 0
        lines = result.output.strip().split("\n")
        header = lines[0]
        assert "duration_minutes" in header
        assert "stops" in header
        assert "departure_time" in header
        assert "arrival_time" in header
        assert "airlines" in header
        # Data row should have duration value
        assert "315" in lines[1]  # travel_time=315

    @patch("swoop.cli.commands._run_search")
    def test_table_shows_co2_column(self, mock_search):
        itin = _make_itinerary(
            carbon_emissions=CarbonEmissions(
                this_flight_grams=150000,
                typical_for_route_grams=170000,
                difference_percent=-12,
            ),
        )
        option = _make_trip_option(itin, index=1)
        mock_search.return_value = SearchResult(results=[option])
        runner = CliRunner()
        result = runner.invoke(main, [
            "search", "JFK", "LAX", _FUTURE, "-q",
        ])
        assert result.exit_code == 0
        assert "-12%" in result.output

    @patch("swoop.cli.commands._run_search")
    def test_table_co2_absent_shows_dash(self, mock_search):
        mock_search.return_value = _make_search_result(1)
        runner = CliRunner()
        result = runner.invoke(main, [
            "search", "JFK", "LAX", _FUTURE, "-q",
        ])
        assert result.exit_code == 0
        # CO2 column header should be present
        assert "CO2" in result.output

    @patch("swoop.cli.commands._run_search")
    def test_table_shows_legroom_nonstop(self, mock_search):
        """Nonstop flights show legroom in the trip line."""
        mock_search.return_value = _make_search_result(1)
        runner = CliRunner()
        result = runner.invoke(main, [
            "search", "JFK", "LAX", _FUTURE, "-q",
        ])
        assert result.exit_code == 0
        assert "32 inches" in result.output

    @patch("swoop.cli.commands._run_search")
    def test_table_overnight_layover(self, mock_search):
        """Overnight layovers are indicated."""
        f1 = _make_segment(
            departure_airport_code="JFK", arrival_airport_code="ORD",
            departure_time=(22, 0), arrival_time=(23, 45),
        )
        f2 = _make_segment(
            departure_airport_code="ORD", arrival_airport_code="LAX",
            departure_time=(7, 0), arrival_time=(9, 15),
        )
        lay = Layover(
            minutes=435,
            departure_airport_code="ORD",
            departure_airport_name="O'Hare International Airport",
            is_overnight=True,
        )
        itin = Itinerary(
            airline_code="DL",
            airline_names=["Delta Air Lines"],
            segments=[f1, f2],
            layovers=[lay],
            travel_time=675,
            departure_airport_code="JFK",
            arrival_airport_code="LAX",
            departure_date=(2026, 6, 15),
            arrival_date=(2026, 6, 16),
            departure_time=(22, 0),
            arrival_time=(9, 15),
            direct_price=180,
            booking_token="token-ov",
            stop_count=1,
        )
        option = _make_trip_option(itin, index=1)
        mock_search.return_value = SearchResult(results=[option])
        runner = CliRunner()
        result = runner.invoke(main, [
            "search", "JFK", "LAX", _FUTURE, "-q",
        ])
        assert result.exit_code == 0
        assert "overnight" in result.output.lower()

    @patch("swoop.cli.commands._run_search")
    def test_truncation_message_actionable(self, mock_search):
        """Truncated results show actionable guidance."""
        mock_search.return_value = SearchResult(
            results=[_make_trip_option(_make_itinerary(), index=1)],
            is_complete=False,
        )
        runner = CliRunner()
        result = runner.invoke(main, [
            "search", "JFK", "LAX", _FUTURE, "-q",
        ])
        assert result.exit_code == 0
        assert "--max-results" in result.output
        assert "--time-budget" in result.output


# ---------------------------------------------------------------------------
# __main__ entry point
# ---------------------------------------------------------------------------


class TestMainModule:
    def test_python_m_swoop_help(self):
        """python -m swoop --help should work."""
        import subprocess, sys
        result = subprocess.run(
            [sys.executable, "-m", "swoop", "--help"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "search" in result.stdout


# ---------------------------------------------------------------------------
# Past-date warning stream separation (regression guard)
# ---------------------------------------------------------------------------


class TestPastDateWarningStreamSeparation:
    """The past-date warning must land on stderr only; stdout stays a clean,
    parseable payload.

    This is the invariant the dated CLI tests above were silently leaning on.
    When their hardcoded departure dates went stale, swoop correctly printed
    "Warning: <date> is in the past." to stderr — but those tests read
    ``result.output``, which Click 8.3 folds stdout+stderr into, so the warning
    corrupted the JSON/CSV they parsed. The fix made the test dates dynamic;
    this test pins the underlying product contract directly against
    ``result.stdout`` / ``result.stderr`` with a guaranteed-past date, so the
    failure class can't return if someone ever routes the warning to stdout.
    """

    @patch("swoop.cli.commands._run_search")
    def test_search_past_date_warns_on_stderr_only(self, mock_search):
        import json
        mock_search.return_value = _make_search_result()
        past = (_date.today() - _timedelta(days=1)).isoformat()
        result = CliRunner().invoke(main, [
            "search", "JFK", "LAX", past, "-o", "json", "-q",
        ])
        assert result.exit_code == 0
        # stdout is pure JSON — the warning did not leak in.
        data = json.loads(result.stdout)
        assert data["query"]["origin"] == "JFK"
        # the warning fired, and it went to stderr.
        assert "is in the past" in result.stderr
        assert "is in the past" not in result.stdout

    @patch("swoop.check_price")
    def test_price_past_date_warns_on_stderr_only(self, mock_check):
        import json
        mock_check.return_value = PriceResult(price=342, fare_brand="Main Cabin", rpc_calls=1)
        past = (_date.today() - _timedelta(days=1)).isoformat()
        result = CliRunner().invoke(main, [
            "price", "JFK", "LAX", "--depart", past, "DL2300", "-o", "json", "-q",
        ])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["price"] == 342
        assert "is in the past" in result.stderr
        assert "is in the past" not in result.stdout


class TestUpstreamErrorHandling:
    """SwoopUpstreamError is a sibling of SwoopHTTPError/SwoopParseError, so the
    CLI must catch it explicitly — otherwise an upstream outage (which fires
    often under throttling) crashes the command with a raw traceback."""

    @patch("swoop.cli.commands._run_search")
    def test_search_reports_upstream_error_cleanly(self, mock_search):
        mock_search.side_effect = SwoopUpstreamError(
            13, type_url="type.googleapis.com/travel.frontend.flights.ErrorResponse"
        )
        result = CliRunner().invoke(main, ["search", "JFK", "LAX", _FUTURE, "-q"])
        # Handled, not crashed: clean exit code, no leaked exception/traceback.
        assert result.exit_code == 3
        assert not isinstance(result.exception, SwoopUpstreamError)
        assert "gRPC 13" in result.output

    @patch("swoop.check_price")
    def test_price_reports_upstream_error_cleanly(self, mock_price):
        mock_price.side_effect = SwoopUpstreamError(13)
        result = CliRunner().invoke(
            main, ["price", "JFK", "LAX", "--depart", _FUTURE, "DL2300", "-q"]
        )
        assert result.exit_code == 3
        assert not isinstance(result.exception, SwoopUpstreamError)
        assert "upstream error" in result.output.lower()

    @patch("swoop.deals")
    def test_deals_reports_upstream_error_cleanly(self, mock_deals):
        mock_deals.side_effect = SwoopUpstreamError(13)
        result = CliRunner().invoke(main, ["deals", "JFK", "-q"])
        assert result.exit_code == 3
        assert not isinstance(result.exception, SwoopUpstreamError)
        assert "gRPC 13" in result.output

    @patch("swoop.explore")
    def test_explore_reports_upstream_error_cleanly(self, mock_explore):
        mock_explore.side_effect = SwoopUpstreamError(13)
        result = CliRunner().invoke(main, ["explore", "JFK", "-q"])
        assert result.exit_code == 3
        assert not isinstance(result.exception, SwoopUpstreamError)
        assert "gRPC 13" in result.output

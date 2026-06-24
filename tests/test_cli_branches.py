"""Additional CLI branch coverage for validation and error handling."""

from unittest.mock import patch

from click.testing import CliRunner

import swoop
from swoop import PriceResult
from swoop.cli import main
from swoop.cli import commands


class TestSearchCommandBranches:
    def test_run_search_maps_cli_filters_to_swoop_search(self, monkeypatch):
        captured = {}
        sentinel = object()

        def fake_search(origin, destination, date, **kwargs):
            captured["origin"] = origin
            captured["destination"] = destination
            captured["date"] = date
            captured["kwargs"] = kwargs
            return sentinel

        monkeypatch.setattr(swoop, "search", fake_search)

        result = commands._run_search(
            "JFK",
            "LAX",
            "2026-06-15",
            return_date="2026-06-22",
            cabin="business",
            passengers=2,
            children=0,
            infants_in_seat=0,
            infants_on_lap=0,
            sort="duration",
            nonstop=True,
            max_stops=2,
            airline=("DL", "AF"),
            flight_number="DL10",
            include_basic=True,
            depart_after=8,
            depart_before=12,
            arrive_after=10,
            arrive_before=16,
            return_depart_after=9,
            return_depart_before=18,
            timeout=45,
            retries=4,
            country=None,
            proxy=None,
            max_results=None,
            beam_width=None,
            time_budget=None,
        )

        assert result is sentinel
        assert captured["origin"] == "JFK"
        assert captured["destination"] == "LAX"
        assert captured["date"] == "2026-06-15"
        assert captured["kwargs"]["return_date"] == "2026-06-22"
        assert captured["kwargs"]["cabin"] == "business"
        pax = captured["kwargs"]["passengers"]
        assert pax.adults == 2
        assert pax.children == 0
        assert pax.infants_in_seat == 0
        assert pax.infants_on_lap == 0
        assert captured["kwargs"]["sort"] == commands.SORT_MAP["duration"]
        assert captured["kwargs"]["max_stops"] == 0
        assert captured["kwargs"]["airlines"] == ["DL", "AF"]
        assert captured["kwargs"]["flight_number"] == "DL10"
        assert captured["kwargs"]["include_basic_economy"] is True
        assert captured["kwargs"]["earliest_departure"] == 8
        assert captured["kwargs"]["latest_departure"] == 12
        assert captured["kwargs"]["earliest_arrival"] == 10
        assert captured["kwargs"]["latest_arrival"] == 16
        assert captured["kwargs"]["return_earliest_departure"] == 9
        assert captured["kwargs"]["return_latest_departure"] == 18
        transport = captured["kwargs"]["transport"]
        assert transport.timeout == 45
        assert transport.retries == 4
        assert transport.country is None
        assert transport.proxy is None
        assert captured["kwargs"]["max_results"] is None
        assert captured["kwargs"]["beam_width"] is None
        assert captured["kwargs"]["time_budget"] is None

    def test_run_search_legs_builds_search_leg_objects(self, monkeypatch):
        captured = {}
        sentinel = object()

        def fake_search_legs(search_legs, **kwargs):
            captured["search_legs"] = search_legs
            captured["kwargs"] = kwargs
            return sentinel

        monkeypatch.setattr(swoop, "search_legs", fake_search_legs)

        result = commands._run_search_legs(
            [
                ("JFK", "LAX", "2026-06-15"),
                ("LAX", "SFO", "2026-06-18"),
            ],
            cabin="economy",
            passengers=3,
            children=0,
            infants_in_seat=0,
            infants_on_lap=0,
            sort="cheapest",
            nonstop=False,
            max_stops=1,
            airline=("DL",),
            include_basic=False,
            timeout=30,
            retries=5,
            country=None,
            proxy=None,
            max_results=None,
            beam_width=None,
            time_budget=None,
        )

        assert result is sentinel
        assert len(captured["search_legs"]) == 2
        first_leg, second_leg = captured["search_legs"]
        assert first_leg.date == "2026-06-15"
        assert first_leg.from_airports == ["JFK"]
        assert first_leg.to_airports == ["LAX"]
        assert first_leg.max_stops == 1
        assert first_leg.airlines == ["DL"]
        assert second_leg.date == "2026-06-18"
        assert second_leg.from_airports == ["LAX"]
        assert second_leg.to_airports == ["SFO"]
        assert second_leg.max_stops == 1
        assert second_leg.airlines == ["DL"]
        assert captured["kwargs"]["cabin"] == "economy"
        pax = captured["kwargs"]["passengers"]
        assert pax.adults == 3
        assert pax.children == 0
        assert pax.infants_in_seat == 0
        assert pax.infants_on_lap == 0
        assert captured["kwargs"]["sort"] == commands.SORT_MAP["cheapest"]
        assert captured["kwargs"]["include_basic_economy"] is False
        transport = captured["kwargs"]["transport"]
        assert transport.timeout == 30
        assert transport.retries == 5
        assert transport.country is None
        assert transport.proxy is None
        assert captured["kwargs"]["max_results"] is None
        assert captured["kwargs"]["beam_width"] is None
        assert captured["kwargs"]["time_budget"] is None

    def test_build_price_selector_command_shell_quotes_input(self):
        selector = "sel'ector with space"
        command = commands._build_price_selector_command(selector)
        assert command == "swoop price --selector 'sel'\"'\"'ector with space'"

    def test_rejects_positional_and_leg_together(self):
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "search",
                "JFK",
                "LAX",
                "2026-06-15",
                "--leg",
                "JFK",
                "LAX",
                "2026-06-15",
            ],
        )
        assert result.exit_code == 2
        assert "positional args and --leg cannot be used together" in result.stderr

    def test_requires_positional_triplet_or_leg_mode(self):
        runner = CliRunner()
        result = runner.invoke(main, ["search"])
        assert result.exit_code == 2
        assert "provide ORIGIN DESTINATION DATE or use --leg" in result.stderr

    def test_rejects_leg_with_return(self):
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "search",
                "--leg",
                "JFK",
                "LAX",
                "2026-06-15",
                "--return",
                "2026-06-22",
            ],
        )
        assert result.exit_code == 2
        assert "--leg cannot be combined with --return" in result.stderr

    def test_rejects_leg_with_flight_filter(self):
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "search",
                "--leg",
                "JFK",
                "LAX",
                "2026-06-15",
                "--flight",
                "DL2300",
            ],
        )
        assert result.exit_code == 2
        assert "--leg cannot be combined with --flight" in result.stderr

    def test_rejects_leg_with_time_windows(self):
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "search",
                "--leg",
                "JFK",
                "LAX",
                "2026-06-15",
                "--depart-after",
                "8",
            ],
        )
        assert result.exit_code == 2
        assert "time-window filters are not supported with --leg searches" in result.stderr

    def test_requires_complete_positional_triplet(self):
        runner = CliRunner()
        result = runner.invoke(main, ["search", "JFK", "LAX"])
        assert result.exit_code == 2
        assert "ORIGIN DESTINATION DATE are all required" in result.stderr


class TestPriceCommandBranches:
    def test_selector_is_mutually_exclusive_with_shorthand(self):
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "price",
                "JFK",
                "LAX",
                "--depart",
                "2026-06-15",
                "DL2300",
                "--selector",
                "selector-1",
            ],
        )
        assert result.exit_code == 2
        assert "mutually exclusive" in result.stderr

    def test_selector_rejects_pricing_overrides(self):
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "price",
                "--selector",
                "selector-1",
                "--cabin",
                "business",
            ],
        )
        assert result.exit_code == 2
        assert "self-contained and cannot be combined with pricing" in result.stderr.replace("\n", " ")

    def test_leg_pricing_rejects_max_stops(self):
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "price",
                "--leg",
                "JFK",
                "LAX",
                "2026-06-15",
                "DL2300",
                "--max-stops",
                "1",
            ],
        )
        assert result.exit_code == 2
        assert "--max-stops is not supported with explicit --leg pricing" in result.stderr

    def test_price_requires_selector_shorthand_or_leg(self):
        runner = CliRunner()
        result = runner.invoke(main, ["price"])
        assert result.exit_code == 2
        assert "provide ORIGIN DEST with --depart, or use --leg/--selector" in result.stderr

    @patch("swoop.price_selector")
    def test_selector_not_found_message_is_specific(self, mock_price_selector):
        mock_price_selector.return_value = None
        runner = CliRunner()
        result = runner.invoke(main, ["price", "--selector", "selector-1", "-q"])
        assert result.exit_code == 1
        assert "Selected itinerary no longer exists" in result.stderr

    @patch("swoop.price_selector")
    def test_selector_mode_renders_table_for_found_result(self, mock_price_selector):
        mock_price_selector.return_value = PriceResult(price=342, currency="USD", fare_brand="Main Cabin", rpc_calls=1)
        runner = CliRunner()
        result = runner.invoke(main, ["price", "--selector", "selector-1", "-q"])
        assert result.exit_code == 0
        assert "$342" in result.output

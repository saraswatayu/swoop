"""Tests for Google Flights Explore support."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from swoop import ExploreDestination, ExploreResult, explore
from swoop._explore import (
    _build_explore_payload,
    _encode_explore_f_req,
    _parse_explore_response,
    parse_explore_payload,
)

FIXTURE_DIR = (
    Path(__file__).parent.parent
    / "tests"
    / "fixtures"
    / "responses"
    / "explore"
)


def _load_response_text(name: str) -> str:
    return (FIXTURE_DIR / name).read_text()


class TestBuildExplorePayload:
    def test_contains_origin_in_outbound_and_return_segments(self):
        payload = _build_explore_payload("JFK")
        segments = payload[3][13]
        assert segments[0][0] == [[[["JFK", 0]]]][0]
        assert segments[1][1] == [[[["JFK", 0]]]][0]

    def test_passengers_cabin_and_stops(self):
        from swoop import Passengers

        payload = _build_explore_payload(
            "LAX",
            cabin="business",
            passengers=Passengers(adults=2, children=1),
            max_stops=0,
        )
        filters = payload[3]
        assert filters[5] == 3
        assert filters[6] == [2, 1, 0, 0]
        assert filters[13][0][3] == 1
        assert filters[13][1][3] == 1

    def test_encoded_body_has_trailing_ampersand(self):
        body = _encode_explore_f_req(_build_explore_payload("SFO"))
        assert body.startswith(b"f.req=")
        assert body.endswith(b"&")


class TestParseExplore:
    def test_parses_browser_capture(self):
        inner = _parse_explore_response(_load_response_text("jfk_response.txt"))
        result = parse_explore_payload(inner, origin="JFK")

        assert isinstance(result, ExploreResult)
        assert result.origin == "JFK"
        assert result.origin_name == "New York"
        assert len(result.destinations) == 85

        first = result.destinations[0]
        assert isinstance(first, ExploreDestination)
        assert first.place_id == "/m/0d6lp"
        assert first.name == "San Francisco"
        assert first.airport_code == "SFO"
        assert first.country == "United States"
        assert first.latitude == pytest.approx(37.7749295)
        assert first.longitude == pytest.approx(-122.4194155)
        assert first.departure_date == "2026-08-20"
        assert first.return_date == "2026-08-28"
        assert first.image_url

    def test_parses_length_prefixed_response(self):
        inner = _parse_explore_response(_load_response_text("sfo_response.txt"))
        result = parse_explore_payload(inner, origin="SFO")
        assert result.origin == "SFO"
        assert len(result.destinations) == 86
        assert result.destinations[0].name == "Los Angeles"

    def test_error_envelope_raises_parse_error(self):
        from swoop import SwoopParseError

        with pytest.raises(SwoopParseError, match="error envelope"):
            _parse_explore_response(_load_response_text("error_response.txt"))


@pytest.fixture
def fake_primp_explore(monkeypatch):
    """Patch primp.Client for Explore tests."""
    import swoop.rpc as _rpc

    def _install(post_text: str, post_status: int = 200):
        calls = {"get": [], "post": []}

        class FakeClient:
            def __init__(self, **_kw):
                pass

            def get(self, *args, **kwargs):
                calls["get"].append((args, kwargs))
                return MagicMock(status_code=200, text="<html></html>")

            def post(self, *args, **kwargs):
                calls["post"].append((args, kwargs))
                return MagicMock(status_code=post_status, text=post_text)

        _rpc._clients.clear()
        monkeypatch.setitem(sys.modules, "primp", types.SimpleNamespace(Client=FakeClient))
        return calls

    return _install


class TestFetchExplore:
    def test_public_explore_with_mocked_transport(self, fake_primp_explore):
        calls = fake_primp_explore(_load_response_text("lax_response.txt"))

        result = explore("LAX")

        assert isinstance(result, ExploreResult)
        assert result.origin == "LAX"
        assert len(result.destinations) == 88
        assert result.destinations[0].name == "San Francisco"
        assert calls["get"]
        assert calls["post"]
        post_body = calls["post"][0][1]["content"]
        assert b"LAX" in post_body

    def test_invalid_origin_raises(self):
        with pytest.raises(ValueError, match="origin"):
            explore("xx")

    def test_invalid_cabin_raises(self, fake_primp_explore):
        fake_primp_explore("")
        with pytest.raises(ValueError, match="cabin"):
            explore("JFK", cabin="ultra")


class TestExploreCLI:
    def test_explore_help(self):
        from swoop.cli.commands import explore_cmd

        runner = CliRunner()
        result = runner.invoke(explore_cmd, ["--help"])
        assert result.exit_code == 0
        assert "explore" in result.output.lower() or "ORIGIN" in result.output

    def test_explore_json(self, fake_primp_explore):
        from swoop.cli.commands import explore_cmd

        fake_primp_explore(_load_response_text("lax_response.txt"))
        runner = CliRunner()
        result = runner.invoke(explore_cmd, ["LAX", "-o", "json", "-q"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "destinations" in data
        assert len(data["destinations"]) == 88
        assert data["destinations"][0]["name"] == "San Francisco"

    def test_explore_csv(self, fake_primp_explore):
        from swoop.cli.commands import explore_cmd

        fake_primp_explore(_load_response_text("lax_response.txt"))
        runner = CliRunner()
        result = runner.invoke(explore_cmd, ["LAX", "-o", "csv", "-q"])
        assert result.exit_code == 0
        lines = result.output.strip().split("\n")
        assert len(lines) == 89  # header + 88 destinations

    def test_explore_brief_limit(self, fake_primp_explore):
        from swoop.cli.commands import explore_cmd

        fake_primp_explore(_load_response_text("lax_response.txt"))
        runner = CliRunner()
        result = runner.invoke(explore_cmd, ["LAX", "-o", "brief", "-q", "-l", "5"])
        assert result.exit_code == 0
        lines = result.output.strip().split("\n")
        assert len(lines) == 5

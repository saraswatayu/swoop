"""Offline tests for the explore() endpoint."""

from dataclasses import replace
from pathlib import Path

import pytest

from swoop.models import ExploreDestination, Passengers

FIX = Path(__file__).parent / "fixtures" / "responses" / "explore"

_BASE = ExploreDestination(
    origin="JFK",
    destination="SFO",
    destination_name="San Francisco",
    destination_country="United States",
    place_id="/m/0d6lp",
    departure_date="2026-07-02",
    return_date="2026-07-10",
)


def _dest(**kw) -> ExploreDestination:
    return replace(_BASE, **kw)


class TestToSearchKwargs:
    def test_roundtrip_kwargs(self):
        assert _dest().to_search_kwargs() == {
            "origin": "JFK",
            "destination": "SFO",
            "date": "2026-07-02",
            "return_date": "2026-07-10",
        }

    def test_oneway_omits_return(self):
        kw = _dest(return_date=None).to_search_kwargs()
        assert "return_date" not in kw
        assert kw["date"] == "2026-07-02"

    def test_carries_query_context(self):
        kw = _dest(query_cabin="business", query_adults=2).to_search_kwargs()
        assert kw["cabin"] == "business"
        assert kw["passengers"] == Passengers(adults=2)

    def test_missing_destination_raises(self):
        with pytest.raises(ValueError):
            _dest(destination=None).to_search_kwargs()


class TestBuildPayload:
    def test_roundtrip_has_two_segments(self):
        from swoop._explore import _build_explore_payload
        assert len(_build_explore_payload("JFK", one_way=False)[3][13]) == 2

    def test_oneway_has_one_segment(self):
        from swoop._explore import _build_explore_payload
        assert len(_build_explore_payload("JFK", one_way=True)[3][13]) == 1

    def test_trip_type_flag(self):
        from swoop._explore import _build_explore_payload
        assert _build_explore_payload("JFK", one_way=False)[3][2] == 1
        assert _build_explore_payload("JFK", one_way=True)[3][2] == 2

    def test_origin_block_shape(self):
        from swoop._explore import _build_explore_payload
        # filters[13][0][0] is the origin block: [[[code, flag]]]
        assert _build_explore_payload("JFK")[3][13][0][0] == [[["JFK", 0]]]

    def test_cabin_and_stops(self):
        from swoop._explore import _build_explore_payload
        pl = _build_explore_payload("JFK", cabin="business", max_stops=0)
        assert pl[3][5] == 3              # business cabin code
        assert pl[3][13][0][3] == 1       # max_stops=0 -> stops_val 1 (nonstop)

    def test_encoded_body_trailing_ampersand(self):
        from swoop._explore import _build_explore_payload, _encode_explore_f_req
        body = _encode_explore_f_req(_build_explore_payload("JFK"))
        assert body.startswith(b"f.req=")
        assert body.endswith(b"&")


class TestParse:
    def test_parses_jfk_fixture(self):
        from swoop._explore import _extract_inner, parse_explore_payload
        inner = _extract_inner((FIX / "jfk_response.txt").read_text())
        result = parse_explore_payload(inner, origin="JFK")
        assert result.origin == "JFK"
        assert len(result.destinations) > 0
        d = result.destinations[0]
        assert d.destination_name
        assert d.place_id.startswith("/m/")
        assert d.origin == "JFK"
        assert d.query_cabin == "economy" and d.query_adults == 1

    def test_error_fixture_raises(self):
        from swoop._explore import _extract_inner
        from swoop.exceptions import SwoopParseError
        with pytest.raises(SwoopParseError):
            _extract_inner((FIX / "error_response.txt").read_text())


class TestFetchExplore:
    def test_end_to_end_mocked(self, monkeypatch):
        from swoop import _explore

        page_html = 'x"cfb2h":"BL123"y"FdrFJe":"SID123"z'
        body_text = (FIX / "jfk_response.txt").read_text()
        captured: dict = {}

        class FakeRes:
            def __init__(self, text, status=200):
                self.text = text
                self.status_code = status

        class FakeClient:
            def get(self, url, **kw):
                return FakeRes(page_html)

            def post(self, url, content=None, **kw):
                captured["url"] = url
                captured["body"] = content
                return FakeRes(body_text)

        monkeypatch.setattr(_explore, "_get_client", lambda *a, **k: FakeClient())
        result = _explore.fetch_explore("JFK")
        assert result.origin == "JFK"
        assert len(result.destinations) > 0
        assert b"f.req=" in captured["body"]
        assert "bl=BL123" in captured["url"]


class TestPublicExplore:
    def test_invalid_origin_raises(self):
        import swoop
        with pytest.raises(ValueError):
            swoop.explore("xx")

    def test_invalid_cabin_raises(self):
        import swoop
        with pytest.raises(ValueError):
            swoop.explore("JFK", cabin="ultra")  # type: ignore[arg-type]

    def test_invalid_max_stops_raises(self):
        import swoop
        with pytest.raises(ValueError):
            swoop.explore("JFK", max_stops=9)


class TestPriceExplore:
    def _fake_option(self, price, selector):
        return type("Opt", (), {"price": price, "selector": selector})()

    def test_prices_cheapest(self, monkeypatch):
        import swoop
        from swoop.models import SearchResult

        fake_result = SearchResult(results=[
            self._fake_option(400, "sel-expensive"),
            self._fake_option(250, "sel-cheapest"),
        ])
        captured: dict = {}
        monkeypatch.setattr(swoop, "search", lambda **kw: fake_result)
        monkeypatch.setattr(swoop, "price_selector", lambda sel, **kw: captured.setdefault("sel", sel))
        swoop.price_explore(_dest())
        assert captured["sel"] == "sel-cheapest"

    def test_no_results_returns_none(self, monkeypatch):
        import swoop
        from swoop.models import SearchResult

        monkeypatch.setattr(swoop, "search", lambda **kw: SearchResult(results=[]))
        assert swoop.price_explore(_dest()) is None

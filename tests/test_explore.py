"""Offline tests for the explore() endpoint."""

import json
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

    def test_missing_departure_date_raises(self):
        # A dateless destination must fail loudly here, not crash deep in
        # search()'s date validation.
        with pytest.raises(ValueError, match="departure date"):
            _dest(departure_date=None).to_search_kwargs()


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

    def test_passenger_count_wired_into_payload(self):
        from swoop._explore import _build_explore_payload
        # Default party is a single adult.
        assert _build_explore_payload("JFK")[3][6] == [1, 0, 0, 0]
        # A multi-passenger request must reach the RPC, not be hardcoded.
        pl = _build_explore_payload(
            "JFK", passengers=Passengers(adults=2, children=1, infants_on_lap=1)
        )
        assert pl[3][6] == [2, 1, 0, 1]


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

    def test_extract_inner_prefers_destination_chunk(self):
        from swoop._explore import _extract_inner, parse_explore_payload
        # First data line is garbled, second is a wrb.fr with an empty
        # destination list, third carries the real destinations. Robust
        # parsing must skip the first two and find the third.
        empty_inner = json.dumps([None, None, None, [[]]])
        row: list = [None] * 16
        row[0], row[2], row[11], row[12], row[15] = "/m/x", "Test", "2026-07-02", "2026-07-10", "SFO"
        real_inner = json.dumps([None, None, None, [[row]]])
        text = (
            ")]}'\n\n"
            "5\n[[garbled\n"
            f'99\n[["wrb.fr",null,{json.dumps(empty_inner)}]]\n'
            f'99\n[["wrb.fr",null,{json.dumps(real_inner)}]]\n'
        )
        result = parse_explore_payload(_extract_inner(text), origin="JFK")
        assert [d.destination for d in result.destinations] == ["SFO"]

    def test_extract_inner_detects_mixed_case_consent(self):
        from swoop._explore import _extract_inner
        from swoop.exceptions import SwoopParseError
        # A mixed-case consent host must still be flagged as a block.
        with pytest.raises(SwoopParseError, match="consent page"):
            _extract_inner(")]}'\n\nredirecting to Consent.Google.com now")

    def test_oneway_drops_return_date_even_when_present(self):
        from swoop._explore import _parse_destination
        row: list = [None] * 18
        row[0], row[2] = "/m/x", "Test City"
        row[11], row[12], row[15] = "2026-07-02", "2026-07-10", "SFO"
        # Roundtrip keeps [12]; one-way must null it regardless of the server.
        rt = _parse_destination(row, origin="JFK", cabin="economy", adults=1, one_way=False)
        ow = _parse_destination(row, origin="JFK", cabin="economy", adults=1, one_way=True)
        assert rt is not None and rt.return_date == "2026-07-10"
        assert ow is not None and ow.return_date is None


class TestFetchExplore:
    def test_end_to_end_mocked(self, monkeypatch):
        from swoop import _explore
        _explore._browser_params_cache.clear()

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

    def test_browser_params_cached_across_calls(self, monkeypatch):
        from swoop import _explore
        _explore._browser_params_cache.clear()

        page_html = 'x"cfb2h":"BL123"y"FdrFJe":"SID123"z'
        body_text = (FIX / "jfk_response.txt").read_text()
        gets = {"n": 0}

        class FakeRes:
            def __init__(self, text, status=200):
                self.text, self.status_code = text, status

        class FakeClient:
            def get(self, url, **kw):
                gets["n"] += 1
                return FakeRes(page_html)

            def post(self, url, content=None, **kw):
                return FakeRes(body_text)

        monkeypatch.setattr(_explore, "_get_client", lambda *a, **k: FakeClient())
        _explore.fetch_explore("JFK")
        _explore.fetch_explore("LAX")
        # bl/f.sid page fetched once, then reused on the second call.
        assert gets["n"] == 1

    def _stale_param_client(self, state, *, bad):
        """A FakeClient whose POST rejects stale (bl=STALE) params via `bad`."""
        page_html = 'x"cfb2h":"BL123"y"FdrFJe":"SID123"z'
        good = (FIX / "jfk_response.txt").read_text()

        class FakeRes:
            def __init__(self, text, status=200):
                self.text, self.status_code = text, status

        class FakeClient:
            def get(self, url, **kw):
                state["gets"] += 1
                return FakeRes(page_html)

            def post(self, url, content=None, **kw):
                state["posts"] += 1
                if "bl=STALE" in url:
                    return bad
                return FakeRes(good)

        return FakeClient()

    def test_stale_cached_params_self_heal_on_parse_error(self, monkeypatch):
        from swoop import _explore
        _explore._browser_params_cache.clear()
        _explore._browser_params_cache["||"] = {"bl": "STALE"}
        state = {"gets": 0, "posts": 0}

        class FakeRes:
            def __init__(self, text, status=200):
                self.text, self.status_code = text, status

        client = self._stale_param_client(state, bad=FakeRes("garbage-not-json"))
        monkeypatch.setattr(_explore, "_get_client", lambda *a, **k: client)
        result = _explore.fetch_explore("JFK")
        assert len(result.destinations) > 0
        # Only the refresh fetched the page; the cache now holds fresh params.
        assert state["gets"] == 1 and state["posts"] == 2
        assert _explore._browser_params_cache["||"] == {"bl": "BL123", "f.sid": "SID123"}

    def test_stale_cached_params_self_heal_on_http_error(self, monkeypatch):
        # The poisoning case: a 4xx (not a parse error) must still invalidate
        # the cache and recover, instead of leaving the bad params cached.
        from swoop import _explore
        _explore._browser_params_cache.clear()
        _explore._browser_params_cache["||"] = {"bl": "STALE"}
        state = {"gets": 0, "posts": 0}

        class FakeRes:
            def __init__(self, text, status=200):
                self.text, self.status_code = text, status

        client = self._stale_param_client(state, bad=FakeRes("", status=400))
        monkeypatch.setattr(_explore, "_get_client", lambda *a, **k: client)
        result = _explore.fetch_explore("JFK")
        assert len(result.destinations) > 0
        assert _explore._browser_params_cache["||"]["bl"] == "BL123"

    def test_fresh_params_failure_propagates_without_retry(self, monkeypatch):
        from swoop import _explore
        from swoop.exceptions import SwoopParseError
        _explore._browser_params_cache.clear()  # cold cache -> attempt uses fresh params
        page_html = 'x"cfb2h":"BL123"y"FdrFJe":"SID123"z'
        state = {"gets": 0, "posts": 0}

        class FakeRes:
            def __init__(self, text, status=200):
                self.text, self.status_code = text, status

        class FakeClient:
            def get(self, url, **kw):
                state["gets"] += 1
                return FakeRes(page_html)

            def post(self, url, content=None, **kw):
                state["posts"] += 1
                return FakeRes("garbage-not-json")

        monkeypatch.setattr(_explore, "_get_client", lambda *a, **k: FakeClient())
        with pytest.raises(SwoopParseError):
            _explore.fetch_explore("JFK")
        # Params were already fresh, so no pointless second page fetch/POST.
        assert state["gets"] == 1 and state["posts"] == 1

    def test_empty_params_not_cached(self):
        # A page with neither token must not poison the cache: caching {} and
        # serving it forever (the `if cached is not None` bug) would send every
        # later request session-less.
        from swoop import _explore
        from swoop.models import TransportConfig

        class FakeRes:
            def __init__(self, text):
                self.text, self.status_code = text, 200

        class FakeClient:
            def get(self, url, **kw):
                return FakeRes("<page with no session tokens>")

        params, from_cache = _explore._get_browser_params(
            FakeClient(), "http://x", key="EMPTY", transport=TransportConfig()
        )
        assert params == {} and from_cache is False
        assert "EMPTY" not in _explore._browser_params_cache
        # A second call still fetches — the empty result was never cached.
        _, from_cache2 = _explore._get_browser_params(
            FakeClient(), "http://x", key="EMPTY", transport=TransportConfig()
        )
        assert from_cache2 is False

    def test_partial_params_not_cached(self):
        # Only bl, no f.sid — incomplete params must not be cached either.
        from swoop import _explore
        from swoop.models import TransportConfig

        class FakeRes:
            def __init__(self, text):
                self.text, self.status_code = text, 200

        class FakeClient:
            def get(self, url, **kw):
                return FakeRes('boot with "cfb2h":"BL999" but no f.sid token')

        params, from_cache = _explore._get_browser_params(
            FakeClient(), "http://x", key="PARTIAL", transport=TransportConfig()
        )
        assert params == {"bl": "BL999"} and from_cache is False
        assert "PARTIAL" not in _explore._browser_params_cache

    def test_block_response_not_retried(self, monkeypatch):
        # A consent/HTML interstitial is a hard block, not a stale session, so
        # fetch_explore must NOT burn a second page GET + POST trying to "heal".
        from swoop import _explore
        from swoop.exceptions import SwoopParseError
        _explore._browser_params_cache.clear()
        _explore._browser_params_cache["||"] = {"bl": "BL123", "f.sid": "SID123"}
        state = {"gets": 0, "posts": 0}

        class FakeRes:
            def __init__(self, text, status=200):
                self.text, self.status_code = text, status

        class FakeClient:
            def get(self, url, **kw):
                state["gets"] += 1
                return FakeRes('x"cfb2h":"BL123"y"FdrFJe":"SID123"z')

            def post(self, url, content=None, **kw):
                state["posts"] += 1
                return FakeRes("<!doctype html><html>consent.google.com</html>")

        monkeypatch.setattr(_explore, "_get_client", lambda *a, **k: FakeClient())
        with pytest.raises(SwoopParseError):
            _explore.fetch_explore("JFK")
        # Warm cache used (no GET); the block short-circuits the retry.
        assert state["gets"] == 0 and state["posts"] == 1

    def test_concurrent_calls_single_flight(self, monkeypatch):
        # Concurrent cold callers for the same key must trigger exactly one page
        # GET — the per-key lock makes the fetch single-flight.
        import threading
        import time
        from swoop import _explore
        _explore._browser_params_cache.clear()
        body_text = (FIX / "jfk_response.txt").read_text()
        page_html = 'x"cfb2h":"BL123"y"FdrFJe":"SID123"z'
        state = {"gets": 0}
        glock = threading.Lock()

        class FakeRes:
            def __init__(self, text):
                self.text, self.status_code = text, 200

        class FakeClient:
            def get(self, url, **kw):
                with glock:
                    state["gets"] += 1
                time.sleep(0.05)  # widen the contention window
                return FakeRes(page_html)

            def post(self, url, content=None, **kw):
                return FakeRes(body_text)

        monkeypatch.setattr(_explore, "_get_client", lambda *a, **k: FakeClient())
        threads = [threading.Thread(target=lambda: _explore.fetch_explore("JFK")) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert state["gets"] == 1


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

    def test_one_way_with_trip_length_raises(self):
        import swoop
        with pytest.raises(ValueError, match="roundtrip-only"):
            swoop.explore("JFK", one_way=True, trip_length=(5, 10))

    def test_filters_applied_to_result(self, monkeypatch):
        import swoop
        from swoop import _explore
        from swoop.models import ExploreResult

        full = ExploreResult(origin="JFK", destinations=[
            _dest(destination="SFO"), _dest(destination="LAX"),
        ])
        monkeypatch.setattr(_explore, "fetch_explore", lambda *a, **k: full)
        result = swoop.explore("JFK", destinations=["sfo"])
        assert [d.destination for d in result.destinations] == ["SFO"]


class TestExploreFilter:
    def _dests(self):
        return [
            _dest(destination="SFO", departure_date="2026-07-02", return_date="2026-07-10"),  # 8 nights
            _dest(destination="LAX", departure_date="2026-07-02", return_date="2026-07-05"),  # 3 nights
            _dest(destination="ORD", departure_date="2026-07-02", return_date=None),          # no return
        ]

    def test_whitelist_case_insensitive(self):
        from swoop._explore_filter import filter_explore
        out = filter_explore(self._dests(), destinations=["sfo", "lax"])
        assert {d.destination for d in out} == {"SFO", "LAX"}

    def test_blacklist(self):
        from swoop._explore_filter import filter_explore
        out = filter_explore(self._dests(), exclude_destinations=["SFO"])
        assert "SFO" not in {d.destination for d in out}

    def test_trip_length_keeps_in_range_and_drops_dateless(self):
        from swoop._explore_filter import filter_explore
        out = filter_explore(self._dests(), trip_length=(5, 10))
        # SFO (8 nights) kept; LAX (3 nights) and ORD (no return date) dropped.
        assert {d.destination for d in out} == {"SFO"}


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


class TestPriceExploreAll:
    def test_empty_returns_empty(self):
        import swoop
        assert swoop.price_explore_all([]) == []

    def test_preserves_order_and_skips_unpriceable(self, monkeypatch):
        import swoop

        # Price by destination code to assert ordering; the dateless ORD entry
        # raises ValueError in to_search_kwargs and must become None.
        def fake_price_explore(dest, **kw):
            if dest.departure_date is None:
                dest.to_search_kwargs()  # raises ValueError
            return f"price-{dest.destination}"

        monkeypatch.setattr(swoop, "price_explore", fake_price_explore)
        dests = [
            _dest(destination="SFO"),
            _dest(destination="ORD", departure_date=None),
            _dest(destination="LAX"),
        ]
        assert swoop.price_explore_all(dests) == ["price-SFO", None, "price-LAX"]

    def test_transport_error_on_one_does_not_sink_batch(self, monkeypatch):
        import swoop
        from swoop.exceptions import SwoopHTTPError

        # A transport failure on ONE destination must not discard the prices
        # already computed for the others.
        def fake(dest, **kw):
            if dest.destination == "ERR":
                raise SwoopHTTPError(503)
            return f"price-{dest.destination}"

        monkeypatch.setattr(swoop, "price_explore", fake)
        dests = [_dest(destination="SFO"), _dest(destination="ERR"), _dest(destination="LAX")]
        assert swoop.price_explore_all(dests) == ["price-SFO", None, "price-LAX"]

    def test_rate_limit_stops_dispatch_and_propagates(self, monkeypatch):
        import swoop
        from swoop.exceptions import SwoopRateLimitError

        # A 429 is batch-wide: it must propagate (so the caller backs off) and
        # stop further dispatch instead of hammering the limiter with the rest.
        calls = {"n": 0}

        def fake(dest, **kw):
            calls["n"] += 1
            if dest.destination == "RL":
                raise SwoopRateLimitError()
            return f"price-{dest.destination}"

        monkeypatch.setattr(swoop, "price_explore", fake)
        dests = [_dest(destination="RL")] + [_dest(destination=f"D{i:02d}") for i in range(20)]
        with pytest.raises(SwoopRateLimitError):
            # One worker => strictly sequential, so the first item's 429
            # short-circuits all 20 that follow.
            swoop.price_explore_all(dests, max_workers=1)
        assert calls["n"] == 1


class TestExploreCLI:
    def _run(self, args):
        from click.testing import CliRunner
        from swoop.cli import main
        return CliRunner().invoke(main, args)

    def test_help(self):
        out = self._run(["explore", "--help"])
        assert out.exit_code == 0
        assert "explore" in out.output.lower()
        assert "--one-way" in out.output

    def test_bad_iata_rich_error(self):
        out = self._run(["explore", "xx"])
        assert out.exit_code == 2
        # The rich IATA error (via the Click IATA type), not deals' weak one.
        assert "3 uppercase letters" in out.output

    def test_trip_length_with_one_way_rejected(self):
        # Must fail fast with a clear message, not run the query and then
        # report "No destinations found" after filtering everything out.
        out = self._run(["explore", "JFK", "--one-way", "--trip-length", "5-10"])
        assert out.exit_code == 2
        assert "roundtrip-only" in out.output

    def test_json_output(self, monkeypatch):
        import swoop
        from swoop.models import ExploreResult, ExploreDestination

        monkeypatch.setattr(swoop, "explore", lambda *a, **k: ExploreResult(
            origin="JFK",
            destinations=[ExploreDestination(
                origin="JFK", destination="SFO", destination_name="San Francisco",
                destination_country="United States", place_id="/m/0d6lp",
                departure_date="2026-07-02", return_date="2026-07-10",
            )],
        ))
        out = self._run(["explore", "JFK", "-o", "json", "-q"])
        assert out.exit_code == 0
        assert '"destination": "SFO"' in out.output
        assert '"total_destinations": 1' in out.output

    def test_brief_limit(self, monkeypatch):
        import swoop
        from swoop.models import ExploreResult, ExploreDestination

        dests = [
            ExploreDestination(
                origin="JFK", destination=f"D{i:02d}", destination_name=f"City {i}",
                destination_country="US", place_id=f"/m/{i}", departure_date="2026-07-02",
            )
            for i in range(8)
        ]
        monkeypatch.setattr(swoop, "explore", lambda *a, **k: ExploreResult(origin="JFK", destinations=dests))
        out = self._run(["explore", "JFK", "-o", "brief", "-q", "-l", "3"])
        assert out.exit_code == 0
        assert len([ln for ln in out.output.splitlines() if ln.strip()]) == 3

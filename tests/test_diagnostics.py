"""Measure actual fake HTTP dispatch, retries, errors and concurrent requests."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, asdict, replace
import json
from threading import Barrier, Event

import pytest

import swoop
import swoop.diagnostics as diagnostics
import swoop.rpc as rpc
from swoop.exceptions import SwoopHTTPError, SwoopParseError, SwoopRateLimitError
from tests.factories import FakeHTTPResponse, encode_rpc_outer, make_full_response, make_itinerary_element, make_flight_segment


def payload():
    return encode_rpc_outer(make_full_response(best_itins=[make_itinerary_element([make_flight_segment()])]))


class Client:
    def __init__(self, *responses, clock=None):
        self.responses = iter(responses)
        self.calls = 0
        self.clock = clock

    def post(self, *_args, **_kwargs):
        self.calls += 1
        if self.clock is not None:
            self.clock[0] += 0.4
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def search(recorder, *, origin="JFK", destination="LAX", proxy=None, retries=0):
    return swoop.search(origin, destination, "2026-11-15",
        transport=swoop.TransportConfig(diagnostics=recorder, proxy=proxy, retries=retries))


def test_counts_actual_retry_dispatch_and_timings_without_changing_logical_rpc_calls(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(diagnostics, "perf_counter", lambda: clock[0])
    monkeypatch.setattr("time.sleep", lambda delay: clock.__setitem__(0, clock[0] + delay))
    monkeypatch.setattr("random.uniform", lambda *_args: 0)
    original = rpc.decode_result

    def decode(data):
        result = original(data)
        clock[0] += 0.02
        return result

    monkeypatch.setattr(rpc, "decode_result", decode)
    body = payload()
    client = Client(FakeHTTPResponse(429, "unread"), FakeHTTPResponse(200, body), clock=clock)
    monkeypatch.setattr(rpc, "_get_client", lambda *_args: client)
    recorder = swoop.DiagnosticsRecorder()
    result = search(recorder, retries=1)
    snapshot = recorder.snapshot()
    if hasattr(result, "rpc_calls"):
        assert result.rpc_calls == 1
    assert snapshot.http_attempts == snapshot.rpc_attempts == client.calls == 2
    assert snapshot.page_attempts == 0 and snapshot.retry_attempts == 1
    assert [attempt.status_code for attempt in snapshot.attempts] == [429, 200]
    assert snapshot.network_ms == pytest.approx(800)
    assert snapshot.decode_ms == pytest.approx(20)
    assert snapshot.retry_wait_ms == pytest.approx(1000)
    assert snapshot.elapsed_ms == pytest.approx(1820)
    assert snapshot.response_bytes == len(body.encode("utf-8"))
    assert snapshot.responses_with_unknown_bytes == 1
    query = snapshot.queries[0]
    assert query.bounds == (swoop.QueryBounds(("JFK",), ("LAX",), "2026-11-15"),)
    assert query.decoded_result_count == 1 and query.outcome == "success"
    assert query.decode_ms == pytest.approx(20)


@pytest.mark.parametrize("response,error_type", [
    (FakeHTTPResponse(500, "not stored"), SwoopHTTPError),
    (FakeHTTPResponse(200, "not JSON"), SwoopParseError),
    (FakeHTTPResponse(429, "not stored"), SwoopRateLimitError),
])
def test_failures_keep_typed_error_fields_and_request_diagnostics(monkeypatch, response, error_type):
    client = Client(response)
    monkeypatch.setattr(rpc, "_get_client", lambda *_args: client)
    recorder = swoop.DiagnosticsRecorder()
    with pytest.raises(error_type) as raised:
        search(recorder)
    assert raised.value.diagnostics == recorder.snapshot()
    assert raised.value.diagnostics.http_attempts == client.calls == 1
    assert raised.value.diagnostics.queries[0].outcome == "error"
    assert raised.value.diagnostics.queries[0].error_type == error_type.__name__
    if isinstance(raised.value, SwoopHTTPError):
        assert raised.value.status_code == response.status_code


def test_deferred_response_body_failure_is_measured_and_not_an_empty_result(monkeypatch):
    class DeferredBody:
        status_code = 200

        @property
        def text(self):
            raise TimeoutError("secret message must not appear in diagnostics")

    client = Client(DeferredBody())
    monkeypatch.setattr(rpc, "_get_client", lambda *_args: client)
    recorder = swoop.DiagnosticsRecorder()
    with pytest.raises(Exception):
        search(recorder)
    snapshot = recorder.snapshot()
    assert snapshot.http_attempts == client.calls == 1
    assert snapshot.attempts[0].status_code == 200
    assert snapshot.attempts[0].error_type == "TimeoutError"
    assert snapshot.responses_with_unknown_bytes == 1
    assert snapshot.queries[0].outcome == "error"
    assert "secret" not in json.dumps(asdict(snapshot))


def test_same_request_recorder_retains_failed_proxy_attempt_before_success(monkeypatch):
    failed, healthy = Client(FakeHTTPResponse(503)), Client(FakeHTTPResponse(200, payload()))
    monkeypatch.setattr(rpc, "_get_client", lambda proxy, *_args: failed if proxy == "private-first" else healthy)
    recorder = swoop.DiagnosticsRecorder()
    with pytest.raises(SwoopHTTPError):
        search(recorder, proxy="private-first")
    result = search(recorder, proxy="private-second")
    snapshot = recorder.snapshot()
    assert result.results
    assert snapshot.http_attempts == failed.calls + healthy.calls == 2
    assert [query.outcome for query in snapshot.queries] == ["error", "success"]
    assert "private" not in json.dumps(asdict(snapshot))


def test_empty_response_is_observable_without_inventing_decode_coverage(monkeypatch):
    client = Client(FakeHTTPResponse(200, encode_rpc_outer(make_full_response())))
    monkeypatch.setattr(rpc, "_get_client", lambda *_args: client)
    recorder = swoop.DiagnosticsRecorder()
    result = search(recorder)
    assert result.results == []
    query = recorder.snapshot().queries[0]
    assert query.outcome == "empty" and query.decoded_result_count == 0
    assert query.raw_result_count is None or query.raw_result_count == 0
    # Unknown mode/count is never described as complete by the diagnostics API.
    assert not hasattr(query, "is_complete")


def test_snapshot_is_immutable_and_pending_query_stays_pending_while_http_is_blocked(monkeypatch):
    entered, release = Event(), Event()

    class BlockingClient:
        def post(self, *_args, **_kwargs):
            entered.set()
            assert release.wait(5)
            return FakeHTTPResponse(200, payload())

    monkeypatch.setattr(rpc, "_get_client", lambda *_args: BlockingClient())
    recorder = swoop.DiagnosticsRecorder()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(search, recorder)
        assert entered.wait(5)
        snapshot = recorder.snapshot()
        assert snapshot.queries[0].outcome == "pending"
        with pytest.raises(FrozenInstanceError):
            snapshot.http_attempts = 99
        release.set()
        assert pending.result().results
    assert snapshot.queries[0].outcome == "pending"
    assert recorder.snapshot().queries[0].outcome == "success"


def test_forced_interleaving_keeps_separate_request_recorders_and_queries_isolated(monkeypatch):
    barrier = Barrier(2)

    class InterleavedClient:
        def post(self, *_args, **_kwargs):
            barrier.wait(timeout=5)
            return FakeHTTPResponse(200, payload())

    monkeypatch.setattr(rpc, "_get_client", lambda *_args: InterleavedClient())
    first, second = swoop.DiagnosticsRecorder(), swoop.DiagnosticsRecorder()
    with ThreadPoolExecutor(max_workers=2) as pool:
        calls = [pool.submit(search, first), pool.submit(search, second, origin="BOS", destination="MIA")]
        assert all(call.result().results for call in calls)
    assert first.snapshot().http_attempts == second.snapshot().http_attempts == 1
    assert first.snapshot().queries[0].bounds[0].origin_airports == ("JFK",)
    assert second.snapshot().queries[0].bounds[0].origin_airports == ("BOS",)
    assert first.snapshot().attempts[0].query_id == second.snapshot().attempts[0].query_id == 1


def test_shared_request_workers_keep_unique_query_ids_without_mutating_original_config(monkeypatch):
    barrier = Barrier(2)

    class InterleavedClient:
        def post(self, *_args, **_kwargs):
            barrier.wait(timeout=5)
            return FakeHTTPResponse(200, payload())

    monkeypatch.setattr(rpc, "_get_client", lambda *_args: InterleavedClient())
    recorder = swoop.DiagnosticsRecorder()
    config = swoop.TransportConfig(diagnostics=recorder, retries=0)
    with ThreadPoolExecutor(max_workers=2) as pool:
        calls = [pool.submit(rpc.search_raw, "JFK", "LAX", "2026-11-15", transport=config),
                 pool.submit(rpc.search_raw, "BOS", "MIA", "2026-11-15", transport=replace(config, proxy="hidden"))]
        assert all(call.result() is not None for call in calls)
    snapshot = recorder.snapshot()
    assert snapshot.http_attempts == 2
    assert {attempt.query_id for attempt in snapshot.attempts} == {query.query_id for query in snapshot.queries} == {1, 2}
    assert config._diagnostics_query_id is None


def test_bounded_samples_preserve_exact_aggregate_attempt_counts(monkeypatch):
    client = Client(FakeHTTPResponse(200, payload()), FakeHTTPResponse(200, payload()))
    monkeypatch.setattr(rpc, "_get_client", lambda *_args: client)
    recorder = swoop.DiagnosticsRecorder(max_attempts=0, max_queries=0)
    search(recorder)
    search(recorder)
    snapshot = recorder.snapshot()
    assert snapshot.http_attempts == client.calls == 2
    assert snapshot.omitted_attempts == snapshot.omitted_queries == 2
    assert snapshot.attempts == snapshot.queries == ()


def test_unicode_bytes_and_secrets_are_handled_without_storing_payloads(monkeypatch):
    body = payload().replace("Delta", "Deltá")
    client = Client(FakeHTTPResponse(200, body))
    monkeypatch.setattr(rpc, "_get_client", lambda *_args: client)
    recorder = swoop.DiagnosticsRecorder()
    search(recorder, proxy="socks5://user:password@proxy.invalid")
    snapshot = recorder.snapshot()
    assert snapshot.response_bytes == len(body.encode("utf-8")) > len(body)
    serialized = json.dumps(asdict(snapshot))
    assert "password" not in serialized and "Delt" not in serialized and "token" not in serialized


def test_recording_is_opt_in_and_preserves_default_error_and_result_behavior(monkeypatch):
    client = Client(FakeHTTPResponse(200, payload()))
    monkeypatch.setattr(rpc, "_get_client", lambda *_args: client)
    result = search(None)
    assert result.results
    if hasattr(result, "rpc_calls"):
        assert result.rpc_calls == 1
    assert swoop.TransportConfig().diagnostics is None
    assert "diagnostics" not in repr(swoop.TransportConfig())
    assert swoop.SwoopHTTPError(503).diagnostics is None

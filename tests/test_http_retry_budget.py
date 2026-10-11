"""POST attempts, setup, body reads, and backoff share one HTTP timeout."""
import time
from types import SimpleNamespace

import pytest

from swoop import rpc
from swoop.exceptions import SwoopError, SwoopHTTPError, SwoopRateLimitError
from swoop.models import TransportConfig


@pytest.fixture
def clock(monkeypatch):
    class Clock:
        now = 0.0
        waits = []

        def sleep(self, seconds):
            self.waits.append(seconds)
            self.now += seconds

    clock = Clock()
    monkeypatch.setattr(time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(time, "sleep", clock.sleep)
    import random
    monkeypatch.setattr(random, "uniform", lambda *_: 0)
    return clock


def _client(clock, replies):
    class Client:
        calls = []

        def post(self, *_args, **kwargs):
            self.calls.append(kwargs["timeout"])
            seconds, status = replies[len(self.calls) - 1]
            clock.now += seconds
            return SimpleNamespace(status_code=status, text="ok")

    return Client()


def test_retry_gets_only_budget_remaining_after_post_and_wait(clock):
    client = _client(clock, [(2, 429), (1, 200)])
    response = rpc._post_with_retry(client, "https://example.invalid/rpc", b"", {},
        transport=TransportConfig(timeout=8, retries=2))
    assert response.text == "ok" and client.calls == [8, 5]
    assert clock.waits == [1] and clock.now == 4


def test_insufficient_retry_budget_raises_429_immediately_without_wait(clock):
    client = _client(clock, [(0.75, 429)])
    with pytest.raises(SwoopRateLimitError):
        rpc._post_with_retry(client, "https://example.invalid/rpc", b"", {},
            transport=TransportConfig(timeout=1, retries=2))
    assert client.calls == [1] and not clock.waits and clock.now == 0.75


def test_repeated_429_cannot_reset_timeout(clock):
    client = _client(clock, [(0.2, 429), (0.2, 429)])
    with pytest.raises(SwoopRateLimitError):
        rpc._post_with_retry(client, "https://example.invalid/rpc", b"", {},
            transport=TransportConfig(timeout=3, retries=5))
    assert client.calls == pytest.approx([3, 1.8])
    assert clock.waits == [1] and clock.now == pytest.approx(1.4)


def test_wait_overrun_cannot_dispatch_another_attempt(clock, monkeypatch):
    client = _client(clock, [(0.5, 429)])

    def late_sleep(seconds):
        clock.waits.append(seconds)
        clock.now += seconds + 1

    monkeypatch.setattr(time, "sleep", late_sleep)
    with pytest.raises(SwoopRateLimitError):
        rpc._post_with_retry(client, "https://example.invalid/rpc", b"", {},
            transport=TransportConfig(timeout=2.5, retries=2))
    assert client.calls == [2.5] and clock.waits == [1] and clock.now == 2.5


@pytest.mark.parametrize("timeout", [0, -1, float("nan")])
def test_invalid_or_expired_budget_cannot_dispatch(clock, timeout):
    client = _client(clock, [])
    with pytest.raises(SwoopError):
        rpc._post_with_retry(client, "https://example.invalid/rpc", b"", {}, transport=TransportConfig(timeout=timeout))
    assert not client.calls


def test_client_setup_consumes_the_same_timeout(clock, monkeypatch):
    client = _client(clock, [])

    def setup(*_args):
        clock.now += 2
        return client

    monkeypatch.setattr(rpc, "_get_client", setup)
    with pytest.raises(SwoopError):
        rpc._http_post("https://example.invalid/rpc", b"", transport=TransportConfig(timeout=1))
    assert not client.calls


@pytest.mark.parametrize("phase", ["post", "body"])
def test_late_success_cannot_be_returned(clock, phase):
    class Response:
        status_code = 200

        @property
        def text(self):
            if phase == "body": clock.now += 9
            return "ok"

    class Client:
        def post(self, *_args, **_kwargs):
            if phase == "post": clock.now += 9
            return Response()

    with pytest.raises(SwoopError):
        rpc._post_with_retry(Client(), "https://example.invalid/rpc", b"", {}, transport=TransportConfig(timeout=8))


def test_non_429_http_failure_is_not_reclassified_or_retried(clock):
    client = _client(clock, [(0.1, 503)])
    with pytest.raises(SwoopHTTPError) as caught:
        rpc._post_with_retry(client, "https://example.invalid/rpc", b"", {}, transport=TransportConfig(timeout=1, retries=5))
    assert caught.value.status_code == 503 and client.calls == [1] and not clock.waits


def test_existing_provider_exception_is_preserved(clock):
    error = TimeoutError("provider timeout")

    class Client:
        def post(self, *_args, **_kwargs):
            raise error

    with pytest.raises(TimeoutError) as caught:
        rpc._post_with_retry(Client(), "https://example.invalid/rpc", b"", {}, transport=TransportConfig(timeout=1))
    assert caught.value is error

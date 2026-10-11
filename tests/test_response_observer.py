"""Raw observation is opt-in, request-owned, and precedes real decoding."""
from dataclasses import FrozenInstanceError, asdict
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

import swoop
from swoop import rpc
from swoop._deals import _establish_session
from swoop._explore import _get_browser_params
from swoop.exceptions import SwoopHTTPError, SwoopParseError, SwoopRateLimitError
from tests.factories import encode_rpc_outer, make_full_response


class Response:
    def __init__(self, status, body):
        self.status_code, self.body, self.reads = status, body, 0

    @property
    def text(self):
        self.reads += 1
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


class Client:
    def __init__(self, *responses):
        self.responses, self.posts, self.gets = iter(responses), 0, 0

    def post(self, *_args, **_kwargs):
        self.posts += 1
        return next(self.responses)

    def get(self, *_args, **_kwargs):
        self.gets += 1
        return next(self.responses)


def test_success_observes_once_before_real_decode_without_exposing_body(monkeypatch):
    events = []
    body = encode_rpc_outer(make_full_response())
    response = Response(200, body)
    client = Client(response)
    monkeypatch.setattr(rpc, '_get_client', lambda *_: client)
    original = rpc._parse_rpc_response

    def parse(text):
        assert events[0].body == text == body
        return original(text)

    monkeypatch.setattr(rpc, '_parse_rpc_response', parse)
    config = swoop.TransportConfig(retries=0, response_observer=events.append)
    result = swoop.search_raw('JFK', 'LAX', '2026-11-15', transport=config)
    assert result is not None and client.posts == response.reads == 1
    assert len(events) == 1 and events[0].operation == 'shopping'
    assert events[0].transport == 'rpc' and events[0].method == 'POST'
    assert body not in repr(events[0]) and 'body=' not in repr(events[0])
    assert 'response_observer' not in repr(config)
    assert body not in repr(result)
    assert set(asdict(events[0])) == {
        'operation', 'transport', 'method', 'retry_index', 'status_code', 'body', 'body_unavailable'}
    with pytest.raises(FrozenInstanceError):
        events[0].status_code = 500


@pytest.mark.parametrize('status,error', [(200, SwoopParseError), (503, SwoopHTTPError), (429, SwoopRateLimitError)])
def test_unknown_non_json_failure_body_survives_before_existing_error(monkeypatch, status, error):
    response = Response(status, '<html>unknown Google response fields</html>')
    client, events = Client(response), []
    monkeypatch.setattr(rpc, '_get_client', lambda *_: client)
    with pytest.raises(error):
        swoop.search_raw('JFK', 'LAX', '2026-11-15', transport=swoop.TransportConfig(
            retries=0, response_observer=events.append))
    assert events[0].body == response.body and events[0].status_code == status
    assert client.posts == response.reads == 1


def test_rejected_body_is_not_read_without_opt_in():
    response = Response(503, AssertionError('rejection body must stay unread'))
    with pytest.raises(SwoopHTTPError):
        rpc._post_with_retry(Client(response), rpc.BOOKING_RPC_URL, b'', {},
                            transport=swoop.TransportConfig(retries=0))
    assert response.reads == 0


def test_disabled_success_preserves_original_response_object():
    response = Response(200, 'ok')
    assert rpc._post_with_retry(Client(response), rpc.BOOKING_RPC_URL, b'', {}) is response


@pytest.mark.parametrize('status,error', [(200, TimeoutError), (429, SwoopRateLimitError), (503, SwoopHTTPError)])
def test_unreadable_body_preserves_existing_failure(status, error):
    response, events = Response(status, TimeoutError('private read message')), []
    with pytest.raises(error):
        rpc._post_with_retry(Client(response), rpc.BOOKING_RPC_URL, b'', {},
            transport=swoop.TransportConfig(retries=0, response_observer=events.append))
    assert len(events) == 1 and events[0].body is None and events[0].body_unavailable


def test_observer_failure_cannot_change_success_or_http_rejection():
    def reject(_event):
        raise RuntimeError('private storage error')

    response = rpc._post_with_retry(Client(Response(200, 'ok')), rpc.BOOKING_RPC_URL, b'', {},
        transport=swoop.TransportConfig(response_observer=reject))
    assert response.text == 'ok'
    with pytest.raises(SwoopHTTPError):
        rpc._post_with_retry(Client(Response(503, 'upstream rejection')), rpc.BOOKING_RPC_URL, b'', {},
            transport=swoop.TransportConfig(retries=0, response_observer=reject))


def test_each_actual_retry_is_observed_without_an_extra_request(monkeypatch):
    events, waits = [], []
    first, second = Response(429, 'rate-limit body'), Response(200, 'success body')
    client = Client(first, second)
    monkeypatch.setattr('time.sleep', waits.append)
    monkeypatch.setattr('random.uniform', lambda *_: 0)
    response = rpc._post_with_retry(client, rpc.BOOKING_RPC_URL, b'', {},
        transport=swoop.TransportConfig(retries=1, response_observer=events.append))
    assert response.text == 'success body'
    assert client.posts == 2 and first.reads == second.reads == 1 and waits == [1]
    assert [(event.retry_index, event.status_code) for event in events] == [(0, 429), (1, 200)]


@pytest.mark.parametrize('endpoint,expected', [
    ('GetBookingResults', 'booking'), ('GetCalendarGraph', 'calendar'),
    ('GetFlightDealsStreaming', 'deals'), ('GetExploreDestinations', 'explore'), ('UnknownRPC', 'other'),
])
def test_metadata_keeps_only_finite_operation_label(endpoint, expected):
    events = []
    rpc._post_with_retry(Client(Response(200, 'body')), f'https://example.test/{endpoint}?token=URL_SECRET',
        b'private request', {'authorization': 'HEADER_SECRET'},
        transport=swoop.TransportConfig(response_observer=events.append))
    event = asdict(events[0])
    assert event['operation'] == expected
    assert not {'url', 'headers', 'request'} & event.keys()
    assert 'URL_SECRET' not in str(event) and 'HEADER_SECRET' not in str(event)


def test_deals_bootstrap_is_optional_and_ignored_read_failure_does_not_change_session():
    events = []
    response = Response(200, TimeoutError('ignored page body'))
    client = Client(response)
    _establish_session(client, transport=swoop.TransportConfig(response_observer=events.append))
    assert client.gets == response.reads == 1 and events[0].body_unavailable
    assert events[0].operation == 'deals' and events[0].transport == 'page'
    ignored = Response(200, 'not read')
    _establish_session(Client(ignored))
    assert ignored.reads == 0


def test_explore_bootstrap_is_observed_without_duplicate_page_read():
    events = []
    response = Response(200, 'unknown explore bootstrap fields')
    client = Client(response)
    _get_browser_params(client, 'https://example.test', key='raw-observer-fixture', force_refresh=True,
        transport=swoop.TransportConfig(response_observer=events.append))
    assert response.reads == client.gets == 1 and events[0].body == response.body
    assert events[0].operation == 'explore' and events[0].transport == 'page'


def test_parallel_callers_keep_their_own_observers():
    barrier = threading.Barrier(2)
    received = {'one': [], 'two': []}

    def run(label):
        class InterleavedClient(Client):
            def post(self, *args, **kwargs):
                barrier.wait(timeout=5)
                return super().post(*args, **kwargs)

        client = InterleavedClient(Response(200, label))
        return rpc._post_with_retry(client, rpc.SHOPPING_RPC_URL, b'', {},
            transport=swoop.TransportConfig(response_observer=received[label].append)).text

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(run, ['one', 'two'])) == ['one', 'two']
    assert [event.body for event in received['one']] == ['one']
    assert [event.body for event in received['two']] == ['two']


@pytest.mark.parametrize('observe', [False, True])
def test_rejected_explore_bootstrap_required_read_preserves_failure(observe):
    events = []
    response = Response(503, TimeoutError('synthetic required bootstrap read'))
    client = Client(response)
    with pytest.raises(TimeoutError):
        _get_browser_params(client, 'https://example.test', key='required-bootstrap', force_refresh=True,
            transport=swoop.TransportConfig(response_observer=events.append if observe else None))
    assert client.gets == response.reads == 1 and client.posts == 0
    assert len(events) == int(observe)

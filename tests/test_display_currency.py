"""Display currency is independent of the point of sale and survives replay."""
import json
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

import swoop
import swoop.rpc as rpc
import swoop._selection as selection
from swoop.cli import main
from swoop.models import TransportConfig
from tests.factories import make_simple_itinerary, make_raw_result


@pytest.mark.parametrize('currency', ['us', 'USDD', '123', 'éur', ''])
def test_invalid_currency(currency):
    with pytest.raises(ValueError, match='currency'):
        TransportConfig(currency=currency)


@pytest.mark.parametrize('country', ['GB', None])
@pytest.mark.parametrize('endpoint', ['GetShoppingResults', 'GetBookingResults', 'GetCalendarGrid', 'GetCalendarPicker'])
def test_rpc_header_preserves_pos(monkeypatch, country, endpoint):
    calls = []
    client = SimpleNamespace(post=lambda url, **kw: (calls.append((url, kw)) or SimpleNamespace(status_code=200, text='[]')))
    monkeypatch.setattr(rpc, '_get_client', lambda *a: client)
    monkeypatch.setattr(rpc, '_default_country', None)
    rpc._http_post('https://www.google.com/' + endpoint, b'f.req=x', transport=TransportConfig(country=country, currency='jpy'))
    url, kwargs = calls[0]
    assert ('gl=GB' in url) == (country == 'GB')
    assert json.loads(kwargs['headers']['x-goog-ext-259736195-jspb'])[:3] == ['en', country, 'JPY']


def test_default_country_and_no_currency_legacy_headers(monkeypatch):
    calls = []
    monkeypatch.setattr(rpc, '_default_country', 'GB')
    monkeypatch.setattr(rpc, '_get_client', lambda *a: SimpleNamespace(post=lambda url, **kw: (calls.append((url, kw)) or SimpleNamespace(status_code=200, text='[]'))))
    rpc._http_post('https://www.google.com/test', b'')
    assert calls[0][0].endswith('?gl=GB')
    assert 'x-goog-ext-259736195-jspb' not in calls[0][1]['headers']
    rpc._http_post('https://www.google.com/test', b'', transport=TransportConfig(currency='EUR'))
    assert json.loads(calls[1][1]['headers']['x-goog-ext-259736195-jspb'])[:3] == ['en', 'GB', 'EUR']


def test_selector_currency_used_for_shopping_and_booking(monkeypatch):
    itinerary = make_simple_itinerary(origin='JFK', destination='LAX', date='2026-12-03', flight_number='123', booking_token='synthetic-token')
    calls = []
    monkeypatch.setattr(selection, '_search_from_legs', lambda legs, **kw: (calls.append(kw['transport']) or make_raw_result(itinerary)))
    result = selection.search_trip_options([{'origin': 'JFK', 'destination': 'LAX', 'date': '2026-12-03'}], transport=TransportConfig(country='GB', currency='JPY'))
    monkeypatch.setattr(selection, 'fetch_trip_booking_options', lambda *a, **kw: (calls.append(kw['transport']) or []))
    assert selection.price_trip_selector(result.results[0].selector) is not None
    assert [(c.country, c.currency) for c in calls] == [('GB', 'JPY')] * 3
    calls.clear()
    selection.price_trip_selector(result.results[0].selector, transport=TransportConfig(currency='EUR'))
    assert [(c.country, c.currency) for c in calls] == [('GB', 'EUR')] * 2


@pytest.mark.parametrize('command', [
    ['search', 'JFK', 'LAX', '2026-12-03'],
    ['search', '--leg', 'JFK', 'LAX', '2026-12-03'],
    ['price', 'JFK', 'LAX', '--depart', '2026-12-03', 'AA123'],
])
def test_cli_currency(monkeypatch, command):
    calls = []
    for name in ['search', 'search_legs', 'check_price']:
        monkeypatch.setattr(swoop, name, lambda *a, **kw: (calls.append(kw['transport']) or None))
    result = CliRunner().invoke(main, [*command, '--country', 'GB', '--currency', 'JPY', '-q'])
    assert result.exit_code == 1, result.output
    assert len(calls) == 1
    assert (calls[0].country, calls[0].currency) == ('GB', 'JPY')


@pytest.mark.live
def test_live_currency_independent_of_point_of_sale():
    from datetime import date, timedelta
    day = (date.today() + timedelta(days=42)).isoformat()
    transport = TransportConfig(country='GB', currency='JPY', timeout=45, retries=0)
    raw = swoop.search_raw('JFK', 'LAX', day, transport=transport)
    assert raw and raw.best + raw.other
    itinerary = (raw.best + raw.other)[0]
    assert itinerary.currency == 'JPY'
    assert itinerary.price and itinerary.price > 1000
    options = swoop.get_booking_results(itinerary, transport=transport)
    assert options and min(o.price for o in options) > 1000

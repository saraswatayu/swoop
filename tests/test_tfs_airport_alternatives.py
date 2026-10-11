"""Google-produced TFS wire evidence and frozen singleton request bytes."""
import base64
import json
from pathlib import Path

import pytest

from swoop import flights_pb2 as PB
from swoop.builders import SearchLeg, TFSData, _PBPassengers, _apply_tfs_leg
from swoop.decoder import decode_result

_FIXTURES = Path(__file__).parent / 'fixtures/tfs'
_SINGLETONS = json.loads((_FIXTURES / 'tfs_singleton_baseline.json').read_text())


@pytest.mark.parametrize('field', ['native_ui_tfs', 'accepted_minimal_tfs'])
def test_captured_tfs_contains_every_endpoint_alternative(field):
    fixture = json.loads((_FIXTURES / 'shopping_page_airport_alternatives.json').read_text())
    token = fixture[field]
    info = PB.Info.FromString(base64.urlsafe_b64decode(token + '=' * (-len(token) % 4)))
    assert len(info.data) == 1
    leg = info.data[0]
    assert [entry.airport for entry in leg.from_flight] == fixture['origin_airports']
    assert [entry.airport for entry in leg.to_flight] == fixture['destination_airports']
    assert leg.date == fixture['departure_date']
    assert leg.HasField('max_stops') and leg.max_stops == 0
    assert info.trip == PB.Trip.ONE_WAY


def test_per_leg_encoder_keeps_alternative_order_and_input_lists():
    origins, destinations = ['JFK', 'EWR', 'LGA'], ['LAX', 'BUR', 'SNA']
    info = PB.Info()
    _apply_tfs_leg(info, date='2026-10-26', origin=origins, destination=destinations,
                   max_stops=0, airlines=['ua', 'aa'])
    assert len(info.data) == 1
    assert [entry.airport for entry in info.data[0].from_flight] == origins
    assert [entry.airport for entry in info.data[0].to_flight] == destinations
    assert list(info.data[0].airlines) == ['UA', 'AA']
    assert origins == ['JFK', 'EWR', 'LGA'] and destinations == ['LAX', 'BUR', 'SNA']


def test_sanitized_google_page_rows_retain_actual_airport_identities():
    fixture = json.loads((_FIXTURES / 'shopping_page_airport_alternatives.json').read_text())
    result = decode_result(fixture['sanitized_page_payload'])
    rows = [*result.best, *result.other]
    assert {(row.departure_airport_code, row.arrival_airport_code) for row in rows} == {
        ('JFK', 'LAX'), ('EWR', 'LAX')}
    assert {(row.segments[0].departure_airport_code, row.segments[-1].arrival_airport_code)
            for row in rows} == {('JFK', 'LAX'), ('EWR', 'LAX')}
    assert all(row.departure_date == (2026, 10, 26) and row.booking_token == '' for row in rows)


@pytest.mark.parametrize('case', _SINGLETONS)
def test_singleton_requests_remain_byte_identical(case):
    """Baseline bytes came from unmodified upstream 59fbafc, before schema edits."""
    filters = case['filters']
    legs = [SearchLeg(date='2026-10-26', from_airport='JFK', to_airport='LAX',
                      max_stops=0 if filters else None, airlines=['AA', 'DL'] if filters else None)]
    if case['trip'] == PB.Trip.ROUND_TRIP:
        legs.append(SearchLeg(date='2026-11-02', from_airport='LAX', to_airport='JFK',
                              max_stops=0 if filters else None, airlines=['AA', 'DL'] if filters else None))
    passengers = (_PBPassengers(adults=2, children=1, infants_in_seat=1, infants_on_lap=1)
                  if filters else _PBPassengers())
    tfs = TFSData(flight_data=legs, seat=case['seat'], trip=case['trip'], passengers=passengers,
                  exclude_basic_economy=filters)
    assert tfs.as_b64().decode() == case['tfs']
    assert legs[0].from_airport == 'JFK' and legs[0].to_airport == 'LAX'


@pytest.mark.parametrize('case', _SINGLETONS)
def test_private_encoder_preserves_all_singleton_bytes(case):
    filters = case['filters']
    passengers = (_PBPassengers(adults=2, children=1, infants_in_seat=1, infants_on_lap=1)
                  if filters else _PBPassengers())
    info = TFSData(flight_data=[], seat=case['seat'], trip=case['trip'], passengers=passengers,
                  exclude_basic_economy=filters).pb()
    _apply_tfs_leg(info, date='2026-10-26', origin=['JFK'], destination=['LAX'],
                   max_stops=0 if filters else None, airlines=['AA', 'DL'] if filters else None)
    if case['trip'] == PB.Trip.ROUND_TRIP:
        _apply_tfs_leg(info, date='2026-11-02', origin=['LAX'], destination=['JFK'],
                       max_stops=0 if filters else None, airlines=['AA', 'DL'] if filters else None)
    assert base64.b64encode(info.SerializeToString()).decode() == case['tfs']

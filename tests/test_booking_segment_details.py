"""Ordered booking facts must stay attached to their exact flight and cabin."""

from __future__ import annotations

import base64
import json
from copy import deepcopy
from pathlib import Path

import pytest

from swoop import BookingOption, BookingSegmentDetails
from swoop._booking import _parse_booking_rpc_response, parse_booking_payload
from tests.factories import encode_rpc_outer, make_booking_option


def _varint(value: int) -> bytes:
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def _field(number: int, value: str | bytes) -> bytes:
    raw = value.encode() if isinstance(value, str) else value
    return _varint((number << 3) | 2) + _varint(len(raw)) + raw


IDENTITIES = [
    {1: "JFK", 2: "2026-10-26T05:55:00-04:00", 3: "MIA",
     4: "2026-10-26T09:00:00-04:00", 5: "AA", 6: "2161", 10: "738"},
    {1: "MIA", 2: "2026-10-26T10:56:00-04:00", 3: "PTY",
     4: "2026-10-26T13:02:00-05:00", 5: "AA", 6: "959", 10: "738"},
]
FLAGS = [None, True, None, None, None, None, None, None, None, None, True, 2]


def _token(identities=IDENTITIES, *, suffix=b""):
    raw = b"".join(_field(1, b"".join(_field(number, value) for number, value in identity.items()))
                   for identity in identities)
    return base64.b64encode(raw + suffix).decode()


def _option(*, token=None, facts=None):
    # Sanitized shape from a live JFK-MIA-PTY response: no booking/redirect tokens.
    option = make_booking_option(brand_code="BUSINESS", brand_label="Business")
    option[19] = json.dumps(["", [token if token is not None else _token()]])
    option[21][6] = facts if facts is not None else [
        [4, FLAGS, 8, None, None, None],
        [3, FLAGS, 8, None, None, None],
    ]
    return option


def _parse(option):
    return _parse_booking_rpc_response(encode_rpc_outer([None, [[option]]]))[0]


def test_connecting_mixed_cabin_details_keep_both_exact_identities():
    option = _parse(_option())
    first, second = option.segment_details
    assert all(isinstance(detail, BookingSegmentDetails) for detail in option.segment_details)
    assert (first.origin_iata, first.destination_iata, first.flight_number) == ("JFK", "MIA", "2161")
    assert (second.origin_iata, second.destination_iata, second.flight_number) == ("MIA", "PTY", "959")
    assert first.departure_local_iso == "2026-10-26T05:55:00-04:00"
    assert second.arrival_local_iso == "2026-10-26T13:02:00-05:00"
    assert [detail.cabin_class for detail in option.segment_details] == [4, 3]
    assert first.aircraft_code == second.aircraft_code == "738"
    assert first.amenities.has_power and second.amenities.has_stream_media
    assert first.amenities.wifi == 2  # raw Google code, not a free-Wi-Fi promise
    assert [detail.seat_type for detail in option.segment_details] == [8, 8]
    # Existing singular fields deliberately preserve the old last-descriptor behavior.
    assert option._context_origin_iata == "MIA"
    assert option._context_flight_number == "959"


def test_economy_pitch_legroom_and_unknown_facts_are_preserved():
    option = _parse(_option(facts=[[1, FLAGS, 1, None, "30 in", "30 inches"], [None]]))
    first, second = option.segment_details
    assert (first.cabin_class, first.seat_pitch_short, first.legroom) == (1, "30 in", "30 inches")
    assert second.cabin_class is None and second.amenities is None and second.seat_type is None
    assert second.seat_pitch_short == second.legroom == ""


def test_segment_facts_follow_the_adaptive_brand_extractor():
    raw = _option()
    brand = raw[21]
    raw[21] = None
    raw.append(brand)
    option = _parse(raw)
    assert [detail.cabin_class for detail in option.segment_details] == [4, 3]


@pytest.mark.parametrize("facts", [None, [], [[1]], [[1], [3], [4]], [[1], None], [[1], []]])
def test_missing_or_mismatched_fact_counts_never_shift_identity(facts):
    raw = _option()
    raw[21][6] = facts
    assert _parse(raw).segment_details == []


@pytest.mark.parametrize("mutation", ["missing", "invalid-airport", "naive-time", "duplicate-field", "wrong-wire"])
def test_malformed_descriptor_withholds_all_details(mutation):
    identities = deepcopy(IDENTITIES)
    if mutation == "missing":
        del identities[0][4]
    elif mutation == "invalid-airport":
        identities[0][1] = "bad"
    elif mutation == "naive-time":
        identities[0][2] = "2026-10-26T05:55:00"
    if mutation in ("duplicate-field", "wrong-wire"):
        first = b"".join(_field(number, value) for number, value in identities[0].items())
        first += _field(1, "JFK") if mutation == "duplicate-field" else b"\x08\x01"
        second = b"".join(_field(number, value) for number, value in identities[1].items())
        token = base64.b64encode(_field(1, first) + _field(1, second)).decode()
    else:
        token = _token(identities)
    assert _parse(_option(token=token)).segment_details == []


@pytest.mark.parametrize("suffix", [b"\x0a\xff\x01", b"\x08\x01", b"\x09\x00", b"\x00", b"\x80"])
def test_truncated_or_wrong_wire_context_does_not_expose_partial_details(suffix):
    assert _parse(_option(token=_token(suffix=suffix))).segment_details == []


def test_unknown_fields_are_skipped_without_reordering_segments():
    option = _parse(_option(token=_token(suffix=b"\x18\x01" + _field(5, "unknown"))))
    assert [detail.flight_number for detail in option.segment_details] == ["2161", "959"]


def test_boolean_numeric_facts_are_not_interpreted_as_enum_codes():
    flags = deepcopy(FLAGS)
    flags[11] = True
    option = _parse(_option(facts=[[True, flags, False, None, 30, 30], [3]]))
    first = option.segment_details[0]
    assert first.cabin_class is None and first.seat_type is None and first.amenities.wifi is None
    assert first.seat_pitch_short == first.legroom == ""


def test_segment_detail_defaults_are_independent_and_additive():
    first, second = BookingOption(), BookingOption()
    first.segment_details.append(BookingSegmentDetails())
    assert second.segment_details == []


CORPUS = Path(__file__).parent / "fixtures" / "corpus"


@pytest.mark.parametrize("fixture", sorted(CORPUS.glob("booking*.txt")), ids=lambda path: path.name)
def test_existing_booking_corpus_keeps_every_ordered_fact_row(fixture):
    text = fixture.read_text()
    parsed = _parse_booking_rpc_response(text)
    raw = parse_booking_payload(text)
    assert len(parsed) == len(raw)
    for option, raw_option in zip(parsed, raw):
        facts = raw_option[21][6]
        assert len(option.segment_details) == len(facts) > 0
        assert [detail.cabin_class for detail in option.segment_details] == [row[0] for row in facts]
        assert all(detail.origin_iata and detail.arrival_local_iso for detail in option.segment_details)

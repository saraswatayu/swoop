"""Corpus-backed regression tests for the OTA booking-option path.

Issue #24 asked whether ``get_booking_results()`` can surface the full
third-party seller list (Expedia, FlightHub, ...) that the Google Flights
web UI shows, or whether swoop is limited to airline-direct fares.

The answer is that the parser already surfaces every seller Google returns —
OTAs included — but it only does so for itineraries Google chooses to offer
them on (international economy on foreign carriers). The original booking
corpus was entirely premium-cabin / major-carrier itineraries, all of which
are airline-direct, so nothing in the test suite exercised the OTA path. The
synthetic ``_extract_seller`` unit tests live in ``test_rpc.py``; this file
locks in the behaviour against a *real* captured OTA-bearing response so a
future reshape of ``opt[1]`` fails loudly.

Fixture: ``corpus/booking_intl_economy_ota_response.txt`` — SFO->MNL economy
on Philippine Airlines (PR), the exact carrier that motivated the null-brand
OTA parsing fix in commit b9dfd7d. One airline-direct option (PR) plus a wall
of OTAs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from swoop._booking import _parse_booking_rpc_response, parse_booking_payload
from swoop.exceptions import SwoopUpstreamError
from tests.factories import make_error_response

FIXTURES = Path(__file__).parent / "fixtures"
OTA_FIXTURE = FIXTURES / "corpus" / "booking_intl_economy_ota_response.txt"
REGISTRY_VERSION = "2026-06-02"


def _ota_options():
    text = OTA_FIXTURE.read_text()
    return _parse_booking_rpc_response(text, registry_version=REGISTRY_VERSION)


def test_booking_payload_raises_on_error_envelope():
    with pytest.raises(SwoopUpstreamError) as excinfo:
        parse_booking_payload(make_error_response())
    assert excinfo.value.grpc_code == 13


def test_booking_rpc_response_propagates_upstream_error():
    # The higher-level parser must propagate it too, not swallow into [].
    with pytest.raises(SwoopUpstreamError):
        _parse_booking_rpc_response(make_error_response(), registry_version=REGISTRY_VERSION)


def test_ota_fixture_surfaces_full_seller_list() -> None:
    """The OTA list is surfaced — not collapsed to a single airline seller.

    This is the direct answer to issue #24: when Google returns OTAs, swoop
    returns one BookingOption per seller with a distinct seller_code.
    """
    options = _ota_options()
    distinct = {opt.seller_code for opt in options}

    # A wall of distinct sellers, not "all the same airline".
    assert len(distinct) >= 10, f"expected a diverse OTA list, got {sorted(distinct)}"
    # Recognizable OTAs the web UI shows must come through verbatim.
    assert {"EXPEDIA", "FLIGHTHUB", "CHEAPOAIR"} <= distinct, sorted(distinct)


def test_ota_fixture_distinguishes_airline_direct_from_otas() -> None:
    """is_airline_direct cleanly splits the operating carrier from resellers."""
    options = _ota_options()

    direct = [opt for opt in options if opt.is_airline_direct]
    otas = [opt for opt in options if not opt.is_airline_direct]

    # Exactly the airline (PR) is flagged direct; everything else is a reseller.
    assert [opt.seller_code for opt in direct] == ["PR"]
    assert otas, "fixture should contain OTA (non-direct) options"
    assert all(opt.seller_code for opt in otas), "every OTA must carry a seller_code"
    # No OTA is mislabeled as the operating carrier.
    assert "PR" not in {opt.seller_code for opt in otas}


def test_ota_fixture_parses_null_brand_options_instead_of_dropping_them() -> None:
    """OTA/codeshare options carry no brand text but must still be parsed.

    Guards the option[21] null-brand fallback in _parse_booking_rpc_response.
    Before commit b9dfd7d these options were silently dropped, so for routes
    like this one (SFO->MNL via PR) ALL options vanished and callers saw zero
    booking options.
    """
    options = _ota_options()

    # This particular itinerary has empty brand text on every option.
    assert options, "null-brand OTA options must not be dropped"
    assert all(opt.brand_label == "" for opt in options)
    # ...yet each still carries a usable seller_code and a known cabin bucket
    # sourced from option[21][6][0][0] rather than brand text.
    assert all(opt.seller_code for opt in options)
    assert all(opt._cabin_bucket in {"economy", "business"} for opt in options)


def test_ota_fixture_options_are_bookable() -> None:
    """At least one OTA option exposes a booking_url so callers can route out."""
    options = _ota_options()
    ota_urls = [opt for opt in options if not opt.is_airline_direct and opt.booking_url]

    assert ota_urls, "OTA options should carry google.com/travel/clk redirects"
    for opt in ota_urls:
        assert opt.booking_url.startswith("https://www.google.com/travel/clk/f?")

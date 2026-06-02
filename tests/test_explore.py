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

"""Offline tests for Google Flights price insights (``data[5]``).

The block only rides the HTML/SSR page payload; the shopping RPC returns
``data[5] = null``. The fixture is synthetic but shaped per
``.claude/docs/google-flights-protobuf-schema.md`` § Price Insights.
"""

import json
from pathlib import Path

from swoop import PriceInsightPoint, PriceInsights
from swoop.decoder import _decode_price_insights, decode_result

FIX = Path(__file__).parent / "fixtures" / "responses" / "price_insights"


def _payload() -> list:
    return json.loads((FIX / "page_insights.json").read_text())


def test_decodes_full_block() -> None:
    insights = _decode_price_insights(_payload())
    assert isinstance(insights, PriceInsights)
    assert insights.price_level == 3
    assert insights.typical_price == 321
    assert insights.low_price == 264
    assert insights.price_difference == -57
    assert insights.lowest_seen == 225
    assert insights.highest_seen == 395
    assert insights.destination_city == "Los Angeles"
    assert insights.history[0] == PriceInsightPoint(timestamp_ms=1767848400000, price=194)
    assert insights.history[-1] == PriceInsightPoint(timestamp_ms=1773028800000, price=321)


def test_decode_result_attaches_insights_and_range() -> None:
    result = decode_result(_payload())
    assert result.price_insights is not None
    assert result.price_insights.typical_price == 321
    # The existing [7][0] range is decoded alongside and unaffected.
    assert result.price_range is not None
    assert (result.price_range.low, result.price_range.high) == (225, 395)


def test_missing_or_null_block_returns_none() -> None:
    assert _decode_price_insights([]) is None
    assert _decode_price_insights([None] * 5) is None
    assert _decode_price_insights([None, None, None, None, None, None, None]) is None


def test_garbage_block_returns_none_without_raising() -> None:
    assert _decode_price_insights([None, None, None, None, None, "nope"]) is None
    # A list that carries no usable value collapses to None rather than an
    # all-null PriceInsights object.
    assert _decode_price_insights([None, None, None, None, None, [None, 1, 2]]) is None


def test_malformed_history_entries_are_skipped() -> None:
    block = [
        1, None, None, None, None, None, 1, None, None, None,
        [[[None, 100], "x", [1767848400000, 150], [1234, "no"], [True, 9]]],
    ]
    insights = _decode_price_insights([None, None, None, None, None, block])
    assert insights is not None
    assert insights.price_level == 1
    assert insights.history == [PriceInsightPoint(timestamp_ms=1767848400000, price=150)]

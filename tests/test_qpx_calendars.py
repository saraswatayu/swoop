"""Calendar protocol fixtures, streaming failures and filter parity."""

import base64
from datetime import date, timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

import swoop
import swoop._calendar as cal
from swoop import flights_pb2 as PB
from swoop.cli import main
from tests.test_qpx_pricing_filters import decode_body, PRICING, ROUTING
from tests.factories import make_error_frame

FIXTURES = Path(__file__).parent / "fixtures" / "qpx_pricing"


@pytest.mark.parametrize(
    "name,count",
    [
        ("grid_ow", 2),
        ("grid_rt", 4),
        ("picker_ow", 2),
        ("picker_rt", 2),
        ("graph_ow", 2),
        ("graph_rt", 2),
    ],
)
def test_sanitized_live_calendar_fixtures(name, count):
    text = (FIXTURES / (name + ".json")).read_text()
    prices = cal._parse_calendar_prices(text)
    assert len(prices) == count
    assert {p.currency for p in prices} == {"USD"}
    assert all(p.price > 0 for p in prices)
    assert all((p.return_date is not None) == name.endswith("rt") for p in prices)
    # Fixture summaries contain currency only, no live flight identities/tokens.
    for frame in cal._calendar_frames(text):
        for cell in json.loads(frame[2])[1]:
            summary = PB.ItinerarySummary()
            summary.ParseFromString(base64.b64decode(cell[2][1]))
            assert summary.flights == ""
            assert summary.price.price == 0


def test_framed_stream_reads_all_chunks_and_deduplicates_cells():
    frames = cal._calendar_frames((FIXTURES / "grid_rt.json").read_text())
    cells = json.loads(frames[0][2])[1]
    chunks = [
        json.dumps([["di", 123]]),
        json.dumps([["wrb.fr", None, json.dumps([None, cells[:2]])]]),
        json.dumps([["wrb.fr", None, json.dumps([None, cells[1:]])]]),
    ]
    framed = ")]}'\n" + "".join(str(len(c.encode())) + "\n" + c + "\n" for c in chunks)
    assert cal._parse_calendar_prices(framed) == cal._parse_calendar_prices(
        (FIXTURES / "grid_rt.json").read_text()
    )


@pytest.mark.parametrize(
    "text",
    [
        "",
        "{}",
        "nonsense",
        "12\n[[",
        json.dumps([["wrb.fr", None, "oops"]]),
        json.dumps([["wrb.fr", None, "{}"]]),
        json.dumps([["di", 123]]),
    ],
)
def test_malformed_or_missing_calendar_payload_raises(text):
    with pytest.raises(swoop.SwoopParseError):
        cal._parse_calendar_prices(text)


def test_error_envelope_in_later_chunk_is_not_an_empty_calendar():
    with pytest.raises(swoop.SwoopUpstreamError):
        cal._parse_calendar_prices(json.dumps([["di", 123], make_error_frame()]))


def test_empty_and_unpublished_calendar_prices():
    text = json.dumps(
        [
            [
                "wrb.fr",
                None,
                json.dumps(
                    [
                        None,
                        [
                            ["2026-12-03", None, None],
                            ["2026-12-04", None, [[None, False], None]],
                            ["2026-12-05", None, [[None, -1], None]],
                        ],
                    ]
                ),
            ]
        ]
    )
    assert cal._parse_calendar_prices(text) == []


@pytest.fixture
def calendar_wire(monkeypatch):
    calls = []

    def post(url, content, **kwargs):
        body = decode_body(content)
        calls.append((url, body, kwargs))
        method = url.rsplit("/", 1)[-1]
        name = {
            "GetCalendarGrid": "grid",
            "GetCalendarPicker": "picker",
            "GetCalendarGraph": "graph",
        }[method]
        return SimpleNamespace(text=(FIXTURES / (name + "_ow.json")).read_text())

    monkeypatch.setattr(cal, "_http_post", post)
    return calls


@pytest.mark.parametrize(
    "name,method",
    [
        ("calendar_prices", "GetCalendarGrid"),
        ("get_calendar", "GetCalendarPicker"),
        ("get_price_graph", "GetCalendarGraph"),
    ],
)
def test_full_filter_parity_on_every_calendar_rpc(calendar_wire, name, method):
    response = getattr(swoop, name)(
        "JFK",
        "LAX",
        "2026-12-03",
        "2026-12-04",
        cabin="business",
        passengers=swoop.Passengers(adults=2, children=1),
        max_stops=1,
        airlines=["AA"],
        exclude_basic_economy=True,
        exclude_separate_tickets=True,
        earliest_departure=9,
        latest_departure=18,
        **PRICING,
        **ROUTING,
    )
    assert response
    url, body, _ = calendar_wire[0]
    assert url.endswith("/" + method)
    assert body[2] == ["2026-12-03", "2026-12-04"]
    f = body[1]
    assert f[2] == 2
    assert f[5:7] == [3, [2, 1, 0, 0]]
    assert f[7] == [None, 500] and f[10] == [1, 1]
    assert f[18] == f[28] == 1
    leg = f[13][0]
    assert leg[2] == [9, 18, None, None]
    assert leg[3:5] == [2, ["AA"]]
    assert leg[7] == [700] and leg[9] == ["ATL"] and leg[11:13] == [30, 870]


def test_roundtrip_grid_and_picker_payloads(calendar_wire):
    swoop.calendar_prices(
        "JFK",
        "LAX",
        "2026-12-03",
        "2026-12-04",
        return_start="2026-12-08",
        return_end="2026-12-09",
    )
    _, body, _ = calendar_wire[-1]
    assert body[3] == ["2026-12-08", "2026-12-09"]
    assert body[1][2] == 1 and len(body[1][13]) == 2
    assert body[1][13][1][0] == [[["LAX", 0]]]
    for fn in [swoop.get_calendar, swoop.get_price_graph]:
        fn("JFK", "LAX", "2026-12-03", "2026-12-04", min_stay=5)
        body = calendar_wire[-1][1]
        assert body[3:] == [None, [5, 5]]
        assert body[1][2] == 1 and len(body[1][13]) == 2
        fn("JFK", "LAX", "2026-12-03", "2026-12-04", max_stay=7)
        assert calendar_wire[-1][1][4] == [0, 7]


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(return_start="2026-12-08"),
        dict(return_end="2026-12-09"),
        dict(return_start="2026-12-09", return_end="2026-12-08"),
    ],
)
def test_invalid_grid_windows_never_send(calendar_wire, kwargs):
    with pytest.raises(ValueError):
        swoop.calendar_prices("JFK", "LAX", "2026-12-03", "2026-12-04", **kwargs)
    assert calendar_wire == []


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(min_stay=-1),
        dict(min_stay=6, max_stay=5),
        dict(max_stay=366),
        dict(min_stay=True),
        dict(max_duration=-1),
        dict(checked_bags=3),
    ],
)
def test_invalid_calendar_filters_never_send(calendar_wire, kwargs):
    with pytest.raises(ValueError):
        swoop.get_calendar("JFK", "LAX", "2026-12-03", "2026-12-04", **kwargs)
    assert calendar_wire == []


@pytest.mark.parametrize("mode", ["grid", "picker", "graph"])
@pytest.mark.parametrize("output", ["json", "csv", "table"])
def test_calendar_cli(calendar_wire, mode, output):
    result = CliRunner().invoke(
        main,
        [
            "calendar",
            "JFK",
            "LAX",
            "2026-12-03",
            "2026-12-04",
            "--mode",
            mode,
            "--checked-bags",
            "1",
            "--max-price",
            "500",
            "--max-duration",
            "700",
            "--output",
            output,
            "-q",
        ],
    )
    assert result.exit_code == 0, (result.output, result.exception)
    assert calendar_wire[0][1][1][10] == [0, 1]
    assert calendar_wire[0][1][1][7] == [None, 500]
    assert "2026-12-03" in result.output


@pytest.mark.live
@pytest.mark.parametrize("name", ["calendar_prices", "get_calendar", "get_price_graph"])
@pytest.mark.parametrize("roundtrip", [False, True])
def test_live_filtered_calendars(name, roundtrip):
    start = date.today() + timedelta(days=42)
    end = start + timedelta(days=1)
    extra = {}
    if roundtrip:
        extra = (
            dict(
                return_start=(start + timedelta(days=5)).isoformat(),
                return_end=(end + timedelta(days=5)).isoformat(),
            )
            if name == "calendar_prices"
            else dict(min_stay=5, max_stay=5)
        )
    result = getattr(swoop, name)(
        "JFK",
        "LAX",
        start.isoformat(),
        end.isoformat(),
        max_stops=0,
        airlines=["B6"],
        exclude_basic_economy=True,
        exclude_separate_tickets=True,
        carry_on_bags=1,
        checked_bags=1,
        max_price=2000,
        max_duration=450,
        transport=swoop.TransportConfig(country="US", timeout=45, retries=0),
        **extra,
    )
    cells = result.days if isinstance(result, swoop.CalendarResult) else result
    assert len(cells) == (4 if name == "calendar_prices" and roundtrip else 2)
    assert all(0 < p.price <= 2000 and p.currency == "USD" for p in cells)
    if roundtrip and name != "calendar_prices":
        assert all(
            (
                date.fromisoformat(p.return_date) - date.fromisoformat(p.departure_date)
            ).days
            == 5
            for p in cells
        )


def test_stream_metadata_before_price_cells():
    frames = cal._calendar_frames((FIXTURES / "grid_rt.json").read_text())
    frames.insert(0, ["wrb.fr", None, json.dumps([[None, []], None])])
    assert len(cal._parse_calendar_prices(json.dumps(frames))) == 4


@pytest.mark.parametrize("name", ["calendar_prices", "get_calendar", "get_price_graph"])
@pytest.mark.parametrize("kwargs", [
    {"earliest_departure": -1}, {"earliest_departure": 24}, {"earliest_departure": "9"},
    {"latest_departure": 0}, {"latest_departure": 25},
    {"earliest_arrival": -1}, {"earliest_arrival": 24},
    {"latest_arrival": 0}, {"latest_arrival": 25}, {"latest_arrival": []},
    {"return_earliest_departure": -1}, {"return_latest_departure": 25},
    {"earliest_departure": 9, "latest_departure": 9},
    {"earliest_departure": 18, "latest_departure": 9},
    {"earliest_arrival": 9, "latest_arrival": 9},
    {"earliest_arrival": 18, "latest_arrival": 9},
    {"return_earliest_departure": 9, "return_latest_departure": 9},
    {"return_earliest_departure": 18, "return_latest_departure": 9},
])
def test_calendar_time_validation_precedes_rpc(calendar_wire, name, kwargs):
    with pytest.raises(ValueError):
        getattr(swoop, name)("JFK", "LAX", "2026-12-03", "2026-12-04", **kwargs)
    assert calendar_wire == []


@pytest.mark.parametrize("name", ["calendar_prices", "get_calendar", "get_price_graph"])
def test_calendar_outbound_window_does_not_restrict_the_return(calendar_wire, name):
    stay = {"return_start": "2026-12-08", "return_end": "2026-12-09"} if name == "calendar_prices" else {"min_stay": 5}
    getattr(swoop, name)("JFK", "LAX", "2026-12-03", "2026-12-04",
        earliest_departure=9, latest_departure=18, earliest_arrival=12, latest_arrival=24, **stay)
    legs = calendar_wire[-1][1][1][13]
    assert legs[0][2] == [9, 18, 12, 24]
    assert legs[1][2] is None


@pytest.mark.parametrize("name", ["calendar_prices", "get_calendar", "get_price_graph"])
def test_calendar_return_has_its_own_departure_window(calendar_wire, name):
    stay = {"return_start": "2026-12-08", "return_end": "2026-12-09"} if name == "calendar_prices" else {"min_stay": 5}
    getattr(swoop, name)("JFK", "LAX", "2026-12-03", "2026-12-04",
        earliest_departure=0, latest_departure=24, earliest_arrival=0, latest_arrival=24,
        return_earliest_departure=10, return_latest_departure=20, **stay)
    legs = calendar_wire[-1][1][1][13]
    assert legs[0][2] == [0, 24, 0, 24]
    assert legs[1][2] == [10, 20, None, None]

# explore() Endpoint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `explore()` — swoop's fourth primitive — for Google Flights destination discovery, plus a `price_explore()` bridge and a `swoop explore` CLI command.

**Architecture:** New `swoop/_explore.py` calls the `GetExploreDestinations` RPC (session GET → `f.req` POST → nested-list parse), returning `ExploreResult`/`ExploreDestination` from `models.py`. Public `explore()`/`price_explore()` in `__init__.py` mirror the `deals()`/`price_deal()` shape. CLI mirrors `deals_cmd`. Prices are not in the RPC, so `price_explore()` composes via the existing `search()`/`price_selector()` path. A live canary in `tests/test_live_contract.py` guards response drift.

**Tech Stack:** Python ≥3.10, `primp` (TLS-impersonating HTTP), `click` (CLI), `pytest`. Reference modules: `swoop/_deals.py`, `swoop/rpc.py`, `swoop/cli/commands.py`.

**Spec:** `docs/superpowers/specs/2026-06-02-explore-endpoint-design.md` (read it; this plan implements its §10 delta).

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `swoop/models.py` | `ExploreDestination`, `ExploreResult` dataclasses + `to_search_kwargs()` | Modify (append) |
| `swoop/_explore.py` | RPC client: payload, session, parse, `fetch_explore()`, place_id resolution | Create |
| `swoop/__init__.py` | Public `explore()`, `price_explore()`; `__all__` exports | Modify |
| `swoop/cli/commands.py` | `explore_cmd` (mirrors `deals_cmd`) | Modify (append) |
| `swoop/cli/formatters.py` | `format_explore_{table,json,csv,brief}` | Modify (append) |
| `swoop/cli/__init__.py` | Register `explore_cmd` on the group | Modify |
| `tests/test_explore.py` | Offline unit tests (payload, parse, bridge, CLI) | Create |
| `tests/test_api_surface.py` | Frozen field + signature assertions | Modify |
| `tests/test_live_contract.py` | Live canary for explore | Modify |
| `tests/fixtures/responses/explore/*.txt` | Captured RPC responses for offline parse tests | Create (copy from pr-20) |
| `README.md`, `CHANGELOG.md`, `MIGRATION.md`, `CLAUDE.md` | Docs | Modify |

---

## Task 0: Branch + import reusable fixtures

**Files:**
- Create: `tests/fixtures/responses/explore/{jfk,sfo,lax,error}_response.txt`

- [ ] **Step 1: Create the feature branch from main**

```bash
git checkout main && git pull --ff-only
git checkout -b feat/explore-endpoint
```

- [ ] **Step 2: Copy the captured response fixtures from the PR branch**

The pr-20 branch has real captured responses we reuse for offline parse tests. (The row structure is identical whether or not prices are present, so these are valid parse fixtures.)

```bash
git fetch origin 'pull/20/head:pr-20' 2>/dev/null || true
mkdir -p tests/fixtures/responses/explore
for f in jfk_response sfo_response lax_response error_response; do
  git show pr-20:tests/fixtures/responses/explore/$f.txt > tests/fixtures/responses/explore/$f.txt
done
ls -1 tests/fixtures/responses/explore/
```

Expected: four `.txt` files listed.

- [ ] **Step 3: Commit**

```bash
git add tests/fixtures/responses/explore/
git commit -m "test: add explore RPC response fixtures"
```

---

## Task 1: `ExploreDestination` + `ExploreResult` models

**Files:**
- Modify: `swoop/models.py` (append after `Deal`/`DealsResult`)
- Test: `tests/test_api_surface.py:179` (add to `TestFrozenDataclassFields`)

- [ ] **Step 1: Write the failing frozen-field tests**

Add to `tests/test_api_surface.py` inside `class TestFrozenDataclassFields` (after `test_deals_result_currency_property`). Also add `ExploreDestination, ExploreResult` to the `from swoop.models import (...)` block at the top of the file.

```python
    def test_explore_destination_fields(self):
        expected = {
            "origin", "destination", "destination_name", "destination_country",
            "place_id", "latitude", "longitude",
            "departure_date", "return_date",
            "image_url", "secondary_image_url", "duration_minutes",
            "query_cabin", "query_adults",
        }
        assert self._field_names(ExploreDestination) == expected

    def test_explore_result_fields(self):
        expected = {
            "destinations", "origin", "origin_name", "origin_place_id",
            "origin_latitude", "origin_longitude",
        }
        assert self._field_names(ExploreResult) == expected
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_api_surface.py::TestFrozenDataclassFields::test_explore_destination_fields -v`
Expected: FAIL with `ImportError: cannot import name 'ExploreDestination'`.

- [ ] **Step 3: Implement the dataclasses**

Append to `swoop/models.py` (it already imports `dataclass`, `field`, `Optional`):

```python
@dataclass
class ExploreDestination:
    """A single destination suggestion from Google Flights Explore.

    Explore is destination *discovery* ("where could I go?"), not pricing:
    the RPC returns no price (see the design spec). ``departure_date`` /
    ``return_date`` are Google's per-destination suggestions; ``return_date``
    is ``None`` for one-way queries. ``origin`` and the ``query_*`` fields are
    denormalized so :func:`swoop.price_explore` / :meth:`to_search_kwargs` are
    self-contained, mirroring :class:`Deal`.
    """

    origin: str
    destination: Optional[str]          # destination IATA; None when Google omits it
    destination_name: str               # city / region / landmark display name
    destination_country: str
    place_id: str                       # Google Knowledge Graph id, e.g. "/m/0d6lp"
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    departure_date: Optional[str] = None
    return_date: Optional[str] = None
    image_url: Optional[str] = None
    secondary_image_url: Optional[str] = None
    duration_minutes: Optional[int] = None
    query_cabin: Optional[str] = None
    query_adults: int = 1

    def __repr__(self) -> str:
        code = self.destination or "?"
        parts = [f"{code} {self.destination_name} from {self.origin}"]
        if self.departure_date:
            parts.append(self.departure_date)
        return f"ExploreDestination({', '.join(parts)})"


@dataclass
class ExploreResult:
    """Result of an :func:`swoop.explore` destination-discovery query."""

    destinations: list[ExploreDestination] = field(default_factory=list)
    origin: str = ""
    origin_name: Optional[str] = None
    origin_place_id: Optional[str] = None
    origin_latitude: Optional[float] = None
    origin_longitude: Optional[float] = None

    def __repr__(self) -> str:
        return f"ExploreResult({len(self.destinations)} destinations from {self.origin})"
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_api_surface.py -k explore -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add swoop/models.py tests/test_api_surface.py
git commit -m "feat: add ExploreDestination and ExploreResult models"
```

---

## Task 2: `ExploreDestination.to_search_kwargs()`

**Files:**
- Modify: `swoop/models.py` (method on `ExploreDestination`)
- Test: `tests/test_explore.py` (create)

This mirrors `Deal.to_search_kwargs()` (`swoop/models.py:211`). `search()` takes `date` (not `departure_date`), `return_date`, `cabin`, `passengers`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_explore.py`:

```python
from swoop.models import ExploreDestination, Passengers


def _dest(**kw):
    base = dict(
        origin="JFK", destination="SFO", destination_name="San Francisco",
        destination_country="United States", place_id="/m/0d6lp",
        departure_date="2026-07-02", return_date="2026-07-10",
    )
    base.update(kw)
    return ExploreDestination(**base)


class TestToSearchKwargs:
    def test_roundtrip_kwargs(self):
        kw = _dest().to_search_kwargs()
        assert kw == {
            "origin": "JFK", "destination": "SFO",
            "date": "2026-07-02", "return_date": "2026-07-10",
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
        import pytest
        with pytest.raises(ValueError):
            _dest(destination=None).to_search_kwargs()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_explore.py::TestToSearchKwargs -v`
Expected: FAIL with `AttributeError: 'ExploreDestination' object has no attribute 'to_search_kwargs'`.

- [ ] **Step 3: Implement the method**

Add to `ExploreDestination` (before `__repr__`):

```python
    def to_search_kwargs(self) -> dict:
        """Convert this destination into keyword args for :func:`swoop.search`.

        Explore surfaces dates but not the actual itineraries; this rebuilds
        the search that produced this destination. Raises ``ValueError`` when
        ``destination`` is missing (Google occasionally omits the airport
        code), since a search needs a concrete destination.
        """
        if not self.destination:
            raise ValueError("ExploreDestination has no destination airport to search")
        kwargs: dict = {
            "origin": self.origin,
            "destination": self.destination,
            "date": self.departure_date,
        }
        if self.return_date:
            kwargs["return_date"] = self.return_date
        if self.query_cabin:
            kwargs["cabin"] = self.query_cabin
        if self.query_adults != 1:
            from .models import Passengers
            kwargs["passengers"] = Passengers(adults=self.query_adults)
        return kwargs
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_explore.py::TestToSearchKwargs -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add swoop/models.py tests/test_explore.py
git commit -m "feat: add ExploreDestination.to_search_kwargs bridge helper"
```

---

## Task 3: Confirm the priced/full-set payload form (D8 spike)

**Files:** none (investigation; record the result in a comment in Task 4)

The spec (§3.6, D8) found the browser uses a **place_id** origin + flag `4` and gets the full ~85 set, vs the IATA+`0` form's 24. We must learn the minimal unlock before writing the payload builder.

- [ ] **Step 1: Probe the three forms live**

```bash
python - <<'PY'
import json, re, urllib.parse
from swoop.rpc import _get_client
RPC=("https://www.google.com/_/FlightsFrontendUi/data/"
     "travel.frontend.flights.FlightsFrontendService/GetExploreDestinations")
c=_get_client(None,"chrome")
pg=c.get("https://www.google.com/travel/explore",headers={"accept":"text/html"},timeout=90).text
bl=re.search(r'"cfb2h":"([^"]+)"',pg); fs=re.search(r'"FdrFJe":"([^"]+)"',pg)
q=urllib.parse.urlencode({"bl":bl.group(1),"f.sid":fs.group(1),"hl":"en-US","soc-app":"162","soc-platform":"1","soc-device":"1","rt":"c"})
def call(origin, flag, tail2=True):
    ob=[[[[origin,flag]]]]
    filt=[None,None,1,None,[],1,[1,0,0,0],None,None,None,None,None,None,
          [[ob,[],None,0],[[],ob,None,0]],None,None,None,0]
    pl=[[],None,None,filt,None,1,None,0,None,1,[1004,833]]+([2] if tail2 else [])
    body=f"f.req={urllib.parse.quote(json.dumps([None,json.dumps(pl,separators=(',',':'))],separators=(',',':')))}&".encode()
    r=c.post(f"{RPC}?{q}",content=body,headers={"content-type":"application/x-www-form-urlencoded;charset=UTF-8","x-same-domain":"1","referer":"https://www.google.com/travel/explore"},timeout=90)
    t=r.text[4:] if r.text.startswith(")]}'") else r.text
    try:
        line=next(l for l in t.splitlines() if l.strip().startswith("[["))
        inner=next(json.loads(e[2]) for e in json.loads(line) if isinstance(e,list) and e and e[0]=="wrb.fr" and len(e)>2 and isinstance(e[2],str))
        return len(inner[3][0])
    except Exception as ex:
        return f"ERR {ex}"
print("IATA + flag 0:", call("JFK",0))
print("IATA + flag 4:", call("JFK",4))
print("place_id + flag 4:", call("/m/02_286",4))
PY
```

- [ ] **Step 2: Record the verdict**

Note which form returns the larger set:
- If `IATA + flag 4` already returns ~85 → **no place_id resolution needed**; the payload builder just uses flag `4`. Skip Task 5; set `PLACE_ID_REQUIRED = False`.
- If only `place_id + flag 4` returns ~85 → place_id resolution is required (Task 5); set `PLACE_ID_REQUIRED = True`.
- If both stay at 24 → keep the IATA+`0` form, accept the regional scope, and document it (spec D8 fallback). Skip Task 5.

Write the chosen form into the Task 4 payload builder's docstring.

---

## Task 4: Payload builder + response parser

**Files:**
- Create: `swoop/_explore.py`
- Test: `tests/test_explore.py`

- [ ] **Step 1: Write failing payload tests**

Append to `tests/test_explore.py`:

```python
import json
import urllib.parse
from swoop._explore import _build_explore_payload, _encode_explore_f_req


class TestBuildPayload:
    def test_roundtrip_has_two_segments(self):
        pl = _build_explore_payload("JFK", one_way=False)
        segs = pl[3][13]
        assert len(segs) == 2  # outbound + return

    def test_oneway_has_one_segment(self):
        pl = _build_explore_payload("JFK", one_way=True)
        segs = pl[3][13]
        assert len(segs) == 1

    def test_trip_type_flag(self):
        assert _build_explore_payload("JFK", one_way=False)[3][2] == 1
        assert _build_explore_payload("JFK", one_way=True)[3][2] == 2

    def test_cabin_and_stops(self):
        pl = _build_explore_payload("JFK", cabin="business", max_stops=0)
        assert pl[3][5] == 3                 # business
        assert pl[3][13][0][3] == 1          # max_stops=0 -> stops_val 1 (nonstop)

    def test_encoded_body_trailing_ampersand(self):
        body = _encode_explore_f_req(_build_explore_payload("JFK"))
        assert body.endswith(b"&")
        assert body.startswith(b"f.req=")
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_explore.py::TestBuildPayload -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'swoop._explore'`.

- [ ] **Step 3: Create `swoop/_explore.py` with payload + parser**

Use the origin form chosen in Task 3 (`_origin_block` below; default shown is the IATA+`0` form — change the `flag` and origin value per Task 3's verdict). The response-index map is from the design spec §7.2 (verified live).

```python
"""Google Flights Explore endpoint client (GetExploreDestinations).

Response row index map (verified live, see design spec §7):
  [0] place_id   [1] [lat, lon]   [2] name   [3] image_url
  [4] country    [7] secondary_image_url     [11] depart_date
  [12] return_date           [15] airport IATA   [17] duration_minutes
  origin metadata: inner[6][0] = [name, [lat,lon], place_id, IATA, ...]
The RPC returns NO price (prices are client-side on the map; see spec §3.7).
"""
from __future__ import annotations

import json
import logging
import urllib.parse
from typing import Any, Optional

from .builders import CABIN_CLASS_MAP, CabinClass
from .decoder import _safe_get
from .exceptions import SwoopParseError
from .models import ExploreDestination, ExploreResult, Passengers, TransportConfig
from .rpc import _apply_country, _encode_f_req_payload, _get_client, _post_with_retry

logger = logging.getLogger(__name__)

EXPLORE_PAGE_URL = "https://www.google.com/travel/explore"
EXPLORE_RPC_URL = (
    "https://www.google.com/_/FlightsFrontendUi/data/"
    "travel.frontend.flights.FlightsFrontendService/GetExploreDestinations"
)


def _origin_block(origin: str, flag: int = 0) -> list:
    # Per Task 3: IATA+0 (regional) or place_id+4 (full set).
    return [[[[origin, flag]]]]


def _build_explore_payload(
    origin: str,
    *,
    cabin: CabinClass = "economy",
    one_way: bool = False,
    max_stops: Optional[int] = None,
    origin_flag: int = 0,
) -> list[Any]:
    """Build the GetExploreDestinations f.req payload (see module docstring)."""
    cabin_code = CABIN_CLASS_MAP.get(cabin, 1)
    stops_val = 0 if max_stops is None else max_stops + 1
    ob = _origin_block(origin, origin_flag)
    trip_type = 2 if one_way else 1
    if one_way:
        segments = [[ob, [], None, stops_val]]
    else:
        segments = [[ob, [], None, stops_val], [[], ob, None, stops_val]]
    filters = [
        None, None, trip_type, None, [], cabin_code,
        [1, 0, 0, 0],
        None, None, None, None, None, None,
        segments,
        None, None, None, 0,
    ]
    return [[], None, None, filters, None, 1, None, 0, None, 1, [1004, 833], 2]


def _encode_explore_f_req(payload: list[Any]) -> bytes:
    return f"f.req={_encode_f_req_payload(payload)}&".encode()


def _extract_inner(text: str) -> list[Any]:
    stripped = text[4:].lstrip() if text.startswith(")]}'") else text
    lowered = stripped.lstrip().lower()
    if lowered.startswith("<!doctype") or lowered.startswith("<html") or "consent.google.com" in stripped:
        raise SwoopParseError("Explore RPC returned an HTML/consent page (likely blocked)")
    line = next((l.strip() for l in stripped.splitlines() if l.strip().startswith("[[")), stripped.strip())
    try:
        outer = json.loads(line)
    except json.JSONDecodeError as exc:
        raise SwoopParseError(f"Failed to parse Explore response JSON: {exc}") from exc
    for entry in outer if isinstance(outer, list) else []:
        if isinstance(entry, list) and entry and entry[0] == "wrb.fr" and len(entry) > 2 and isinstance(entry[2], str):
            try:
                return json.loads(entry[2])
            except json.JSONDecodeError as exc:
                raise SwoopParseError(f"Failed to parse inner Explore JSON: {exc}") from exc
    raise SwoopParseError("Explore response missing inner payload")


def _float_pair(value: Any) -> tuple[Optional[float], Optional[float]]:
    if not isinstance(value, list) or len(value) < 2:
        return (None, None)
    try:
        return (float(value[0]), float(value[1]))
    except (TypeError, ValueError):
        return (None, None)


def _opt_str(value: Any) -> Optional[str]:
    return value if isinstance(value, str) else None


def _opt_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_destination(row: list[Any], *, origin: str, cabin: str, adults: int) -> Optional[ExploreDestination]:
    place_id = _safe_get(row, [0])
    name = _safe_get(row, [2])
    if not isinstance(place_id, str) or not isinstance(name, str):
        return None
    lat, lon = _float_pair(_safe_get(row, [1]))
    country = _safe_get(row, [4])
    return ExploreDestination(
        origin=origin,
        destination=_opt_str(_safe_get(row, [15])),
        destination_name=name,
        destination_country=country if isinstance(country, str) else "",
        place_id=place_id,
        latitude=lat,
        longitude=lon,
        departure_date=_opt_str(_safe_get(row, [11])),
        return_date=_opt_str(_safe_get(row, [12])),
        image_url=_opt_str(_safe_get(row, [3])),
        secondary_image_url=_opt_str(_safe_get(row, [7])),
        duration_minutes=_opt_int(_safe_get(row, [17])),
        query_cabin=cabin,
        query_adults=adults,
    )


def parse_explore_payload(inner: list[Any], *, origin: str, cabin: str = "economy", adults: int = 1) -> ExploreResult:
    raw = _safe_get(inner, [3, 0])
    dests: list[ExploreDestination] = []
    if isinstance(raw, list):
        for row in raw:
            if isinstance(row, list):
                d = _parse_destination(row, origin=origin, cabin=cabin, adults=adults)
                if d is not None:
                    dests.append(d)
    orow = _safe_get(inner, [6, 0])
    if isinstance(orow, list):
        olat, olon = _float_pair(_safe_get(orow, [1]))
        return ExploreResult(
            destinations=dests,
            origin=_opt_str(_safe_get(orow, [3])) or origin,
            origin_name=_opt_str(_safe_get(orow, [0])),
            origin_place_id=_opt_str(_safe_get(orow, [2])),
            origin_latitude=olat,
            origin_longitude=olon,
        )
    return ExploreResult(destinations=dests, origin=origin)
```

- [ ] **Step 4: Run payload tests to verify they pass**

Run: `python -m pytest tests/test_explore.py::TestBuildPayload -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Write + run the parse test against a fixture**

Append to `tests/test_explore.py`:

```python
from pathlib import Path
from swoop._explore import _extract_inner, parse_explore_payload

FIX = Path(__file__).parent / "fixtures" / "responses" / "explore"


class TestParse:
    def test_parses_jfk_fixture(self):
        text = (FIX / "jfk_response.txt").read_text()
        inner = _extract_inner(text)
        result = parse_explore_payload(inner, origin="JFK")
        assert result.origin == "JFK"
        assert len(result.destinations) > 0
        d = result.destinations[0]
        assert d.destination_name
        assert d.place_id.startswith("/m/")
        assert d.query_cabin == "economy" and d.query_adults == 1

    def test_error_fixture_raises(self):
        import pytest
        text = (FIX / "error_response.txt").read_text()
        with pytest.raises(SwoopParseError):
            parse_explore_payload(_extract_inner(text), origin="JFK")
```

Run: `python -m pytest tests/test_explore.py::TestParse -v`
Expected: PASS (2 tests). If the error fixture's shape differs, adjust the assertion to match how `_extract_inner` surfaces it (it raises on the missing inner payload / error envelope).

- [ ] **Step 6: Commit**

```bash
git add swoop/_explore.py tests/test_explore.py
git commit -m "feat: add explore payload builder and response parser"
```

---

## Task 5: place_id resolution (ONLY if Task 3 verdict = PLACE_ID_REQUIRED)

**Files:**
- Modify: `swoop/_explore.py`
- Test: `tests/test_explore.py`

If Task 3 showed the IATA form is sufficient, **skip this task** and have `fetch_explore` (Task 6) call `_build_explore_payload(origin, origin_flag=0)`.

- [ ] **Step 1: Write the failing test (mocked autocomplete)**

```python
class TestResolvePlaceId:
    def test_returns_place_id_from_autocomplete(self, monkeypatch):
        from swoop import _explore
        captured = {}
        class FakeRes:
            status_code = 200
            text = ')]}\'\n[["wrb.fr",null,"[[[\\"/m/02_286\\",\\"New York\\"]]]"]]'
        class FakeClient:
            def get(self, url, **kw):
                return FakeRes()
            def post(self, url, **kw):
                captured["url"] = url
                return FakeRes()
        monkeypatch.setattr(_explore, "_get_client", lambda *a, **k: FakeClient())
        pid = _explore._resolve_place_id("JFK", transport=__import__("swoop").TransportConfig())
        assert pid == "/m/02_286"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_explore.py::TestResolvePlaceId -v`
Expected: FAIL with `AttributeError: ... '_resolve_place_id'`.

- [ ] **Step 3: Implement resolution**

Reverse-engineer the location-autocomplete RPC during this step (capture it the same way Task 3 captured the explore call: load the page, type the IATA into the origin box, watch the network). Add to `swoop/_explore.py`:

```python
def _resolve_place_id(origin_iata: str, *, transport: TransportConfig) -> Optional[str]:
    """Resolve an IATA code to its Google place_id via the location RPC.

    Returns None on any failure; callers fall back to the IATA origin form.
    """
    # Implementation: POST the autocomplete RPC captured during Task 5 build,
    # parse the first result's place_id ("/m/..."). Wrap in try/except and
    # return None on any parse/HTTP error so explore() degrades gracefully.
    ...
```

> NOTE TO IMPLEMENTER: this is the one step requiring live reverse-engineering. If the autocomplete RPC proves unstable, return `None` and document that explore uses the regional (IATA) scope — the spec's D8 fallback. Do not block the feature on it.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_explore.py::TestResolvePlaceId -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add swoop/_explore.py tests/test_explore.py
git commit -m "feat: resolve origin IATA to place_id for full explore set"
```

---

## Task 6: `fetch_explore()` (session + POST + parse)

**Files:**
- Modify: `swoop/_explore.py`
- Test: `tests/test_explore.py`

- [ ] **Step 1: Write the failing test (mocked transport)**

```python
class TestFetchExplore:
    def test_end_to_end_mocked(self, monkeypatch):
        from swoop import _explore
        page_html = 'x"cfb2h":"BL123"x"FdrFJe":"SID123"x'
        body_text = (FIX / "jfk_response.txt").read_text()
        calls = {}
        class FakeRes:
            def __init__(self, text, status=200): self.text = text; self.status_code = status
        class FakeClient:
            def get(self, url, **kw): return FakeRes(page_html)
            def post(self, url, content=None, **kw):
                calls["url"] = url; calls["body"] = content
                return FakeRes(body_text)
        monkeypatch.setattr(_explore, "_get_client", lambda *a, **k: FakeClient())
        result = _explore.fetch_explore("JFK")
        assert result.origin == "JFK"
        assert len(result.destinations) > 0
        assert b"f.req=" in calls["body"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_explore.py::TestFetchExplore -v`
Expected: FAIL with `AttributeError: ... 'fetch_explore'`.

- [ ] **Step 3: Implement `fetch_explore`**

Append to `swoop/_explore.py` (uses `_post_with_retry` from `rpc.py`, not a hand-rolled loop — see `swoop/rpc.py:387`):

```python
import re

PLACE_ID_REQUIRED = False  # set per Task 3 verdict


def _browser_params(page_html: str) -> dict[str, str]:
    params: dict[str, str] = {}
    if (m := re.search(r'"cfb2h":"([^"]+)"', page_html)):
        params["bl"] = m.group(1)
    if (m := re.search(r'"FdrFJe":"([^"]+)"', page_html)):
        params["f.sid"] = m.group(1)
    return params


def fetch_explore(
    origin: str,
    *,
    cabin: CabinClass = "economy",
    one_way: bool = False,
    max_stops: Optional[int] = None,
    passengers: Passengers = Passengers(),
    transport: TransportConfig = TransportConfig(),
) -> ExploreResult:
    client = _get_client(transport.proxy, transport.impersonate)
    page_url = _apply_country(EXPLORE_PAGE_URL, transport.country)
    rpc_url = _apply_country(EXPLORE_RPC_URL, transport.country)

    page = client.get(page_url, headers={"accept": "text/html", "accept-language": "en-US,en;q=0.9"}, timeout=transport.timeout)
    params = _browser_params(page.text)
    if params:
        query = urllib.parse.urlencode({**params, "hl": "en-US", "soc-app": "162", "soc-platform": "1", "soc-device": "1", "rt": "c"})
        rpc_url = f"{rpc_url}{'&' if '?' in rpc_url else '?'}{query}"

    origin_value, origin_flag = origin, 0
    if PLACE_ID_REQUIRED:
        pid = _resolve_place_id(origin, transport=transport)
        if pid:
            origin_value, origin_flag = pid, 4

    body = _encode_explore_f_req(_build_explore_payload(
        origin_value, cabin=cabin, one_way=one_way, max_stops=max_stops, origin_flag=origin_flag,
    ))
    headers = {
        "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
        "x-same-domain": "1",
        "referer": page_url,
    }
    res = _post_with_retry(client, rpc_url, body, headers, transport=transport)
    inner = _extract_inner(res.text)
    return parse_explore_payload(inner, origin=origin, cabin=cabin, adults=passengers.adults)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_explore.py::TestFetchExplore -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add swoop/_explore.py tests/test_explore.py
git commit -m "feat: add fetch_explore (session, post, parse)"
```

---

## Task 7: Public `explore()` + `__all__`

**Files:**
- Modify: `swoop/__init__.py`
- Test: `tests/test_explore.py`, `tests/test_api_surface.py`

- [ ] **Step 1: Write the failing validation + signature tests**

In `tests/test_explore.py`:

```python
class TestPublicExplore:
    def test_invalid_origin_raises(self):
        import swoop, pytest
        with pytest.raises(swoop.SwoopValidationError):
            swoop.explore("xx")

    def test_invalid_cabin_raises(self):
        import swoop, pytest
        with pytest.raises(swoop.SwoopValidationError):
            swoop.explore("JFK", cabin="ultra")  # type: ignore[arg-type]

    def test_invalid_max_stops_raises(self):
        import swoop, pytest
        with pytest.raises(swoop.SwoopValidationError):
            swoop.explore("JFK", max_stops=9)
```

In `tests/test_api_surface.py`, add to `TestFrozenExports` the names `explore`, `price_explore`, and add a signature check in `TestSearchSignature` (mirror its existing `deals` check) asserting `explore`'s params: `{"origin", "cabin", "one_way", "max_stops", "passengers", "transport"}`.

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_explore.py::TestPublicExplore -v`
Expected: FAIL with `AttributeError: module 'swoop' has no attribute 'explore'`.

- [ ] **Step 3: Implement `explore()` in `__init__.py`**

Add near `deals()` (reuse the existing validation helpers `validate_iata_code`, `validate_cabin`, `validate_adults` already used by `deals()`; check their exact names in `__init__.py` and match):

```python
def explore(
    origin: str,
    *,
    cabin: CabinClass = "economy",
    one_way: bool = False,
    max_stops: Optional[int] = None,
    passengers: Passengers = Passengers(),
    transport: TransportConfig = TransportConfig(),
) -> ExploreResult:
    """Discover destinations you could fly to from an origin ("where could I go?").

    swoop's fourth primitive. Returns destination suggestions with images,
    coordinates, and Google's suggested dates — one-way or roundtrip. The
    Explore RPC returns no price; use :func:`price_explore` to price a chosen
    destination. See the design spec for the deals-vs-explore distinction.

    Args:
        origin: Origin airport IATA code.
        cabin: Cabin class. Defaults to "economy".
        one_way: One-way (True) or roundtrip (False, default).
        max_stops: 0 (nonstop), 1, or 2. None = any.
        passengers: Passenger counts.
        transport: HTTP transport configuration.

    Returns:
        An :class:`ExploreResult`.
    """
    validate_iata_code(origin)
    validate_cabin(cabin)
    validate_adults(passengers.adults)
    if max_stops is not None and not (0 <= max_stops <= 2):
        raise SwoopValidationError("max_stops must be 0, 1, or 2")
    from ._explore import fetch_explore
    return fetch_explore(
        origin, cabin=cabin, one_way=one_way, max_stops=max_stops,
        passengers=passengers, transport=transport,
    )
```

Add `ExploreDestination`, `ExploreResult` to the `from .models import (...)` block and `explore` (plus `ExploreDestination`, `ExploreResult`) to `__all__`.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_explore.py::TestPublicExplore tests/test_api_surface.py -k explore -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add swoop/__init__.py tests/test_explore.py tests/test_api_surface.py
git commit -m "feat: add public explore() entry point"
```

---

## Task 8: `price_explore()` bridge

**Files:**
- Modify: `swoop/__init__.py`
- Test: `tests/test_explore.py`

Mirror `price_deal()` (`swoop/__init__.py:722`).

- [ ] **Step 1: Write the failing test**

```python
class TestPriceExplore:
    def test_prices_cheapest(self, monkeypatch):
        import swoop
        from swoop.models import ExploreDestination
        from swoop.decoder import SearchResult  # adjust import to actual SearchResult location
        dest = ExploreDestination(
            origin="JFK", destination="SFO", destination_name="San Francisco",
            destination_country="US", place_id="/m/0d6lp",
            departure_date="2026-07-02", return_date="2026-07-10",
        )
        fake_search = SearchResult(results=[
            type("Opt", (), {"price": 400, "selector": "sel-cheap"})(),
            type("Opt", (), {"price": 250, "selector": "sel-cheapest"})(),
        ])
        monkeypatch.setattr(swoop, "search", lambda **kw: fake_search)
        captured = {}
        monkeypatch.setattr(swoop, "price_selector", lambda sel, **kw: captured.setdefault("sel", sel))
        swoop.price_explore(dest)
        assert captured["sel"] == "sel-cheapest"

    def test_no_results_returns_none(self, monkeypatch):
        import swoop
        from swoop.models import ExploreDestination
        from swoop.decoder import SearchResult
        dest = ExploreDestination("JFK", "SFO", "San Francisco", "US", "/m/0d6lp", departure_date="2026-07-02")
        monkeypatch.setattr(swoop, "search", lambda **kw: SearchResult(results=[]))
        assert swoop.price_explore(dest) is None
```

(Adjust `SearchResult` import to wherever it lives — check `swoop/__init__.py`'s imports.)

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_explore.py::TestPriceExplore -v`
Expected: FAIL with `AttributeError: ... 'price_explore'`.

- [ ] **Step 3: Implement `price_explore()`**

```python
def price_explore(
    destination: ExploreDestination,
    *,
    transport: TransportConfig = TransportConfig(),
) -> Optional[PriceResult]:
    """Get the current bookable price for an explore destination.

    Runs :func:`search` for the destination's route/dates and prices the
    cheapest matching itinerary via :func:`price_selector`. Returns ``None``
    if no itineraries match. Raises ``ValueError`` if the destination has no
    airport code.
    """
    result = search(transport=transport, **destination.to_search_kwargs())
    if not result.results:
        return None
    cheapest = min(
        result.results,
        key=lambda opt: opt.price if opt.price is not None else float("inf"),
    )
    return price_selector(cheapest.selector, transport=transport)
```

Add `price_explore` to `__all__`.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_explore.py::TestPriceExplore -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add swoop/__init__.py tests/test_explore.py
git commit -m "feat: add price_explore bridge"
```

---

## Task 9: CLI formatters

**Files:**
- Modify: `swoop/cli/formatters.py`
- Test: `tests/test_explore.py`

Mirror the four `format_deals_*` functions. Reuse the existing CSV formula-injection escape helper in `formatters.py` (find it — it prefixes values starting with `= + - @ \t \r`).

- [ ] **Step 1: Write failing formatter tests**

```python
class TestFormatters:
    def _result(self):
        from swoop.models import ExploreResult, ExploreDestination
        return ExploreResult(
            origin="JFK", origin_name="New York",
            destinations=[ExploreDestination(
                "JFK", "SFO", "San Francisco", "United States", "/m/0d6lp",
                departure_date="2026-07-02", return_date="2026-07-10", duration_minutes=380,
            )],
        )

    def test_json_shape(self):
        import json
        from swoop.cli.formatters import format_explore_json
        out = json.loads(format_explore_json(self._result(), cabin="economy"))
        assert out["origin"]["code"] == "JFK"
        assert out["destinations"][0]["destination"] == "SFO"

    def test_csv_escapes_formula(self):
        from swoop.cli.formatters import format_explore_csv
        from swoop.models import ExploreResult, ExploreDestination
        r = ExploreResult(origin="JFK", destinations=[ExploreDestination(
            "JFK", "SFO", "=DANGER", "US", "/m/0d6lp")])
        assert "'=DANGER" in format_explore_csv(r)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_explore.py::TestFormatters -v`
Expected: FAIL with `ImportError: cannot import name 'format_explore_json'`.

- [ ] **Step 3: Implement the four formatters**

Add `format_explore_table`, `format_explore_json`, `format_explore_csv`, `format_explore_brief` to `swoop/cli/formatters.py`, mirroring `format_deals_*`. Columns: `# | Destination (name, country) | Airport | Dates | Duration`. JSON shape:

```python
def format_explore_json(result, *, cabin):
    import json
    return json.dumps({
        "query": {"origin": result.origin, "cabin": cabin},
        "origin": {"code": result.origin, "name": result.origin_name,
                   "place_id": result.origin_place_id,
                   "latitude": result.origin_latitude, "longitude": result.origin_longitude},
        "total_destinations": len(result.destinations),
        "destinations": [
            {"index": i + 1, "destination": d.destination, "destination_name": d.destination_name,
             "destination_country": d.destination_country, "place_id": d.place_id,
             "latitude": d.latitude, "longitude": d.longitude,
             "departure_date": d.departure_date, "return_date": d.return_date,
             "duration_minutes": d.duration_minutes,
             "image_url": d.image_url, "secondary_image_url": d.secondary_image_url}
            for i, d in enumerate(result.destinations)
        ],
    }, indent=2)
```

Implement `format_explore_table` with `rich.table` (copy structure from `format_deals_table`), `format_explore_csv` with the formula-escape helper over the same columns as the JSON destination dict, and `format_explore_brief` as one compact line per destination.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_explore.py::TestFormatters -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add swoop/cli/formatters.py tests/test_explore.py
git commit -m "feat: add explore CLI formatters"
```

---

## Task 10: CLI `explore` command + registration

**Files:**
- Modify: `swoop/cli/commands.py`, `swoop/cli/__init__.py`
- Test: `tests/test_explore.py`

Mirror `deals_cmd` (`swoop/cli/commands.py:707`) and its `_output_options` decorator (`:243`). Add `--one-way`; reuse `--destination` / `--exclude-destination` / `--region` / `--trip-length` from the deals command. Validate origin via `IATA_CODE` (`swoop/cli/utils.py:113`) — gives the rich error.

- [ ] **Step 1: Write the failing CLI tests**

```python
from click.testing import CliRunner
from swoop.cli import main


class TestExploreCLI:
    def test_help(self):
        out = CliRunner().invoke(main, ["explore", "--help"])
        assert out.exit_code == 0
        assert "explore" in out.output.lower()
        assert "--one-way" in out.output

    def test_bad_iata_rich_error(self):
        out = CliRunner().invoke(main, ["explore", "xx"])
        assert out.exit_code == 2
        assert "3 uppercase letters" in out.output  # rich IATA error, not deals' weak one

    def test_json_output(self, monkeypatch):
        import swoop
        from swoop.models import ExploreResult, ExploreDestination
        monkeypatch.setattr(swoop, "explore", lambda *a, **k: ExploreResult(
            origin="JFK", destinations=[ExploreDestination("JFK","SFO","San Francisco","US","/m/0d6lp")]))
        out = CliRunner().invoke(main, ["explore", "JFK", "-o", "json", "-q"])
        assert out.exit_code == 0
        assert '"destination": "SFO"' in out.output
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_explore.py::TestExploreCLI -v`
Expected: FAIL with `Error: No such command 'explore'`.

- [ ] **Step 3: Implement `explore_cmd` and register it**

Add `explore_cmd` to `swoop/cli/commands.py` (decorators mirror `deals_cmd`: `@click.command("explore")`, `@click.argument("origin", type=IATA_CODE)`, the shared options, `@_output_options([...])`, plus `--one-way` (`is_flag=True`) and the deals discovery filters). Body: build `Passengers`/`TransportConfig`, call `swoop.explore(...)`, apply client-side `--destination`/`--exclude-destination`/`--region`/`--trip-length` filters to `result.destinations` (reuse `_deals_filter` predicates where shapes match; otherwise inline the same checks), dispatch to the formatter, map exceptions to exit codes exactly as `deals_cmd` does (validation→2, rate/HTTP→3, parse→4, no results→1). Then in `swoop/cli/__init__.py` add `main.add_command(explore_cmd)` after line 20 and import it.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_explore.py::TestExploreCLI -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add swoop/cli/commands.py swoop/cli/__init__.py tests/test_explore.py
git commit -m "feat: add swoop explore CLI command"
```

---

## Task 11: Full offline suite + typecheck gate

**Files:** none (verification)

- [ ] **Step 1: Run the offline suite**

Run: `make check` (typecheck + `pytest -m 'not live'`)
Expected: all pass. Fix any pyright complaints in `_explore.py` (e.g. `list[Any]` annotations) before continuing.

- [ ] **Step 2: Commit any fixes**

```bash
git add -A && git commit -m "chore: satisfy typecheck and offline test gate for explore"
```

---

## Task 12: Live canary

**Files:**
- Modify: `tests/test_live_contract.py`
- Test: itself (run with `-m live`)

Mirror the shopping canary (`tests/test_live_contract.py:253`): `pytestmark = pytest.mark.live` is already module-level. Tolerate variable count / scope (assert `>= 1`, not an exact number).

- [ ] **Step 1: Write the live canary test**

Add a class to `tests/test_live_contract.py`:

```python
class TestExploreContract:
    def test_explore_returns_destinations(self):
        result = swoop.explore("JFK", transport=TransportConfig(timeout=30, retries=1))
        assert result.origin == "JFK"
        assert len(result.destinations) >= 1, "Expected at least one live explore destination"
        d = result.destinations[0]
        assert d.destination_name
        assert d.place_id.startswith("/m/")
        # No price is expected from this RPC (design spec §3.7); just metadata.

    def test_explore_oneway_has_no_return_dates(self):
        result = swoop.explore("JFK", one_way=True, transport=TransportConfig(timeout=30, retries=1))
        assert len(result.destinations) >= 1
        assert all(d.return_date is None for d in result.destinations), \
            "One-way explore should have no return dates"
```

- [ ] **Step 2: Run live (manual / CI only)**

Run: `python -m pytest tests/test_live_contract.py::TestExploreContract -v -m live`
Expected: PASS against the real RPC. (Excluded from normal CI by the `not live` marker; runs in `live-canary.yml`.)

- [ ] **Step 3: Commit**

```bash
git add tests/test_live_contract.py
git commit -m "test: add explore live canary"
```

---

## Task 13: Docs

**Files:**
- Modify: `README.md`, `CHANGELOG.md`, `MIGRATION.md`, `CLAUDE.md`

- [ ] **Step 1: README "fourth primitive" section**

Add an `### Destination discovery (explore)` section after the deals section, matching its pattern: one-line contrast of all four primitives, a short Python example using `explore()` + `price_explore()`, and notes that it's metadata-only (no price) and supports one-way.

```python
from swoop import explore, price_explore

result = explore("JFK")                 # roundtrip; one_way=True for one-way
for d in result.destinations[:5]:
    print(f"{d.destination_name:20} {d.destination}  {d.departure_date}")

price = price_explore(result.destinations[0])   # price a chosen destination
```

- [ ] **Step 2: CHANGELOG `[Unreleased]`**

Add an `### Added` bullet block: `explore()` + `swoop explore` CLI; `price_explore()` bridge; `ExploreDestination`/`ExploreResult`; one-way + roundtrip; metadata-only (no price in the RPC).

- [ ] **Step 3: MIGRATION.md + CLAUDE.md**

Add a short MIGRATION note (new public API). Add two CLAUDE.md gotchas: "Explore RPC returns no prices — use `price_explore()`"; "Explore result count is geographic-scope-driven (IATA origin = regional subset, place_id origin = full set)".

- [ ] **Step 4: Update CLAUDE.md architecture map**

Add `_explore.py` to the module list and the explore flow line, mirroring the deals entries.

- [ ] **Step 5: Commit**

```bash
git add README.md CHANGELOG.md MIGRATION.md CLAUDE.md
git commit -m "docs: document explore() and price_explore()"
```

---

## Task 14: Update the existing PR #20 (close-out)

- [ ] **Step 1: Push the branch and open/repoint the PR**

```bash
git push -u origin feat/explore-endpoint
```

- [ ] **Step 2: Reference and supersede PR #20**

Open a PR for `feat/explore-endpoint` whose description links PR #20, summarizes the design-spec rationale (metadata-only after the price trace, one-way support, place_id full-set, `destination_*` model, `price_explore` bridge, live canary), and closes #20 as superseded. Use the repo's PR template.

---

## Self-Review

- **Spec coverage:** D1 (own endpoint → Task 4/6/7), D2 (metadata-only → Task 4 parser has no price; canary asserts metadata), D3 (`price_explore`, no `--prices` → Task 8/10), D4 (`origin`+query context → Task 1/4), D5 (no `distance`/`parent_place_id` → Task 1 field set), D6 (one-way → Task 4/6/7/12), D7 (`destination_*` names → Task 1), D8 (place_id → Task 3/5). DX contract (shared vocab, rich IATA error, Examples block, canary) → Tasks 10/12. Docs → Task 13.
- **Placeholder scan:** the only intentionally-open step is Task 5 Step 3 (live autocomplete reverse-engineering), gated behind Task 3's verdict and with a documented `None`-fallback so it never blocks. Everything else has concrete code.
- **Type consistency:** `_build_explore_payload(origin, *, cabin, one_way, max_stops, origin_flag)`, `fetch_explore(...)`, and `explore(...)` share the same param names across Tasks 4/6/7. `to_search_kwargs()` produces `date`/`return_date` keys matching `search()`'s real signature (verified at `models.py:211`/`__init__.py:700`).

---

## Execution Handoff

**Two execution options:**
1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks.
2. **Inline Execution** — execute tasks in this session with checkpoints.

Note: Tasks 3 and 5 require live network access (RPC probing / autocomplete reverse-engineering); the rest are offline and TDD-driven.

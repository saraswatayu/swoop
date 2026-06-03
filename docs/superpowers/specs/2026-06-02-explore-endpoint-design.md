# swoop `explore()` — Design Spec
- **Date:** 2026-06-02 (rev 3, after price-call trace)
  
- **Status:** Approved (go, 2026-06-02) — ship for the discovery/agentic segment. Next: implementation plan.
  
- **Scope:** Add a destination-discovery endpoint (`explore()`) to swoop, derived from first principles, superseding the as-written approach of PR #20.
  
- **Base:** PR #20 (`feat/explore`) is mergeable and green, but this spec reshapes it. Section 10 lists the concrete keep / fix / add delta.
  

* * *
## 1. Summary
Add `explore()` as swoop's **fourth primitive**: "where could I go from here?" It calls Google Flights' `GetExploreDestinations` RPC and returns a list of destinations (name, country, coordinates, images, suggested dates, flight duration) from one origin, one-way or roundtrip. A live browser trace settled the price question: the map's prices are not reliably extractable from this endpoint, so explore is metadata-only and prices come from composition (`price_explore()`). The CLI surface matches the shared `search`/`deals`/`price` vocabulary exactly.

* * *
## 2. Problem & intent
swoop has three primitives today:

- `search()` — "what flights from A to B?"
  
- `price()` — "how much for this exact flight?"
  
- `deals()` — "where's cheap from here right now?"
  

The missing fourth intent: **destination discovery for inspiration** — "I want to go somewhere from JFK, show me options." Google Flights serves this with its Explore map.
### 2a. deals vs explore, plainly (addressing the recurring confusion)
Both answer the same _question_ — "where can I go from JFK?" — and both return a **list of destinations**, not specific flights. That's the overlap. The difference is _why a place is on the list_ and _what you get back_:

- `deals()` **is a bargain hunter.** A place is on the list because it's **cheap right now**. You get the price and the discount. At JFK today it returned PIT, CLE, BUF — cheap regional hops nobody daydreams about.
  
- `explore()` **is an inspiration board.** A place is on the list because it's **interesting to visit**. You get images and good suggested dates, but no price (from this RPC). At JFK today it returned Bryce Canyon, Bozeman, Seattle — places you'd actually want to go.
  

Same question, different _answer set_ because of different selection criteria. Some places appear in both (popular and cheap, e.g. SFO). Many don't: the JFK overlap was 11 shared, 13 explore-only, 19 deals-only. Google itself ships these as two separate products (a "cheap flights" feed and an Explore map) for the same reason — "find me a bargain" and "inspire my next trip" are different moods. swoop mirrors that with two sibling primitives.

### 2b. Who actually consumes this, and is it worth it (blunt)

Concrete consumers, most to least compelling:

1. **LLM / agentic travel planners.** "Where could I go from SFO for a week?" is a natural tool call. `explore("SFO")` returns ~85 structured candidates (name, country, coords, dates, image) for the agent to reason over, then `price_explore()` on the shortlist. This is the strongest and fastest-growing use, and nothing else in swoop answers the open-ended "where" for an agent.
2. **Discovery / inspiration UIs.** A "from your home airport" map or grid with photos and good dates. explore is the backend; `deals` (bargains only) and `search` (needs a destination) can't build it.
3. **Content / newsletter generation.** "20 places to fly from Boston," with images, programmatically.

Who does **not** benefit: price-tracking consumers — the Perch-style production user swoop is known for. They already know the route and want prices and drops; they use `search` / `deals` / `price`. explore is irrelevant to them.

**Blunt value:** explore is **not** universal, tremendous value. It is a strong primitive for the discovery / inspiration / agentic segment and irrelevant to the price-tracking segment. The no-price reality bounds it: explore alone gives *inspiration*, and making it actionable ("where can I afford to go") needs the `price_explore()` step, whereas `deals()` gives priced discovery in one call. So explore widens the funnel (inspiration-weighted, ~85 worldwide, one-way, images) but it is a discovery tool, not a pricing tool. Worth shipping **iff** swoop wants to serve discovery/agentic consumers, not only price-tracking — see the go/no-go below.

* * *
## 3. What we verified (live de-risking)
Live probes against the real RPC (origin JFK, 2026-06-02):

1. **No prices in** `GetExploreDestinations`**.** `USD` 0 times, `$` 0 times. It returns place id, name, country, lat/long, image URLs, suggested depart/return dates, airport code, flight duration. Index `[17]` is duration minutes (240/300/420), not price.
  
2. **One-way is supported.** `trip_type=2` + a single outbound segment returns destinations with departure dates and **no** return dates (0/24). The PR hardcodes roundtrip; the endpoint does not require it.
  
3. **Count is driven by geographic scope, not viewport.** Result count held at 24 across viewports from 400×300 to 12000×9000. The response echoes a bounding box at `inner[2]` (`[[49,-66],[23,-125]]` = continental US), and every live result was a US city. The fixtures' 85 were simply a wider geographic scope.
  
4. **Prices exist in the Explore product, via a different call.** `inner[5]` carries the UI's filter metadata (price/duration ranges, airline alliances), so a price source feeds the map. It is not `GetExploreDestinations`. Finding it is the open item in §11.
  
5. **deals already does priced origin-discovery**, roundtrip-only, ~30 results, with price/typical/discount. explore and deals overlap on the question but differ on selection and payload (§2a).
  
6. **The full destination set needs a place_id origin.** Driving the real page in Chrome, the map fires `GetExploreDestinations` with the origin as a Google **place_id** (`/m/02_286` = New York) plus a trailing flag `4`, and returns the full **85-destination** set (worldwide). The IATA+`0` form the PR uses returns only **24** US-scoped results. (Viewport does not change this; geographic scope does — §3.3.)
  
7. **Prices render on the map but are not cleanly in the response.** ~85 `$` prices render, yet the destination rows carry no price (29 fields, none numeric-price), the only extra response slot is US map-outline geometry (a 661-point polygon), and there is no per-destination price array. The prices are client-computed or fetched lazily per pin; bulk extraction would be fragile reverse-engineering of an unstable signal. (Earlier "64/64 integer matches" were coincidental in an 80KB coordinate-heavy body.)
  

* * *
## 4. Design decisions
### D1. explore is its own endpoint (the fourth primitive). **Decided.**
Distinct Google RPC, distinct algorithm, distinct destination set. Folding into `deals()` was rejected: we don't control deals' destination selection (Google picks it server-side), so enrichment can only add columns to bargains, not deliver explore's inspiration set.
### D2. Metadata-only. **Decided (was provisional; now traced).**
A live browser trace settled it (§3.7): the Explore map shows prices, but they are not reliably extractable from `GetExploreDestinations` — no per-destination price in the response, prices client-computed or fetched lazily per pin. Chasing a bulk price would be fragile reverse-engineering of an unstable signal. So explore is metadata-only and never fakes a price. Prices come from composition (D3).
### D3. Pricing composes via `price_explore()`; no `--prices` flag. **Decided.**
With D2 resolved to metadata-only, the bridge is the reliable price path and earns its place. `price_explore(dest)` is **not** a trivial alias: it encapsulates the real four-step dance (build a search from the destination, run the staged search, pick the cheapest itinerary, price its selector) — the same work `price_deal()` hides. No `--prices` flag — `deals` has none, and a flag firing N staged searches would be slow and rate-limit-prone. `ExploreDestination.to_search_kwargs()` ships as the composition seam (`search(**dest.to_search_kwargs())` covers the general case).

`search_explore()` — a hypothetical `search_explore(dest) -> SearchResult` mirroring the existing `search_deal(deal)` — is **not** shipped. `to_search_kwargs()` makes it a one-liner; add it only if users ask.
### D4. `ExploreDestination` carries `origin` + query context. **Decided.**
So `price_explore(dest)` / `to_search_kwargs()` are self-contained, mirroring how `Deal` denormalizes `origin` and stores `query_cabin` / `query_adults`. The PR's `ExploreDestination` has no `origin` (it lives only on `ExploreResult`), which would force the bridge to take two arguments.
### D5. Drop always-null fields. **Decided.**
`distance` (`[8]`) and `parent_place_id` (`[19]`) were null across all observed rows. Public fields are frozen forever (`tests/test_api_surface.py`); we don't ship fields we can't observe populated. Adding later is non-breaking.
### D6. Support one-way and roundtrip. **Decided (corrected — was roundtrip-only).**
§3.2 verified one-way works. explore takes `one_way: bool = False` (roundtrip default, matching the common explore case and deals' behavior). For one-way, `return_date` is `None`. This makes explore strictly more flexible than deals, which is roundtrip-only.
### D7. Field naming: a consistent `destination_*` family. **Decided.**
From first principles, names should be both accurate and consistent with `Deal`. `Deal` uses `destination` (IATA), `destination_city`, `destination_country`. explore's place can be a region or landmark, not a city, so `destination_city` would lie. Resolution: `destination` (IATA, matches `Deal`), `destination_name` (accurate for cities/regions/parks), `destination_country` (matches `Deal`). A consistent prefix family that doesn't misname anything.
### D8. Pursue the full destination set via a place_id origin. **Decided (one impl detail to confirm).**
§3.6 showed the IATA+`0` form returns ~24 (a US-scoped subset) while the place_id+`4` form returns the full ~85. The richer set is clearly the better product, so explore resolves the origin IATA to its Google place_id (a small location lookup) before the call, and the public API still takes a plain IATA `origin`. Open impl question, ~30-min probe during build: confirm whether the unlock is the place_id, the `4` flag, or both. If place_id resolution proves flaky, fall back to the IATA form and document the smaller (regional) scope.

* * *
## 5. Public API
### `ExploreDestination` (new dataclass in `models.py`)
| Field | Type | Notes |
|-------|------|-------|
| `origin` | `str` | Origin IATA (denormalized for self-contained bridge) |
| `destination` | `Optional[str]` | Destination IATA; `None` when Google omits it (bridge skips those) |
| `destination_name` | `str` | Display name — city, region, or landmark ("Bryce Canyon National Park") |
| `destination_country` | `str` | Country name; `""` if absent |
| `place_id` | `str` | Google Knowledge Graph id (`/m/0d6lp`) — stable identity |
| `latitude` | `Optional[float]` | |
| `longitude` | `Optional[float]` | |
| `departure_date` | `Optional[str]` | Suggested outbound (YYYY-MM-DD) |
| `return_date` | `Optional[str]` | Suggested return; `None` for one-way |
| `image_url` | `Optional[str]` | Primary destination image |
| `secondary_image_url` | `Optional[str]` | Secondary image (observed populated) |
| `duration_minutes` | `Optional[int]` | Approximate flight duration |
| `query_cabin` | `Optional[str]` | Cabin used for the query (bridge context) |
| `query_adults` | `int` | Adults used for the query (bridge context) |

Method: `to_search_kwargs() -> dict` — mirrors `Deal.to_search_kwargs()`; reconstructs a `search()` call (origin, destination, depart, return-if-roundtrip, cabin, passengers).
### `ExploreResult` (new dataclass in `models.py`)
| Field | Type | Notes |
|-------|------|-------|
| `destinations` | `list[ExploreDestination]` | |
| `origin` | `str` | Origin IATA |
| `origin_name` | `Optional[str]` | |
| `origin_place_id` | `Optional[str]` | |
| `origin_latitude` | `Optional[float]` | |
| `origin_longitude` | `Optional[float]` | |
### Functions (in `__init__.py`, exported in `__all__`, added to `tests/test_api_surface.py`)
```python
def explore(
    origin: str,
    *,
    cabin: CabinClass = "economy",
    one_way: bool = False,
    max_stops: Optional[int] = None,   # validated here, not just in CLI
    passengers: Passengers = Passengers(),
    transport: TransportConfig = TransportConfig(),
) -> ExploreResult: ...
# Shipped only if D2 leaves explore without native prices (see D3):
def price_explore(
    destination: ExploreDestination,
    *,
    transport: TransportConfig = TransportConfig(),
) -> PriceResult: ...
```

* * *
## 6. CLI surface (DX contract, from the /devex-review audit)
`swoop explore ORIGIN [OPTIONS]`

**Shared vocabulary — identical to** `search`**/**`deals`**/**`price`**:**`-c/--cabin` (default economy), `--one-way`, `-n/--nonstop`, `--max-stops [0-2]`, `-p/--passengers`, `--country`, `--proxy`, `--timeout` (90), `--retries` (2), `-l/--limit`, `-o/--output [table|json|csv|brief]`, `--no-color`, `-q/--quiet`, `-v/--verbose`.

**Discovery filters — mirror** `deals` **(client-side):**`--destination` (whitelist IATA, repeatable), `--exclude-destination` (repeatable), `--region [...]`, `--trip-length MIN-MAX` (roundtrip only). Deferred: `--depart-window` (filters Google's suggested dates — confusing for v1); `--min-discount` (no discount data).

**No** `--prices` **flag** (see D3).

**Examples block** in the docstring, including the jq idiom:

```
swoop explore JFK
swoop explore JFK --one-way --region europe
swoop explore JFK -o json -q | jq '.destinations[0]'
```

**Validation:** origin via the Click `IATA_CODE` type (like `search`), so the error is the rich one (`'XX' is not a valid IATA airport code. Codes are 3 uppercase letters (e.g. JFK, LAX).` + usage), not deals' weaker inline message. Fix the inconsistency, don't copy the worse half.

**Exit codes:** align with `deals`/`search` (validation = 2; verify no-results / rate-limit / parse during implementation).

* * *
## 7. Internals (`swoop/_explore.py`)
Keep the PR's structure (page GET for session params, `f.req` payload, nested-list parse), with:

1. **Reuse shared transport.** Use `rpc._post_with_retry` instead of a re-implemented 429 loop. Keep `_get_client` / `_apply_country`.
  
2. **In-code schema map.** Comment the verified row indices (`[0]` place_id, `[1]` [lat,lon], `[2]` name, `[3]` image, `[4]` country, `[7]` secondary image, `[11]` depart, `[12]` return, `[15]` airport, `[17]` duration mins; origin at `inner[6][0]`; bbox at `inner[2]`). Index parsing is fragile; the map is the mitigation.
  
3. **Anti-bot detection.** Mirror `_deals`: raise `SwoopParseError` on HTML / consent interstitial, so a block never returns silently as "0 destinations."
  
4. **Remove dead code** (the unreachable trailing `return`).
  
5. **Validate** `max_stops` in `explore()` (0-2), not only the CLI.
  
6. **Trip-type toggle.** Build one or two segments from `one_way`; set `filters[2]` accordingly (1 roundtrip, 2 one-way — verified).
  
7. **place_id origin (D8).** Resolve the IATA `origin` to a Google place_id and send the place_id+`4` origin form to get the full ~85-destination set. Confirm the exact unlock during build; fall back to the IATA form if resolution is unreliable.
  

* * *
## 8. Testing
- **Unit** (`tests/test_explore.py`): payload building for both trip types, response parsing against fixtures, `to_search_kwargs()`, `price_explore` wiring if shipped, CLI for all four formats, and the edge cases the PR omits (max_stops out of range, missing origin metadata, partial rows, non-429 HTTP, one-way response with no return dates).
  
- **Live canary (hard gate).** Add an explore contract test to `tests/test_live_contract.py` (run by `.github/workflows/live-canary.yml`). The PR ships mock-only tests and never touches this file — that is the credibility gate every other endpoint clears. Tolerate variable count / scope the way the deals canary tolerates quirks.
  
- `tests/test_api_surface.py` updated for the new dataclasses + functions.
  

* * *
## 9. Docs
- README "fourth primitive" section using the deals-section pattern: one-line contrast of all four primitives, a short example, and the no-price / one-way-capable properties.
  
- CHANGELOG `[Unreleased]` entry; MIGRATION note; CLAUDE.md gotchas (no-price-in-RPC; geographic-scope count behavior).
  

* * *
## 10. Delta from PR #20
**Keep:** `_explore.py` structure, payload builder shape, fixtures, four output formats, CSV formula-injection escape, defensive `_safe_get` parsing.

**Fix:** add `origin` + query context + `to_search_kwargs()`; rename to the `destination_*` family (D7); drop `distance`/`parent_place_id`; validate `max_stops` in `explore()`; validate origin via Click `IATA_CODE`; use `_post_with_retry`; remove dead return; add schema-map comment + anti-bot detection; un-hardcode trip type (add one-way).

**Add:** `price_explore()` (conditional on D2); `--one-way`; `--destination`/`--exclude-destination`/`--region`/`--trip-length`; Examples block; live canary entry; README/CHANGELOG/MIGRATION/CLAUDE.md; edge-case tests.

* * *
## 11. Open items
1. **(Resolved) The Explore price call.** Traced live in Chrome (§3.7). Prices render on the map but are not reliably extractable from `GetExploreDestinations` (rows carry none; extra slot is map geometry; no price array). Conclusion: metadata-only + `price_explore()` bridge (D2/D3). Not an assumption — a measured result.
  
2. **(Resolved) The count question.** Not viewport — geographic scope, and the full set needs a place_id origin (§3.6, D8). Live IATA form defaults to ~the origin's continent (24 US cities from JFK); place_id form returns ~85. We expose _scope_ (`--region`), not a raw count.
  
3. **(Open, impl detail — D8) place_id vs flag unlock.** Confirm during build whether the full set is unlocked by the place_id origin, the `4` flag, or both; ~30-min probe. Fallback: IATA form + documented regional scope.
  

* * *
## 12. Out of scope (YAGNI)
- `--prices` CLI flag (compose instead — D3).
  
- `search_explore()` (one-liner via `to_search_kwargs()` — D3).
  
- `watch_explore()` / diffing (no price to track; deals has it because price changes matter).
  
- `distance` / `parent_place_id` fields (D5).
  

* * *
## 13. Positioning vs `deals()`
| | `deals()` | `explore()` |
|---|---|---|
| Question | "where's cheap right now?" | "where could I go from here?" |
| Selection | cheapest right now | most interesting to visit |
| Price | yes (price, typical, discount %) | no in-response (traced — not reliably extractable) |
| Extras | — | images, coordinates, scenic/leisure spots |
| Trip type | roundtrip only | one-way or roundtrip |
| Scope | ~30 results | ~85 worldwide via place_id origin (D8) |
| Pricing path | `price_deal()` | `price_explore()` |

Siblings, not substitutes: same question, different answer set (§2a).

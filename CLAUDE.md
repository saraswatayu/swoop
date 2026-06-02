# Swoop — Google Flights Price Scraper

Python library for searching Google Flights programmatically via the same RPC endpoints the web app uses. Supports one-way, roundtrip, and official multi-city search/pricing with trip-level results and selector-based bookable pricing.

## Quick Commands

```bash
# Install (editable, with dev deps)
make install-dev

# Test
make test                                     # Skip live API tests (what CI runs)
make test-live                                # Live integration tests
make test-all                                 # Both
python -m pytest tests/test_decoder.py -v     # Single module

# Type check
make typecheck

# Pre-PR gate (typecheck + offline tests)
make check
```

Raw `pip install -e ".[validation,cli]" pytest hypothesis pytest-benchmark` and
`python -m pytest tests/ -v -m 'not live'` still work; the Makefile is just the
canonical entry point.

## Critical Rules

### 1. Commit Format
`<type>: <description>` where type is `feat|fix|refactor|docs|chore|ci|test`.

### 2. Test What You Ship
Every feature or bug fix that touches logic must include tests. Run `make check` (or `python -m pytest tests/ -v -m 'not live'`) before declaring done.

### 3. Never Commit Secrets
Never commit `.env` files, API keys, or tokens.

### 4. Frozen API Surface
Public fields on `SearchResult`, `RawSearchResult`, `TripOption`, `TripLeg`, `PriceResult`, `BookingOption`, `Itinerary`, `Segment`, `Layover`, `Codeshare`, `CarbonEmissions`, `Deal`, `DealsResult`, `DealsDiff`, `PriceChange`, `ExploreDestination`, and `ExploreResult` are part of the public API. When adding or renaming public fields, update `tests/test_api_surface.py`.

`_`-prefixed fields on `BookingOption` and `RawSearchResult` are internal — not public API, not covered by the surface test.

### 5. Commit After Every Logical Unit
One commit per task/phase — not one giant commit at the end. Format: `<type>: <description>`.

## Common Gotchas

| Issue | Fix |
|-------|-----|
| `primp` request fails silently | Content param must be bytes — use `.encode()` |
| `primp` impersonation | Use `impersonate="chrome"` (NOT `chrome_133`) |
| Google Flights RPC returns no results | Airport nesting must be 3 levels `[[[code, 0]]]` not 4 |
| Price shows as cents | `ItinerarySummary.from_b64()` returns cents — divide by 100, use `round()` |
| ItinerarySummary b64 path | `[1]` not `[1][1]` — wrong path causes all prices = $0 |
| Departure time format varies | Sometimes `[hour]`, sometimes `[hour, min]` — use `_safe_tuple` with defaults |
| Roundtrip booking price | GetBookingResults return price IS the roundtrip total — don't sum outbound + return |
| `data[2]` (best flights) often null from RPC | All results come in `data[3]` instead |
| Deals API is roundtrip-only | Upstream ignores `trip_type=2`; deals always come back with a return date. For one-way exploration use `search()` with explicit destination. |
| Deals API ignores payload dates | Server picks its own ~4-month forward window; `depart_window` is enforced client-side in `_deals_filter.filter_deals`. |
| Deals API ignores time-window restrictions | Slot 2 of the segment is honored by `search()` but not by `GetFlightDealsStreaming` — no point exposing it. |
| Basic-economy in deals defaults to excluded | `include_basic_economy=False` mirrors `search()` to prevent the "$200 to Lisbon" no-carry-on surprise. Probed: slot 28 of the inner payload toggles it. |
| `*` airline code in deals = multi-airline | Sentinel value Google returns when the deal spans multiple carriers; preserved as a single-element list in `Deal.airlines`. |
| Explore RPC returns no prices | `GetExploreDestinations` is metadata-only (verified live: 0 `$`/`USD` in the response). The map's prices come from a separate client-side path. Use `price_explore()` to price a destination, or `deals()` for priced discovery. |
| Explore result count = geographic scope | Not a fixed count. The IATA-origin form returns a regional subset (~24 from JFK); a Google place_id origin + flag `4` returns the full worldwide set (~85). place_id resolution is a documented future enhancement; canary asserts `>= 1`. |
| Explore supports one-way | Unlike deals (roundtrip-only), `GetExploreDestinations` honors `trip_type=2` with a single segment — verified live. `one_way=True` → each `ExploreDestination.return_date` is `None`. |

## Architecture

```
swoop/
├── __init__.py       # Public API: search(), search_legs(), check_price(), price_selector(), price_legs(), deals(), search_deal(), price_deal(), explore(), price_explore(), price_explore_all(), watch_deals(), diff_deals(), dataclasses, version
├── __main__.py       # `python -m swoop` entry point
├── models.py         # Public trip-level + deals + explore models (SearchResult, TripOption, TripLeg, PriceResult, Deal, DealsResult, DealsDiff, PriceChange, ExploreDestination, ExploreResult)
├── rpc.py            # HTTP client — builds requests, calls Google Flights RPC, shared _post_with_retry
├── _selection.py     # Staged trip search, selector encoding, selector-based trip pricing helpers
├── builders.py       # Protobuf request builders (filters, segments, SearchLeg)
├── decoder.py        # Response decoder — nested lists → dataclasses
├── _deals.py         # Deals endpoint — session, payload, streaming response parser
├── _deals_filter.py  # Client-side filter pipeline applied to parsed Deal lists
├── _deals_watch.py   # Persistent deals watcher — diff + JSON snapshot save/load
├── _explore.py       # Explore endpoint — destination discovery (GetExploreDestinations); metadata only, no prices; caches bl/f.sid session params
├── _explore_filter.py # Client-side filter pipeline applied to parsed ExploreDestination lists
├── _regions.py       # Region enum + ISO country → region table (airportsdata-backed)
├── _booking.py       # Booking option parsing (GetBookingResults)
├── _validate.py      # IATA code validation (optional airportsdata)
├── exceptions.py     # Custom exceptions
├── flights.proto     # Protobuf schema (ItinerarySummary)
├── flights_pb2.py    # Generated protobuf code (excluded from pyright)
├── flights_pb2.pyi   # Hand-written type stub for flights_pb2 (covers what builders.py touches)
└── cli/
    ├── __init__.py   # Click group, main() entry point
    ├── commands.py   # search_cmd, price_cmd, deals_cmd, explore_cmd definitions
    ├── formatters.py # Table/JSON/CSV/brief output renderers
    └── utils.py      # Custom Click types, time/date helpers
```

**Trip search flow:** `search()` / `search_legs()` → `_selection.search_trip_options()` → staged Google RPC passes → `SearchResult`

**Low-level flow:** `search_raw()` → Google RPC → `decoder.decode_result()` → `RawSearchResult`

**CLI flow:** `swoop search` → `commands.search_cmd()` → `swoop.search()` / `swoop.search_legs()` → `formatters.format_search_table()`

**Price flow:** `swoop price` → `commands.price_cmd()` → `swoop.check_price()` / `swoop.price_selector()` / `swoop.price_legs()` → `formatters.format_price_table()`

**Deals flow:** `swoop deals` → `commands.deals_cmd()` → `swoop.deals()` → (`_deals.fetch_deals()` for one RPC call, or `_fetch_deals_per_origin` for parallel mode) → `_deals_filter.filter_deals()` → `formatters.format_deals_table()`

**Explore flow:** `swoop explore` → `commands.explore_cmd()` → `swoop.explore()` → `_explore.fetch_explore()` → `_explore.parse_explore_payload()` → `_explore_filter.filter_explore()` (client-side filters live in `explore()`, not the CLI) → `formatters.format_explore_table()`

**Deal / explore bridge flows:**
- `swoop.search_deal(deal)` → `search()` with deal's route/dates/airlines → `SearchResult`
- `swoop.price_deal(deal)` → `search_deal(deal)` → cheapest itinerary → `price_selector()` → `PriceResult`
- `swoop.price_explore(destination)` → `search()` for the destination's route/suggested dates → cheapest itinerary → `price_selector()` → `PriceResult`
- `swoop.price_explore_all(destinations)` → `price_explore()` per destination in a thread pool → `list[Optional[PriceResult]]` (order-preserving)

**Deals watcher flow:** `examples/deals_watcher.py` → `swoop.deals()` → `swoop.watch_deals(result, cache_path=...)` → `_deals_watch.load_snapshot()` → `diff_deals()` → `save_snapshot()` → `DealsDiff`

## File Map

| File | Purpose |
|------|---------|
| `models.py` | Public trip-level + deals dataclasses: `SearchResult`, `TripOption`, `TripLeg`, `PriceResult`, `Deal`, `DealsResult`, `DealsDiff`, `PriceChange`, etc. |
| `_selection.py` | Staged multi-leg expansion, selector encode/decode, selector-based trip pricing |
| `rpc.py` | RPC client, HTTP transport, request building, shared `_post_with_retry` |
| `builders.py` | Protobuf filter/segment builders |
| `decoder.py` | Response decoding and low-level `RawSearchResult` / itinerary dataclasses |
| `_deals.py` | Deals endpoint client — session, payload builder, streaming response parser. Payload slot indexing mirrors `rpc._build_request`. |
| `_deals_filter.py` | `filter_deals()` — single-pass AND-style client-side filter (`depart_window`, `trip_length`, `destinations`, `region`, `max_price`, `min_discount_pct`) |
| `_deals_watch.py` | `diff_deals()`, `watch_deals()`, atomic `save_snapshot()` / `load_snapshot()` for JSON-backed deals tracking |
| `_explore.py` | Explore endpoint client — `fetch_explore()`, payload builder, nested-list parser. Metadata only (no prices); supports one-way + roundtrip; caches bl/f.sid session params with parse-failure invalidation. |
| `_explore_filter.py` | `filter_explore()` — single-pass AND-style client-side filter (`destinations`, `exclude_destinations`, `region`, `trip_length`); mirrors `_deals_filter`. |
| `_regions.py` | `Region` enum + ISO 2-letter country → region static table; `region_for_iata()` bridges via `airportsdata` |
| `_booking.py` | `parse_booking_payload()` — booking option extraction |
| `_validate.py` | `validate_iata()` with optional airportsdata |
| `exceptions.py` | `SwoopError`, `SwoopRPCError`, `SwoopValidationError` |
| `__init__.py` | Public re-exports: `search`, `search_legs`, `check_price`, `price_selector`, `price_legs`, `deals`, `search_deal`, `price_deal`, `explore`, `price_explore`, `price_explore_all`, `watch_deals`, `diff_deals`, all dataclasses, `Region`, etc. Also `_fetch_deals_per_origin`, `_price_cheapest` (internal). |
| `cli/__init__.py` | Click group + `main()` entry point (uses `swoop.__version__` for `--version`) |
| `cli/commands.py` | `search_cmd`, `price_cmd`, `deals_cmd`, `explore_cmd`, `_parse_trip_length` (shared deals/explore), `_SearchFormatKwargs` TypedDict for formatter kwargs |
| `cli/formatters.py` | Trip-level table, JSON, CSV (search + price + deals + explore), and brief formatters; shared `_csv_safe` escapes formula prefixes |
| `cli/utils.py` | `IATACodeType`, `DateType`, `format_time()`, `format_duration()`, `resolve_quiet()`, `configure_verbose_logging()` (scoped to Click ctx) |
| `flights_pb2.pyi` | Hand-written stub mirroring `flights.proto` — restores pyright on `PB.*` |
| `__main__.py` | `python -m swoop` with graceful ImportError |
| `tests/test_api_surface.py` | Frozen public API assertions |
| `tests/factories.py` | Test factories for dataclasses |
| `tests/test_cli.py` | CLI tests using `CliRunner` |

## Documentation

| Topic | File |
|-------|------|
| Protobuf response schema | `.claude/docs/google-flights-protobuf-schema.md` |
| Booking option parsing notes | `.claude/docs/booking-options-proto-notes.md` |
| Version-to-version upgrade notes | `MIGRATION.md` (0.3 → 0.4, 0.4 → 0.5) |
| Security policy and threat model | `SECURITY.md` |
| Runnable end-user examples | `examples/README.md`, `examples/price_drop_watcher.py`, `examples/multi_city_finder.py`, `examples/deals_watcher.py` |
| Diagnostic scripts shared helper | `scripts/_booking_helper.py` (fetch_booking_results — reused by the 7 record_*/sweep/validate scripts) |

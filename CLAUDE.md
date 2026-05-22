# Swoop — Google Flights Price Scraper

Python library for searching Google Flights programmatically via the same RPC endpoints the web app uses. Supports one-way, roundtrip, and official multi-city search/pricing with trip-level results and selector-based bookable pricing.

## Quick Commands

```bash
# Install (editable, with dev deps)
pip install -e ".[validation,cli]"
pip install pytest hypothesis pytest-benchmark

# Test
python -m pytest tests/ -v                    # All tests
python -m pytest tests/ -v -m 'not live'      # Skip live API tests
python -m pytest tests/test_decoder.py -v     # Single module

# Type check
pyright
```

## Critical Rules

### 1. Commit Format
`<type>: <description>` where type is `feat|fix|refactor|docs|chore|ci|test`.

### 2. Test What You Ship
Every feature or bug fix that touches logic must include tests. Run `python -m pytest tests/ -v -m 'not live'` before declaring done.

### 3. Never Commit Secrets
Never commit `.env` files, API keys, or tokens.

### 4. Frozen API Surface
Public fields on `SearchResult`, `RawSearchResult`, `TripOption`, `TripLeg`, `PriceResult`, `BookingOption`, `Itinerary`, `Segment`, `Layover`, `Codeshare`, `CarbonEmissions`, `Deal`, and `DealsResult` are part of the public API. When adding or renaming public fields, update `tests/test_api_surface.py`.

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

## Architecture

```
swoop/
├── __init__.py       # Public API: search(), search_legs(), check_price(), price_selector(), price_legs(), deals(), dataclasses, version
├── __main__.py       # `python -m swoop` entry point
├── models.py         # Public trip-level result models (SearchResult, TripOption, TripLeg, PriceResult, Deal, DealsResult)
├── rpc.py            # HTTP client — builds requests, calls Google Flights RPC
├── _selection.py     # Staged trip search, selector encoding, selector-based trip pricing helpers
├── builders.py       # Protobuf request builders (filters, segments, SearchLeg)
├── decoder.py        # Response decoder — nested lists → dataclasses
├── _deals.py         # Deals endpoint — session, payload, streaming response parser
├── _booking.py       # Booking option parsing (GetBookingResults)
├── _validate.py      # IATA code validation (optional airportsdata)
├── exceptions.py     # Custom exceptions
├── flights.proto     # Protobuf schema (ItinerarySummary)
├── flights_pb2.py    # Generated protobuf code (excluded from pyright)
├── flights_pb2.pyi   # Hand-written type stub for flights_pb2 (covers what builders.py touches)
└── cli/
    ├── __init__.py   # Click group, main() entry point
    ├── commands.py   # search_cmd, price_cmd, deals_cmd definitions
    ├── formatters.py # Table/JSON/CSV/brief output renderers
    └── utils.py      # Custom Click types, time/date helpers
```

**Trip search flow:** `search()` / `search_legs()` → `_selection.search_trip_options()` → staged Google RPC passes → `SearchResult`

**Low-level flow:** `search_raw()` → Google RPC → `decoder.decode_result()` → `RawSearchResult`

**CLI flow:** `swoop search` → `commands.search_cmd()` → `swoop.search()` / `swoop.search_legs()` → `formatters.format_search_table()`

**Price flow:** `swoop price` → `commands.price_cmd()` → `swoop.check_price()` / `swoop.price_selector()` / `swoop.price_legs()` → `formatters.format_price_table()`

**Deals flow:** `swoop deals` → `commands.deals_cmd()` → `swoop.deals()` → `_deals.fetch_deals()` → `formatters.format_deals_table()`

## File Map

| File | Purpose |
|------|---------|
| `models.py` | Public trip-level dataclasses: `SearchResult`, `TripOption`, `TripLeg`, `PriceResult`, `Deal`, `DealsResult`, etc. |
| `_selection.py` | Staged multi-leg expansion, selector encode/decode, selector-based trip pricing |
| `rpc.py` | RPC client, HTTP transport, request building |
| `builders.py` | Protobuf filter/segment builders |
| `decoder.py` | Response decoding and low-level `RawSearchResult` / itinerary dataclasses |
| `_deals.py` | Deals endpoint client — session, payload builder, streaming response parser |
| `_booking.py` | `parse_booking_payload()` — booking option extraction |
| `_validate.py` | `validate_iata()` with optional airportsdata |
| `exceptions.py` | `SwoopError`, `SwoopRPCError`, `SwoopValidationError` |
| `__init__.py` | Public re-exports: `search`, `search_legs`, `check_price`, `price_selector`, `price_legs`, `deals`, `RawSearchResult`, `SearchResult`, `TripOption`, `TripLeg`, `Deal`, `DealsResult`, etc. |
| `cli/__init__.py` | Click group + `main()` entry point (uses `swoop.__version__` for `--version`) |
| `cli/commands.py` | `search_cmd`, `price_cmd`, `deals_cmd`, `_SearchFormatKwargs` TypedDict for formatter kwargs |
| `cli/formatters.py` | Trip-level table, JSON, CSV (search + price + deals), and brief formatters; CSV escapes formula prefixes |
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
| Runnable end-user examples | `examples/README.md`, `examples/price_drop_watcher.py`, `examples/multi_city_finder.py` |
| Diagnostic scripts shared helper | `scripts/_booking_helper.py` (fetch_booking_results — reused by the 7 record_*/sweep/validate scripts) |

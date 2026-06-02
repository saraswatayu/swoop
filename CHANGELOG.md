# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Destination discovery API** — `swoop.explore(origin, …)` and `swoop explore <ORIGIN>` CLI: swoop's fourth primitive, "where could I go from here?", via the `GetExploreDestinations` RPC. Returns `ExploreDestination` objects (name, country, coordinates, images, Google's suggested dates, and drive time for nearby drivable destinations), one-way or roundtrip.
- **Explore bridge** — `swoop.price_explore(destination)` prices a chosen destination's cheapest itinerary via the existing search/price pipeline; `swoop.price_explore_all(destinations)` prices a whole page of destinations concurrently (order-preserving, `None` for unpriceable entries); `ExploreDestination.to_search_kwargs()` for direct integration. The Explore RPC returns no price (it is discovery, not pricing), so prices come from composition — mirroring `deals()` → `price_deal()`.
- New `ExploreDestination` and `ExploreResult` dataclasses with frozen API-surface coverage. The deals-style client-side filters (`destinations`, `exclude_destinations`, `region`, `trip_length`) are exposed on `explore()` itself — not just the CLI — so library callers can narrow the set too. CLI supports table/JSON/CSV/brief output (CSV neutralizes spreadsheet formula prefixes) and `--destination`, `--exclude-destination`, `--region`, `--trip-length`, plus `--one-way`. Stable `bl`/`f.sid` session params are cached across calls to skip a redundant page fetch.

## [0.5.0] - 2026-05-24

### Added

- **Deals discovery API** — `swoop.deals(origin, …)` and `swoop deals <ORIGIN>` CLI for finding the best flight deals from an airport (or set of airports). Returns up to 30 `Deal` objects with destination, dates, price, typical price, discount %, carriers, stops, duration, trip length, and `destination_region`.
- **Deal bridges** — `swoop.search_deal(deal)` resolves a deal to specific itineraries; `swoop.price_deal(deal)` prices the cheapest matching itinerary. `Deal.to_search_kwargs()` for direct integration. A discovered deal now flows into swoop's existing search/price pipeline. `Deal` stores its query context (cabin, passengers) so the bridges forward those correctly.
- **Filter surface (RPC-native)** — `airlines`, `max_stops`, `cabin`, `passengers`, `include_basic_economy` (defaults to `False` to match `search()` — prevents the basic-economy footgun at $200-to-Lisbon prices).
- **Filter surface (client-side)** — `depart_window`, `trip_length`, `destinations`, `exclude_destinations`, `region`, `max_price`, `min_discount_pct`. Applied to the 30 deals the RPC returns; combinable with the native filters.
- **Multi-origin support** — `origin` accepts `str` or `list[str]`. Default single-call mode uses the RPC's slot-0 airport set; `per_origin=True` issues parallel calls (capped at 4 workers, thread-safe `_get_client` with a per-worker `primp.Client`), merges by `Deal.fingerprint`, keeps the cheaper variant on collision. CLI: `swoop deals JFK,LGA,EWR [--per-origin]`. `DealsResult.origin` is comma-joined when multiple origins are passed. `DealsResult.partial=True` is set when at least one parallel call fails so callers can avoid persisting incomplete snapshots.
- **Region scoping** — `swoop.Region` enum (`NORTH_AMERICA`, `CARIBBEAN`, `LATIN_AMERICA`, `EUROPE`, `AFRICA`, `MIDDLE_EAST`, `ASIA_PACIFIC`) backed by `airportsdata` for IATA→country→region. `Deal.destination_region` populated automatically when `airportsdata` is installed. CLI `--region` warns once if `airportsdata` is missing.
- **Deals watcher** — `swoop.watch_deals(result, cache_path=…)` loads the prior snapshot, diffs, and persists current. `swoop.diff_deals(prior, current)` is the pure-function diff. New `DealsDiff` and `PriceChange` dataclasses surface `new`, `gone`, `price_changes`, and `unchanged`. `Deal.fingerprint` provides stable identity across runs (includes query context, excludes price so drops surface as `price_changes`). Snapshot cache is schema v2. `examples/deals_watcher.py` is the runnable analogue of `examples/price_drop_watcher.py`. The watcher refuses to overwrite the cache when results are partial or empty, and still surfaces the diff for the successful origins.
- `Deal.airlines: list[str]` / `Deal.airline_names: list[str]` even for single-carrier deals. The `*` sentinel for multi-airline deals is preserved as a single-element list — callers can detect and route on it.
- **BookingOption seller fields** — `seller_name` (e.g. `"Mytrip"`, `"Qatar Airways"`), `seller_code` (e.g. `"ETRAVELI_Mytrip"`, `"QR"`), `booking_url` (the `google.com/travel/clk/f?u=…` redirect that opens the seller's page), `logo_url` (gstatic partner logo when Google provides `logo_code`), and `is_airline_direct` (`True` when the booking is direct with the operating carrier rather than an OTA). Parsed from `GetBookingResults`.
- `swoop price --json` exposes the full `BookingOption` field set, including `fare_family`, `rebookability_signal`, and all five new seller fields.
- `swoop price --csv` — CSV output for price lookups, including `logo_url` and lowercase boolean fields.
- `--verbose` / `-v` CLI flag surfaces RPC debug logging (scoped to the Click invocation, never persists globally).
- Auto-detects non-TTY stdout and runs quietly so output pipes cleanly into other tools.
- CSV formula-injection escape — values starting with `=`, `+`, `-`, `@`, `\t`, `\r` are prefixed with `'` to neutralize Excel/Sheets formula execution. Applies to `swoop search --csv`, `swoop price --csv`, and `swoop deals --csv`.
- CLI validates `--depart-window` and `--trip-length` ranges with a clear error on the offending value.
- `swoop --version` now reports `swoop.__version__` (was stale).
- Shell completion documented in the README.
- Contract corpus tests assert `seller_code`, `is_airline_direct`, and non-empty `booking_url` for every captured response, so a future Google reshape fails loudly.
- New runnable scripts in `examples/`: `deals_watcher.py` (paired with the existing `price_drop_watcher.py` and `multi_city_finder.py`).
- `MIGRATION.md` covering 0.3 → 0.4 and 0.4 → 0.5.
- `SECURITY.md` documenting the threat model and disclosure policy.
- `PyPI` homepage points to the landing page; additional project URLs added.

### Changed

- **One-way pricing now fetches `GetBookingResults`** and returns the cheapest eligible booking option price (with `booking_options` populated and `rpc_calls` incremented by 1), instead of short-circuiting to the search-result price. Affects `check_price`, `price_selector`, and `price_legs` for single-leg trips: `PriceResult.price` may differ from `Itinerary.price` and `PriceResult.booking_options` is no longer empty for one-ways. If the booking RPC fails or returns no eligible options, the search-result price is still used as a fallback. See MIGRATION.md for details.
- `BookingOption.__repr__` surfaces both `seller_name` and `brand_label` together when both are present, and uses `repr()` on each so apostrophes and quotes no longer corrupt log output.
- `_extract_seller` extracts the click URL (`opt[5]`) independently of the seller block (`opt[1]`); a missing seller no longer drops the booking URL.
- `logo_url` is empty when Google omits `logo_code`. The previous behaviour silently constructed a logo URL from `seller_code` which 404s for OTA codes; callers that want the airline-direct fallback can construct it themselves via `{gstatic_base}/{seller_code}.png`.
- Internal symbols are hidden from the public surface via PEP 562 `__dir__` instead of `globals().pop`; tab completion and `dir(swoop)` now show only the documented API.

### Fixed

- `region_for_iata` no longer spends ~37 ms/call — `airportsdata.load()` is now cached. Material speedup for batch deal annotation.
- `_deals_date_range` no longer crashes on malformed input or on Windows.
- `_dict_to_deal` hardened against null fields and string-bool coercion from older cache snapshots.
- `filter_deals` raises `ValueError` on a malformed `depart_window` rather than silently returning empty.
- `Deal.stops` is `Optional[int]`; the CLI renders unknown as `?` instead of crashing.
- `DealsDiff` and `PriceChange` are no longer `frozen=True` — the prior config produced a broken hash.
- Detect HTML/CAPTCHA responses in the deals streaming parser and raise a typed error instead of a confusing decode crash.
- Validate `booking_path` prefix before constructing booking URLs.
- One-way booking-RPC failures are caught at `SwoopError` (broadened from a narrower class) so the search-price fallback always engages.
- `format_deals_json` exposes the `partial` flag and per-`Deal` query context in the JSON output.
- `examples/deals_watcher.py --once` skips the rate-limit sleep and guards against missing `airportsdata`.
- `examples/price_drop_watcher.py --once` exits non-zero on fetch failure and is hardened against partial args and torn writes.
- CLI verbose-logging lifetime is scoped to the Click context (no global handler leakage in long-running hosts).

### Notes (deals upstream behavior, verified by live probe)

- Deals discovery is roundtrip-only — `trip_type=2` (oneway) is ignored by the upstream RPC; the server returns roundtrip-shaped deals regardless. swoop does not expose a `trip_type` parameter on `deals()`. For one-way exploration, use `search()` with an explicit destination.
- The RPC ignores the dates passed in the payload and returns its own forward window (~4 months). `depart_window` is enforced client-side.
- The RPC ignores time-of-day restrictions. Time-window filters are not exposed on `deals()`.

## [0.4.1] - 2026-04-01

### Added

- `impersonate` field on `TransportConfig` for TLS fingerprint rotation (e.g. `"chrome"`, `"firefox"`, `"safari"`, `"edge"`)

## [0.4.0] - 2026-03-18

### Added

- **Multi-currency support** — prices display in the correct currency based on point-of-sale country, with locale-aware formatting via Babel (`$1,234`, `£890`, `¥5,540`)
- `currency` field on `TripOption`, `PriceResult`, and `SearchResult` (derived property)
- `--country` flag (or `set_country()`) for point-of-sale control — affects fares and currency returned by Google
- `--proxy` flag (or `set_proxy()`) for routing requests through HTTP/SOCKS5 proxies
- `--children`, `--infants-in-seat`, `--infants-on-lap` CLI flags for full passenger breakdown
- `Passengers` dataclass consolidating adult/child/infant counts
- `TransportConfig` dataclass consolidating timeout, retries, country, and proxy settings
- `--max-results`, `--beam-width`, `--time-budget` CLI flags for configurable multi-city beam search
- CO₂ emissions column in search table — color-coded percentage vs average
- Legroom display for nonstop single-segment flights
- Overnight indicators on layovers
- Airline names in search table with multi-leg labeling
- `CabinClass` Literal type (`"economy" | "premium-economy" | "business" | "first"`) replacing stringly-typed cabin validation
- `Segment.legroom`, `Segment.has_premium_ife`, `Segment.amenities`, `Segment.seat_type` fields
- `Itinerary.stop_count`, `Itinerary.is_budget_carrier`, `Itinerary.quality_signals` fields
- `BookingOption.fare_family` and `BookingOption.rebookability_signal` fields
- Human-readable `__repr__` on all public dataclasses (`Segment`, `Itinerary`, `TripOption`, `SearchResult`, `PriceResult`, `ResolvedLeg`, `BookingOption`, `Layover`, `TripLeg`)
- HTTP connection reuse across requests for keep-alive
- LRU-evicted client cache (max 32) for proxy rotation without unbounded memory growth

### Fixed

- **Cabin class misidentification** — replaced fragile brand-name text matching with numeric field `brand_block[6][0][0]`; airlines like British Airways ("Upper Class" for business) and Turkish ("Premium Flex") were being silently misclassified
- **OTA/codeshare booking options dropped** — third-party sellers and codeshare flights with null brand fields were rejected entirely, causing zero booking options on some routes (e.g. SFO→NRT via Philippine Airlines)
- **Currency-unaware price division** — hardcoded `/100` divisor was wrong for JPY, INR, KRW, and other whole-unit currencies
- **Roundtrip beam search overhead** — roundtrips were running 16 RPC calls via beam search instead of 1; fast path now covers 1- and 2-leg trips, beam search only triggers for 3+ legs
- **Midnight departure times** — Google encodes midnight as `hour=None`; now treated as 0
- **Missing itinerary-level times** — falls back to first/last segment departure/arrival when itinerary-level times are absent
- **Single-element time tuples** — departure times arriving as `[hour]` without minutes now default minute to 0
- **`exclude_basic_economy` not propagated** — flag was only sent on single-leg first-pass RPC; now sent to all RPC stages for roundtrip and multi-city
- **Proxy/children/infants silently dropped** — multiple internal functions accepted these params but didn't forward them to lower-level calls
- **Currency field lost on flight-number filter** — `_filter_trip_options_by_flight_number()` dropped currency when constructing filtered results
- **Unbounded client cache** — proxy rotation caused unlimited memory growth; now LRU-evicted at 32 entries

### Changed

- **Breaking:** `Flight` class renamed to `Segment` — `Itinerary.segments` now contains `Segment` objects
- **Breaking:** `BookingOption` dict-style access removed (`option['price']` → `option.price`); `__getitem__`, `get()`, `keys()`, `values()`, `items()` all removed
- **Breaking:** `search()`, `check_price()`, and related functions now accept `TransportConfig` and `Passengers` dataclass objects instead of scattered kwargs
- `SearchResult.currency` is now a derived property (computed from first result) instead of a stored field
- Search table redesigned with columnar layout for better multi-leg readability
- CLI options reordered by frequency of use — trip basics first, advanced/transport options last
- Renamed internal `_cents` fields/functions to `_raw` for clarity
- Eliminated `_build_filters`/`_build_f_req` redundancy in `rpc.py`
- Removed backward-compat `SearchResult` alias from `decoder.py`
- Removed dead `correct_trip_option_prices()` function
- Single source of truth for `CABIN_CLASS_MAP` in `builders.py`
- Extracted shared formatting helpers to `_formatting.py`

## [0.3.1] - 2026-03-12

### Fixed

- Cabin-aware booking option filtering — price correction no longer picks cross-cabin fares (e.g. business fare selected as "cheapest" for economy search)
- Negative `travel_time` and layover `minutes` from malformed API responses now clamp to 0 instead of passing through

### Changed

- CI runs offline tests only on push; live API canaries run weekly via separate workflow

## [0.3.0] - 2026-03-11

### Added

- `check_price()` — targeted price lookup for a specific flight (1 RPC one-way, 3 RPCs roundtrip)
- `PriceResult` dataclass with `price`, `fare_brand`, `is_basic_economy`, `booking_options`, `itinerary`, `resolved_legs`, `rpc_calls`
- `search_legs()` — leg-based search API accepting `list[SearchLeg]`
- `price_legs()` — leg-based pricing API accepting `list[SelectedLeg]`
- `price_selector()` — public selector-based bookable pricing API
- Official multi-city search and pricing for 3+ legs in both the Python API and CLI
- Trip-level result models: `TripLeg`, `TripOption`, and `RawSearchResult`
- Opaque itinerary selectors in search results for script-stable pricing
- `swoop price --selector ...` and `swoop search --show-price-commands`
- Repeatable `swoop search --leg ORIGIN DEST DATE` syntax for multi-city search
- `SearchLeg`, `SelectedLeg`, `ResolvedLeg` exports
- `flight_summary` in search output (table, JSON, CSV, brief formats)
- `swoop price ORIGIN DEST --depart DATE FLIGHT [--return DATE FLIGHT]` shorthand
- `--leg` syntax for explicit leg pricing: `swoop price --leg JFK LAX 2026-06-15 DL2300`
- Resolved flight details (aircraft, legroom, times) in price table output
- `resolved_legs` array in price JSON output
- Roundtrip search labels prices as roundtrip totals
- Retry with exponential backoff and jitter on HTTP 429 (default `retries=2` across all RPC functions)
- Roundtrip support for `check_price()` with `return_flight_number` and `return_date`

### Removed

- **Breaking:** `search_flight()` function — use `search()` with `flight_number=` param, or `check_price()` for price lookups
- **Breaking:** `swoop flight` CLI command — use `swoop search --flight` or `swoop price`
- **Breaking:** `swoop book` CLI command — use `swoop price --selector ...` or `swoop price --leg ...` instead
- **Breaking:** `swoop search --price` and `swoop search --price-selector`
- **Breaking:** legacy `swoop price FLIGHT ORIGIN DEST DATE`, `--return-date`, and `--return-flight`
- `format_flight_detail()`, `format_flight_json()`, `format_booking_table()`, `format_booking_json()` formatters

### Fixed

- Don't filter return flights by outbound airline in `check_price()` roundtrip path
- Swap arrival airport decoder indices to match current Google Flights response format
- Remove unnecessary `verify=False` from TLS client
- Pin `protobuf>=6.31.1` to match generated code — fixes install failures on older protobuf versions ([#1](https://github.com/saraswatayu/swoop/issues/1))
- Quiet booking-parser dropped-option logs when usable fare options still exist

### Changed

- **Breaking:** public `SearchResult` is now trip-level with `results`, `price_range`, and `is_complete`
- `search()` and `search_legs()` now return complete trip rows instead of single-pass raw itinerary buckets
- `search()` and `search_legs()` now show shopping totals only; bookable pricing happens via `price`, `price_selector()`, or selectors
- `search_raw()` remains the low-level escape hatch and now returns `RawSearchResult`
- `search_legs()` and `price_legs()` now accept 3+ legs
- `search()` signature unchanged; `check_price()` now populates `resolved_legs`
- Narrow `except Exception` to specific swoop error types
- Rename `departure_airport`/`arrival_airport` fields to `_code` suffix for consistency
- Deduplicate internal `_safe_get` helper

## [0.2.2] - 2026-03-10

### Changed

- Rewrite README with badges, progressive disclosure, terminal screenshot, and mermaid diagram

## [0.2.1] - 2026-03-09

### Fixed

- Use itinerary-level IATA codes for route display instead of first segment codes

## [0.2.0] - 2026-03-09

### Added

- CLI with `swoop search`, `swoop flight`, and `swoop book` commands (`pip install swoop-flights[cli]`)
- Table, JSON, CSV, and brief output formats
- Cabin class, airline, time window, nonstop, and sort filters in the CLI

### Fixed

- Handle `None` values in time tuples from decoder

## [0.1.0] - 2026-03-09

### Added

- `search()` — high-level entry point with cabin class, airline, time window, and stop filters
- `search_flight()` — convenience wrapper to find a specific flight by number
- `search_raw()` — low-level RPC access to `GetShoppingResults`
- `get_booking_results()` — fetch fare tiers (`BookingOption`) for a specific itinerary
- Roundtrip search with automatic price correction via `GetBookingResults`
- Basic economy exclusion: one-way uses RPC-level filter, roundtrip uses booking results
- `Itinerary.price` property — canonical price preferring `direct_price` over protobuf-decoded price
- Typed dataclasses: `SearchResult`, `Itinerary`, `Flight`, `BookingOption`, `Layover`, `Codeshare`, `CarbonEmissions`, `PriceRange`, `AmenityFlags`, `QualitySignals`
- Exception hierarchy: `SwoopError` → `SwoopHTTPError` → `SwoopRateLimitError`, `SwoopParseError`
- Input validation with optional `airportsdata` for IATA code checking (`pip install swoop-flights[validation]`)
- Retry with exponential backoff on HTTP 429
- Sort and stop filter constants (`SORT_CHEAPEST`, `STOPS_NONSTOP`, etc.)
- Flight number parsing and itinerary matching (`parse_flight_number`, `itinerary_matches_flight`)
- Carbon emissions, legroom, amenity flags, and quality signal decoding
- Frozen API surface tests to catch accidental breaking changes

# swoop

[![PyPI](https://img.shields.io/pypi/v/swoop-flights)](https://pypi.org/project/swoop-flights/)
[![Python](https://img.shields.io/pypi/pyversions/swoop-flights)](https://pypi.org/project/swoop-flights/)
[![License](https://img.shields.io/github/license/saraswatayu/swoop)](https://github.com/saraswatayu/swoop/blob/main/LICENSE)
[![CI](https://github.com/saraswatayu/swoop/actions/workflows/ci.yml/badge.svg)](https://github.com/saraswatayu/swoop/actions/workflows/ci.yml)

Search Google Flights and Google Travel Hotels programmatically. Real prices, typed results, no API key.

```python
from swoop import search

results = search("JFK", "LAX", "2026-06-15")
for option in results.results[:3]:
    print(f"${option.price} — {', '.join(option.legs[0].itinerary.airline_names)}")
```

> [!NOTE]
> swoop is not affiliated with Google. It calls undocumented RPC endpoints that can change without notice.

swoop calls Google Flights' internal `GetShoppingResults` and `GetBookingResults` RPC endpoints, plus Google Travel Hotels' internal `TravelFrontendUi` batchexecute endpoints. Requests use TLS fingerprint impersonation via [primp](https://github.com/deedy5/primp) to match a real browser session. Responses are deeply nested lists (matching internal protobuf-style schemas) decoded into typed Python dataclasses.

[Perch](https://perchtravel.com) uses swoop in production to monitor booked flights for price drops, saving users an average of $247 per trip.

**[Landing page](https://ayushsaraswat.com/projects/swoop)** · **[How I built this](https://ayushsaraswat.com/writing/reverse-engineering-google-flights)**

---

## Install

```bash
pip install swoop-flights

# With CLI (adds `swoop` command)
pip install swoop-flights[cli]
```

## CLI

<p align="center">
  <img src="docs/screenshot.svg" alt="swoop search JFK LAX 2026-06-15" width="750">
</p>

```bash
# Search flights
swoop search JFK LAX 2026-06-15

# Nonstop, sorted by price
swoop search JFK LAX 2026-06-15 --nonstop --sort cheapest

# Roundtrip, business class
swoop search JFK LAX 2026-06-15 -r 2026-06-22 --cabin business

# Official multi-city search
swoop search --leg JFK LAX 2026-06-15 --leg LAX SFO 2026-06-18 --leg SFO SEA 2026-06-21

# Bookable fare for a known flight
swoop price JFK LAX --depart 2026-06-15 DL2300

# Show copy/paste price commands for displayed rows
swoop search JFK LAX 2026-06-15 --show-price-commands

# Script-stable pricing via selector
SELECTOR=$(swoop search JFK LAX 2026-06-15 -o json -q | jq -r '.results[0].selector')
swoop price --selector "$SELECTOR"
```

<details>
<summary>More CLI examples</summary>

```bash
# Roundtrip shorthand pricing
swoop price JFK LAX --depart 2026-06-15 DL2300 --return 2026-06-22 DL2301

# Explicit leg pricing (supports 3+ legs)
swoop price --leg JFK LAX 2026-06-15 DL2300 --leg LAX SFO 2026-06-18 UA544 --leg SFO SEA 2026-06-21 AS331

# CSV for spreadsheets
swoop search JFK LAX 2026-06-15 -o csv -q > flights.csv

# Price CSV (one row per booking option, with seller and booking_url)
swoop price JFK LAX --depart 2026-06-15 DL2300 -o csv -q > fares.csv

# Search JSON for piping
swoop search JFK LAX 2026-06-15 -o json -q | jq '.results[0] | {selector, price, legs}'

# Filter by airline and time window
swoop search JFK LAX 2026-06-15 -a DL -a UA --depart-after 8 --depart-before 14

# Surface RPC debug logging on stderr (URLs, response sizes, retries)
swoop search JFK LAX 2026-06-15 --verbose
```

</details>

Run `swoop search --help` for all options.

> [!TIP]
> Search shows shopping totals for browsing. Use `--show-price-commands` for copy/paste `swoop price --selector ...` commands in human output, or use `selector` from JSON with `swoop price --selector ...` in scripts.

### Shell completion

```bash
# bash (~/.bashrc)
eval "$(_SWOOP_COMPLETE=bash_source swoop)"

# zsh (~/.zshrc)
eval "$(_SWOOP_COMPLETE=zsh_source swoop)"

# fish (~/.config/fish/config.fish)
_SWOOP_COMPLETE=fish_source swoop | source
```

After reloading your shell, `swoop <TAB>`, `swoop search --<TAB>`, and `-o <TAB>` will autocomplete.

## Python API

### One-way search

```python
from swoop import search

results = search("SFO", "JFK", "2026-06-15")

for option in results.results[:3]:
    print(f"${option.price}")
    for leg in option.legs:
        itinerary = leg.itinerary
        if itinerary is None:
            continue
        print(f"  {leg.origin} -> {leg.destination}")
        print(f"  {itinerary.airline_names}, {itinerary.stop_count} stops")
        print(f"  {itinerary.travel_time} min")

print(results.is_complete)
```

`search()` and `search_legs()` return shopping totals. Use `check_price()`, `price_legs()`, or `price_selector()` when you need the bookable fare for one chosen itinerary.

Each price-check call costs ~2 RPCs (one search, one booking lookup) — including one-way trips, so `PriceResult.booking_options` is populated and `booking_url` is available. For high-volume scoring of many fares, expect rate-limit pressure to scale accordingly; increase `retries` or pace your calls.

<details>
<summary>More examples</summary>

### Price check for a specific flight

```python
from swoop import check_price

result = check_price("DL2300", origin="JFK", destination="LAX", date="2026-06-15")
if result:
    print(f"${result.price}")

result = check_price(
    "DL2300", origin="JFK", destination="LAX", date="2026-06-15",
    return_flight_number="DL2301", return_date="2026-06-22",
)
if result:
    print(f"${result.price} roundtrip — {result.fare_brand}")
    for leg in result.resolved_legs:
        print(f"  {leg.flight_summary} {leg.origin}->{leg.destination} ({leg.selection})")
```

### Price a chosen search result by selector

```python
from swoop import price_selector, search

results = search("JFK", "LAX", "2026-06-15")
option = results.results[0]

price = price_selector(option.selector)
if price:
    print(f"${price.price} — {price.fare_brand}")
```

### Leg-based search and pricing

```python
from swoop import SearchLeg, SelectedLeg, price_legs, search_legs

# Search with explicit legs (official entrypoint for multi-city)
results = search_legs([
    SearchLeg(date="2026-06-15", from_airport="JFK", to_airport="LAX"),
    SearchLeg(date="2026-06-18", from_airport="LAX", to_airport="SFO"),
    SearchLeg(date="2026-06-21", from_airport="SFO", to_airport="SEA"),
])

for option in results.results:
    print(option.selector, option.price)
    for leg in option.legs:
        print(f"  {leg.origin}->{leg.destination}")

# Price with explicit legs
result = price_legs([
    SelectedLeg(flight_number="DL2300", origin="JFK", destination="LAX", date="2026-06-15"),
    SelectedLeg(flight_number="UA544", origin="LAX", destination="SFO", date="2026-06-18"),
    SelectedLeg(flight_number="AS331", origin="SFO", destination="SEA", date="2026-06-21"),
])
```

### Roundtrip search

```python
results = search("SFO", "JFK", "2026-06-15", return_date="2026-06-22")
for option in results.results:
    print(option.price)  # roundtrip total
```

### Cabin class and filters

```python
from swoop import search, SORT_CHEAPEST

results = search(
    "LAX", "NRT", "2026-06-15",
    cabin="business",       # economy, premium-economy, business, first
    max_stops=0,            # nonstop only
    sort=SORT_CHEAPEST,     # cheapest first
    airlines=["NH", "JL"],  # filter to specific carriers
    earliest_departure=8,   # depart after 8am
    latest_departure=14,    # depart before 2pm
)
```

### Hotel search, prices, and reviews

```python
from swoop import hotel_prices, hotel_reviews, hotels

result = hotels("HI New York City Hostel", "2026-06-01", "2026-06-03")
hotel = result.hotels[0]

print(hotel.name, hotel.price, hotel.total_price, hotel.rating)

if hotel.booking_token:
    priced = hotel_prices(
        hotel.booking_token,
        query=hotel.name,
        check_in="2026-06-01",
        check_out="2026-06-03",
    )
    for provider in priced.providers:
        print(provider.name, provider.total_price, provider.url)

    reviews = hotel_reviews(hotel.booking_token)
    for review in reviews.reviews[:3]:
        print(review.source, review.rating, review.text)
```

Exact hotel queries can return a selected-hotel `booking_token` for provider prices and reviews. Broad destination queries return hotel cards when Google includes them in the first response, but may be marked `is_complete=False` because Google's follow-up list RPC can reject direct non-browser calls.

For broad destination searches, pass `include_booking_tokens=True` to run exact follow-up searches for each returned card and attach tokens when Google resolves the hotel. Use `token_enrichment_limit` to cap the number of extra requests.

### Booking details (fare options)

```python
from swoop import search, get_booking_results

results = search("JFK", "LAX", "2026-06-15")
option = results.results[0]
itinerary = option.legs[0].itinerary

# Get fare tiers — just pass the itinerary
options = get_booking_results(itinerary)

for opt in options:
    label = opt.seller_name or "airline-direct"
    print(f"${opt.price} — {opt.brand_label} ({opt.fare_family}) via {label}")
    if opt.booking_url:
        print(f"  book at: {opt.booking_url}")
```

</details>

> [!TIP]
> Google rate-limits aggressively. All RPC functions default to `retries=2` with exponential backoff and jitter. Increase to `retries=3` for extra resilience.

### Runnable examples

Real-world patterns are in [`examples/`](examples/):

- [`examples/price_drop_watcher.py`](examples/price_drop_watcher.py) — Watch a known flight for price drops on a schedule (the pattern Perch uses to save users ~$247/trip).
- [`examples/multi_city_finder.py`](examples/multi_city_finder.py) — Multi-city / open-jaw search with beam-search tuning knobs.

## How it works

swoop reverse-engineers the `FlightsFrontendService` RPC interface that powers Google Flights and selected `TravelFrontendUi` batchexecute RPCs that power Google Travel Hotels. Search parameters are encoded as nested JSON arrays matching Google's internal protobuf-style schemas, then sent as HTTP POST requests. The HTTP client uses TLS fingerprint impersonation (via [primp](https://github.com/deedy5/primp)) so requests are indistinguishable from a real Chrome session.

Responses arrive as deeply nested list structures, no field names, just positional indices. swoop's decoder walks these structures and maps them to typed Python dataclasses (`Itinerary`, `Segment`, `Layover`, `CarbonEmissions`, etc.) with named attributes.

```
                                    ┌─────────────────────────────────────┐
                                    │         Google Flights              │
                                    │    FlightsFrontendService RPC       │
                                    └──────────┬──────────────────────────┘
                                               │
                                    protobuf response
                                    (nested arrays, no field names)
                                               │
┌──────────────┐   HTTP POST    ┌──────────────▼──────────────┐   typed    ┌──────────────────┐
│  search()    │──────────────▶ │     TLS fingerprint         │──────────▶│  Itinerary       │
│  price()     │  JSON-in-JSON  │     impersonation (primp)   │  Python   │  Segment         │
│  check_price │  URL-encoded   │                             │  dataclass│  Layover         │
└──────────────┘                └─────────────────────────────┘           │  CarbonEmissions │
                                                                          └──────────────────┘
```

For the full reverse-engineering story (744 lines of handmade schema, binary protobuf decoding, cabin class debugging), read **[How I built this](https://ayushsaraswat.com/writing/reverse-engineering-google-flights)**.

<details>
<summary>API reference</summary>

### `search(origin, destination, date, **kwargs)`

Search Google Flights and return a `SearchResult`.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `origin` | `str` | required | Origin IATA code |
| `destination` | `str` | required | Destination IATA code |
| `date` | `str` | required | Departure date (`YYYY-MM-DD`) |
| `return_date` | `str \| None` | `None` | Return date for roundtrip |
| `cabin` | `str` | `"economy"` | `economy`, `premium-economy`, `business`, `first` |
| `adults` | `int` | `1` | Number of adults |
| `children` | `int` | `0` | Number of children (2–11) |
| `infants_in_seat` | `int` | `0` | Number of infants in seat |
| `infants_on_lap` | `int` | `0` | Number of infants on lap |
| `max_stops` | `int \| None` | `None` | `None`=any, `0`=nonstop, `1`=1 stop, `2`=2 stops |
| `sort` | `int` | `SORT_DEPARTURE_TIME` | Sort order constant |
| `airlines` | `list[str] \| None` | `None` | Filter by airline codes |
| `flight_number` | `str \| None` | `None` | Filter to a specific flight number; carrier is also added to the first-leg airline filter |
| `include_basic_economy` | `bool` | `False` | Include basic economy fares (excluded by default so prices reflect Main Cabin) |
| `timeout` | `int` | `90` | HTTP timeout in seconds |
| `retries` | `int` | `2` | Retries on HTTP 429 with exponential backoff + jitter |
| `country` | `str \| None` | `None` | Two-letter country code for point of sale (e.g. `"GB"`). Controls currency and available fares |
| `proxy` | `str \| None` | `None` | Proxy URL for routing requests |
| `max_results` | `int \| None` | `None` | Max trip combinations for beam search (multi-leg only) |
| `beam_width` | `int \| None` | `None` | Candidate prefixes per stage (multi-leg only) |
| `time_budget` | `int \| None` | `None` | Seconds before beam search stops exploring (multi-leg only) |

Returns `SearchResult`. Empty results mean no matches were found. Prices in search results are shopping totals.

### `search_legs(legs, **kwargs)`

Search one or more explicit legs and return a trip-level `SearchResult`. This is the public multi-city entrypoint.

### `price_legs(legs, **kwargs)`

Price one or more explicit legs and return `PriceResult | None`.

### `price_selector(selector, **kwargs)`

Price a selected trip row by opaque selector and return `PriceResult | None`.

### `search_raw(origin, destination, date, **kwargs)`

Low-level single-pass search escape hatch. Returns `RawSearchResult` with raw `best` and `other` itinerary buckets from one RPC pass.

### `check_price(flight_number, *, origin, destination, date, **kwargs)`

Look up the current bookable fare for a specific flight. Optimized for the "what does flight X cost?" use case.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `flight_number` | `str` | required | Flight number (e.g. `"DL2300"`) |
| `origin` | `str` | required | Origin IATA code |
| `destination` | `str` | required | Destination IATA code |
| `date` | `str` | required | Departure date (`YYYY-MM-DD`) |
| `return_flight_number` | `str \| None` | `None` | Return flight number for roundtrip |
| `return_date` | `str \| None` | `None` | Return date for roundtrip |
| `cabin` | `str` | `"economy"` | Cabin class |
| `adults` | `int` | `1` | Number of adults |
| `children` | `int` | `0` | Number of children (2–11) |
| `infants_in_seat` | `int` | `0` | Number of infants in seat |
| `infants_on_lap` | `int` | `0` | Number of infants on lap |
| `include_basic_economy` | `bool` | `False` | Include basic economy fares |
| `timeout` | `int` | `90` | HTTP timeout in seconds |
| `retries` | `int` | `2` | Retries on HTTP 429 |
| `country` | `str \| None` | `None` | Two-letter country code for point of sale |
| `proxy` | `str \| None` | `None` | Proxy URL for routing requests |

Returns `PriceResult | None`. `PriceResult` has `price`, `fare_brand`, `is_basic_economy`, `booking_options`, `itinerary`, `resolved_legs`, `rpc_calls`.

### `hotels(query, check_in, check_out, **kwargs)`

Search Google Travel Hotels and return a `HotelSearchResult`.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | `str` | required | Hotel or destination query |
| `check_in` | `str` | required | Check-in date (`YYYY-MM-DD`) |
| `check_out` | `str` | required | Check-out date (`YYYY-MM-DD`) |
| `adults` | `int` | `2` | Adult guest count |
| `child_ages` | `list[int] \| None` | `None` | Ages for child guests |
| `rooms` | `int` | `1` | Room count |
| `currency` | `str` | `"USD"` | Requested ISO 4217 currency |
| `include_booking_tokens` | `bool` | `False` | Run exact hotel follow-up searches to attach provider pricing/review tokens to broad result cards |
| `token_enrichment_limit` | `int \| None` | `None` | Maximum number of hotel cards to enrich when token enrichment is enabled |
| `transport` | `TransportConfig` | `TransportConfig()` | HTTP transport configuration |

Exact hotel queries can include `Hotel.booking_token`, which can be passed to `hotel_prices()` and `hotel_reviews()`.

### `hotel_prices(hotel_token, **kwargs)`

Fetch provider offers for a selected hotel token. Returns a `Hotel` with `providers: list[HotelProvider]`. Pass the original hotel name in `query` when available so swoop can rehydrate Google's destination context before pricing.

### `hotel_reviews(hotel_token, **kwargs)`

Fetch reviews for a selected hotel token. Returns `HotelReviewsResult`.
### `get_booking_results(itinerary_or_token, **kwargs)`

Get fare options for a specific itinerary. Pass an `Itinerary` object directly, or a booking token string with explicit `origin`, `destination`, `date`, and `selected_legs`. Returns `list[BookingOption]` with `price`, `brand_label`, `brand_code`, `is_basic`, `fare_family`, `rebookability_signal`, plus seller fields `seller_name`, `seller_code`, `booking_url`, `logo_url`, and `is_airline_direct` for routing users to the actual booking page.

### `set_country(country_code)`

Set the default country code for all subsequent requests. Controls point of sale, currency, and available fares. Pass `None` to clear.

### `set_proxy(proxy_url)`

Set the default proxy URL for all subsequent requests. Pass `None` to clear.

### Result types

- **`PriceResult`** — `price: int`, `currency: str | None`, `fare_brand: str | None`, `is_basic_economy: bool`, `booking_options: list[BookingOption]`, `itinerary: Itinerary | None`, `resolved_legs: list[ResolvedLeg]`, `rpc_calls: int`
- **`ResolvedLeg`** — `flight_summary: str`, `origin: str`, `destination: str`, `date: str`, `itinerary: Itinerary | None`, `selection: str`
- **`SelectedLeg`** — `flight_number: str`, `origin: str`, `destination: str`, `date: str`
- **`SearchLeg`** — `date: str`, `from_airport: str`, `to_airport: str`, `max_stops: int | None`, `airlines: list[str] | None`
- **`HotelSearchResult`** — `hotels: list[Hotel]`, `query`, `destination_name`, `currency`, `is_complete`
- **`Hotel`** — Hotel card/details with `hotel_id`, `name`, prices, currency, rating, class, location, image, `booking_token`, and provider offers
- **`HotelProvider`** — Provider offer with name, nightly/total prices, currency, booking URL, logo, room name, and cancellation signal
- **`HotelReviewsResult`** — `reviews: list[HotelReview]`, `hotel_token`
- **`HotelReview`** — Review source, author, rating, max rating, relative date, text, and source URL
- **`SearchResult`** — `results: list[TripOption]`, `price_range: PriceRange | None`, `is_complete: bool`, `currency: str | None`
- **`TripOption`** — `selector: str`, `price: int | None`, `currency: str | None`, `legs: list[TripLeg]`
- **`TripLeg`** — `origin: str`, `destination: str`, `date: str`, `itinerary: Itinerary | None`
- **`RawSearchResult`** — low-level `best: list[Itinerary]`, `other: list[Itinerary]`, `price_range: PriceRange | None`
- **`Itinerary`** — Full itinerary with `price`, `flights`, `layovers`, `travel_time`, `booking_token`, `carbon_emissions`
- **`Segment`** — Segment details: `airline`, `flight_number`, `aircraft`, `legroom`, `co2_grams`, `amenities`
- **`Layover`** — Stop info: `minutes`, airports, `is_overnight`
- **`CarbonEmissions`** — `this_flight_grams`, `typical_for_route_grams`, `difference_percent`

### Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `SORT_TOP` | `1` | Google's default ranking |
| `SORT_CHEAPEST` | `2` | Cheapest first |
| `SORT_DEPARTURE_TIME` | `3` | By departure time |
| `SORT_ARRIVAL_TIME` | `4` | By arrival time |
| `SORT_DURATION` | `5` | Shortest first |

### Error handling

All exceptions inherit from `SwoopError`. Catch `SwoopRateLimitError` for HTTP 429, `SwoopHTTPError` for other HTTP failures, and `SwoopParseError` for response decoding issues.

</details>

## Contributing

Issues and pull requests welcome at [github.com/saraswatayu/swoop](https://github.com/saraswatayu/swoop/issues).

Testing notes:

- Push and PR CI runs the deterministic offline suite only: `python -m pytest tests/ -v -m 'not live'`
- Benchmarks are opt-in and stay skipped in normal runs unless you pass `--run-benchmarks` (or use `--benchmark-only`).
- Live Google canaries run separately in the `live-canary` workflow on a weekly schedule or by manual dispatch.
- Mutation testing is available by manual dispatch in the `mutation` workflow and is scoped to `_selection`, `_booking`, `decoder`, and `rpc`.
- Real-world bugs should be added to the incident regression bank in [`tests/incidents/manifest.json`](tests/incidents/manifest.json) with a linked regression test or sanitized fixture.
- When a live canary finds a useful new payload shape, promote it manually into `tests/fixtures/contract_corpus_manifest.json` and the tracked fixture corpus after review.

## License

MIT

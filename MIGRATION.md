# Migration Guide

Upgrade notes for swoop. Each section shows the old call shape, the new one, and what (if anything) you have to change.

## Unreleased — trip discovery and exact selectors

Roundtrip and two-bound open-jaw searches still default to quick discovery: one
search call returns outbound flight details and an estimated **whole-trip**
price. That price does not identify a chosen return. Such `TripOption` rows now
have `is_resolved=False`. One-way rows and fully expanded trip rows have
`is_resolved=True`.
Manually constructed `TripOption` instances default to unresolved; the search
builder explicitly sets resolution from the number of selected/requested bounds.

Pass `expand_legs=True` to `search()` or `search_legs()` when you need exact
complete flight combinations. Each outbound is sent back to Google as a selected
prefix to discover valid remaining bounds. The final stage supplies the trip
total and booking token; prices from individual stages are never added together.
All trip shapes now default to discovery. Three or more bounds no longer expand
automatically; use `expand_legs=True` to retain the previous behavior.

```python
from swoop import search, price_selector

quick = search("JFK", "LHR", "2030-04-15", return_date="2030-04-22")
assert all(not option.is_resolved for option in quick.results)

complete = search(
    "JFK", "LHR", "2030-04-15", return_date="2030-04-22",
    expand_legs=True, max_results=20, beam_width=20, time_budget=30,
)
for option in complete.results:
    assert option.is_resolved
    price = price_selector(option.selector)
```

**Behavioral migration:** `price_selector()` now returns `None` for any selector
that leaves a requested bound unselected, including old outbound-only roundtrip
selectors. It no longer silently selects Google's first return. Search again
with `expand_legs=True` to obtain an exact selector, or use `check_price()` /
`price_legs()` to explicitly supply the desired flights. `price_deal()` and
`price_explore()` request complete expansion internally.

`SearchResult.is_complete` continues to describe search coverage, independently
of per-trip `is_resolved`. A fast discovery result can be complete while its
trips remain unresolved. Expanded searches share the beam fairly across chosen
prefixes, but still expose a bounded subset: `max_results` (default 10),
`beam_width` (default 15), and `time_budget` (default 90 seconds) apply. Truncation,
budget exhaustion, and partial upstream rejections set `is_complete=False`.
Expansion includes the initial call in its time budget and caps each request's
timeout to remaining time; retries can extend total elapsed time.

`SwoopTransportError` now wraps failures while sending a POST or reading its
response body, including lazy body timeouts. Catch it (or `SwoopError`) instead
of a provider-specific exception. Expanded searches retain completed choices
from other prefixes with `is_complete=False`; a failed initial call or an empty
beam after transport failure raises. HTTP 429 retry policy remains unchanged.

The CLI adds `--expand-legs`; search JSON and CSV expose `is_resolved`.
`--show-price-commands` requires resolved rows and explains the expansion flag
when a quick roundtrip would produce an incomplete selector.

### Wider inventory and chosen-prefix continuation

Searches request Google's wider result list by default (`show_all_results=True`).
Set it to `False` (CLI `--default-results`) to use the shortened list. The flag is
stored in every selector and replayed during continuation and pricing. Existing
selectors without the flag retain their original shortened mode.

`search_next_leg(selector, time_budget=30)` returns every next-bound choice for
that exact selected prefix without a beam or result cap. It replays selected
flights, preserving all query bounds, cabin, party and filters; disappeared
flights raise `ValueError`. Repeat until `is_resolved=True`, then call
`price_selector(selector, time_budget=30)`. A completed selector cannot continue.
The CLI equivalent is `swoop next --selector SEL --time-budget 30` (JSON).

`SearchResult` now exposes `result_scope` (`all`, `default`, `limited`),
`truncation_reasons`, `raw_result_count`, `decoded_result_count`, `rpc_calls`,
and `unexpanded_prefixes`. Counts include every search RPC, including replay;
`unexpanded_prefixes` counts branches skipped before completion, not discarded
complete choices. Reasons distinguish default results, limited transport,
parse loss, beam/result limits, time budget and failed branches. `is_complete`
means complete relative to the requested Google response, never all market
inventory. `price_selector` shares its optional budget across replay and lookup.
Transport retries may still extend elapsed time; use `retries=0` for a strict
per-request budget. No return or next bound is chosen automatically.

## 0.6 → 0.7

### Upstream outages now raise instead of returning empty

swoop now has a stated error-handling contract (see the module docstring in
`swoop/exceptions.py`). When Google rejects a request with a structured
`ErrorResponse` envelope (an HTTP 200 carrying a gRPC status code, e.g. the
2026-06-11 outage), single-shot calls — `search`, `search_legs`, `check_price`,
`price_selector`, `price_legs`, `deals`, `explore`, `get_booking_results` — now
raise `SwoopUpstreamError` instead of silently returning an empty result. A
genuinely empty result (no flights) still returns empty.

What you need to do: if you have code that treated "empty result" as "maybe an
outage", switch to catching the exception.

```python
# 0.6 — outage and "no flights" were indistinguishable
result = search("SFO", "JFK", "2027-01-01")
if not result.results:
    ...  # could be no flights OR Google was down

# 0.7
from swoop import search, SwoopUpstreamError
try:
    result = search("SFO", "JFK", "2027-01-01")
    if not result.results:
        ...  # genuinely no flights
except SwoopUpstreamError as e:
    ...  # Google rejected the request (e.grpc_code); retry/alert
```

`transport.retries` still covers HTTP 429 only; upstream errors are surfaced
(not auto-retried) so you can apply your own retry policy.

### Also new in 0.7 (no migration required)

- **`PriceResult.is_estimate`**: `True` when the price is the search-derived
  shopping figure rather than a confirmed bookable fare (booking lookup skipped
  or degraded). Additive field, defaults `False`.
- **Aggregate calls degrade, not crash**: the multi-city beam (`search` with 3+
  legs) and `price_explore_all` keep partial results and raise
  `SwoopUpstreamError` only on a *total* outage (every branch / destination
  rejected). A partial outage leaves the affected entries out / `None`.

## 0.4 → 0.5

### What changed

One-way pricing now fetches `GetBookingResults` instead of short-circuiting to the search-result price. `check_price`, `price_selector`, and `price_legs` make one extra RPC for single-leg trips (2 total instead of 1) and return the cheapest eligible booking option price.

Two consequences:

1. `PriceResult.price` for one-ways may differ from `Itinerary.price` from the search response. The booking-result price is the bookable fare; the search-result price is the shopping total.
2. `PriceResult.booking_options` is no longer empty for one-ways.

If the booking RPC fails or returns no eligible options, swoop falls back to the search-result price.

### What you need to do

For most callers: nothing. The function signatures are unchanged and `PriceResult.price` is still authoritative.

If you were comparing `PriceResult.price` against `Itinerary.price` and expecting equality, stop. They can now diverge legitimately on one-ways, the same way they always could on roundtrips.

If you were relying on `rpc_calls == 1` for one-way price lookups in tests, that's now `2` on the happy path and `1` on the fallback path.

### New capabilities: seller fields on BookingOption

`BookingOption` now exposes who's selling the fare and where the booking link goes:

- `seller_name` — display name, e.g. `"Mytrip"`, `"Qatar Airways"`
- `seller_code` — short code, e.g. `"ETRAVELI_Mytrip"`, `"QR"`
- `booking_url` — the `google.com/travel/clk/f?u=…` redirect that opens the seller's checkout
- `logo_url` — gstatic partner logo when Google provides `logo_code`, otherwise empty
- `is_airline_direct` — `True` when the booking is direct with the operating carrier, `False` for OTAs

```python
from swoop import check_price

result = check_price("DL2300", origin="JFK", destination="LAX", date="2026-06-15")
for option in result.booking_options:
    via = "direct" if option.is_airline_direct else "OTA"
    print(f"${option.price} {option.seller_name} ({via}) -> {option.booking_url}")
```

`swoop price --json` and `swoop price --csv` now emit the full `BookingOption` field set, including `fare_family`, `rebookability_signal`, and all five seller fields.

Note on `logo_url`: it's only populated when Google sends a `logo_code`. The previous behaviour silently constructed a URL from `seller_code`, which 404'd for OTA codes. If you want the airline-direct fallback, build it yourself:

```python
logo = option.logo_url or f"https://www.gstatic.com/flights/airline_logos/70px/{option.seller_code}.png"
```

## 0.3 → 0.4

Three breaking changes: `Flight` → `Segment` rename, `BookingOption` dict-style access removed, and `search()` / `check_price()` now take `TransportConfig` and `Passengers` dataclasses instead of scattered kwargs.

### Passenger counts

Scattered `children` / `infants_in_seat` / `infants_on_lap` kwargs collapsed into a single `Passengers` dataclass.

```python
# 0.3
from swoop import search

results = search(
    "SFO", "JFK", "2026-06-15",
    adults=2,
    children=1,
    infants_in_seat=1,
)
```

```python
# 0.4
from swoop import search, Passengers

results = search(
    "SFO", "JFK", "2026-06-15",
    passengers=Passengers(adults=2, children=1, infants_in_seat=1),
)
```

`Passengers()` defaults to one adult, so most callers can drop the kwarg entirely.

### Transport configuration

`timeout`, `retries`, `country`, and `proxy` collapsed into `TransportConfig`. (0.4.1 added `impersonate` to the same dataclass for TLS fingerprint rotation.)

```python
# 0.3
results = search(
    "SFO", "JFK", "2026-06-15",
    timeout=30,
    retries=3,
    country="GB",
    proxy="http://user:pass@proxy:8080",
)
```

```python
# 0.4
from swoop import search, TransportConfig

results = search(
    "SFO", "JFK", "2026-06-15",
    transport=TransportConfig(
        timeout=30,
        retries=3,
        country="GB",
        proxy="http://user:pass@proxy:8080",
        impersonate="chrome",  # added in 0.4.1
    ),
)
```

This applies to every public function that takes transport settings: `search`, `search_legs`, `check_price`, `price_selector`, `price_legs`.

### Flight → Segment rename

`Flight` was renamed to `Segment` so the terminology matches every other flights API on earth. `Itinerary.segments` returns `Segment` objects.

```python
# 0.3
from swoop.decoder import Flight

for f in itinerary.segments:
    assert isinstance(f, Flight)
```

```python
# 0.4
from swoop import Segment

for f in itinerary.segments:
    assert isinstance(f, Segment)
```

If you were destructuring fields off the object, nothing else changes — field names are identical.

### BookingOption dict-style access removed

`BookingOption.__getitem__`, `.get()`, `.keys()`, `.values()`, and `.items()` are gone. Use attribute access.

```python
# 0.3
price = option["price"]
brand = option.get("brand_label", "")
```

```python
# 0.4
price = option.price
brand = option.brand_label or ""
```

### Cabin class type

`cabin` was a free-form string. It's now a `Literal["economy", "premium-economy", "business", "first"]` exported as `CabinClass`. Same string values, but type checkers catch typos.

```python
# 0.3 — silently accepted "premiumeconomy", "Business", etc.
results = search("SFO", "JFK", "2026-06-15", cabin="premiumeconomy")
```

```python
# 0.4 — pyright/mypy reject anything outside the four canonical values
from swoop import CabinClass, search

cabin: CabinClass = "premium-economy"
results = search("SFO", "JFK", "2026-06-15", cabin=cabin)
```

Underlying cabin detection was also fixed in 0.4: airlines like British Airways ("Upper Class") and Turkish ("Premium Flex") used to be silently misclassified by brand-name text matching. Cabin is now read from the numeric protobuf field. If you were filtering on `is_basic_economy` or seeing the wrong fare brand, those results should now be correct without code changes.

### Also new in 0.4 (no migration required)

These are additive and don't require code changes, but they're worth knowing:

- **Multi-currency**: `TripOption.currency`, `PriceResult.currency`, and `SearchResult.currency` are populated with ISO 4217 codes. Prices for JPY/INR/KRW are no longer mangled by a hardcoded `/100` divisor.
- **CO₂ and amenities**: `Segment.legroom`, `Segment.has_premium_ife`, `Segment.amenities`, `Segment.seat_type`, `Itinerary.stop_count`, `Itinerary.is_budget_carrier`, `Itinerary.quality_signals` are now decoded.
- **Booking fare metadata**: `BookingOption.fare_family` and `BookingOption.rebookability_signal`.
- **CLI flags**: `--country`, `--proxy`, `--children`, `--infants-in-seat`, `--infants-on-lap`, `--max-results`, `--beam-width`, `--time-budget`.

### Additive separate-ticket exclusion

`exclude_separate_tickets=False` is a new keyword on flight search/pricing and
deals entry points. Set it to `True` to request exclusion of separate tickets
and self-transfer. Existing selectors without the field retain their original
unrestricted behavior; new selectors carry the exact boolean through replay.

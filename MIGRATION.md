# Migration Guide

Upgrade notes for swoop. Each section shows the old call shape, the new one, and what (if anything) you have to change.

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

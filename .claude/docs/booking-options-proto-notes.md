# Booking Options RPC Proto Notes

Reverse-engineering notes for:

- `GetShoppingResults`
- `GetBookingResults`

These notes focus on fields we validated as stable across Delta, American, and United in March 2026 samples.

## Request/response path

1. Run shopping RPC (`GetShoppingResults`) and pick an itinerary.
2. Read itinerary summary token at `itinerary[1][1]`.
3. Build booking RPC request:
   - inner payload: `[[null, booking_token], filter_block, null, 0]`
   - selected leg must be set at `filter_block[13][0][8]`.
4. Parse booking RPC options from the `wrb.fr` frame with `payload[1][0]`.

## Option field map (validated)

Per booking option list:

- `option[7]` = price block:
  - `option[7][0][1]` = display price (USD integer)
  - `option[7][1]` = base64 protobuf (`ItinerarySummary`) with:
    - `flights = "options:<index>"`
    - `price.price = cents`
    - `price.currency = "USD"`
- `option[19]` = JSON string with two base64 protobuf tokens:
  - token 0 contains display currency + display price context
  - token 1 contains segment descriptor (origin/date/destination/carrier/flight/aircraft)
- `option[21]` = brand block:
  - `option[21][0][1]` = brand code
  - `option[21][1]` = attribute vector
  - `option[21][2]` and `option[21][16]` are useful Basic-economy flags
  - `option[21][3]` = brand label
  - `option[21][6][0][0]` = **cabin class** (Seat enum: 1=economy, 2=premium-economy, 3=business, 4=first)
  - `option[21][6][0][1]` = amenity flags array (same format as `segment[12]`)
  - `option[21][6][0][2]` = seat type indicator (similar to `segment[13]`)
- `option[24]` = additional boolean tail flag used with `option[21]` flags.

## Parser outputs (current)

Each parsed `BookingOption` dataclass includes:

**Public fields:**
- `price`
- `brand_code`
- `brand_label`
- `is_basic`
- `fare_family` (`basic|standard|enhanced|premium|unknown`)
- `rebookability_signal` (`restricted|standard_rebookable|upgraded_rebookable|unknown`)

**Internal fields (`_`-prefixed, not public API):**
- `_is_basic_by_flags`, `_is_basic_by_text`
- `_option_index`, `_token_price_cents`, `_display_price_cents`
- `_price_delta_cents`
- `_context_origin_iata`, `_context_destination_iata`
- `_context_departure_local_iso`, `_context_arrival_local_iso`
- `_context_carrier_code`, `_context_flight_number`, `_context_aircraft_code`
- `_brand_attribute_vector` (scalar-normalized diagnostic slice)
- `_registry_version`

## Seller list (airline-direct vs OTA)

`option[1]` is a list of seller entries; `option[1][0]` is
`[seller_code, seller_name, logo_code_or_None, is_airline_direct_bool]`. The
parser takes the first entry per option (multi-seller `option[1]` is permitted
by the wire format but unobserved — logged at DEBUG if it ever appears).

Each booking option is one **booking channel**, not just a fare tier. Whether
Google returns OTAs is route-dependent, not a swoop limitation:

- Premium cabins and major US carriers (AA/DL/UA/B6) → airline-direct only.
- International economy on foreign carriers → airline-direct + a wall of OTAs.

OTA options carry `is_airline_direct = False`, a non-IATA `seller_code`, and
typically **null brand fields** (no `brand_label`/`brand_code`) — they still
carry cabin class at `[6][0][0]`, so the parser keeps them via the option[21]
fallback rather than dropping them.

Seller codes observed live on SFO→MNL economy (Philippine Airlines), captured
as fixture `booking_intl_economy_ota_response.txt` / contract case
`booking_intl_economy_ota_v1`:

`PR` (airline-direct), `EXPEDIA`, `FLIGHTHUB`, `CHEAPOAIR`, `JUSTFLY`,
`ONETRAVEL`, `BYOJET`, `OVAGO`, `OOJO`, `WEGO`, `TRAVOMINT`, `SCHOLAR_TRIP`,
`ADAMVACATIONS`, `BUSINESSCLASS`, `ARANGRANT`. Other captures also show
`ETRAVELI_*` prefixed codes (e.g. `ETRAVELI_Gotogate`, `ETRAVELI_FALLBACK`).

## Basic economy signal

Observed robust signal:

- `option[21][2] == true`
- `option[21][16] == true`
- `option[24] == true`

This matched Basic options across AA/UA/DL main-basic samples.

Important exception observed:

- `DELTA COMFORT BASIC` may include `BASIC` in brand text while not setting all three flags.

Current production logic therefore uses:

1. flag-based basic signal
2. text fallback (`"BASIC"` in brand code/label)

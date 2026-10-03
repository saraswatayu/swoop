# Google Flights Protobuf/Response Schema Reference

Comprehensive reverse-engineered schema for Google Flights' `GetShoppingResults` and `GetBookingResults` RPC responses. This document covers every known field in the nested-list data structure returned by the FlightsFrontendService endpoints.

**Last updated:** March 2026
**Data sources:** JFK-LAX (domestic economy), JFK-LHR (international economy + business class), SFO-JFK (domestic economy), SFO-NRT (international business), ORD-ASE (regional/operated-by), plus booking options corpus.

---

## Response Envelope

RPC responses are wrapped in a security prefix (`)]}'`) followed by a JSON array. The flight data lives at:

```
outer[0][2] → JSON string → parse again → data (33-element array, indices 0-32)
```

For HTML/SSR pages, flight data is in `AF_initDataCallback({key: 'ds:1', data: [...]})`.

---

## Top-Level Structure (`data[0..32]`)

| Index | Type | What It Contains | Currently Used? |
|-------|------|------------------|-----------------|
| **0** | Array[5] | **Session metadata** — request timestamp (microseconds), request IDs, session tokens | No |
| **1** | Array[1-2] | **Airport details** — origin/destination with full metadata (IATA, name, Knowledge Graph ID, city, images, lat/lon, country code). Length 1 for one-way, 2 for roundtrip | No |
| **2** | Array[5]/null | **Best flights** — `[0]` = itinerary list. **Often null from RPC** — all results in `data[3]` instead | **Yes** — `[2][0]` |
| **3** | Array[5] | **Other flights** — same structure as index 2 | **Yes** — `[3][0]` |
| **4** | null | Unused | — |
| **5** | Array[14]/null | **Price insights** — price level, typical/low/high prices, 60-day price history graph, calendar data. **Only available in HTML/SSR, null from RPC** | No |
| **6** | null | Unused | — |
| **7** | Array[8] | **Filter metadata** — price range, alliances, airlines, connecting airports, duration range, stop options | **Partial** — `[7][0]` for price range |
| **8** | null | Unused | — |
| **9** | null | Unused | — |
| **10** | null | Unused | — |
| **11** | Array[N] | **Baggage policy URLs** — per-airline `[iata, name, url]` links to checked baggage policies | No |
| **12** | Array[2]/null | **Search parameters token** — encoded search params + flag. **Only available in HTML/SSR, null from RPC** | No |
| **13** | null | Unused | — |
| **14** | Array[6] | **Request context** — timestamps + metadata, possibly for pagination | No |
| **15** | null | Unused | — |
| **16** | null | Unused | — |
| **17** | Array[N] | **Location reference table** — all airports/cities referenced in results with full metadata (64+ entries typical) | No |
| **18** | Array[2]/null | **Compact search token** — shorter encoded search params + flag. **Only available in HTML/SSR, null from RPC** | No |
| **19** | null | Unused | — |
| **20** | bool | **Flag** — `false` in all observed RPC data | No |
| **21** | null | Unused | — |
| **22** | null | Unused | — |
| **23** | null | Unused in RPC (may be present in HTML/SSR) | — |
| **24** | null | Unused | — |
| **25** | Array[1] | **Current lowest price** — `[[null, price_usd]]` | No |
| **26** | Array[N] | **Accessibility URLs** — per-airline `[iata, name, url]` links to special assistance pages | No |
| **27-28** | null | Unused | — |
| **29** | int | **Flag** — observed value: 2 | No |
| **30** | Array[2] | **Comprehensiveness lure** — `[[null, price], "base64_token"]` for "view all" prompt | No |
| **31** | bool | **Flag** — `true` in all observed data | No |
| **32** | bool | **Flag** — `true` in all observed data | No |

---

## Price Insights (`data[5]`) — NOT CURRENTLY USED

**⚠️ Only available in HTML/SSR responses, null from RPC endpoint.**

This is the data behind Google Flights' "Price insights" panel. Not currently accessible via RPC.

| Index | Type | Meaning | Example |
|-------|------|---------|---------|
| **[0]** | int | **Price level indicator** — observed values: 1 (low), 3 (high), 5 (very high). Scale and exact breakpoints TBD | 3 |
| **[1]** | [null, int] | **Current/typical price** (USD) | [null, 321] |
| **[2]** | [null, int] | **Low price** for this route (USD) | [null, 264] |
| **[3]** | [null, int] | **Price difference** from typical (negative = cheaper) | [null, -57] |
| **[4]** | [null, int] | **Lowest price seen** in tracking window | [null, 225] |
| **[5]** | [null, int] | **Highest price seen** in tracking window | [null, 395] |
| **[6]** | int | **Flag** — always 1 in observed data | 1 |
| **[7]** | [int, [null, int]] | **Time period metadata** — first value may be months of history, second is delta or threshold | [5, [null, 35]] |
| **[8-9]** | null | Unused | — |
| **[10]** | Array | **Price history graph** — `[0]` contains array of `[timestamp_ms, price_usd]` pairs. Typically ~60 data points covering ~60 days | See below |
| **[11]** | Array | **Session metadata** (same structure as `data[0]`) | — |
| **[12]** | string | **Destination city name** | "Los Angeles" |
| **[13]** | Array | **Calendar/tracking metadata** — date milestones with days-until-departure | See below |

### Price History Graph (`data[5][10][0]`)

Array of `[timestamp_ms, price_usd]` pairs, one per day, ~60 days:

```json
[
  [1767848400000, 194],   // Jan 7 2026, $194
  [1767934800000, 194],   // Jan 8 2026, $194
  ...
  [1773028800000, 321]    // Mar 7 2026, $321 (most recent)
]
```

### Calendar Metadata (`data[5][13]`)

Tracking milestones showing when price tracking started and key dates:

```json
[
  [[2025, 12, 6], 104, [3, 3]],  // Tracking start date, 104 days before flight, [price_level, ?]
  [[2026, 3, 6], 14, [3, 1]],    // Recent date, 14 days before flight
  [[2026, 3, 9], 11],             // Today, 11 days before flight
  [null, 48]                      // Total tracking period? Or booking window?
]
```

---

## Filter Metadata (`data[7]`)

| Index | Type | Meaning | Example |
|-------|------|---------|---------|
| **[0]** | [[null, low], [null, high]] | **Price range** across all results | [[null, 321], [null, 1988]] |
| **[1]** | [alliances[], airlines[]] | **Airline filters** — alliances with codes/names, airlines with IATA/names | See below |
| **[2]** | Array | **Connecting airports** — `[[iata, city], ...]` for layover filter | See below |
| **[3]** | [min, max] | **Duration range** in minutes | [303, 1981] |
| **[4]** | [[stops...]] | **Available stop counts** — e.g., `[[1, 2, 3]]` means 1-stop, 2-stop, 3-stop available | [[1, 2, 3]] |
| **[5]** | [bool...] | **Filter availability flags** | [true, true, false, true] |
| **[6]** | null | Unused in observed data | null |
| **[7]** | [bool] | **Flag** | [false] |

### Alliance/Airline Filters (`data[7][1]`)

```json
[
  // [0] = alliances
  [["ONEWORLD", "Oneworld"], ["SKYTEAM", "SkyTeam"], ["STAR_ALLIANCE", "Star Alliance"]],
  // [1] = airlines
  [["AA", "American"], ["DL", "Delta"], ["B6", "JetBlue"], ...]
]
```

### Connecting Airports (`data[7][2]`)

Nested array of `[iata_code, city_name]` pairs for available layover airports:

```json
[
  [["ATL", "Atlanta"], ["ORD", "Chicago"], ["DFW", "Dallas"], ...]
]
```

---

## Airport Details (`data[1]`)

Rich airport metadata not currently extracted:

```
data[1][0] = outbound: [[origin_info], [destination_info]]
data[1][1] = return:   [[origin_info], [destination_info]]  (roundtrip only)
```

Each airport entry:

| Index | Type | Meaning | Example |
|-------|------|---------|---------|
| **[0]** | [code, type] | IATA code + type (0=airport) | ["JFK", 0] |
| **[1]** | string | Full airport name | "John F. Kennedy International Airport" |
| **[2]** | [kg_id, city, [images...]] | Knowledge Graph ID, city name, thumbnail image URLs | ["/m/02_286", "New York", [["https://..."]]] |
| **[3]** | [lat, lon] | GPS coordinates | [40.6397222, -73.778889] |
| **[4]** | string | Country code (ISO 2-letter) | "US" |
| **[5]** | int | Flag (always 0 for airports) | 0 |
| **[6]** | string | Country name | "United States" |

---

## Location Reference Table (`data[17]`)

Array of all airports and cities referenced in the search results. Each entry has 7 fields:

| Index | Type | Meaning | Example |
|-------|------|---------|---------|
| **[0]** | [id, type] | IATA code or KG ID + type (0=airport, 4=city) | ["ATL", 0] or ["/m/013yq", 4] |
| **[1]** | string | Full name | "Hartsfield-Jackson Atlanta International Airport" or "Atlanta" |
| **[2]** | [kg_id, city, images] | Same as airport details field [2] | — |
| **[3]** | [lat, lon] | GPS coordinates | — |
| **[4]** | string | Country code | "US" |
| **[5]** | int/null | Flag (0=airport, null=city) | 0 |
| **[6]** | string | Country name | "United States" |

---

## Itinerary Structure

Each itinerary in `data[2][0]` or `data[3][0]` is an **11-element array**:

| Index | Type | Meaning | Currently Used? |
|-------|------|---------|-----------------|
| **[0]** | Array[25] | **Flight data** — all segment/route info (see below) | **Yes** |
| **[1]** | Array[2] | **Price & booking token** — `[[null, price_usd], "base64_booking_token"]` | **Yes** |
| **[2]** | null | Unused | — |
| **[3]** | bool | **Budget carrier flag** — `true` for budget carriers (Frontier, Spirit), `false` for standard carriers | No |
| **[4]** | Array[7] | **Quality signals** — fare class/amenity flags (see below) | No |
| **[5]** | Array[3] | **Warning flags** — `[is_budget_warning, flag1, flag2]` (all booleans) | No |
| **[6]** | bool | **Flag** — always `false` | No |
| **[7]** | Array | **Empty array** `[]` | No |
| **[8]** | string | **Extended booking token** — JSON-wrapped base64 protobuf containing full segment identity (see below) | No |
| **[9]** | Array/null | **Cabin class indicator** — `[[1]]` = economy-class seating, `[[2]]` = premium-class seating, `null` = sometimes absent | No |
| **[10]** | bool | **Flag** — always `false` | No |

### Quality Signals (`itinerary[4]`)

Structure varies by cabin class:

**Economy:**
```json
[null, null, 3, null, quality_tier, false, [bag_flag_1, bag_flag_2]]
```

**Business/First:**
```json
[null, null, null, null, 1, null, [null, 1]]
```

| Subfield | Meaning | Values |
|----------|---------|--------|
| **[2]** | **Cabin flag** | 3 = economy, null = premium cabin |
| **[4]** | **Quality/service tier** | 1 = standard carrier (AA, DL, B6), 3 = budget carrier (F9) |
| **[5]** | **Economy flag** | `false` = economy, null = premium cabin |
| **[6]** | **Bag inclusion flags** | `[0,0]` = no bags, `[0,1]` = personal item, `[null,1]` = bags included (business) |

### Warning Flags (`itinerary[5]`)

```json
[is_budget_warning, flag1, flag2]
```

| Subfield | Meaning | Values |
|----------|---------|--------|
| **[0]** | **Budget carrier warning** | `true` = Frontier/Spirit/budget, `false` = standard |
| **[1]** | Flag | Always `false` in observed data |
| **[2]** | Flag | Always `false` in observed data |

### Extended Booking Token (`itinerary[8]`)

JSON string wrapping a base64-encoded protobuf. Decode: `JSON.parse(itin[8])[0]` → base64 decode → protobuf.

Contains full segment identity:
- Field 1 (varint): version/type indicator (observed: 2)
- Field 2 (string): currency code ("USD")
- Field 3 (nested): price in cents (varint)
- Field 4 (nested): segment descriptors, each containing:
  - Origin IATA code
  - Departure datetime (local ISO with timezone offset)
  - Destination IATA code
  - Arrival datetime (local ISO with timezone offset)
  - Marketing carrier code
  - Flight number
  - Aircraft type code (IATA format, e.g., "32S")
  - Airline name

This token provides a self-contained itinerary descriptor that's more complete than the standard booking token — useful for fare class expansion and identity verification.

---

## Flight Data (Itinerary Level — `itinerary[0]`)

The main flight data is a **25-element array**:

| Index | Type | Meaning | Currently Used? |
|-------|------|---------|-----------------|
| **[0]** | string | **Primary airline IATA code** | **Yes** |
| **[1]** | string[] | **Airline names** (may include multiple for codeshare/interline) | **Yes** |
| **[2]** | Array[] | **Segments list** — each element is a flight segment (see below) | **Yes** |
| **[3]** | string | **Departure airport IATA** | **Yes** |
| **[4]** | [y, m, d] | **Departure date** | **Yes** |
| **[5]** | [h] or [h, m] | **Departure time** | **Yes** |
| **[6]** | string | **Arrival airport IATA** | **Yes** |
| **[7]** | [y, m, d] | **Arrival date** | **Yes** |
| **[8]** | [h, m] | **Arrival time** | **Yes** |
| **[9]** | int | **Total travel time** (minutes, gate-to-gate including layovers) | **Yes** |
| **[10]** | null | Unused | — |
| **[11]** | null | Unused | — |
| **[12]** | bool | **Flag** — always `false` | No |
| **[13]** | Array[] | **Layovers** — see Layover Structure below | **Yes** |
| **[14]** | null | Unused | — |
| **[15]** | null | Unused | — |
| **[16]** | null | Unused | — |
| **[17]** | string | **Itinerary ID** — short hash for UI references (e.g., "BwJxif") | No |
| **[18]** | Array | **Session context** — request timestamps + metadata | No |
| **[19]** | int | **Flag** — always 1 | No |
| **[20]** | null | Unused | — |
| **[21]** | null | Unused | — |
| **[22]** | Array[18] | **Carbon emissions** — comprehensive CO2 data (see below) | **Partial** |
| **[23]** | [int] | **Flag** — always `[1]` | No |
| **[24]** | Array[] | **Accessibility/special services URLs** — per airline | No |

---

## Flight Segment Structure

Each segment in `flightData[2]` is a **33-element array**:

| Index | Type | Meaning | Currently Used? |
|-------|------|---------|-----------------|
| **[0]** | null | Reserved | No |
| **[1]** | null | Reserved | No |
| **[2]** | string/null | **Operating airline name** — only set when different from marketing airline (e.g., "SkyWest DBA United Express") | **Yes** |
| **[3]** | string | **Departure airport IATA code** | **Yes** |
| **[4]** | string | **Departure airport full name** | **Yes** |
| **[5]** | string | **Arrival airport full name** | **Yes** (mapped as arrival name) |
| **[6]** | string | **Arrival airport IATA code** | **Yes** |
| **[7]** | null | Reserved | No |
| **[8]** | [h, m] | **Departure time** (24h format, minutes may be absent if :00) | **Yes** |
| **[9]** | int/null | **Premium IFE indicator** — 1 = has premium in-flight entertainment system, null = no. Correlates with international/wide-body flights (B787, A330, B777, A321neo transatlantic) | No |
| **[10]** | [h, m] | **Arrival time** (24h format) | **Yes** |
| **[11]** | int | **Flight duration** (minutes) | **Yes** |
| **[12]** | Array | **Amenity/feature flags** — 12-element array (see Amenities below) | No |
| **[13]** | int | **Seat type indicator** — 1 = standard economy, 2 = basic/budget economy, 3 = extra legroom/premium economy class | No |
| **[14]** | string | **Seat pitch** (short format) | **Yes** |
| **[15]** | Array/null | **Codeshare flights** — list of `[airline_code, flight_num, null, airline_name]` | **Yes** |
| **[16]** | int | **Actual segment cabin** — 1=economy, 2=premium economy, 3=business, 4=first; independent of requested cabin | **Yes** |
| **[17]** | string | **Aircraft type** | **Yes** |
| **[18]** | Array/null | **Aircraft configuration flag** — `[true]` or `[null, true]` on some aircraft, null on others. Not consistently correlated with aircraft type or age | No |
| **[19]** | bool | **Overnight flag** — `false` = same day, `true` = crosses midnight | **Yes** |
| **[20]** | [y, m, d] | **Departure date** | **Yes** |
| **[21]** | [y, m, d] | **Arrival date** | **Yes** |
| **[22]** | Array[4] | **Airline metadata** — `[iata_code, flight_number, null, airline_name]` | **Yes** |
| **[23]** | null | Reserved | No |
| **[24]** | null | Reserved | No |
| **[25]** | int | **Flag** — always 1 | No |
| **[26]** | null | Reserved | No |
| **[27]** | null | Reserved | No |
| **[28]** | null | Reserved | No |
| **[29]** | null | Reserved | No |
| **[30]** | string/null | **Legroom** (full format, e.g., "28 inches", "31 inches"). **Null for business/first class** (lie-flat seats don't have legroom specs) | **Yes** |
| **[31]** | int | **CO2 emissions** for this segment in grams | **Yes** |
| **[32]** | int | **Configuration type** — observed values 0, 1, 2. Not correlated with aircraft size (A320=0/1, B777=1/2, A321neo=2). Possibly route-type or equipment variant indicator | No |

### Amenity Flags (`segment[12]`)

12-element array of feature indicators. Empty `[]` for budget carriers with no amenities.

| Index | Meaning | Values | Notes |
|-------|---------|--------|-------|
| **[0]** | Reserved | null | — |
| **[1]** | **In-seat power & USB outlets** | `true` = yes, null = no | Most mainline carriers. **UI-validated**. Mutually exclusive with [5] |
| **[2]** | Reserved | null | — |
| **[3]** | **Unknown amenity** | `true` = yes, null = no | Regional jets (E175). May be "overhead bin" or basic amenity |
| **[4]** | Reserved | null | — |
| **[5]** | **Cabin-specific amenity** | `true` = yes, null = no | **Mutually exclusive with [1]**. Appears on BA B777 economy (no [1]), VS A350 economy (no [1]). When same aircraft is searched in business class, [1] appears instead. Exact meaning TBD |
| **[6]** | Reserved | null | — |
| **[7]** | Reserved | null | — |
| **[8]** | **Live TV** | `true` = yes, null = no | JetBlue A320. **UI-validated** |
| **[9]** | **On-demand video** (seatback IFE screens) | `true` = yes, null = no | Delta B767, VS, UA wide-bodies. **UI-validated** |
| **[10]** | **Stream media to your device** (wireless streaming) | `true` = yes, null = no | AA A321, E175 regional. **UI-validated** |
| **[11]** | **WiFi availability** | 2/3 = positive availability tiers, null = unknown | Preserve the raw tier. Do not promise free/included service from the tier alone |

**IMPORTANT: The amenity array is cabin-class specific**, not aircraft-level. The same BA B777 shows different amenities in economy vs business class:
- BA B777 **economy**: `[null, null, null, null, null, true, null, null, null, true, null, 3]` — [5]=true, no [1]
- BA B777 **business**: `[null, true, null, null, null, null, null, null, null, true, null, 3]` — [1]=true, no [5]

**UI-validated amenity mapping** (economy class, cross-referenced with Google Flights expanded flight details):

| Airline | Aircraft | Amenity Fields | UI Shows |
|---------|----------|----------------|----------|
| Frontier | A321neo | `[]` (empty) | No amenities listed |
| SkyWest/United Express | E175 | [3]=true, [10]=true, [11]=2 | Stream media + Free Wi-Fi |
| American (domestic) | A321 | [1]=true, [10]=true, [11]=2 | In-seat power, Stream media, Free Wi-Fi |
| Delta | B767 | [1]=true, [9]=true, [11]=2 | In-seat power, On-demand video, Free Wi-Fi |
| JetBlue | A320 | [1]=true, [8]=true, [11]=2 | In-seat power, Live TV, Free Wi-Fi |
| Alaska | B737 | [1]=true, [10]=true, [11]=3 | In-seat power, Stream media, Free Wi-Fi |
| United | B757 | [1]=true, [9]=true, [11]=3 | In-seat power, On-demand video, Free Wi-Fi |
| Virgin Atlantic | B787 | [1]=true, [9]=true, [11]=3 | In-seat power, On-demand video, Free Wi-Fi |
| AA (intl) | B777 | [1]=true, [9]=true, [11]=3 | In-seat power, On-demand video, Free Wi-Fi |
| BA (intl economy) | B777 | [5]=true, [9]=true, [11]=3 | On-demand video, Free Wi-Fi |
| VS (economy) | A350 | [5]=true, [9]=true, [11]=3 | On-demand video, Free Wi-Fi |

### Seat Type Indicator (`segment[13]`)

Numeric indicator for seat product quality. **UI-validated** — correlates directly with the legroom label shown in expanded flight details.

| Value | UI Label | Meaning | Observed On |
|-------|----------|---------|-------------|
| 1 | "Average legroom" | Standard economy | AA, DL, VS, BA, UA |
| 2 | "Below average legroom" | Basic/budget economy | Frontier (28 in) |
| 3 | "Above average legroom" | Extra legroom / premium economy | JetBlue (32 in, Even More Space branding) |
| 4 | — | Short-haul business (recliner) | Philippine Airlines A321 regional |
| 5 | — | Long-haul business (lie-flat) | UA Polaris, JAL Sky Suite, EVA, Air Canada |
| 6 | — | Premium business suite | ANA "The Room" (B777) |
| 9 | — | Business class (older/mixed product) | Philippine Airlines B777 long-haul |

**Note:** Seat pitch (`[14]`) and legroom (`[30]`) are null for business/first class (values 4+), since these products are characterized by lie-flat capability rather than pitch measurements.

---

## Layover Structure

Each layover in `flightData[13]` is an 8-element array:

| Index | Type | Meaning | Currently Used? |
|-------|------|---------|-----------------|
| **[0]** | int | **Duration** in minutes | **Yes** |
| **[1]** | string | **Layover airport IATA** (departing from) | **Yes** |
| **[2]** | string | **Layover airport IATA** (arriving at — usually same as [1]) | **Yes** |
| **[3]** | [int]/null | **Overnight layover flag** — `[1]` if layover spans overnight, null otherwise | No |
| **[4]** | string | **Airport full name** (departing) | **Yes** |
| **[5]** | string | **City name** (departing) | **Yes** |
| **[6]** | string | **Airport full name** (arriving) | **Yes** |
| **[7]** | string | **City name** (arriving) | **Yes** |

**Note:** Field `[3]` is `[1]` when the layover is extremely long (e.g., 1431 minutes / ~24 hours at MCO). This is the **overnight layover indicator**.

---

## Carbon Emissions (`flightData[22]`)

18-element array with comprehensive CO2 data:

| Index | Type | Meaning | Currently Used? |
|-------|------|---------|-----------------|
| **[0]** | null | Reserved | — |
| **[1]** | null | Reserved | — |
| **[2]** | int | **Emissions rating** — 1 = below average (green), 3 = above average (orange). Correlates with UI color coding | No |
| **[3]** | int | **Difference from typical** (percent) — negative = fewer emissions. E.g., -22 means 22% less than typical, +19 means 19% more | **Yes** |
| **[4]** | null | Reserved | — |
| **[5]** | bool | **Flag** — always `true` | No |
| **[6]** | bool | **Flag** — always `true` | No |
| **[7]** | int | **This flight's CO2** in grams (rounded to nearest 1000). Sum of per-segment `seg[31]` values ≈ this total | **Yes** |
| **[8]** | int | **Typical CO2 for this route** in grams | **Yes** |
| **[9]** | Array/null | **Flag** — `[true]` on some itineraries, null on others. Pattern unclear | No |
| **[10]** | int | **Median/reference CO2** in grams — slightly different from "typical" ([8]), possibly median vs mean | No |
| **[11]** | int | **Emissions tier** — matches [2] in most cases | No |
| **[12]** | bool | **Flag** — always `false` | No |
| **[13]** | null | Reserved | — |
| **[14]** | null | Reserved | — |
| **[15]** | int | **Emissions sub-tier** — observed values 1 and 2. Differs from [2]/[11]/[17] in some cases | No |
| **[16]** | null | Reserved | — |
| **[17]** | int | **Extended emissions score** — usually mirrors [2] but can diverge (e.g., [2]=3 while [17]=6 for high-emissions flights). May encode additional environmental factors like contrail warming potential | No |

**Emissions rating scale (field [2]):**

| Value | Meaning | UI Display |
|-------|---------|------------|
| 1 | Below average emissions | Green indicator, e.g., "-22% emissions" |
| 3 | Above average emissions | Orange indicator, e.g., "+53% emissions" |

**Contrail warming potential:** The expanded flight details in the UI show "Contrail warming potential: Low" as an additional environmental metric. The source field for this is not yet identified in the response structure — it may be derived from route/altitude data or stored in one of the reserved null fields within the carbon emissions array.

---

## Booking Token & Price (`itinerary[1]`)

```json
[[null, 321], "CjRIbUFNS2NB...base64..."]
```

| Index | Type | Meaning |
|-------|------|---------|
| **[0]** | [null, int] | **Display price** in USD |
| **[1]** | string | **Base64 booking token** — decodes to `ItinerarySummary` protobuf |

The base64 token contains:
- `flights` field — "options:N" format for booking option indexing
- `price.price` — price in cents (divide by 100)
- `price.currency` — "USD"

---

## Booking Options (`GetBookingResults`)

Per booking option (see `rpc.py` and `booking-options-proto-notes.md`):

### Raw Protobuf Fields

| Path | Meaning | Currently Used? |
|------|---------|-----------------|
| `option[7]` | **Price block** (auto-detected via `_looks_like_price_block`) | **Yes** |
| `option[7][0][1]` | Display price (USD integer) | **Yes** |
| `option[7][1]` | Base64 `ItinerarySummary` protobuf — `.flights` field contains `"options:N"` index | **Yes** |
| `option[19]` | **Context tokens** — JSON string (not list!) containing `[token0_b64, [token1_b64, ...]]` | **Yes** |
| `option[21]` | **Brand block** (auto-detected via `_looks_like_brand_block`) | **Yes** |
| `option[21][0][1]` | Brand code (internal, e.g., "DELTA MAIN CLASSIC") | **Yes** |
| `option[21][1]` | Attribute vector — raw list, normalized to first 32 scalar elements for diagnostics | **Yes** |
| `option[21][2]` | Basic economy flag (primary boolean) | **Yes** |
| `option[21][3]` | Brand label (user-facing display name) | **Yes** |
| `option[21][6][0][0]` | **Cabin class** — Seat enum: 1=economy, 2=premium-economy, 3=business, 4=first. Present on all observed options including codeshare/OTA with no brand text. Primary signal for `_cabin_bucket`. | **Yes** |
| `option[21][6][0][1]` | Amenity flags array (same format as `segment[12]`) | No |
| `option[21][6][0][2]` | Seat type indicator (similar to `segment[13]`) | No |
| `option[21][16]` | Basic economy flag (secondary boolean) | **Yes** |
| `option[24]` | Basic economy tail flag (boolean) | **Yes** |

### Basic Economy Detection (Triple Signal)

Three flags must ALL be true for flag-based basic detection:
1. `option[21][2] == true` (primary)
2. `option[21][16] == true` (secondary)
3. `option[24] == true` (tail)

Text fallback: `"BASIC"` in brand_code or brand_label (catches edge cases like "DELTA COMFORT BASIC").

Final: `is_basic = is_basic_by_flags OR is_basic_by_text`

### Context Token 0 (Display Price)

Protobuf structure (manual varint decoding):
- Top-level field 3 (wire type 2 = length-delimited message)
  - Nested field 1 (wire type 0 = varint) = **display_price_cents**

### Context Token 1 (Segment Identity)

Protobuf structure: top-level field 1 (wire type 2 = nested message), then string fields (all wire type 2):

| Field | Wire Type | Meaning | Format |
|-------|-----------|---------|--------|
| 1 | 2 (string) | Origin IATA code | "JFK" |
| 2 | 2 (string) | Departure datetime | **Local ISO** (e.g., "2026-03-20T06:00:00-04:00") |
| 3 | 2 (string) | Destination IATA code | "LAX" |
| 4 | 2 (string) | Arrival datetime | **Local ISO** (not UTC!) |
| 5 | 2 (string) | Carrier code | "AA" |
| 6 | 2 (string) | Flight number | "171" |
| 10 | 2 (string) | Aircraft code | "32B" |

### Parsed Booking Option Output Fields

Each parsed `BookingOption` dataclass has these fields (see `decoder.py`):

| Field | Source | Description |
|-------|--------|-------------|
| `price` | `option[7][0][1]` | Display price in USD |
| `brand_code` | `option[21][0][1]` | Internal brand identifier |
| `brand_label` | `option[21][3]` | User-facing fare name |
| `is_basic` | Derived | Final basic economy flag (flags OR text) |
| `fare_family` | Derived | Classification: `basic\|standard\|enhanced\|premium\|unknown` |
| `rebookability_signal` | Derived | `restricted\|standard_rebookable\|upgraded_rebookable\|unknown` |
| `_is_basic_by_flags` | Triple AND | Protobuf flag-based detection |
| `_is_basic_by_text` | Pattern match | Text-based detection ("BASIC" in brand strings) |
| `_option_index` | `ItinerarySummary.flights` | Index from "options:N" string |
| `_token_price_cents` | `ItinerarySummary.price` | Price from protobuf token (cents) |
| `_display_price_cents` | Context token 0 | Price from display context (cents) |
| `_price_delta_cents` | Derived | `_display_price_cents - _token_price_cents` |
| `_brand_attribute_vector` | `option[21][1]` | Normalized scalar slice (first 32 elements) |
| `_context_origin_iata` | Context token 1 | Origin from segment identity |
| `_context_destination_iata` | Context token 1 | Destination from segment identity |
| `_context_departure_local_iso` | Context token 1 | Departure in local ISO time |
| `_context_arrival_local_iso` | Context token 1 | Arrival in local ISO time |
| `_context_carrier_code` | Context token 1 | Marketing carrier code |
| `_context_flight_number` | Context token 1 | Flight number |
| `_context_aircraft_code` | Context token 1 | Aircraft type code |
| `_registry_version` | Constant | Parser version for drift detection |

### Fare Family Classification Logic

```
if is_basic → "basic"
if "FIRST"/"BUSINESS"/"PREMIUM" in brand → "premium"
if "DELTA MAIN CLASSIC" or "MAIN CABIN" → "standard"
if "ECONOMY" (standalone or with "FULLY REFUNDABLE") → "standard"
if "PLUS"/"SELECT"/"COMFORT" in brand → "enhanced"
else → "unknown"
```

### Rebookability Signal Derivation

```
basic/restricted → "restricted"
standard → "standard_rebookable"
enhanced/premium → "upgraded_rebookable"
unknown → "unknown"
```

---

## Airline Reference Data

### Baggage Policy URLs (`data[11]`)

```json
[
  ["AA", "American", "https://www.aa.com/i18n/travel-info/baggage/checked-baggage-policy.jsp"],
  ["DL", "Delta", "https://www.delta.com/content/.../checked.html"],
  ...
]
```

### Accessibility URLs (`data[26]` and `flightData[24]`)

```json
[
  ["AA", "American", "https://www.aa.com/i18n/travel-info/special-assistance/special-assistance.jsp"],
  ["DL", "Delta", "https://www.delta.com/us/en/accessible-travel-services/overview"],
  ...
]
```

---

## RPC Request Structure

### GetShoppingResults

Filters payload structure (see `rpc.py`):

```
filters[0]  = []                          # empty
filters[1]  = main filter block:
  [1][2]    = trip type (1=roundtrip, 2=one-way)
  [1][5]    = seat type (1=economy, 2=premium-economy, 3=business, 4=first)
  [1][6]    = passengers [adults, children, infants_lap, infants_seat]
  [1][7]    = price limit (null = no limit)
  [1][13]   = segments array (outbound + optional return)
  [1][17]   = 1 (constant)
filters[2]  = sort order (1=top, 2=cheapest, 3=dep_time, 4=arr_time, 5=duration)
filters[3]  = 0 (constant)
filters[4]  = 0 (constant)
filters[5]  = 2 (constant)
```

### Per-segment filter:

```
segment[0]  = [[[origin_iata, 0]]]        # MUST be 3 levels of nesting!
segment[1]  = [[[destination_iata, 0]]]
segment[2]  = [earliest_dep, latest_dep, earliest_arr, latest_arr]  # time restrictions
segment[3]  = max_stops (0=any, 1=nonstop, 2=one, 3=two)
segment[4]  = airline_codes_filter
segment[5]  = null
segment[6]  = "YYYY-MM-DD"               # travel date
segment[7]  = null                        # max duration
segment[8]  = selected_legs              # for return expansion
segment[9]  = null                        # layover airports
segment[10-12] = null
segment[13] = null                        # layover duration
segment[14] = 3                          # constant
```

### GetBookingResults

```
[[null, booking_token], filter_block, null, 0]
```

Selected legs must be at `filter_block[13][0][8]` — each leg: `[dep_airport, dep_date, arr_airport, null, airline_code, flight_number]`.

---

## Roundtrip Pricing — Critical Architecture

Roundtrip searches use a **two-pass RPC approach** with critical pricing implications:

### Pass 1: Initial Shopping Search

- Send `GetShoppingResults` with `trip_type=1` (roundtrip) and both outbound/return segments
- Response contains outbound itineraries at `data[2][0]` and `data[3][0]`
- **Prices in this response are roundtrip totals** (outbound + cheapest return combined)

### Pass 2: Return Expansion

For each promising outbound itinerary:
1. Build `selected_outbound_legs` from the outbound itinerary's segments
2. Call `GetShoppingResults` again with those legs at `segment[8]` of the outbound filter
3. Response now contains **return itineraries** paired with the selected outbound
4. **CRITICAL: The return itinerary's price IS the roundtrip total** — do NOT sum outbound + return prices

### Price Location in Roundtrip Responses

| Context | Price Location | What It Represents |
|---------|---------------|-------------------|
| One-way search | `itinerary[1][0][1]` | One-way price |
| Roundtrip pass 1 | `itinerary[1][0][1]` | **Roundtrip total** (outbound + cheapest return) |
| Roundtrip pass 2 (return) | `itinerary[1][0][1]` | **Roundtrip total** (selected outbound + this return) |
| Booking options (return) | `option[7][0][1]` | **Roundtrip total** for this fare class |

### Economy Roundtrip Pricing

For economy bookings, booking options can be used to get non-basic prices:

1. Get outbound's non-basic economy `GetBookingResults` option price
2. Get return's non-basic economy `GetBookingResults` option price
3. Use **return's booking price as the paired total** (it already includes the outbound)
4. If booking options unavailable, fall back to the RPC response's paired total

### Roundtrip Expansion Budget

- Max 10 outbound itineraries expanded (`ROUNDTRIP_OUTBOUND_EXPANSION_LIMIT`)
- Target 5 paired results (`ROUNDTRIP_TARGET_PAIRS`) — stop early once reached
- 90-second time budget (`ROUNDTRIP_TIME_BUDGET_SECONDS`)

### Multi-City / Multi-Segment

Not currently implemented. The RPC supports it via `filters[1][13]` having 3+ segments (each with its own origin/destination/date), and `filters[1][2]` would be set to 3 (multi-city). Response structure is expected to be similar but with additional segments. Requires exploration/validation if needed.

---

## What We're NOT Extracting (Opportunities)

### High Value — Should Extract

| Field | Location | Why It Matters |
|-------|----------|----------------|
| **Price history graph** | `data[5][10][0]` | 60-day price trends. **HTML/SSR only, not in RPC** |
| **Price level indicator** | `data[5][0]` | Google's own assessment of whether current price is low/typical/high. **HTML/SSR only** |
| **Typical/low/high prices** | `data[5][1-5]` | Route price benchmarks. **HTML/SSR only** |
| **Amenity flags** | `segment[12]` | WiFi, IFE, power availability. **Cabin-class specific** |
| **Seat type** | `segment[13]` | Distinguish basic economy (2) from standard (1) from premium (3) |
| **Quality tier** | `itinerary[4][4]` | Budget (3) vs standard (1) carrier classification |
| **Budget carrier flag** | `itinerary[3]` | Boolean flag for budget carriers (Frontier, Spirit) |

### Medium Value — Nice to Have

| Field | Location | Why It Matters |
|-------|----------|----------------|
| **Codeshare flights** | `segment[15]` | Already extracted; verify working correctly |
| **Airport coordinates** | `data[1][*][3]` | Could enable map visualizations |
| **Baggage policy URLs** | `data[11]` | Link users to checked bag policies |
| **Overnight layover flag** | `layover[3]` | Warn users about overnight connections |
| **Emissions rating** | `carbon[2]` | Color-coded eco rating (1=green, 3=orange) |
| **Median CO2** | `carbon[10]` | Additional emissions reference point |
| **Available airlines/alliances** | `data[7][1]` | For search filtering UI |
| **Duration range** | `data[7][3]` | For filter sliders |
| **Configuration type** | `segment[32]` | Values 0, 1, 2 — meaning unclear |

### Low Value — Reference Only

| Field | Location | Why It Matters |
|-------|----------|----------------|
| Itinerary ID | `flightData[17]` | Internal hash, limited use |
| Session metadata | `data[0]`, `data[14]` | Request tracking |
| Search tokens | `data[12]`, `data[18]` | Re-executing searches |
| KG IDs | `data[1][*][2][0]` | Knowledge Graph references |
| Airport images | `data[1][*][2][2]` | Thumbnail URLs |
| Accessibility URLs | `data[26]` | Special assistance links |
| Comprehensiveness lure | `data[30]` | "View all flights" prompt data |
| Extended booking token | `itinerary[8]` | Full segment identity protobuf (see above) |
| Cabin class indicator | `itinerary[9]` | `[[1]]` or `[[2]]`, redundant with segment data |
| Configuration type | `segment[32]` | Values 0, 1, 2 — pattern unclear |
| Aircraft config flag | `segment[18]` | `[true]` or `[null, true]` on some aircraft |

---

## Known Gotchas

1. **Airport nesting must be 3 levels** — `[[[code, 0]]]` not 4. Four levels silently returns zero results.
2. **ItinerarySummary b64 path is `[1]`** not `[1][1]` — past bug caused all prices = $0.
3. **Prices from ItinerarySummary are in cents** — divide by 100, use `round()`.
4. **`primp` content param must be bytes** — use `.encode()`, str silently fails.
5. **Departure time format varies** — sometimes `[hour]` (no minutes), sometimes `[hour, min]`. Always use `_safe_tuple` with defaults.
6. **Roundtrip booking price** — GetBookingResults for return leg returns the roundtrip total, not just the return leg. Don't sum outbound + return.
7. **Field indices may shift** — Google can change the response structure. Always use `_safe_get` with defaults.
8. **`segment[5]` is arrival airport name, not arrival code** — arrival code is `segment[6]`. The decoder already handles this correctly.
9. **Price insights (`data[5]`) only in HTML/SSR** — null from RPC endpoint. Also `data[12]` and `data[18]` search tokens.
10. **Best flights (`data[2]`) often null from RPC** — all results come back in `data[3]` (other flights) instead.
11. **Boolean vs integer types** — many flags are JSON booleans (`true`/`false`), not integers. Use `is True` checks, not `== 1`. Affected fields: `segment[19]` (overnight), `itinerary[3]` (budget flag), `itinerary[6]`/`[10]` (flags), carbon `[5]`/`[6]`/`[12]`.
12. **Amenity array is cabin-class specific** — same aircraft shows different amenities for economy vs business. Don't assume aircraft-level consistency.

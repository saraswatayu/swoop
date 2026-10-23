Captured against Google Flights on 2026-10-22 using JFK→LAX, departure
2026-12-03..04; roundtrip returns 2026-12-08..09 (or exact five-night stays).

`browser_filters.json` retains only relevant numeric slots from Chrome's actual
GetShoppingResults requests and public search TFS values. It contains no request
headers, cookies, session IDs, response tokens, or trip selectors. The TFS values
encode only route/date/filter settings and Google UI constants.

The six calendar fixtures retain date/price cells from live grid, picker and
graph responses. Every live itinerary-summary/selector token was removed and
replaced with a freshly serialized ItinerarySummary containing only the
currency (empty flights, zero price). RPC metadata and session IDs were removed.
No live booking token, selector, cookie, or session identifier is committed.

Observed wire positions (zero-based):

| Setting | Shopping filter block / segment | TFS protobuf |
|---|---|---|
| Maximum price | filter[7] = [null, ceiling] | Info.max_price = 12 |
| Bags | filter[10] = [carry_on, checked] | Info.baggage = 13; counts = 2, 3 |
| Duration | segment[7] = [minutes] | FlightData.max_duration = 12 |
| Allowed connecting airports | segment[9] = [IATA, ...] | FlightData field 15 |
| Excluded connecting airports | segment[10] = [IATA, ...] | FlightData field 16 |
| Minimum layover | segment[11] = minutes | FlightData field 17 |
| Maximum layover | segment[12] = minutes | FlightData field 18 |

These captures corrected the initially proposed baggage slot 8 and layover
slot 13. The graph control calls GetCalendarGraph. A live GetPriceGraph probe
returned HTTP 404; no wrapper for that unsupported method is provided.

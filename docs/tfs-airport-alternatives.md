# Native airport alternatives in Google Flights TFS

Google's TFS search parameter represents an endpoint's airport alternatives
with repeated `Airport` messages at `FlightData` fields **13** (origins) and
**14** (destinations). Modeling these fields as singular messages silently
discards all but the last airport when reading a Google-produced TFS URL.
The corrected schema preserves every airport. Singleton serialization stays
byte-identical, and the public `SearchLeg` input and output fields retain
their existing singular-airport semantics.

The private `_apply_tfs_leg()` encoder can write a single leg with explicit
origin/destination lists. Existing `SearchLeg.apply_to()` uses that helper
with its singular airports. Callers using low-level protobuf messages now
access `leg.from_flight` and `leg.to_flight` as repeated message containers;
the generated protobuf field numbers and singleton wire format are unchanged.
This is a precise schema/builder correction, not an expansion of high-level
`search()` or `search_legs()` input semantics or a new HTTP fallback policy.

## Dated wire acceptance

On 2026-10-10 Panama time (2026-10-11 UTC), Google's own airport picker was
used to add EWR to JFK and BUR to LAX while searching one-way, nonstop flights
on **2026-10-26**. The resulting Google-generated TFS URL contained repeated
origin and destination messages, each with field 1 set to 1 and field 2 set
to its IATA code. The sanitized actual parameter is archived as
`native_ui_tfs` in `tests/fixtures/tfs/shopping_page_airport_alternatives.json`.

A plain HTTP GET to Google's flight search page with this TFS returned 42
rows. Page route metadata echoed JFK/EWR and LAX/BUR. A second GET using the
existing minimal Swoop airport messages (field 2 only) with repeated fields
13/14 also returned 42 rows and the same airport metadata. Its exact accepted
parameter is archived as `accepted_minimal_tfs`. Both JFK and EWR appeared
as actual flight departure airports. No BUR nonstop row appeared on that
date; preserving a requested alternative does not guarantee returned
coverage for it.

The fixture retains only request TFS, airport/date metadata, and representative
flight facts. Cookies, session IDs, booking summaries/tokens, selectors, and
opaque context values were removed. These read-only probes searched flight
metadata; no booking or price selection was performed.

The regressions decode both actual accepted wire forms, preserve alternative
order, and compare 16 existing singleton requests against bytes captured from
unmodified upstream `59fbafc`: all cabins, one-way/roundtrip, mixed passenger
types, airline/stops filters, and basic-economy exclusion.

## Related upstream work

[PR #17](https://github.com/saraswatayu/swoop/pull/17) and
[PR #32](https://github.com/saraswatayu/swoop/pull/32) describe native RPC
airport lists. PR #32 explicitly blocks alternatives on its protobuf path;
the Google-generated TFS capture establishes the independent protobuf shape.
[PR #35](https://github.com/saraswatayu/swoop/pull/35) adds a narrow raw-RPC
airport-list primitive. This schema correction can support a page builder
for those explicit alternatives, but integrating a compact-status-13 HTML
fallback requires its own transport and coverage policy. This patch does
not add that runtime retry machinery.

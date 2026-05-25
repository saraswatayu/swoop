<!--
Thanks for sending a PR! Keep this terse — bullet points and short sentences
are fine. Delete sections that don't apply (e.g. "Public API" for an internal
refactor).
-->

## What changed

<!-- One or two sentences. What does this PR do, end-to-end? -->

## Why

<!-- The motivation. Linked issue if there is one (e.g. "Closes #42"). -->

## Test plan

<!-- How did you verify it? Paste the relevant pytest command or output.
For live/RPC changes, note whether you ran `make test-live` or only the
offline suite. -->

```bash
make check
```

## Public API impact

<!-- Did you add, rename, or remove anything on a public dataclass listed
in CLAUDE.md's "Frozen API Surface" section (SearchResult, RawSearchResult,
TripOption, TripLeg, PriceResult, BookingOption, Itinerary, Segment,
Layover, Codeshare, CarbonEmissions)?

If yes:
- [ ] Updated `tests/test_api_surface.py` to reflect the new field set
- [ ] Noted the change under `## [Unreleased]` in CHANGELOG.md

If no, delete this section. -->

## Checklist

- [ ] `make check` passes (typecheck + offline tests)
- [ ] Added or updated tests covering the change
- [ ] CHANGELOG.md updated if user-visible
- [ ] No secrets, proxy credentials, or recorded auth tokens in the diff

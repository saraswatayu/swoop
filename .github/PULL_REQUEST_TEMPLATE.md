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

## Public API impact

_Only fill this in if you touched a frozen dataclass (see CLAUDE.md's
Frozen API Surface). If this PR doesn't touch public API, **delete this
whole section** so the checkboxes don't show as unchecked work._

- [ ] Updated `tests/test_api_surface.py`
- [ ] Added an entry under `## [Unreleased]` in `CHANGELOG.md`

## Checklist

- [ ] `make check` passes (typecheck + offline tests)
- [ ] Added or updated tests covering the change
- [ ] CHANGELOG.md updated if user-visible
- [ ] No secrets, proxy credentials, or recorded auth tokens in the diff

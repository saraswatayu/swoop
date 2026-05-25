# Contributing to swoop

Thanks for considering a contribution. swoop is a small library with a
deliberately tight surface — most contributions land cleanly if they
match the existing voice (terse, typed, zero unnecessary dependencies).

## Dev setup

```bash
git clone https://github.com/saraswatayu/swoop.git
cd swoop
make install-dev   # editable install with [validation,cli] + pytest, hypothesis, pytest-benchmark
```

If you don't have `make`, the equivalent is:

```bash
pip install -e ".[validation,cli]"
pip install pytest hypothesis pytest-benchmark
```

Run `make` (no target) to see every recipe.

## Running tests

```bash
make test        # offline suite (skips @pytest.mark.live tests) — what CI runs
make test-live   # live integration tests (hits real Google Flights)
make test-all    # both
```

The offline suite is the one to run before opening a PR. `make test-live`
needs network and may be flaky if Google changes the RPC shape; CI does
not currently gate on it.

## Type checking

```bash
make typecheck   # pyright
```

`pyright` is the canonical type checker for swoop. Run it before opening
a PR; CI does not currently gate on it but reviewers will.

## Pre-PR check

```bash
make check       # typecheck + offline tests, the one-liner gate
```

## Project structure

```
swoop/
├── __init__.py       # search(), search_legs(), check_price(), price_selector(), price_legs(), public API
├── __main__.py       # python -m swoop entry point
├── models.py         # Public trip-level dataclasses (SearchResult, TripOption, TripLeg, PriceResult, ...)
├── rpc.py            # HTTP/RPC client, search_raw(), get_booking_results()
├── _selection.py     # Staged multi-leg expansion, selector encode/decode, selector-based trip pricing
├── builders.py       # Protobuf payload builders (TFSData, SearchLeg)
├── decoder.py        # Response decoder, low-level dataclass definitions
├── _booking.py       # Booking option parsing (GetBookingResults)
├── _validate.py      # Input validation
├── exceptions.py     # Exception hierarchy
├── flights_pb2.py    # Generated protobuf module (excluded from pyright)
├── flights_pb2.pyi   # Hand-written type stub for flights_pb2
└── cli/
    ├── __init__.py   # Click group, main() entry point
    ├── commands.py   # search_cmd, price_cmd
    ├── formatters.py # table / JSON / CSV / brief renderers
    └── utils.py      # Custom Click types, --verbose wiring, --quiet auto-detect
tests/
├── test_api.py               # Integration-style tests
├── test_api_surface.py       # Frozen API surface tests + dir() leakage checks
├── test_cli.py               # CliRunner-driven CLI tests
├── test_decoder.py           # Decoder unit tests
├── test_rpc.py               # RPC client tests
├── test_selection.py         # Staged selection / selector pricing tests
├── test_validation.py        # Validation tests
├── factories.py              # Test factories
└── fixtures/                 # Recorded RPC responses + corpora
scripts/
├── _booking_helper.py        # Shared GetBookingResults helper for the diagnostic scripts
└── ...                       # record_*_corpus / sweep / validate / dump utilities
examples/
├── price_drop_watcher.py     # Watch a flight for price drops on a schedule
└── multi_city_finder.py      # Multi-city / open-jaw search demo
```

## Guidelines

- Run `make check` before submitting a PR
- If you add or rename public API, update `tests/test_api_surface.py` frozen field sets
- Keep `swoop/` zero-dependency beyond `primp` and `protobuf` (extras like `airportsdata`, `click`, `rich`, `babel` go behind `[project.optional-dependencies]`)
- Mark tests that hit real Google Flights with `@pytest.mark.live`
- Match the existing voice in docstrings and CHANGELOG entries: terse, concrete, user-visible

## Commit format

```
<type>: <description>
```

Types: `feat`, `fix`, `refactor`, `docs`, `chore`, `ci`, `test`.

Examples:

```
feat: add cabin filter to swoop.search
fix: handle missing booking option price in decoder
refactor: move _SearchFormatKwargs below imports
docs: clarify price_drop_watcher cache scope
```

One commit per logical unit — small, focused commits review faster than
one large omnibus commit.

## PR process

1. Fork and create a feature branch
2. Make your changes (one logical unit per commit)
3. Run `make check`
4. Open a PR — the template will prompt for the slots reviewers care about
5. If your change touches frozen public API, update `tests/test_api_surface.py` and add a `## [Unreleased]` line to `CHANGELOG.md`

## Release process

Maintainer-only. Releases are tagged and CI publishes to PyPI automatically.

1. Bump `__version__` in `swoop/__init__.py`
2. Move `## [Unreleased]` entries in `CHANGELOG.md` under a new `## [X.Y.Z] - YYYY-MM-DD` heading
3. Commit: `chore: release vX.Y.Z`
4. Tag: `git tag swoop-vX.Y.Z && git push origin swoop-vX.Y.Z`
5. CI builds, publishes to PyPI, and creates a GitHub Release with the
   changelog section as release notes

See `.github/workflows/ci.yml` for the exact publish job.

## Questions

- Found a bug? File a [bug report](https://github.com/saraswatayu/swoop/issues/new?template=bug_report.md).
- Want a feature? Open a [feature request](https://github.com/saraswatayu/swoop/issues/new?template=feature_request.md).
- Wondering how it works? The [RPC walkthrough](https://ayushsaraswat.com/writing/reverse-engineering-google-flights) covers the design.
- Security concern? See [SECURITY.md](SECURITY.md) for the disclosure policy.

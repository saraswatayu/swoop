# Contributing to Swoop

## Dev setup

```bash
git clone https://github.com/saraswatayu/swoop.git
cd swoop
pip install -e ".[validation]"
pip install pytest
```

## Running tests

```bash
# All tests (excluding live API tests)
pytest tests/ -v -m "not live"

# Live integration tests (hits real Google Flights)
pytest tests/ -v -m live

# Full suite
pytest tests/ -v
```

## Type checking

```bash
pyright
```

`pyright` is the canonical type checker for swoop. Run it before opening
a PR; CI does not currently gate on it but reviewers will.

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

- Run `pytest tests/ -v -m "not live"` before submitting a PR
- If you add or rename public API, update `test_api_surface.py` frozen field sets
- Keep `swoop/` zero-dependency beyond `primp` and `protobuf`
- Mark tests that hit real Google Flights with `@pytest.mark.live`

## PR process

1. Fork and create a feature branch
2. Make your changes
3. Run the test suite
4. Open a PR with a clear description of what changed and why

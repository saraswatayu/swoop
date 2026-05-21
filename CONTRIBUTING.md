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
├── __init__.py       # search(), check_price(), public API
├── rpc.py            # HTTP/RPC client, search_raw(), get_booking_results()
├── builders.py       # Protobuf payload builders (TFSData, SearchLeg)
├── decoder.py        # Response decoder, dataclass definitions
├── _booking.py       # Booking option parsing (GetBookingResults)
├── _validate.py      # Input validation
├── exceptions.py     # Exception hierarchy
└── flights_pb2.py    # Generated protobuf module
tests/
├── test_api.py               # Integration-style tests
├── test_api_surface.py       # Frozen API surface tests
├── test_decoder.py           # Decoder unit tests
├── test_rpc.py               # RPC client tests
├── test_validation.py        # Validation tests
└── fixtures/                 # Test data
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

"""Property-based tests using Hypothesis.

These tests fuzz swoop's decoder and validator functions with random input
to ensure they never crash, regardless of what data Google returns.
"""

from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from swoop.decoder import (
    _decode_flight,
    _decode_itinerary,
    _decode_layover,
    _safe_get,
    Flight,
    Itinerary,
    Layover,
    decode_result,
    SearchResult,
)
from swoop._booking import _classify_cabin_bucket
from swoop._selection import _eligible_booking_options
from swoop._validate import (
    validate_cabin,
    validate_iata_code,
    validate_date,
    validate_adults,
)


# --- Strategy helpers ---

# Generates nested lists up to depth 5, with mixed types at leaves
nested_lists = st.recursive(
    st.one_of(
        st.none(),
        st.integers(-1000, 10000),
        st.text(max_size=20),
        st.floats(allow_nan=False),
        st.booleans(),
    ),
    lambda children: st.lists(children, max_size=10),
    max_leaves=50,
)

# Generates arbitrary paths for _safe_get
paths = st.lists(st.integers(0, 20), max_size=5)
missing_paths = st.lists(st.integers(50, 100), min_size=1, max_size=5)


def _reference_safe_get(data, path, default=None):
    value = data
    for index in path:
        if not isinstance(value, list) or index >= len(value):
            return default
        value = value[index]
    return value


class TestSafeGetProperty:
    """_safe_get must never raise, regardless of input."""

    @given(data=nested_lists, path=paths)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_never_raises(self, data, path):
        assert _safe_get(data, path) == _reference_safe_get(data, path)

    @given(data=nested_lists, path=missing_paths, default=st.text())
    @settings(max_examples=100)
    def test_default_returned_on_miss(self, data, path, default):
        assert _safe_get(data, path, default) == default


class TestDecodeFlightProperty:
    """_decode_flight must never crash."""

    @given(data=nested_lists)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_never_crashes(self, data):
        result = _decode_flight(data)
        if result is not None:
            assert isinstance(result, Flight)
            assert isinstance(result.codeshares, list)
            assert len(result.departure_date) == 3
            assert len(result.arrival_date) == 3
            assert len(result.departure_time) == 2
            assert len(result.arrival_time) == 2
            assert result.travel_time >= 0


class TestDecodeItineraryProperty:
    """_decode_itinerary must never crash."""

    @given(data=nested_lists)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_never_crashes(self, data):
        result = _decode_itinerary(data)
        if result is not None:
            assert isinstance(result, Itinerary)
            assert isinstance(result.flights, list)
            assert isinstance(result.layovers, list)
            assert result.travel_time >= 0
            assert result.price is None or isinstance(result.price, int)


class TestDecodeLayoverProperty:
    """_decode_layover must never crash."""

    @given(data=st.lists(
        st.one_of(st.none(), st.integers(), st.text(max_size=10), st.lists(st.integers(), max_size=3)),
        min_size=0,
        max_size=10,
    ))
    @settings(max_examples=200)
    def test_never_crashes(self, data):
        result = _decode_layover(data)
        if result is not None:
            assert isinstance(result, Layover)
            assert result.minutes >= 0


class TestDecodeResultProperty:
    """decode_result must always return a SearchResult."""

    @given(data=nested_lists)
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_always_returns_search_result(self, data):
        result = decode_result(data)
        assert isinstance(result, SearchResult)
        assert isinstance(result.best, list)
        assert isinstance(result.other, list)
        assert all(isinstance(itinerary, Itinerary) for itinerary in result.best + result.other)


class TestValidatorProperties:
    """Validators must either raise ValueError or succeed — never crash with other errors."""

    @given(value=st.text(max_size=10))
    @settings(max_examples=100)
    def test_validate_cabin_raises_or_passes(self, value):
        try:
            validate_cabin(value)
        except ValueError:
            pass  # Expected for invalid input
        # No other exception type should be raised

    @given(value=st.text(max_size=10))
    @settings(max_examples=100)
    def test_validate_iata_raises_or_passes(self, value):
        try:
            validate_iata_code(value, "test_field")
        except ValueError:
            pass

    @given(value=st.text(max_size=20))
    @settings(max_examples=100)
    def test_validate_date_raises_or_passes(self, value):
        try:
            validate_date(value, "test_field")
        except ValueError:
            pass

    @given(value=st.integers(-10, 100))
    @settings(max_examples=50)
    def test_validate_adults_raises_or_passes(self, value):
        try:
            validate_adults(value)
        except ValueError:
            pass


class TestCabinClassifierProperties:
    """Property-based tests for cabin classification pipeline."""

    @given(text=st.text(max_size=50))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_classify_cabin_bucket_never_crashes(self, text):
        result = _classify_cabin_bucket(text, text)
        assert result in ("economy", "premium-economy", "business", "first", "unknown")

    @given(brand_label=st.sampled_from(["Main", "Blue", "Blue Extra", "Even More", "Saver", "Value"]))
    @settings(max_examples=50)
    def test_known_economy_brands_never_leak_to_business(self, brand_label):
        from swoop.decoder import BookingOption
        code = brand_label.upper()
        bucket = _classify_cabin_bucket(code, brand_label)
        option = BookingOption(
            price=300, brand_label=brand_label, brand_code=code, _cabin_bucket=bucket,
        )
        result = _eligible_booking_options([option], True, cabin="business")
        assert result == []

"""Tests for swoop._regions."""
from swoop._regions import Region, region_for_country, region_for_iata


class TestRegionEnum:
    def test_string_values(self):
        assert Region.EUROPE == "europe"
        assert Region.NORTH_AMERICA == "north-america"

    def test_distinct_values(self):
        values = {r.value for r in Region}
        assert len(values) == 7


class TestRegionForCountry:
    def test_us_is_north_america(self):
        assert region_for_country("US") == Region.NORTH_AMERICA

    def test_gb_is_europe(self):
        assert region_for_country("GB") == Region.EUROPE

    def test_jp_is_asia_pacific(self):
        assert region_for_country("JP") == Region.ASIA_PACIFIC

    def test_zw_is_africa(self):
        assert region_for_country("ZW") == Region.AFRICA

    def test_ae_is_middle_east(self):
        assert region_for_country("AE") == Region.MIDDLE_EAST

    def test_jm_is_caribbean(self):
        assert region_for_country("JM") == Region.CARIBBEAN

    def test_br_is_latin_america(self):
        assert region_for_country("BR") == Region.LATIN_AMERICA

    def test_lowercase_input(self):
        assert region_for_country("us") == Region.NORTH_AMERICA

    def test_unknown_country(self):
        assert region_for_country("ZZ") is None

    def test_none(self):
        assert region_for_country(None) is None

    def test_empty(self):
        assert region_for_country("") is None

    def test_tr_is_europe(self):
        # Edge case: Turkey assigned to Europe per IATA convention
        assert region_for_country("TR") == Region.EUROPE

    def test_ru_is_europe(self):
        assert region_for_country("RU") == Region.EUROPE

    def test_bm_is_caribbean(self):
        # Edge case: Bermuda treated as Caribbean for flight-deals purposes
        assert region_for_country("BM") == Region.CARIBBEAN


class TestRegionForIata:
    def test_jfk_is_north_america(self):
        # Requires airportsdata
        try:
            import airportsdata  # noqa
        except ImportError:
            import pytest
            pytest.skip("airportsdata not installed")
        assert region_for_iata("JFK") == Region.NORTH_AMERICA

    def test_lhr_is_europe(self):
        try:
            import airportsdata  # noqa
        except ImportError:
            import pytest
            pytest.skip("airportsdata not installed")
        assert region_for_iata("LHR") == Region.EUROPE

    def test_nrt_is_asia_pacific(self):
        try:
            import airportsdata  # noqa
        except ImportError:
            import pytest
            pytest.skip("airportsdata not installed")
        assert region_for_iata("NRT") == Region.ASIA_PACIFIC

    def test_unknown_iata(self):
        try:
            import airportsdata  # noqa
        except ImportError:
            import pytest
            pytest.skip("airportsdata not installed")
        assert region_for_iata("ZZZ") is None

    def test_none(self):
        assert region_for_iata(None) is None

    def test_empty(self):
        assert region_for_iata("") is None

    def test_lowercase_input(self):
        try:
            import airportsdata  # noqa
        except ImportError:
            import pytest
            pytest.skip("airportsdata not installed")
        assert region_for_iata("jfk") == Region.NORTH_AMERICA

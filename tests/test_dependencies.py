"""Regression tests for the validation extra's published dependency metadata."""

from importlib.metadata import requires

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version
import pytest


# PyPI releases spanning the version-format change and the Python 3.11 floor.
AIRPORTSDATA_RELEASES = {
    "20260315": SpecifierSet(">=3.10"),
    "2026.6.24": SpecifierSet(">=3.10"),
    "20260905": SpecifierSet(">=3.11"),
}


def _airportsdata_requirements(python_version: str, extra: str) -> list[Requirement]:
    environment = {
        "python_version": python_version,
        "python_full_version": f"{python_version}.0",
        "extra": extra,
    }
    metadata = requires("swoop-flights")
    assert metadata is not None
    requirements = [Requirement(value) for value in metadata]
    return [
        requirement
        for requirement in requirements
        if requirement.name == "airportsdata"
        and (requirement.marker is None or requirement.marker.evaluate(environment))
    ]


@pytest.mark.parametrize(
    ("python_version", "expected"),
    [("3.10", "2026.6.24"), ("3.11", "20260905"), ("3.13", "20260905")],
)
def test_validation_extra_selects_newest_compatible_airport_data(python_version, expected):
    requirements = _airportsdata_requirements(python_version, "validation")
    assert len(requirements) == 1
    candidates = [
        Version(version)
        for version, supported_python in AIRPORTSDATA_RELEASES.items()
        if python_version in supported_python
        and all(version in requirement.specifier for requirement in requirements)
    ]
    # An unqualified >=1.3.0 incorrectly selects the older 20260315 on 3.10:
    # its integer version sorts above the chronologically newer 2026.6.24.
    assert candidates
    assert str(max(candidates)) == expected


@pytest.mark.parametrize("python_version", ["3.10", "3.11", "3.13"])
@pytest.mark.parametrize("extra", ["", "cli"])
def test_airport_data_remains_optional(python_version, extra):
    assert _airportsdata_requirements(python_version, extra) == []

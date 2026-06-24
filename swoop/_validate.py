"""Input validation for swoop search parameters."""

import logging
import re
from datetime import date as _date
from typing import Optional, Tuple

from .builders import CabinClass

logger = logging.getLogger(__name__)

_IATA_RE = re.compile(r"^[A-Z]{3}$")
_FLIGHT_NUMBER_RE = re.compile(
    r"^([A-Z]{2}|[A-Z]\d|\d[A-Z])\s?(\d{1,4})([A-Z])?$", re.IGNORECASE
)
_DIGITS_ONLY_RE = re.compile(r"^\d{1,4}$")
_VALID_CABINS = ("economy", "premium-economy", "business", "first")

# Cache airportsdata lookup if installed (optional dependency)
_iata_db: Optional[dict] = None
_iata_db_loaded = False


def _get_iata_db() -> Optional[dict]:
    """Load IATA airport database if airportsdata is installed."""
    global _iata_db, _iata_db_loaded
    if _iata_db_loaded:
        return _iata_db
    _iata_db_loaded = True
    try:
        import airportsdata

        _iata_db = airportsdata.load("IATA")
        logger.debug("Loaded airportsdata with %d IATA codes", len(_iata_db))
    except ImportError:
        _iata_db = None
    return _iata_db


def validate_iata_code(code: str, field_name: str) -> None:
    """Validate an IATA airport code.

    Checks format (3 uppercase letters) and optionally verifies against
    airportsdata if installed.

    Raises:
        ValueError: If the code is not a valid IATA code.
    """
    if not isinstance(code, str) or not _IATA_RE.match(code):
        raise ValueError(
            f"{field_name} must be a 3-letter uppercase IATA code, got {code!r}"
        )
    db = _get_iata_db()
    if db is not None and code not in db:
        raise ValueError(
            f"{field_name} {code!r} is not a known IATA airport code"
        )


def validate_iata_codes(codes: str | list[str], field_name: str) -> None:
    """Validate a single IATA code or a non-empty list of IATA codes.

    Raises:
        ValueError: If any code is invalid or the list is empty.
    """
    if isinstance(codes, str):
        validate_iata_code(codes, field_name)
        return
    if not isinstance(codes, list) or len(codes) == 0:
        raise ValueError(f"{field_name} must be a non-empty IATA code or list of codes")
    for i, code in enumerate(codes):
        validate_iata_code(code, f"{field_name}[{i}]")


def validate_date(date_str: str, field_name: str) -> None:
    """Validate a date string in YYYY-MM-DD format.

    Raises:
        ValueError: If the string is not a valid date.
    """
    if not isinstance(date_str, str):
        raise ValueError(f"{field_name} must be a date string, got {type(date_str).__name__}")
    try:
        _date.fromisoformat(date_str)
    except ValueError:
        raise ValueError(
            f"{field_name} must be a valid YYYY-MM-DD date, got {date_str!r}"
        )


def validate_adults(adults: int) -> None:
    """Validate adult passenger count.

    Raises:
        ValueError: If adults < 1.
    """
    if not isinstance(adults, int) or adults < 1:
        raise ValueError(f"adults must be >= 1, got {adults!r}")


def validate_cabin(cabin: CabinClass) -> None:
    """Validate cabin class.

    Raises:
        ValueError: If cabin is not a valid cabin class.
    """
    if cabin not in _VALID_CABINS:
        raise ValueError(
            f"Invalid cabin {cabin!r}. Must be one of: {', '.join(_VALID_CABINS)}"
        )


def validate_time_range(hour: Optional[int], field_name: str, min_val: int, max_val: int) -> None:
    """Validate a time-range hour value.

    Raises:
        ValueError: If hour is outside [min_val, max_val].
    """
    if hour is None:
        return
    if not isinstance(hour, int) or hour < min_val or hour > max_val:
        raise ValueError(
            f"{field_name} must be between {min_val} and {max_val}, got {hour!r}"
        )


def parse_flight_number(value: str) -> Tuple[Optional[str], str]:
    """Parse a flight number string into (carrier, number).

    Accepts formats like ``"DL 171"``, ``"DL171"``, ``"9W302"``, or just ``"171"``.
    Carrier codes follow IATA patterns: two letters (DL), letter+digit (G4),
    or digit+letter (9W).

    Returns:
        A tuple of ``(carrier, number)`` where carrier is ``None`` for
        digit-only input. Leading zeros are stripped from the number.

    Raises:
        ValueError: If the input cannot be parsed as a flight number.
    """
    if not isinstance(value, str):
        raise ValueError(f"flight_number must be a string, got {type(value).__name__}")
    value = value.strip()
    if not value:
        raise ValueError("flight_number must not be empty")

    # Digits-only → no carrier
    if _DIGITS_ONLY_RE.match(value):
        return (None, value.lstrip("0") or "0")

    m = _FLIGHT_NUMBER_RE.match(value)
    if not m:
        raise ValueError(f"Cannot parse flight number {value!r}")

    carrier = m.group(1).upper()
    number = m.group(2).lstrip("0") or "0"
    return (carrier, number)


def validate_search_params(
    origin: str | list[str],
    destination: str | list[str],
    date: str,
    *,
    return_date: Optional[str] = None,
    cabin: CabinClass = "economy",
    adults: int = 1,
    earliest_departure: Optional[int] = None,
    latest_departure: Optional[int] = None,
    earliest_arrival: Optional[int] = None,
    latest_arrival: Optional[int] = None,
    return_earliest_departure: Optional[int] = None,
    return_latest_departure: Optional[int] = None,
) -> None:
    """Validate all search parameters.

    Called from ``search()`` only (not ``search_raw()``).

    Raises:
        ValueError: If any parameter is invalid.
    """
    logger.debug("Validating search params: %s -> %s on %s", origin, destination, date)
    validate_iata_codes(origin, "origin")
    validate_iata_codes(destination, "destination")
    validate_date(date, "date")
    if return_date is not None:
        validate_date(return_date, "return_date")
    validate_cabin(cabin)
    validate_adults(adults)
    validate_time_range(earliest_departure, "earliest_departure", 0, 23)
    validate_time_range(latest_departure, "latest_departure", 1, 24)
    validate_time_range(earliest_arrival, "earliest_arrival", 0, 23)
    validate_time_range(latest_arrival, "latest_arrival", 1, 24)
    validate_time_range(return_earliest_departure, "return_earliest_departure", 0, 23)
    validate_time_range(return_latest_departure, "return_latest_departure", 1, 24)

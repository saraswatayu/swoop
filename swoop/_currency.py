"""Shared Google Flights locale / display-currency header."""

import json
from typing import Optional

_COUNTRY_CURRENCY: dict[str, str] = {
    "US": "USD", "GB": "GBP", "CA": "CAD", "AU": "AUD", "NZ": "NZD",
    "JP": "JPY", "KR": "KRW", "CN": "CNY", "IN": "INR", "SG": "SGD",
    "HK": "HKD", "TW": "TWD", "TH": "THB", "MY": "MYR", "PH": "PHP",
    "ID": "IDR", "VN": "VND", "MX": "MXN", "BR": "BRL", "AR": "ARS",
    "CL": "CLP", "CO": "COP", "PE": "PEN", "ZA": "ZAR", "AE": "AED",
    "SA": "SAR", "IL": "ILS", "TR": "TRY", "RU": "RUB", "UA": "UAH",
    "PL": "PLN", "CZ": "CZK", "HU": "HUF", "RO": "RON", "SE": "SEK",
    "NO": "NOK", "DK": "DKK", "CH": "CHF", "EG": "EGP", "NG": "NGN",
    "KE": "KES", "PA": "USD",
}

# EUR countries
for _cc in ("DE", "FR", "IT", "ES", "NL", "BE", "AT", "PT", "IE", "FI",
            "GR", "LT", "LV", "EE", "SK", "SI", "LU", "MT", "CY", "HR"):
    _COUNTRY_CURRENCY[_cc] = "EUR"


def _currency_header(country: Optional[str], currency: Optional[str] = None) -> str:
    """Build the x-goog-ext-259736195-jspb header value."""
    cc = country.upper() if country else (None if currency else "US")
    curr = currency.upper() if currency else _COUNTRY_CURRENCY.get(cc or "US", "USD")
    return json.dumps(["en", cc, curr, 1, None, [300], None, None, 7, []])


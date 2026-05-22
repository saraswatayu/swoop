"""Region classification for IATA airport codes.

Maps IATA codes → ISO 3166-1 alpha-2 country codes (via the optional
``airportsdata`` dependency) → coarse aviation-style ``Region`` buckets used
by the ``deals()`` endpoint to filter results.

The country-to-region table is a static dict covering ~200 commonly-traveled
countries. Edge cases (where the political/geographic answer differs from
the aviation-marketing answer) are noted inline.
"""

from enum import Enum
from typing import Optional


class Region(str, Enum):
    NORTH_AMERICA = "north-america"
    CARIBBEAN = "caribbean"
    LATIN_AMERICA = "latin-america"
    EUROPE = "europe"
    AFRICA = "africa"
    MIDDLE_EAST = "middle-east"
    ASIA_PACIFIC = "asia-pacific"


# ISO 3166-1 alpha-2 country code → Region. Static table, covers
# ~200 commonly-traveled countries. Edge cases documented inline.
_COUNTRY_TO_REGION: dict[str, Region] = {
    # --- NORTH_AMERICA ---
    "US": Region.NORTH_AMERICA,
    "CA": Region.NORTH_AMERICA,
    "MX": Region.NORTH_AMERICA,
    "GL": Region.NORTH_AMERICA,  # Greenland — NA per typical aviation grouping
    "PM": Region.NORTH_AMERICA,  # Saint Pierre and Miquelon
    # --- CARIBBEAN ---
    "AG": Region.CARIBBEAN,  # Antigua
    "AI": Region.CARIBBEAN,  # Anguilla
    "AW": Region.CARIBBEAN,  # Aruba
    "BB": Region.CARIBBEAN,  # Barbados
    "BL": Region.CARIBBEAN,  # Saint Barthelemy
    "BM": Region.CARIBBEAN,  # Bermuda — debated, treated as Caribbean for flight-deals purposes
    "BQ": Region.CARIBBEAN,  # Caribbean Netherlands
    "BS": Region.CARIBBEAN,  # Bahamas
    "CU": Region.CARIBBEAN,  # Cuba
    "CW": Region.CARIBBEAN,  # Curacao
    "DM": Region.CARIBBEAN,  # Dominica
    "DO": Region.CARIBBEAN,  # Dominican Republic
    "GD": Region.CARIBBEAN,  # Grenada
    "GP": Region.CARIBBEAN,  # Guadeloupe
    "HT": Region.CARIBBEAN,  # Haiti
    "JM": Region.CARIBBEAN,  # Jamaica
    "KN": Region.CARIBBEAN,  # Saint Kitts and Nevis
    "KY": Region.CARIBBEAN,  # Cayman Islands
    "LC": Region.CARIBBEAN,  # Saint Lucia
    "MF": Region.CARIBBEAN,  # Saint Martin
    "MQ": Region.CARIBBEAN,  # Martinique
    "MS": Region.CARIBBEAN,  # Montserrat
    "PR": Region.CARIBBEAN,  # Puerto Rico
    "SX": Region.CARIBBEAN,  # Sint Maarten
    "TC": Region.CARIBBEAN,  # Turks and Caicos
    "TT": Region.CARIBBEAN,  # Trinidad and Tobago
    "VC": Region.CARIBBEAN,  # Saint Vincent
    "VG": Region.CARIBBEAN,  # British Virgin Islands
    "VI": Region.CARIBBEAN,  # US Virgin Islands
    # --- LATIN_AMERICA ---
    "AR": Region.LATIN_AMERICA,  # Argentina
    "BO": Region.LATIN_AMERICA,  # Bolivia
    "BR": Region.LATIN_AMERICA,  # Brazil
    "BZ": Region.LATIN_AMERICA,  # Belize
    "CL": Region.LATIN_AMERICA,  # Chile
    "CO": Region.LATIN_AMERICA,  # Colombia
    "CR": Region.LATIN_AMERICA,  # Costa Rica
    "EC": Region.LATIN_AMERICA,  # Ecuador
    "FK": Region.LATIN_AMERICA,  # Falkland Islands
    "GF": Region.LATIN_AMERICA,  # French Guiana
    "GT": Region.LATIN_AMERICA,  # Guatemala
    "GY": Region.LATIN_AMERICA,  # Guyana
    "HN": Region.LATIN_AMERICA,  # Honduras
    "NI": Region.LATIN_AMERICA,  # Nicaragua
    "PA": Region.LATIN_AMERICA,  # Panama
    "PE": Region.LATIN_AMERICA,  # Peru
    "PY": Region.LATIN_AMERICA,  # Paraguay
    "SR": Region.LATIN_AMERICA,  # Suriname
    "SV": Region.LATIN_AMERICA,  # El Salvador
    "UY": Region.LATIN_AMERICA,  # Uruguay
    "VE": Region.LATIN_AMERICA,  # Venezuela
    # --- EUROPE ---
    "AD": Region.EUROPE,  # Andorra
    "AL": Region.EUROPE,  # Albania
    "AT": Region.EUROPE,  # Austria
    "AX": Region.EUROPE,  # Aland Islands
    "BA": Region.EUROPE,  # Bosnia and Herzegovina
    "BE": Region.EUROPE,  # Belgium
    "BG": Region.EUROPE,  # Bulgaria
    "BY": Region.EUROPE,  # Belarus
    "CH": Region.EUROPE,  # Switzerland
    "CY": Region.EUROPE,  # Cyprus
    "CZ": Region.EUROPE,  # Czechia
    "DE": Region.EUROPE,  # Germany
    "DK": Region.EUROPE,  # Denmark
    "EE": Region.EUROPE,  # Estonia
    "ES": Region.EUROPE,  # Spain
    "FI": Region.EUROPE,  # Finland
    "FO": Region.EUROPE,  # Faroe Islands
    "FR": Region.EUROPE,  # France
    "GB": Region.EUROPE,  # United Kingdom
    "GG": Region.EUROPE,  # Guernsey
    "GI": Region.EUROPE,  # Gibraltar
    "GR": Region.EUROPE,  # Greece
    "HR": Region.EUROPE,  # Croatia
    "HU": Region.EUROPE,  # Hungary
    "IE": Region.EUROPE,  # Ireland
    "IM": Region.EUROPE,  # Isle of Man
    "IS": Region.EUROPE,  # Iceland
    "IT": Region.EUROPE,  # Italy
    "JE": Region.EUROPE,  # Jersey
    "LI": Region.EUROPE,  # Liechtenstein
    "LT": Region.EUROPE,  # Lithuania
    "LU": Region.EUROPE,  # Luxembourg
    "LV": Region.EUROPE,  # Latvia
    "MC": Region.EUROPE,  # Monaco
    "MD": Region.EUROPE,  # Moldova
    "ME": Region.EUROPE,  # Montenegro
    "MK": Region.EUROPE,  # North Macedonia
    "MT": Region.EUROPE,  # Malta
    "NL": Region.EUROPE,  # Netherlands
    "NO": Region.EUROPE,  # Norway
    "PL": Region.EUROPE,  # Poland
    "PT": Region.EUROPE,  # Portugal
    "RO": Region.EUROPE,  # Romania
    "RS": Region.EUROPE,  # Serbia
    "RU": Region.EUROPE,  # Russia — included in Europe by aviation convention
    "SE": Region.EUROPE,  # Sweden
    "SI": Region.EUROPE,  # Slovenia
    "SJ": Region.EUROPE,  # Svalbard
    "SK": Region.EUROPE,  # Slovakia
    "SM": Region.EUROPE,  # San Marino
    "TR": Region.EUROPE,  # Turkey — per IATA convention
    "UA": Region.EUROPE,  # Ukraine
    "VA": Region.EUROPE,  # Vatican
    "XK": Region.EUROPE,  # Kosovo
    # --- AFRICA ---
    "DZ": Region.AFRICA,  # Algeria
    "AO": Region.AFRICA,  # Angola
    "BJ": Region.AFRICA,  # Benin
    "BW": Region.AFRICA,  # Botswana
    "BF": Region.AFRICA,  # Burkina Faso
    "BI": Region.AFRICA,  # Burundi
    "CM": Region.AFRICA,  # Cameroon
    "CV": Region.AFRICA,  # Cabo Verde
    "CF": Region.AFRICA,  # Central African Republic
    "TD": Region.AFRICA,  # Chad
    "KM": Region.AFRICA,  # Comoros
    "CG": Region.AFRICA,  # Congo
    "CD": Region.AFRICA,  # DR Congo
    "DJ": Region.AFRICA,  # Djibouti
    "EG": Region.AFRICA,  # Egypt
    "GQ": Region.AFRICA,  # Equatorial Guinea
    "ER": Region.AFRICA,  # Eritrea
    "ET": Region.AFRICA,  # Ethiopia
    "GA": Region.AFRICA,  # Gabon
    "GM": Region.AFRICA,  # Gambia
    "GH": Region.AFRICA,  # Ghana
    "GN": Region.AFRICA,  # Guinea
    "GW": Region.AFRICA,  # Guinea-Bissau
    "CI": Region.AFRICA,  # Cote d'Ivoire
    "KE": Region.AFRICA,  # Kenya
    "LS": Region.AFRICA,  # Lesotho
    "LR": Region.AFRICA,  # Liberia
    "LY": Region.AFRICA,  # Libya
    "MG": Region.AFRICA,  # Madagascar
    "MW": Region.AFRICA,  # Malawi
    "ML": Region.AFRICA,  # Mali
    "MR": Region.AFRICA,  # Mauritania
    "MU": Region.AFRICA,  # Mauritius
    "YT": Region.AFRICA,  # Mayotte
    "MA": Region.AFRICA,  # Morocco
    "MZ": Region.AFRICA,  # Mozambique
    "NA": Region.AFRICA,  # Namibia
    "NE": Region.AFRICA,  # Niger
    "NG": Region.AFRICA,  # Nigeria
    "RE": Region.AFRICA,  # Reunion
    "RW": Region.AFRICA,  # Rwanda
    "SH": Region.AFRICA,  # Saint Helena
    "ST": Region.AFRICA,  # Sao Tome and Principe
    "SN": Region.AFRICA,  # Senegal
    "SC": Region.AFRICA,  # Seychelles
    "SL": Region.AFRICA,  # Sierra Leone
    "SO": Region.AFRICA,  # Somalia
    "ZA": Region.AFRICA,  # South Africa
    "SS": Region.AFRICA,  # South Sudan
    "SD": Region.AFRICA,  # Sudan
    "SZ": Region.AFRICA,  # Eswatini
    "TZ": Region.AFRICA,  # Tanzania
    "TG": Region.AFRICA,  # Togo
    "TN": Region.AFRICA,  # Tunisia
    "UG": Region.AFRICA,  # Uganda
    "EH": Region.AFRICA,  # Western Sahara
    "ZM": Region.AFRICA,  # Zambia
    "ZW": Region.AFRICA,  # Zimbabwe
    # --- MIDDLE_EAST ---
    "AE": Region.MIDDLE_EAST,  # United Arab Emirates
    "BH": Region.MIDDLE_EAST,  # Bahrain
    "IL": Region.MIDDLE_EAST,  # Israel
    "IQ": Region.MIDDLE_EAST,  # Iraq
    "IR": Region.MIDDLE_EAST,  # Iran
    "JO": Region.MIDDLE_EAST,  # Jordan
    "KW": Region.MIDDLE_EAST,  # Kuwait
    "LB": Region.MIDDLE_EAST,  # Lebanon
    "OM": Region.MIDDLE_EAST,  # Oman
    "PS": Region.MIDDLE_EAST,  # Palestine
    "QA": Region.MIDDLE_EAST,  # Qatar
    "SA": Region.MIDDLE_EAST,  # Saudi Arabia
    "SY": Region.MIDDLE_EAST,  # Syria
    "YE": Region.MIDDLE_EAST,  # Yemen
    # --- ASIA_PACIFIC ---
    "AF": Region.ASIA_PACIFIC,  # Afghanistan — grouped with Asia-Pacific here
    "AS": Region.ASIA_PACIFIC,  # American Samoa (NOT Asia — careful: AS is a Pacific territory)
    "AU": Region.ASIA_PACIFIC,  # Australia
    "BD": Region.ASIA_PACIFIC,  # Bangladesh
    "BN": Region.ASIA_PACIFIC,  # Brunei
    "BT": Region.ASIA_PACIFIC,  # Bhutan
    "CC": Region.ASIA_PACIFIC,  # Cocos Islands
    "CK": Region.ASIA_PACIFIC,  # Cook Islands
    "CN": Region.ASIA_PACIFIC,  # China
    "CX": Region.ASIA_PACIFIC,  # Christmas Island
    "FJ": Region.ASIA_PACIFIC,  # Fiji
    "FM": Region.ASIA_PACIFIC,  # Micronesia
    "GU": Region.ASIA_PACIFIC,  # Guam
    "HK": Region.ASIA_PACIFIC,  # Hong Kong
    "ID": Region.ASIA_PACIFIC,  # Indonesia
    "IN": Region.ASIA_PACIFIC,  # India
    "IO": Region.ASIA_PACIFIC,  # British Indian Ocean
    "JP": Region.ASIA_PACIFIC,  # Japan
    "KG": Region.ASIA_PACIFIC,  # Kyrgyzstan
    "KH": Region.ASIA_PACIFIC,  # Cambodia
    "KI": Region.ASIA_PACIFIC,  # Kiribati
    "KP": Region.ASIA_PACIFIC,  # North Korea
    "KR": Region.ASIA_PACIFIC,  # South Korea
    "KZ": Region.ASIA_PACIFIC,  # Kazakhstan
    "LA": Region.ASIA_PACIFIC,  # Laos
    "LK": Region.ASIA_PACIFIC,  # Sri Lanka
    "MH": Region.ASIA_PACIFIC,  # Marshall Islands
    "MM": Region.ASIA_PACIFIC,  # Myanmar
    "MN": Region.ASIA_PACIFIC,  # Mongolia
    "MO": Region.ASIA_PACIFIC,  # Macau
    "MP": Region.ASIA_PACIFIC,  # Northern Mariana
    "MV": Region.ASIA_PACIFIC,  # Maldives
    "MY": Region.ASIA_PACIFIC,  # Malaysia
    "NC": Region.ASIA_PACIFIC,  # New Caledonia
    "NF": Region.ASIA_PACIFIC,  # Norfolk Island
    "NP": Region.ASIA_PACIFIC,  # Nepal
    "NR": Region.ASIA_PACIFIC,  # Nauru
    "NU": Region.ASIA_PACIFIC,  # Niue
    "NZ": Region.ASIA_PACIFIC,  # New Zealand
    "PF": Region.ASIA_PACIFIC,  # French Polynesia
    "PG": Region.ASIA_PACIFIC,  # Papua New Guinea
    "PH": Region.ASIA_PACIFIC,  # Philippines
    "PK": Region.ASIA_PACIFIC,  # Pakistan
    "PN": Region.ASIA_PACIFIC,  # Pitcairn
    "PW": Region.ASIA_PACIFIC,  # Palau
    "SB": Region.ASIA_PACIFIC,  # Solomon Islands
    "SG": Region.ASIA_PACIFIC,  # Singapore
    "TH": Region.ASIA_PACIFIC,  # Thailand
    "TJ": Region.ASIA_PACIFIC,  # Tajikistan
    "TK": Region.ASIA_PACIFIC,  # Tokelau
    "TL": Region.ASIA_PACIFIC,  # Timor-Leste
    "TM": Region.ASIA_PACIFIC,  # Turkmenistan
    "TO": Region.ASIA_PACIFIC,  # Tonga
    "TV": Region.ASIA_PACIFIC,  # Tuvalu
    "TW": Region.ASIA_PACIFIC,  # Taiwan
    "UZ": Region.ASIA_PACIFIC,  # Uzbekistan
    "VN": Region.ASIA_PACIFIC,  # Vietnam
    "VU": Region.ASIA_PACIFIC,  # Vanuatu
    "WF": Region.ASIA_PACIFIC,  # Wallis and Futuna
    "WS": Region.ASIA_PACIFIC,  # Samoa
}


def region_for_country(country_code: Optional[str]) -> Optional[Region]:
    """Map an ISO 2-letter country code to a Region, or None."""
    if not country_code:
        return None
    return _COUNTRY_TO_REGION.get(country_code.upper())


def region_for_iata(iata: Optional[str]) -> Optional[Region]:
    """Map a 3-letter IATA airport code to a Region.

    Returns None if the IATA is unknown, the country has no mapping, or
    the optional `airportsdata` dependency is not installed.
    """
    if not iata:
        return None
    try:
        import airportsdata
    except ImportError:
        return None
    db = airportsdata.load("IATA")
    airport = db.get(iata.upper())
    if not airport:
        return None
    return region_for_country(airport.get("country"))

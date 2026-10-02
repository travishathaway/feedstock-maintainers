"""Tally which countries a set of GitHub users come from.

Reads a JSON file of GitHub user objects (as produced by `fetch maintainer-info`), maps each
user's free-text ``location`` field to a country, and tallies the result. Resolution works
entirely offline, in this order:

1. Hand-written overrides (``OVERRIDES`` below, plus an extra file merged in by the CLI)
2. The whole string is a country name / ISO code ("Germany", "UK", "DE")
3. Any comma/slash-separated part is a country name ("Berlin, Germany")
4. A trailing 2-letter code, disambiguated by the city before it
   ("Portland, OR" -> US, "Delft, NL" -> Netherlands)
5. A US/Canadian/Australian/... state or province ("Ontario")
6. A city from the bundled GeoNames snapshot (most populous match wins)
7. Country names or large cities (100k+) appearing anywhere in the text

Anything left over ("Earth", "127.0.0.1", "Europe") is reported as unresolved and left out of
the tally. Check the CLI's --report output before trusting the numbers: free text is messy, and
new data will need new entries in OVERRIDES.
"""

from __future__ import annotations

import collections
import gzip
import json
import re
import unicodedata
from collections import Counter
from importlib import resources
from pathlib import Path

import pycountry

US, UK = "United States", "United Kingdom"


def _load_geonames_cities() -> list[tuple[list[str], str, int]]:
    """Load the bundled GeoNames cities15000 snapshot (see data/README.md).

    Each row is (name_variants, country_code, population). Vendored instead of depending on the
    `geonamescache` package, which isn't available on conda-forge.
    """
    path = resources.files("feedstock_maintainers") / "data" / "geonames_cities15000.json.gz"
    with path.open("rb") as f:
        raw = gzip.decompress(f.read())
    return json.loads(raw)


def norm(s: str) -> str:
    """Lowercase, strip accents and collapse whitespace."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s.lower()).strip(" .")


# --- Country names -> canonical name ----------------------------------------

COUNTRY: dict[str, str] = {}
for _c in pycountry.countries:
    _name = getattr(_c, "common_name", None) or _c.name
    for _alias in (
        _c.name,
        getattr(_c, "official_name", None),
        getattr(_c, "common_name", None),
        _c.alpha_3,
    ):
        if _alias:
            COUNTRY[norm(_alias)] = _name

CODE2 = {c.alpha_2: getattr(c, "common_name", None) or c.name for c in pycountry.countries}

COUNTRY_ALIASES = {
    "usa": US,
    "us": US,
    "u.s.": US,
    "u.s.a.": US,
    "united states of america": US,
    "america": US,
    "the united states": US,
    "uk": UK,
    "u.k.": UK,
    "the uk": UK,
    "england": UK,
    "scotland": UK,
    "wales": UK,
    "northern ireland": UK,
    "great britain": UK,
    "britain": UK,
    "gb": UK,
    "deutschland": "Germany",
    "brasil": "Brazil",
    "espana": "Spain",
    "italia": "Italy",
    "schweiz": "Switzerland",
    "suisse": "Switzerland",
    "nederland": "Netherlands",
    "the netherlands": "Netherlands",
    "holland": "Netherlands",
    "osterreich": "Austria",
    "sverige": "Sweden",
    "norge": "Norway",
    "danmark": "Denmark",
    "suomi": "Finland",
    "polska": "Poland",
    "belgique": "Belgium",
    "belgie": "Belgium",
    "mexico": "Mexico",
    "russia": "Russian Federation",
    "south korea": "Korea, Republic of",
    "korea": "Korea, Republic of",
    "republic of korea": "Korea, Republic of",
    "czech republic": "Czechia",
    "czechia": "Czechia",
    "iran": "Iran",
    "vietnam": "Viet Nam",
    "turkey": "Türkiye",
    "turkiye": "Türkiye",
    "taiwan": "Taiwan",
    "prc": "China",
    "p.r. china": "China",
    "p.r.china": "China",
    "hong kong": "Hong Kong",
    "hk": "Hong Kong",
    "aotearoa": "New Zealand",
    "nz": "New Zealand",
    "uae": "United Arab Emirates",
    "ksa": "Saudi Arabia",
    "bolivia": "Bolivia",
    "venezuela": "Venezuela",
    "tanzania": "Tanzania",
    "moldova": "Moldova",
    "palestine": "Palestine",
    "syria": "Syria",
    "laos": "Laos",
}
COUNTRY.update({norm(k): v for k, v in COUNTRY_ALIASES.items()})

# Short or awkward pycountry names -> the names people expect to read.
PRETTY = {
    "Korea, Republic of": "South Korea",
    "Russian Federation": "Russia",
    "Viet Nam": "Vietnam",
    "Iran, Islamic Republic of": "Iran",
    "Taiwan, Province of China": "Taiwan",
    "Bolivia, Plurinational State of": "Bolivia",
    "Venezuela, Bolivarian Republic of": "Venezuela",
    "Tanzania, United Republic of": "Tanzania",
    "Moldova, Republic of": "Moldova",
    "Palestine, State of": "Palestine",
    "Syrian Arab Republic": "Syria",
    "Lao People's Democratic Republic": "Laos",
    "Czechia": "Czech Republic",
}


def pretty(name: str) -> str:
    return PRETTY.get(name, name)


# Country tokens of 3 characters or fewer that are safe to match inside longer text.
SHORT_COUNTRY_TOKENS = {"usa", "uk", "us", "uae", "prc", "gb", "nz", "hk"}

# --- ISO 3166-1 codes, keyed by the exact display string resolve() returns -------------------
# ISO_NUMERIC is the join key the frontend map uses, since it's what a world-atlas TopoJSON
# feature's `id` is -- it sidesteps any name mismatch between pycountry's names and a map
# dataset's names. ISO_ALPHA2 is what the frontend uses to render a flag emoji.

ISO_NUMERIC: dict[str, str] = {}
ISO_ALPHA2: dict[str, str] = {}
for _c in pycountry.countries:
    _display = pretty(getattr(_c, "common_name", None) or _c.name)
    ISO_NUMERIC[_display] = _c.numeric
    ISO_ALPHA2[_display] = _c.alpha_2

# --- States / provinces ------------------------------------------------------
# (country code, whether to also match 2-letter codes like "WA" / "ON")

REGION: dict[str, str] = {}
for _cc, _with_codes in (
    ("US", True),
    ("CA", True),
    ("AU", True),
    ("IN", False),
    ("DE", False),
    ("CN", False),
    ("BR", False),
):
    _country_name = US if _cc == "US" else CODE2[_cc]
    for _s in pycountry.subdivisions.get(country_code=_cc):
        REGION.setdefault(norm(_s.name), _country_name)
        if _with_codes:
            REGION.setdefault(norm(_s.code.split("-")[1]), _country_name)

REGION.update(
    {
        norm(k): v
        for k, v in {
            "bay area": US,
            "silicon valley": US,
            "socal": US,
            "norcal": US,
            "pnw": US,
            "washington dc": US,
            "dc": US,
            "d.c.": US,
            "nyc": US,
            "sf": US,
            "la": US,
            "texas": US,
            "ontario": "Canada",
            "quebec": "Canada",
            "bavaria": "Germany",
            "bayern": "Germany",
            "catalonia": "Spain",
            "catalunya": "Spain",
        }.items()
    }
)

US_CODES = {s.code.split("-")[1] for s in pycountry.subdivisions.get(country_code="US")}

# --- Cities ------------------------------------------------------------------
# name -> (country, population); the most populous city with a name wins.

CITY: dict[str, tuple[str, int]] = {}
for _names, _country_code, _population in _load_geonames_cities():
    for _cname in _names:
        _key = norm(_cname)
        if len(_key) < 3:
            continue
        if _key not in CITY or _population > CITY[_key][1]:
            CITY[_key] = (CODE2.get(_country_code, _country_code), _population)

# Ambiguous names where the most populous match is not the one people mean.
CITY_PREFERENCES = {
    "cambridge": UK,
    "london": UK,
    "oxford": UK,
    "manchester": UK,
    "birmingham": UK,
    "paris": "France",
    "brest": "France",
    "berlin": "Germany",
    "barcelona": "Spain",
    "valencia": "Spain",
    "madeira": "Portugal",
    "delft": "Netherlands",
    "noordwijk": "Netherlands",
    "vancouver": "Canada",
    "melbourne": "Australia",
    "sydney": "Australia",
    "bangalore": "India",
    "bengaluru": "India",
    "cordoba": "Argentina",
    "boston": US,
    "portland": US,
    "richmond": US,
    "palo alto": US,
    "berkeley": US,
    "princeton": US,
    "pasadena": US,
    "los alamos": US,
    "ithaca": US,
    "urbana": US,
    "champaign": US,
    "stanford": US,
    "santa clara": US,
    "santa cruz": US,
    "san mateo": US,
}
for _key, _name in CITY_PREFERENCES.items():
    CITY[_key] = (_name, 10**9)

# --- Overrides ---------------------------------------------------------------
# Exact location strings (compared after norm()) that the rules above get wrong or miss. None
# means "deliberately unresolved". These were built from the conda-forge maintainer list; extend
# them for your own data via `generate maintainer-countries --overrides FILE`.

OVERRIDES: dict[str, str | None] = {
    # Misspellings, abbreviations, small towns missing from GeoNames
    "Isreal": "Israel",
    "Gemany": "Germany",
    "Fairbanks Alaksa": US,
    "The U.S.": US,
    "NY": US,
    "TX": US,
    "SF": US,
    "SF Bay Area": US,
    "Greater NYC area": US,
    "MTV": US,
    "City of Angels": US,
    "Chicagoland": US,
    "Central Illinois": US,
    "Western Mass": US,
    "Ironton OH": US,
    "Lemont IL": US,
    "Ames Iowa": US,
    "Bend OR": US,
    "Flagstaff AZ": US,
    "Yorktown Heights": US,
    "CA94025": US,
    "Ames Research Center, Moffett Field, CA 94035": US,
    "40°47'11.2\"N 119°12'24.2\"W": US,
    "BC": "Canada",
    "Thetford Mines": "Canada",
    "DE": "Germany",
    "Holmfirth": UK,
    "Oxfordshire": UK,
    "Devon": UK,
    "West Sussex": UK,
    "West Yorkshire": UK,
    "Cymru": UK,
    "Villigen": "Switzerland",
    "CERN": "Switzerland",
    "Houten": "Netherlands",
    "Schoonhoven": "Netherlands",
    "Cuijk": "Netherlands",
    "Zeuthen": "Germany",
    "Ploen": "Germany",
    "Havixbeck": "Germany",
    "Garching Forschungszentrum": "Germany",
    "Saclay": "France",
    "Elsass": "France",
    "Ecole polytechnique": "France",
    "Sorbonne University Pierre and Marie Curie Campus": "France",
    "Københavns Universitet": "Denmark",
    "Kamchatka": "Russia",
    "Ribeira Grande, Azores": "Portugal",
    "Recife - PE": "Brazil",
    "Atlanta, Georgia": US,
    "Vienna ⋄ Helsinki ⋄ Quito": "Austria",
    # Institutions
    "Janelia": US,
    "GSFC": US,
    "Goddard Space Flight Center": US,
    "NOAA NCWCP": US,
    "Clark Center": US,
    "Duke University": US,
    "University of Alaska Fairbanks": US,
    "University of Central Florida": US,
    "Oak Ridge National Laboratory": US,
    "Memorial Sloan Kettering Cancer Center": US,
    # Jokes / ambiguous strings that would otherwise match a real place
    "Sturgeon Pond, SV": None,
    "Atlantis": None,
    "Data Center": None,
    "Planet Earth, most likely": None,
    "where time series is observed & valued": None,
    "Gaia, Sayshell Sector": None,
    "Angola/IN": None,
    "Kota Kahuana y La Cumbrecita: los pies en el suelo, y andar por las nubes": None,
}

# Checked before norm(), which drops non-Latin scripts entirely.
RAW_OVERRIDES = {"Байкаловск": "Russia"}


# --- Resolution --------------------------------------------------------------


def resolve(loc: str, overrides: dict[str, str | None]) -> str | None:
    """Return the country for a free-text location, or None."""
    if loc in RAW_OVERRIDES:
        return RAW_OVERRIDES[loc]
    n = norm(loc)
    if not n:
        return None
    if n in overrides:
        return overrides[n]

    # Whole string is a country, or a bare 2-letter code (US states win).
    if n in COUNTRY:
        return pretty(COUNTRY[n])
    if len(n) == 2 and n.upper() in US_CODES:
        return US
    if len(n) == 2 and n.upper() in CODE2:
        return pretty(CODE2[n.upper()])

    parts = [p.strip() for p in re.split(r"[,/|;·•\-–—()]+| and | & ", n) if p.strip()]  # noqa: RUF001

    # Any part is a country name (right-most first).
    for p in reversed(parts):
        if p in COUNTRY and (len(p) > 3 or p in SHORT_COUNTRY_TOKENS):
            return pretty(COUNTRY[p])

    # Trailing 2-letter code: state/province vs ISO country, decided by the city part.
    if len(parts) > 1 and len(parts[-1]) == 2:
        p = parts[-1]
        candidates = [x for x in (REGION.get(p), CODE2.get(p.upper())) if x]
        city_country = CITY.get(parts[0], (None,))[0]
        if city_country is not None and city_country in candidates:
            return pretty(city_country)
        if p.upper() in US_CODES and REGION.get(p) == US:
            return US
        if candidates:
            return pretty(candidates[-1])

    # State / province name.
    for p in reversed(parts):
        if p in REGION and not (len(p) == 2 and len(parts) == 1):
            return pretty(REGION[p])

    # City.
    for p in parts:
        if p in CITY:
            return pretty(CITY[p][0])

    # Country names or large cities anywhere in the text.
    words = re.findall(r"[a-z][a-z.]+", n)
    for w in reversed(words):
        if w in COUNTRY and (len(w) > 3 or w in ("usa", "uk", "ksa")):
            return pretty(COUNTRY[w])
    for i in range(len(words)):
        for j in (3, 2, 1):
            key = " ".join(words[i : i + j])
            if len(key) >= 4 and key in CITY and CITY[key][1] >= 100_000:
                return pretty(CITY[key][0])

    if len(parts) == 1 and parts[0].upper() in CODE2:
        return pretty(CODE2[parts[0].upper()])
    return None


def load_locations(path: Path, field: str = "location") -> dict[str, str]:
    """Return {user: location} from a dict keyed by login or a list of user objects."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    records = (
        data.items()
        if isinstance(data, dict)
        else ((r.get("login", i), r) for i, r in enumerate(data))
    )
    return {user: (r.get(field) or "").strip() for user, r in records if isinstance(r, dict)}


def build_overrides(extra_file: Path | None = None) -> dict[str, str | None]:
    """The built-in OVERRIDES table, normalized and optionally merged with an extra JSON file."""
    overrides = {norm(k): v for k, v in OVERRIDES.items()}
    if extra_file is not None:
        extra = json.loads(Path(extra_file).read_text(encoding="utf-8"))
        overrides.update({norm(k): v for k, v in extra.items()})
    return overrides


def tally_countries(
    locations: dict[str, str], overrides: dict[str, str | None]
) -> tuple[Counter[str], Counter[str], dict[str, str]]:
    """Resolve every location and tally the results.

    Returns (tally, unresolved, resolved): `tally` counts users per country, `unresolved` counts
    raw location strings that couldn't be resolved, and `resolved` maps each resolved user to
    their country (useful for building an audit report without re-running resolution).
    """
    resolved: dict[str, str] = {}
    unresolved: collections.Counter[str] = collections.Counter()
    for user, loc in locations.items():
        if not loc:
            continue
        country = resolve(loc, overrides)
        if country:
            resolved[user] = country
        else:
            unresolved[loc] += 1
    return collections.Counter(resolved.values()), unresolved, resolved


def to_rows(tally: Counter[str], count_key: str = "count") -> list[dict]:
    """Tally rows sorted by count descending, each carrying its ISO 3166-1 numeric/alpha-2 codes."""
    return [
        {
            "country": c,
            "iso_numeric": ISO_NUMERIC.get(c),
            "iso_alpha2": ISO_ALPHA2.get(c),
            count_key: n,
        }
        for c, n in tally.most_common()
    ]

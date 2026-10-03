"""Finnish / Norwegian -> English rendering helpers.

Upstream feeds (Tilannehuone, Fintraffic RSS, MET Norway metalerts) publish
only in the local language. The app is English-only, so all user-visible
strings go through this module.
"""
from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Tilannehuone category translations (halytrans[] index -> English label)
# ---------------------------------------------------------------------------

HALY_CATEGORY: dict[str, str] = {
    "...": "Other incident",
    "rakennuspalo": "Building fire",
    "rakennuspalo: pieni": "Building fire (minor)",
    "rakennuspalo: keskisuuri": "Building fire (medium)",
    "rakennuspalo: suuri": "Building fire (major)",
    "liikennev\u00e4linepalo": "Vehicle fire",
    "liikennev\u00e4linepalo: pieni": "Vehicle fire (minor)",
    "liikennev\u00e4linepalo: keskisuuri": "Vehicle fire (medium)",
    "liikenneonnettomuus": "Traffic accident",
    "tieliikenneonnettomuus": "Road traffic accident",
    "tieliikenneonnettomuus: pieni": "Road accident (minor)",
    "tieliikenneonnettomuus: keskisuuri": "Road accident (medium)",
    "tieliikenneonnettomuus: suuri": "Road accident (major)",
    "paloh\u00e4lytys": "Fire alarm",
    "\u00f6ljyvahinko": "Oil spill",
    "ihmisen pelastaminen: muu": "Rescue (other)",
    "ihmisen pelastaminen": "Rescue",
    "el\u00e4imen pelastaminen": "Animal rescue",
    "onnettomuus tai vaaratilanne rannalla tai maissa (smps)":
        "Accident / hazardous situation at sea or ashore",
    "tekninen vika, kohde ei ajelehdi (smps)": "Vessel breakdown (not under way)",
    "tekninen vika": "Technical fault",
    "muu": "Other",
}

# Municipality names appearing in halytrans[] at the odd indexes stay as-is;
# they are places, not categories.

# ---------------------------------------------------------------------------
# Finnish word-level replacements for free-text incident descriptions
# ---------------------------------------------------------------------------

FI_WORDS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bsilminn\u00e4kij\u00e4havainto\b", re.I), "Eyewitness report"),
    (re.compile(r"\bhavainto\b", re.I), "report"),
    (re.compile(r"\btarkistamaton\b", re.I), "unverified"),
    (re.compile(r"\bperustuu\b", re.I), "based on"),
    (re.compile(r"\bOnnettomuus\b", re.I), "Accident"),
    (re.compile(r"\bLiikenneonnettomuus\b", re.I), "Traffic accident"),
    (re.compile(r"\brakennuspalo\b", re.I), "building fire"),
    (re.compile(r"\btieli\u00e4?\s*liikenneonnettomuus\b", re.I), "road traffic accident"),
    (re.compile(r"\bensitiedote\b", re.I), "first notice"),
    (re.compile(r"\bliikennetiedote\b", re.I), "traffic notice"),
    (re.compile(r"\bjatkotiedote\b", re.I), "follow-up notice"),
    (re.compile(r"\bliikennev\u00e4linepalo\b", re.I), "vehicle fire"),
    (re.compile(r"\bTarkempi paikka\b", re.I), "Exact location"),
    (re.compile(r"\bPaikka\b", re.I), "Location"),
    (re.compile(r"\bV\u00e4lill\u00e4\b", re.I), "Between"),
    (re.compile(r"\bliittym\u00e4\b", re.I), "junction"),
    (re.compile(r"\bpohjoinen\b", re.I), "northern"),
    (re.compile(r"\betel\u00e4inen\b", re.I), "eastern"),
    (re.compile(r"\bl\u00e4nsinainen\b", re.I), "western"),
    (re.compile(r"\bpohjoinen liittym\u00e4\b", re.I), "northern junction"),
    (re.compile(r"\betel\u00e4inen liittym\u00e4\b", re.I), "eastern junction"),
    (re.compile(r"\bl\u00e4nsinainen liittym\u00e4\b", re.I), "western junction"),
    (re.compile(r"\bPelastuslaitos\b", re.I), "Rescue service"),
    (re.compile(r"\bpalokunta\b", re.I), "fire brigade"),
    (re.compile(r"\bsammutti\b", re.I), "extinguished"),
    (re.compile(r"\bsavutti\b", re.I), "smoke"),
    (re.compile(r"\bsavunvahingoja?\b", re.I), "smoke damage"),
    (re.compile(r"\bvahingoittui\b", re.I), "was damaged"),
    (re.compile(r"\bvaurioitti\b", re.I), "damaged"),
    (re.compile(r"\bs\u00e4hkisi\u00e4\b", re.I), "was extinguished"),
    (re.compile(r"\bpaloi\b", re.I), "burned"),
    (re.compile(r"\bsyttyi\b", re.I), "caught fire"),
    (re.compile(r"\bkipin\u00f6i\b", re.I), "sparked"),
    (re.compile(r"\bhenkil\u00f6vahinkoja?\b", re.I), "personal injuries"),
    (re.compile(r"\bei henkil\u00f6vahinkoja\b", re.I), "no personal injuries"),
    (re.compile(r"\bilmoitettu\b", re.I), "reported"),
    (re.compile(r"\bTilannehuone\.fi\b", re.I), "Tilannehuone.fi"),
    (re.compile(r"\btarkastettiin\b", re.I), "were inspected"),
    (re.compile(r"\btarkastivat\b", re.I), "inspected"),
    (re.compile(r"\btilannetta\b", re.I), "situation"),
    (re.compile(r"\btapahtui\b", re.I), "occurred"),
    (re.compile(r"\bkello\b", re.I), "at"),
]

# Swedish and Norwegian touches also appear in Nordic Fintraffic notices.
NO_WORDS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bKuling,?\b", re.I), "Gale"),
    (re.compile(r"\bvindvarsel\b", re.I), "wind warning"),
    (re.compile(r"\bb\u00f8rsvarsel\b", re.I), "drifting snow warning"),
    (re.compile(r"\bkraftig vind\b", re.I), "strong wind"),
    (re.compile(r"\bsterk vind\b", re.I), "strong wind"),
    (re.compile(r"\bmoderat vindstyrke\b", re.I), "moderate wind"),
    (re.compile(r"\bsn\u00f8fall\b", re.I), "snowfall"),
    (re.compile(r"\bstormfullt\b", re.I), "stormy"),
    (re.compile(r"\bmake TS 9\b", re.I), "rough sea TS 9"),
    (re.compile(r"\brolig\b", re.I), "calm"),
    (re.compile(r"\bsj\u00f8en\b", re.I), "the sea"),
    (re.compile(r"\bb\u00f8lger med\b", re.I), "followed by"),
    (re.compile(r"\b\+\b"), " to "),
]


def translate(text: str) -> str:
    """Best-effort Finnish/Norwegian -> English free-text translation."""
    if not text:
        return ""
    s = text
    for pat, rep in FI_WORDS:
        s = pat.sub(rep, s)
    for pat, rep in NO_WORDS:
        s = pat.sub(rep, s)
    s = s.replace("\u00e4", "a").replace("\u00f6", "o").replace("\u00e5", "a")
    s = s.replace("\u00e8", "e").replace("\u00fc", "u")
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()


def category(fi_label: str) -> str:
    """Translate a Tilannehuone category string."""
    key = (fi_label or "").strip().lower()
    if not key or key == "...":
        return "Other incident"
    if key in HALY_CATEGORY:
        return HALY_CATEGORY[key]
    return translate(fi_label).capitalize() or "Other incident"


def strip_html(fragment: str) -> str:
    """Flatten Tilannehuone's HTML description to plain text."""
    if not fragment:
        return ""
    s = re.sub(r"<\s*(br|/p|/div|/tr)\s*/?\s*>", "\n", fragment, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = (s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&quot;", '"')
          .replace("&#039;", "'").replace("&lt;", "<").replace("&gt;", ">"))
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()


def wmo_weather(code: int | None) -> tuple[str, str]:
    """WMO weather code -> (english description, leaflet-ready icon kind)."""
    table: dict[int, tuple[str, str]] = {
        0: ("Clear sky", "clear"),
        1: ("Mainly clear", "clear"),
        2: ("Partly cloudy", "partly"),
        3: ("Overcast", "overcast"),
        45: ("Fog", "fog"),
        48: ("Depositing rime fog", "fog"),
        51: ("Light drizzle", "drizzle"),
        53: ("Moderate drizzle", "drizzle"),
        55: ("Dense drizzle", "drizzle"),
        56: ("Light freezing drizzle", "sleet"),
        57: ("Dense freezing drizzle", "sleet"),
        61: ("Slight rain", "rain"),
        63: ("Moderate rain", "rain"),
        65: ("Heavy rain", "rain"),
        66: ("Light freezing rain", "sleet"),
        67: ("Heavy freezing rain", "sleet"),
        71: ("Slight snow fall", "snow"),
        73: ("Moderate snow fall", "snow"),
        75: ("Heavy snow fall", "snow"),
        77: ("Snow grains", "snow"),
        80: ("Slight rain showers", "showers"),
        81: ("Moderate rain showers", "showers"),
        82: ("Violent rain showers", "showers"),
        85: ("Slight snow showers", "snow"),
        86: ("Heavy snow showers", "snow"),
        95: ("Thunderstorm", "thunder"),
        96: ("Thunderstorm with slight hail", "thunder"),
        99: ("Thunderstorm with heavy hail", "thunder"),
    }
    return table.get(int(code or 0), ("Unknown", "clear"))


def severity_color(sev: str | None) -> str:
    s = (sev or "").lower()
    if "extreme" in s:
        return "#c81e3a"
    if "severe" in s:
        return "#e8590c"
    if "moderate" in s:
        return "#f08c00"
    return "#f5c518"


def traffic_kind(fi: str) -> str:
    """Classify a Fintraffic notice into an English category key."""
    s = (fi or "").lower()
    if "onnettomu" in s:
        return "accident"
    if "tiikki" in s or "ruuhka" in s:
        return "congestion"
    if "ty\u00f6" in s or "rakennusty\u00f6" in s:
        return "roadwork"
    if "suljettu" in s or "suljettuna" in s:
        return "closure"
    if "s\u00e4\u00e4" in s or "s\u00e4\u00e4nti" in s:
        return "weather"
    if "paino" in s or "rajoitus" in s:
        return "weightlimit"
    if "kypp\u00e4" in s:
        return "obstacle"
    if "kypp\u00e4m\u00e4\u00e4ritys" in s:
        return "crane"
    if "s\u00e4hk\u00f6" in s or "kaapeli" in s:
        return "power"
    if "vika" in s or "toimintah\u00e4iri\u00f6" in s:
        return "fault"
    return "notice"


def traffic_label(kind: str) -> str:
    return {
        "accident": "Accident",
        "congestion": "Congestion / queue",
        "roadwork": "Road works",
        "closure": "Road closed",
        "weather": "Weather related",
        "weightlimit": "Weight / width limit",
        "obstacle": "Obstacle on road",
        "crane": "Crane operation",
        "power": "Power line work",
        "fault": "Technical fault",
    }.get(kind, "Traffic notice")


def finnish_phrase_strip(text: str) -> dict[str, Any]:
    """Split a Fintraffic title into road/street, place and detail parts."""
    raw = (text or "").strip()
    parts = [p.strip() for p in raw.split(".") if p.strip()]
    road = parts[0] if parts else raw
    place = ""
    for p in parts[1:2]:
        place = p
    if not place and "." in road:
        bits = road.split(".")
        road, place = bits[0], bits[1] if len(bits) > 1 else ""
    road = re.sub(r"^tie\s+(\d+[A-Za-z]?)\b", r"Highway \1", road, flags=re.I)
    road = re.sub(r"^katu\b", "Street", road, flags=re.I)
    road = road.replace("eli", "also known as")
    return {
        "road": translate(road).strip(),
        "place": translate(place).strip(),
        "raw": raw,
    }
"""Weather warnings from two open sources.

Finnish Meteorological Institute (CAP v1.2 Atom fat feed, English locale):
    https://alerts.fmi.fi/cap/feed/atom_en-GB.xml
Each entry embeds the CAP alert inline, including <polygon> geometry and the
structured parameters (wind speed, colour, probability).

MET Norway metalerts (covers the neighbouring Nordic areas, GeoJSON):
    https://api.met.no/weatherapi/metalerts/2.0/all.json
Requires a descriptive User-Agent; the shared client sets one.
"""
from __future__ import annotations

from typing import Any
from xml.etree import ElementTree as ET

import httpx

import lang

ATOM = "{http://www.w3.org/2005/Atom}"
CAP = "{urn:oasis:names:tc:emergency:cap:1.2}"

FMI_FEED = "https://alerts.fmi.fi/cap/feed/atom_en-GB.xml"
METNO_URL = "https://api.met.no/weatherapi/metalerts/2.0/all.json"

FMI_COLOR = {
    "yellow": "#f5c518",
    "orange": "#e8590c",
    "red": "#c81e3a",
}


def _text(info: ET.Element, tag: str) -> str | None:
    el = info.find(f"{CAP}{tag}")
    return el.text.strip() if el is not None and el.text else None


def _polygons(info: ET.Element) -> list[list[list[float]]]:
    out: list[list[list[float]]] = []
    for poly in info.findall(f".//{CAP}polygon"):
        if not poly.text:
            continue
        nums = [float(v) for v in poly.text.replace(",", " ").split()]
        ring: list[list[float]] = []
        for i in range(0, len(nums) - 1, 2):
            # CAP polygons are "lat,lon lat,lon ..."
            ring.append([round(nums[i + 1], 5), round(nums[i], 5)])
        if len(ring) >= 3:
            out.append([ring, ring[0]])
    return out


def _params(info: ET.Element) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in info.findall(f"{CAP}parameter"):
        name = p.find(f"{CAP}valueName")
        val = p.find(f"{CAP}value")
        if name is not None and val is not None and name.text:
            out[name.text.strip()] = (val.text or "").strip()
    return out


def _fmi_severity(color: str | None, severity: str | None) -> str:
    if color:
        c = color.lower()
        if c in FMI_COLOR:
            return c
    s = (severity or "").lower()
    if "extreme" in s:
        return "red"
    if "severe" in s:
        return "orange"
    return "yellow"


async def warnings(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """FMI CAP warnings, translated to English (feed is already en-GB)."""
    r = await client.get(FMI_FEED, headers={"Accept": "application/atom+xml"})
    r.raise_for_status()
    root = ET.fromstring(r.content)

    out: list[dict[str, Any]] = []
    for entry in root.findall(f"{ATOM}entry"):
        info_blocks = list(entry.iter(f"{CAP}info"))
        if not info_blocks:
            continue
        info = info_blocks[0]

        area = _text(info, "area")
        area_el = info.find(f"{CAP}area")
        area_desc = None
        if area_el is not None:
            ad = area_el.find(f"{CAP}areaDesc")
            area_desc = ad.text.strip() if ad is not None and ad.text else None

        # The en-GB Atom feed keeps the Finnish <cap:headline>, so the Atom
        # <title> (which is the translated one) is the primary source here.
        title = lang.translate(entry.findtext(f"{ATOM}title") or _text(info, "headline") or "")
        summary = entry.findtext(f"{ATOM}summary") or ""
        params = _params(info)
        severity = _fmi_severity(params.get("color"), _text(info, "severity"))

        out.append({
            "source": "FMI",
            "id": _text(info, "identifier") or "",
            "msg_type": _text(info, "msgType") or "",
            "title": title,
            "event": lang.translate(_text(info, "event") or ""),
            "description": summary.strip() or lang.translate(_text(info, "description") or ""),
            "instruction": lang.translate(_text(info, "instruction") or ""),
            "area": lang.translate(area_desc or area or ""),
            "area_raw": area_desc or area or "",
            "severity": severity,
            "severity_label": severity.capitalize(),
            "color": FMI_COLOR.get(severity, "#f5c518"),
            "onset": _text(info, "onset"),
            "expires": _text(info, "expires"),
            "effective": _text(info, "effective"),
            "certainty": _text(info, "certainty"),
            "urgency": _text(info, "urgency"),
            "params": params,
            "polygons": _polygons(info),
            "url": "https://en.ilmatieteenlaitos.fi/warnings",
        })
    return out


def _metalerts_title(p: dict[str, Any]) -> str:
    """met.no titles are Norwegian; the event field carries a stable key."""
    title = p.get("title") or ""
    event = (p.get("event") or "").lower()
    pretty = {
        "gale": "Gale",
        "storm": "Storm",
        "rain": "Heavy rain",
        "snow": "Heavy snow",
        "ice": "Black ice",
        "wind": "Strong wind",
        "temperature": "Extreme temperature",
        "fog": "Dense fog",
        "thunder": "Thunderstorm",
    }.get(event, title.split(",")[0] if title else "Weather warning")
    level = {"Moderate": "yellow", "Minor": "yellow"}.get(p.get("severity") or "", "yellow")
    return f"{pretty}, {level} level"


async def metalerts(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """MET Norway warnings (relevant over the sea and the far north)."""
    r = await client.get(METNO_URL)
    r.raise_for_status()
    data = r.json()

    out: list[dict[str, Any]] = []
    for feat in data.get("features", []):
        p = feat.get("properties") or {}
        geom = feat.get("geometry") or {}
        polys: list[list[list[float]]] = []
        if geom.get("type") == "Polygon":
            rings = [[list(c) for c in geom["coordinates"]]]
            polys = [[ring, ring[0]] for ring in rings if len(ring) >= 3]
        elif geom.get("type") == "MultiPolygon":
            polys = [[list(ring), list(ring[0])] for ring in geom["coordinates"] if len(ring) >= 3]

        sev = (p.get("severity") or "Minor").lower()
        out.append({
            "source": "MET Norway",
            "id": p.get("id") or "",
            "msg_type": p.get("type") or "",
            "title": lang.translate(_metalerts_title(p)),
            "event": (p.get("event") or "").capitalize(),
            "description": lang.translate(p.get("description") or ""),
            "instruction": lang.translate(p.get("instruction") or ""),
            "area": lang.translate(p.get("area") or ""),
            "area_raw": p.get("area") or "",
            "severity": "orange" if sev == "severe" else "yellow",
            "severity_label": p.get("severity") or "Minor",
            "color": lang.severity_color(p.get("severity")),
            "onset": (p.get("onset") or "").replace("Z", "+00:00") or None,
            "expires": None,
            "effective": (p.get("effective") or "").replace("Z", "+00:00") or None,
            "certainty": p.get("certainty"),
            "urgency": p.get("urgency"),
            "params": {},
            "polygons": polys,
            "url": p.get("web") or "https://api.met.no/weatherapi/metalerts/2.0/documentation",
        })
    return out
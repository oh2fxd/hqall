"""Fintraffic traffic notices as GeoRSS.

Feed: https://liikennetilanne.fintraffic.fi/rss

Geometry is Web Mercator metres (EPSG:3857) with the axis pair ordered
northing-first, i.e. "y x". Verified against known Finnish places
(Tampere ~ 61.495 N 23.773 E, Vuosaari harbour road ~ 60.245 N 25.146 E).

Finnish notice text is translated to English via lang.py.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any
from xml.etree import ElementTree as ET

import httpx

import lang

RSS_URL = "https://liikennetilanne.fintraffic.fi/rss"
GEORSS_NS = "{http://www.georss.org/georss}"

# WGS84 semi-major axis used by the spherical Mercator definition.
_R = 6378137.0


def mercator_to_wgs84(y: float, x: float) -> tuple[float, float] | None:
    """Inverse Web Mercator from (northing, easting) metres to (lat, lon)."""
    if abs(y) > 20_000_000 or abs(x) > 20_000_000:
        return None
    lon = math.degrees(x / _R)
    lat = math.degrees(math.atan(math.sinh(y / _R)))
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return lat, lon


def _geometry(item: ET.Element) -> tuple[list[list[float]], list[float] | None]:
    line = item.find(f"{GEORSS_NS}line")
    point = item.find(f"{GEORSS_NS}point")

    if line is not None and line.text:
        nums = [float(v) for v in line.text.split()]
        coords: list[list[float]] = []
        for i in range(0, len(nums) - 1, 2):
            ll = mercator_to_wgs84(nums[i], nums[i + 1])
            if ll:
                coords.append([round(ll[0], 6), round(ll[1], 6)])
        if len(coords) >= 2:
            # Notices with a line rarely carry a separate point, so give the map
            # a pin at the middle of the affected stretch.
            mid = coords[len(coords) // 2]
            return coords, [mid[0], mid[1]]

    if point is not None and point.text:
        nums = [float(v) for v in point.text.split()]
        if len(nums) >= 2:
            ll = mercator_to_wgs84(nums[0], nums[1])
            if ll:
                return [], [round(ll[0], 6), round(ll[1], 6)]
    return [], None


def _parse_pubdate(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip()
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z"):
        try:
            return datetime.strptime(s, fmt).astimezone(timezone.utc).isoformat(timespec="seconds")
        except ValueError:
            continue
    return None


def normalise(item: ET.Element) -> dict[str, Any] | None:
    title = (item.findtext("title") or "").strip()
    desc = (item.findtext("description") or "").strip()
    if not title:
        return None

    line, point = _geometry(item)
    if not line and not point:
        return None

    parts = lang.finnish_phrase_strip(title)
    body = " ".join(desc.split())
    kind = lang.traffic_kind(f"{title} {body}")

    url = (item.findtext("link") or "").strip()
    return {
        "id": (item.findtext("guid") or "").strip(),
        "kind": kind,
        "label": lang.traffic_label(kind),
        "title": lang.translate(title),
        "title_raw": title,
        "description": lang.translate(body),
        "description_raw": body,
        "road": parts["road"],
        "place": parts["place"],
        "time": _parse_pubdate(item.findtext("pubDate")),
        "url": url,
        "line": line or None,
        "point": point,
    }


async def fetch(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    r = await client.get(RSS_URL, headers={"Accept": "application/rss+xml"})
    r.raise_for_status()
    root = ET.fromstring(r.content)

    out: list[dict[str, Any]] = []
    for item in root.iter("item"):
        n = normalise(item)
        if n:
            out.append(n)

    out.sort(key=lambda x: x.get("time") or "", reverse=True)
    return out
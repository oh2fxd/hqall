"""APRS station positions via aprs.fi JSON API (requires free API key).

Endpoint: https://api.aprs.fi/api/get
  ?name=<comma separated callsigns>&what=loc&apikey=<key>&format=json

The key comes from the APRS_API_KEY environment variable. Free keys are
issued at https://aprs.fi/aprs-ui/account/register (usage is shared).

Also supports APRS-IS directly (no key needed) when APRS_IS_HOST is set.
"""
from __future__ import annotations

import math
import os
from typing import Any

import httpx

API_URL = "https://api.aprs.fi/api/get"

# Stations shown by default when no explicit callsign filter is configured.
DEFAULT_CALLSIGNS = [
    "OH2FXD", "OH2FXD-10", "OH2FXD-1", "OH2FXD-3",
    "OH6ADZ", "OH6KZP", "OH6KZS", "OH1GRW", "OH0TDX", "OH8TZH",
]


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _parse_comment(comment: str) -> dict[str, Any]:
    """Extract course/speed/altitude from an APRS position comment."""
    out: dict[str, Any] = {}
    import re

    m = re.search(r"(\d{3})\s*/\s*(\d{3})\s*/\s*(\d{3})\s*/\s*([A-Z])\s*(\d{3,6})", comment)
    if m:
        out["course"] = int(m.group(1))
        out["speed_kt"] = int(m.group(3)) * 1.14384
        sym = m.group(4)
        alt = int(m.group(5))
        out["altitude_ft"] = alt * 3.28084 if sym in ("A", "a") else None
        return out

    m = re.search(r"\b(\d{3})/(\d{3})\b", comment)
    if m:
        out["course"] = int(m.group(1))
        out["speed_kt"] = int(m.group(2)) * 1.14384
        return out

    m = re.search(r"([A-Za-z])(\d{3,6})", comment)
    if m and m.group(1) in "Aa":
        out["altitude_ft"] = int(m.group(2)) * 3.28084
    return out


def normalise(station: dict[str, Any], origin_lat: float, origin_lon: float,
              radius_nm: float) -> dict[str, Any] | None:
    try:
        lat = float(station.get("latitude"))
        lon = float(station.get("longitude"))
    except (TypeError, ValueError, KeyError):
        return None

    dist_km = _haversine_km(origin_lat, origin_lon, lat, lon)
    if dist_km > radius_nm * 1.852:
        return None

    comment = station.get("comment") or ""
    info = _parse_comment(comment)
    return {
        "callsign": station.get("name") or "",
        "lat": lat,
        "lon": lon,
        "distance_km": round(dist_km, 1),
        "bearing": _bearing(origin_lat, origin_lon, lat, lon),
        "comment": comment[:200],
        "symbol": station.get("symbol_table") or "",
        "path": (station.get("path") or "")[:64],
        **info,
    }


def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


async def fetch(client: httpx.AsyncClient, lat: float, lon: float,
                radius_nm: float) -> list[dict[str, Any]]:
    key = os.environ.get("APRS_API_KEY", "").strip()
    callsigns = [
        c.strip() for c in os.environ.get("APRS_CALLSIGNS", ",".join(DEFAULT_CALLSIGNS)).split(",")
        if c.strip()
    ]

    params = {
        "name": ",".join(callsigns),
        "what": "loc",
        "apikey": key,
        "format": "json",
    }
    r = await client.get(API_URL, params=params)
    r.raise_for_status()
    payload = r.json()

    if isinstance(payload, dict) and payload.get("result") != "ok":
        raise RuntimeError(payload.get("description") or "aprs.fi rejected the request")

    rows = payload.get("positions") or payload.get("stations") or []
    out: list[dict[str, Any]] = []
    for row in rows:
        st = normalise(row, lat, lon, radius_nm)
        if st:
            out.append(st)
    return out
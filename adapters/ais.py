"""AIS vessel positions via AISHub free Web Service.

Endpoint: https://data.aishub.net/ws.php
  ?username=<user>&format=1&output=json&compress=0&minlat=..&maxlat=..&minlon=..&maxlon=..

AIS_API_KEY env var holds the AISHub username (their API "key" is the account
name; accounts are free in exchange for sharing your own AIS feed).
"""
from __future__ import annotations

import json
import math
import os
from typing import Any

import httpx

API_URL = "https://data.aishub.net/ws.php"

NAV_STATUS = {
    0: "Under way using engine",
    1: "At anchor",
    2: "Not under command",
    3: "Restricted manoeuvrability",
    4: "Constrained by draught",
    5: "Moored",
    6: "Aground",
    7: "Engaged in fishing",
    8: "Under way sailing",
    9: "Reserved (HSC)",
    10: "Reserved (WIG)",
    11: "Towing astern",
    12: "Pushing ahead",
    13: "Reserved",
    14: "AIS-SART / MOB",
    15: "Undefined",
}


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _f(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def normalise(rec: dict[str, Any], lat: float, lon: float,
              radius_nm: float) -> dict[str, Any] | None:
    try:
        vlat = float(rec.get("lat"))
        vlon = float(rec.get("lon"))
    except (TypeError, ValueError):
        return None

    dist_km = _haversine_km(lat, lon, vlat, vlon)
    if dist_km > radius_nm * 1.852:
        return None

    sog_kn = _f(rec.get("speed"))
    if sog_kn is not None and sog_kn > 80:
        sog_kn *= 0.514444  # some AISHub feeds report m/s
    cog = _f(rec.get("course"))
    heading = _f(rec.get("heading"))
    status_raw = _f(rec.get("status"))

    return {
        "mmsi": str(rec.get("mmsi") or ""),
        "name": (rec.get("name") or "").strip(),
        "type": _f(rec.get("type")),
        "lat": vlat,
        "lon": vlon,
        "distance_km": round(dist_km, 1),
        "sog_kn": round(sog_kn, 1) if sog_kn is not None else None,
        "cog": round(cog) % 360 if cog is not None else None,
        "heading": round(heading) % 360 if heading is not None else None,
        "destination": (rec.get("destination") or "").strip(),
        "nav_status": NAV_STATUS.get(int(status_raw)) if status_raw is not None else "Unknown",
        "timestamp": rec.get("timestamp") or rec.get("received_utc") or "",
    }


async def fetch(client: httpx.AsyncClient, lat: float, lon: float,
                radius_nm: float) -> list[dict[str, Any]]:
    key = os.environ.get("AIS_API_KEY", "").strip()
    if not key:
        raise RuntimeError("AIS_API_KEY not set")

    # AISHub takes a bounding box; build one from the radius.
    dlat = radius_nm / 60.0
    dlon = radius_nm / (60.0 * max(0.01, math.cos(math.radians(lat))))
    params = {
        "username": key,
        "format": 1,
        "output": "json",
        "compress": 0,
        "minlat": round(lat - dlat, 4),
        "maxlat": round(lat + dlat, 4),
        "minlon": round(lon - dlon, 4),
        "maxlon": round(lon + dlon, 4),
    }
    r = await client.get(API_URL, params=params, timeout=30.0)
    r.raise_for_status()
    text = r.text.strip()
    if not text:
        raise RuntimeError("AISHub returned an empty body")

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        raise RuntimeError(f"AISHub returned non-JSON: {text[:120]}")

    if isinstance(payload, list) and payload and isinstance(payload[0], dict) \
            and payload[0].get("ERROR"):
        raise RuntimeError(payload[0].get("ERROR_MESSAGE") or "AISHub rejected the request")

    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("SHIP") or payload.get("vessels") or payload.get("data") or []
    else:
        rows = []

    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        v = normalise(row, lat, lon, radius_nm)
        if v:
            out.append(v)
    return out
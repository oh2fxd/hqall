"""ADS-B live aircraft via adsb.fi open data (free, no API key).

Primary:   https://opendata.adsb.fi/api/v3/lat/{lat}/lon/{lon}/dist/{nm}
Fallback 1: https://api.adsb.lol/v2/lat/{lat}/lon/{lon}/dist/{nm}
Fallback 2: https://opensky-network.org/api/states/all?lamin=..&lomin=..&lamax=..&lomax=..
            (anonymous access is rate limited but allowed; different JSON shape,
            so it is converted to the same record layout before returning)

Response shape (ADSBexchange-compatible): {"ac": [ {...}, ... ]}
"""
from __future__ import annotations

import math
from typing import Any

import httpx

OPENDATA_URL = "https://opendata.adsb.fi/api/v3"
LOL_URL = "https://api.adsb.lol/v2"
OPENSKY_URL = "https://opensky-network.org/api/states/all"

MAX_ALT_FT = 45000.0


async def fetch(client: httpx.AsyncClient, lat: float, lon: float, dist_nm: float) -> list[dict]:
    last_err: Exception | None = None
    for base in (OPENDATA_URL, LOL_URL):
        url = f"{base}/lat/{lat:.4f}/lon/{lon:.4f}/dist/{dist_nm:.0f}"
        try:
            r = await client.get(url)
            r.raise_for_status()
            data = r.json()
        except Exception as exc:  # noqa: BLE001 - try the next provider
            last_err = exc
            continue
        ac = data.get("ac") if isinstance(data, dict) else None
        if ac is not None:
            return ac

    # OpenSky last: anonymous quota is small, but it covers the whole world.
    try:
        return await _fetch_opensky(client, lat, lon, dist_nm)
    except Exception as exc:  # noqa: BLE001
        last_err = exc

    if last_err:
        raise last_err
    return []


async def _fetch_opensky(
    client: httpx.AsyncClient, lat: float, lon: float, dist_nm: float
) -> list[dict]:
    """OpenSky uses a bounding box instead of a radius."""
    dlat = dist_nm / 60.0
    dlon = dist_nm / max(1e-6, 60.0 * math.cos(math.radians(lat)))
    r = await client.get(
        OPENSKY_URL,
        params={
            "lamin": round(max(-90.0, lat - dlat), 4),
            "lomin": round(lon - dlon, 4),
            "lamax": round(min(90.0, lat + dlat), 4),
            "lomax": round(lon + dlon, 4),
        },
    )
    r.raise_for_status()
    states = r.json().get("states") or []
    out: list[dict] = []
    for st in states:
        ac = _opensky_to_ac(st)
        if ac:
            out.append(ac)
    return out


def _opensky_to_ac(st: list) -> dict | None:
    """Convert one OpenSky state vector (17 fields) to the adsb.fi layout."""
    try:
        icao24 = st[0]
        callsign = (st[1] or "").strip()
        lon = st[5]
        lat = st[6]
        baro_alt_m = st[7]
        on_ground = bool(st[8])
        velocity_ms = st[9]
        true_track = st[10]
        vertical_rate_ms = st[11]
        geo_alt_m = st[13]
        squawk = st[14]
    except (IndexError, TypeError):
        return None
    if icao24 is None or lat is None or lon is None:
        return None

    def ft(metres: Any) -> Any:
        if metres is None:
            return None
        return round(float(metres) * 3.28084)

    return {
        "hex": icao24,
        "flight": callsign,
        "r": "",
        "t": "",
        "lat": round(float(lat), 5),
        "lon": round(float(lon), 5),
        "alt_baro": "ground" if on_ground else ft(baro_alt_m),
        "alt_geom": ft(geo_alt_m),
        "gs": round(float(velocity_ms) * 1.94384, 1) if velocity_ms is not None else None,
        "track": round(float(true_track), 1) if true_track is not None else None,
        "baro_rate": round(float(vertical_rate_ms) * 196.85, 0)
        if vertical_rate_ms is not None
        else None,
        "squawk": (squawk or "").strip() if isinstance(squawk, str) else "",
        "category": "",
    }


def _num(v: Any) -> float | None:
    if v is None or isinstance(v, str):
        if v == "ground":
            return 0.0
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def normalise(ac: dict[str, Any], max_alt_ft: float = MAX_ALT_FT) -> dict[str, Any] | None:
    """Convert one upstream aircraft record to the app's shape."""
    lat = _num(ac.get("lat"))
    lon = _num(ac.get("lon"))
    if lat is None or lon is None:
        return None

    alt_raw = ac.get("alt_baro")
    on_ground = alt_raw == "ground"
    alt_ft = 0.0 if on_ground else _num(alt_raw)
    if alt_ft is None:
        # Fall back to geometric altitude so aircraft are not dropped.
        alt_ft = _num(ac.get("alt_geom")) or 0.0
    if alt_ft > max_alt_ft:
        return None

    return {
        "hex": (ac.get("hex") or "").strip(),
        "flight": (ac.get("flight") or "").strip(),
        "registration": (ac.get("r") or "").strip(),
        "type": (ac.get("t") or "").strip(),
        "desc": (ac.get("desc") or "").strip(),
        "lat": lat,
        "lon": lon,
        "altitude_ft": int(round(alt_ft)),
        "on_ground": bool(on_ground),
        "speed_kt": _num(ac.get("gs")),
        "track": _num(ac.get("track")),
        "heading": _num(ac.get("true_heading") or ac.get("mag_heading")),
        "vertical_rate": _num(ac.get("baro_rate")),
        "squawk": (ac.get("squawk") or "").strip(),
        "category": (ac.get("category") or "").strip(),
        "signal": (ac.get("rssi") and int(ac["rssi"]) or None),
        "seen": _num(ac.get("seen_pos")),
    }
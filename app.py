#!/usr/bin/env python3
"""hqall — single-page situational awareness map for Finland.

Fullscreen Leaflet map with live layers:
  * ADS-B aircraft      opendata.adsb.fi (free, no key), fallback api.adsb.lol
  * Real cloud cover    RainViewer satellite/radar tiles (free)
  * APRS stations       APRS-IS stream (adapters/aprs_is.py) or aprs.fi JSON API
  * AIS vessels         AISHub ws.php (free account, AIS_API_KEY env)
  * Tilannehuone        tilannehuone.fi halytysmap.php embedded JSON array
  * Traffic alerts      liikennetilanne.fintraffic.fi/rss GeoRSS
  * Country borders     bundled Natural Earth 110m outlines, no runtime fetch
  * Weather             Open-Meteo current conditions for a chosen city

Also served but not shown in the UI: /api/alerts (FMI CAP Atom feed +
MET Norway metalerts) is kept as a data source for anything that wants the
raw warnings, while the map itself stays warning-free on purpose.

All upstream text is Finnish (Tilannehuone, Fintraffic RSS) or Norwegian
(metalerts); a translation layer in lang.py renders everything in English.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from adapters import (
    adsb,
    ais,
    aprs,
    aprs_is,
    cloudcover,
    fintraffic,
    fmi,
    openmeteo,
    rainviewer,
    tilannehuone,
)
from cache import TTLCache

HERE = os.path.dirname(os.path.abspath(__file__))

# "Self-hosted, update-safe" marker: the only hardcoded version in the repo.
# Bump it together with the git tag (vX.Y.Z) and the GitHub update banner
# (enabled via HQALL_REPO) will tell every running instance to upgrade.
VERSION = "1.0.0"

logger = logging.getLogger("hqall")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)

APP_STARTED = time.monotonic()


def validate_runtime_settings() -> dict[str, list[str]]:
    """Return deployment warnings for optional integrations.

    Most data sources are intentionally optional; the app should remain usable in
    a minimal local setup, but operators still benefit from a clear startup log of
    which integrations are unavailable.
    """
    warnings: list[str] = []
    if not os.environ.get("AIS_API_KEY"):
        warnings.append("AIS_API_KEY not set; vessel layer disabled")
    if not os.environ.get("APRS_CALLSIGN"):
        warnings.append("APRS_CALLSIGN not set; APRS-IS login will be anonymous/default when allowed")
    if not os.environ.get("APRS_PASSCODE") and not os.environ.get("APRS_CALLSIGN"):
        warnings.append("APRS_PASSCODE not set; APRS login will use the generated default passcode when possible")
    if not os.environ.get("HQALL_REPO"):
        warnings.append("HQALL_REPO not set; version update check disabled")
    if os.environ.get("HQALL_CONTACT"):
        warnings.append(f"HQALL_CONTACT set to {os.environ['HQALL_CONTACT']}")
    return {"warnings": warnings}


def _normalise_float(value: Any, default: float, lo: float, hi: float) -> float:
    """Coerce a JSON value to a finite float and clamp it to a valid range."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(f):
        return default
    return _clamp(f, lo, hi)

# owner/repo, e.g. "oh2fxd/hqall" — when set, /api/version asks
# api.github.com how far ahead the published tags are.
HQALL_REPO = os.environ.get("HQALL_REPO", "").strip().lstrip("/")

@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = validate_runtime_settings()
    logger.info("hqall starting: version=%s env_warnings=%d", VERSION, len(settings["warnings"]))
    for msg in settings["warnings"]:
        logger.warning(msg)
    aprs_stream.start()
    try:
        yield
    finally:
        await aprs_stream.stop()
        await client.aclose()
        logger.info("hqall shutdown complete")


app = FastAPI(
    title="hqall",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)


@app.middleware("http")
async def _request_middleware(request: Request, call_next):
    started = time.monotonic()
    try:
        response = await call_next(request)
        elapsed_ms = round((time.monotonic() - started) * 1000, 1)
        if response.status_code >= 500:
            logger.warning("request_failed status=%s path=%s elapsed_ms=%s", response.status_code, request.url.path, elapsed_ms)
        return response
    except Exception:
        elapsed_ms = round((time.monotonic() - started) * 1000, 1)
        logger.exception("unhandled_request_error path=%s elapsed_ms=%s", request.url.path, elapsed_ms)
        return JSONResponse(
            {"ok": False, "error": "internal_server_error", "path": request.url.path},
            status_code=500,
        )


client = httpx.AsyncClient(
    timeout=httpx.Timeout(12.0, connect=8.0),
    follow_redirects=True,
    headers={"User-Agent": f"hqall/1.0 ({os.environ.get('HQALL_CONTACT', 'local')})"},
    limits=httpx.Limits(max_connections=24, max_keepalive_connections=12),
)

cache = TTLCache()

# One long lived APRS-IS connection for the whole process (no key needed for the
# protocol itself, but a registered callsign is required to receive packets).
aprs_stream = aprs_is.AprsStream()

# Live tracker state (position markers on the map).
STATE: dict[str, Any] = {
    "origin": {"lat": 62.60, "lon": 25.30, "label": "Central Finland"},
    "aircraft": [],
    "aprs": [],
    "ais": [],
    "incidents": [],
    "traffic": [],
    "weather": None,
    "alerts": [],
}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _ver_tuple(v: str) -> tuple[int, int, int]:
    """'v1.2.3-rc1' / '1.2.3' -> (1,2,3); anything else -> () so it is skipped."""
    match = __import__("re").match(r"v?(\d+)\.(\d+)\.(\d+)", str(v).strip())
    return (int(match[1]), int(match[2]), int(match[3])) if match else ()


_VERSION_CACHE_KEY = "github:latest:" + (HQALL_REPO or "none")


@app.get("/api/version")
async def version_endpoint() -> dict[str, Any]:
    """Installed version + whether the GitHub repo publishes anything newer.

    Update detection is opt-in: set HQALL_REPO=owner/repo and tag releases as
    vX.Y.Z. Without the env var the check is skipped (an air-gapped RPi is not
    nagged). The GitHub answer is cached for an hour so the banner never
    causes per-session latency or rate-limit noise.
    """
    base: dict[str, Any] = {
        "version": VERSION,
        "repo": HQALL_REPO or None,
        "latest": None,
        "update_available": False,
        "release_url": None,
        "reason": None,
    }
    if not HQALL_REPO:
        base["reason"] = "HQALL_REPO not set"
        return {"ok": True, **base}

    cached = cache.get(_VERSION_CACHE_KEY)
    if cached is not None:
        return {"ok": True, **base, **cached}

    headers = {"Accept": "application/vnd.github+json", "User-Agent": f"hqall/{VERSION}"}
    latest: str | None = None
    try:
        r = await client.get(
            f"https://api.github.com/repos/{HQALL_REPO}/releases/latest",
            headers=headers, timeout=8.0,
        )
        if r.status_code == 404:
            # no releases yet — read the git tags instead (git push --tags gives an
            # update signal without needing a GitHub release page)
            tr = await client.get(f"https://api.github.com/repos/{HQALL_REPO}/tags", headers=headers, timeout=8.0)
            if tr.status_code == 200:
                latest = next(
                    (t.get("name") for t in tr.json()
                     if _ver_tuple(t.get("name") or "")),
                    None,
                )
            else:
                r = tr  # let the generic branch report the failure reason-less
        else:
            r.raise_for_status()
            latest = (r.json() or {}).get("tag_name")
    except Exception as exc:  # noqa: BLE001 - never take the map down for this
        base["reason"] = f"update check failed ({exc})"
        return {"ok": True, **base}

    update = bool(latest) and _ver_tuple(latest) > _ver_tuple(VERSION)
    info = {
        "latest": latest,
        "update_available": update,
        "release_url": f"https://github.com/{HQALL_REPO}/releases/latest" if latest else None,
        "reason": None,
    }
    cache.put(_VERSION_CACHE_KEY, info, ttl=3600)
    return {"ok": True, **base, **info}


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(os.path.join(HERE, "static", "index.html"))


@app.get("/api/health")
async def health() -> dict[str, Any]:
    aprs_src = f"aprs-is {aprs_stream.host or 'connecting'} ({aprs_stream.status})"
    if os.environ.get("APRS_API_KEY") and not aprs_stream.packets:
        aprs_src += " | aprs.fi JSON API as fallback"
    return {
        "ok": True,
        "uptime_s": round(time.monotonic() - APP_STARTED, 1),
        "sources": {
            "adsb": "opendata.adsb.fi",
            "clouds": "api.rainviewer.com",
            "cloudcover": "gibs.earthdata.nasa.gov (MODIS cloud fraction)",
            "aprs": aprs_src,
            "ais": "data.aishub.net" if os.environ.get("AIS_API_KEY") else "disabled (no AIS_API_KEY)",
            "incidents": "tilannehuone.fi",
            "traffic": "liikennetilanne.fintraffic.fi",
            "weather": "api.open-meteo.com",
            "alerts": "alerts.fmi.fi + api.met.no",
        },
        "cache_entries": len(cache._d),
    }


@app.get("/api/ready")
async def ready() -> dict[str, Any]:
    return {
        "ok": True,
        "ready": True,
        "uptime_s": round(time.monotonic() - APP_STARTED, 1),
        "cache_entries": len(cache._d),
    }


# --------------------------------------------------------------------------
# ADS-B
# --------------------------------------------------------------------------

@app.get("/api/aircraft")
async def aircraft(
    lat: float | None = None,
    lon: float | None = None,
    radius: float = Query(150.0, ge=5, le=250),
    max_alt: float | None = None,
) -> Any:
    lat = STATE["origin"]["lat"] if lat is None else _clamp(lat, -90, 90)
    lon = STATE["origin"]["lon"] if lon is None else lon
    max_alt = adsb.MAX_ALT_FT if max_alt is None else max_alt

    key = f"adsb:{lat:.2f}:{lon:.2f}:{radius:.0f}:{max_alt:.0f}"
    cached = cache.get(key)
    if cached:
        return cached

    try:
        raw = await adsb.fetch(client, lat, lon, radius)
    except Exception as exc:  # noqa: BLE001 - surfaced to the client as JSON
        return JSONResponse({"error": f"adsb: {exc}", "aircraft": []}, status_code=502)

    out = []
    for ac in raw:
        a = adsb.normalise(ac, max_alt)
        if a:
            out.append(a)

    payload = {"now": time.time(), "aircraft": out, "count": len(out)}
    cache.put(key, payload, ttl=4.0)
    return payload


# --------------------------------------------------------------------------
# APRS
# --------------------------------------------------------------------------

@app.get("/api/aprs")
async def aprs_endpoint(
    lat: float | None = None,
    lon: float | None = None,
    radius: float = Query(120.0, ge=5, le=250),
    moving: int = Query(1, ge=0, le=1),
    rf: int = Query(1, ge=0, le=1),
    symbols: str = Query(""),
    exclude: str = Query(""),
    ssid: str = Query(""),
) -> Any:
    lat = STATE["origin"]["lat"] if lat is None else lat
    lon = STATE["origin"]["lon"] if lon is None else lon

    # Live APRS-IS stream, default endpoint aprs.to: it forwards packets to
    # unverified logins, so no key and no registration are needed. Keep the
    # area filter pointing at what the map is looking at.
    aprs_stream.area(lat, lon, radius)
    return aprs_stream.snapshot(
        lat,
        lon,
        radius,
        moving_only=bool(moving),
        rf_only=bool(rf),
        symbols=[s for s in (spec.strip() for spec in symbols.split(",")) if s],
        exclude=[s for s in (spec.strip() for spec in exclude.split(",")) if s],
        ssid=[s for s in (spec.strip() for spec in ssid.split(",")) if s],
    )

    key = f"aprs:{lat:.1f}:{lon:.1f}:{radius:.0f}"
    cached = cache.get(key)
    if cached:
        return cached

    try:
        stations = await aprs.fetch(client, lat, lon, radius)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "reason": str(exc), "stations": []}, status_code=502)

    payload = {
        "ok": True,
        "source": "aprs.fi",
        "now": time.time(),
        "stations": stations,
        "count": len(stations),
    }
    cache.put(key, payload, ttl=60.0)
    return payload


# --------------------------------------------------------------------------
# AIS
# --------------------------------------------------------------------------

@app.get("/api/ais")
async def ais_endpoint(
    lat: float | None = None,
    lon: float | None = None,
    radius: float = Query(120.0, ge=5, le=250),
) -> Any:
    if not os.environ.get("AIS_API_KEY"):
        return {"ok": False, "reason": "AIS_API_KEY not set", "vessels": []}

    lat = STATE["origin"]["lat"] if lat is None else lat
    lon = STATE["origin"]["lon"] if lon is None else lon

    key = f"ais:{lat:.1f}:{lon:.1f}:{radius:.0f}"
    cached = cache.get(key)
    if cached:
        return cached

    try:
        vessels = await ais.fetch(client, lat, lon, radius)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "reason": str(exc), "vessels": []}, status_code=502)

    payload = {"ok": True, "now": time.time(), "vessels": vessels, "count": len(vessels)}
    cache.put(key, payload, ttl=300.0)
    return payload


# --------------------------------------------------------------------------
# Tilannehuone incidents (Finnish, translated to English)
# --------------------------------------------------------------------------

@app.get("/api/incidents")
async def incidents(minutes: float = Query(45.0, ge=1, le=720)) -> Any:
    """Tilannehuone incidents, but only the ones that are actually current.

    The upstream map feed keeps yesterday's reports in the list, which is noise on
    a live map, so anything older than `minutes` is dropped here and each item
    gets an `age_min` for the fade-out in the UI.
    """
    key = f"incidents:{minutes:.0f}"
    cached = cache.get(key)
    if cached:
        return cached

    try:
        items = await tilannehuone.fetch(client)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            {"error": f"tilannehuone: {exc}", "incidents": []}, status_code=502
        )

    now = datetime.now(timezone.utc)
    fresh: list[dict[str, Any]] = []
    for item in items:
        stamp = item.get("time")
        age = None
        if stamp:
            try:
                moment = datetime.fromisoformat(stamp)
                if moment.tzinfo is None:
                    moment = moment.astimezone()
                age = round((now - moment.astimezone(timezone.utc)).total_seconds() / 60.0, 1)
            except ValueError:
                age = None
        if age is not None and age > minutes:
            continue
        row = dict(item)
        row["age_min"] = age
        fresh.append(row)

    fresh.sort(key=lambda r: (r.get("age_min") is None, r.get("age_min") or 0.0))
    payload = {"now": time.time(), "window_min": minutes, "incidents": fresh,
               "count": len(fresh)}
    cache.put(key, payload, ttl=20.0)
    return payload


# --------------------------------------------------------------------------
# Fintraffic traffic alerts
# --------------------------------------------------------------------------

@app.get("/api/traffic")
async def traffic_alerts() -> Any:
    cached = cache.get("traffic")
    if cached:
        return cached

    try:
        items = await fintraffic.fetch(client)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            {"error": f"fintraffic: {exc}", "alerts": []}, status_code=502
        )

    payload = {"now": time.time(), "alerts": items, "count": len(items)}
    cache.put("traffic", payload, ttl=120.0)
    return payload


# --------------------------------------------------------------------------
# Weather + alerts
# --------------------------------------------------------------------------

@app.get("/api/weather")
async def weather(lat: float | None = None, lon: float | None = None) -> Any:
    lat = STATE["origin"]["lat"] if lat is None else _clamp(lat, -90, 90)
    lon = STATE["origin"]["lon"] if lon is None else lon

    key = f"weather:{lat:.2f}:{lon:.2f}"
    cached = cache.get(key)
    if cached:
        return cached

    try:
        w = await openmeteo.current(client, lat, lon)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": f"open-meteo: {exc}"}, status_code=502)

    cache.put(key, w, ttl=300.0)
    return w


@app.get("/api/alerts")
async def alerts(lat: float | None = None, lon: float | None = None) -> Any:
    lat = STATE["origin"]["lat"] if lat is None else lat
    lon = STATE["origin"]["lon"] if lon is None else lon

    cached = cache.get("alerts")
    if cached:
        return cached

    results = await asyncio.gather(
        fmi.warnings(client), fmi.metalerts(client), return_exceptions=True
    )

    items: list[dict[str, Any]] = []
    errors: list[str] = []
    for r in results:
        if isinstance(r, Exception):
            errors.append(str(r))
        else:
            items.extend(r)

    payload = {"now": time.time(), "alerts": items, "count": len(items), "errors": errors}
    cache.put("alerts", payload, ttl=300.0)
    return payload


# --------------------------------------------------------------------------
# Cloud layer tile proxy
# --------------------------------------------------------------------------

@app.get("/api/clouds")
async def clouds() -> Any:
    cached = cache.get("clouds")
    if cached:
        return cached

    try:
        manifest = await rainviewer.rainviewer_manifest(client)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": f"rainviewer: {exc}"}, status_code=502)

    cache.put("clouds", manifest, ttl=120.0)
    return manifest


@app.get("/api/cloudcover")
async def cloud_cover_endpoint() -> Any:
    """Cloud deck from NASA GIBS, as opposed to the rain in it from RainViewer.

    The images are daily, so the answer only changes a few times a day and is
    cached for an hour; the tile URLs it returns carry a timestamp, which is
    what makes the browser pick up the next pass when it appears.
    """
    cached = cache.get("cloudcover")
    if cached:
        return cached

    try:
        spec = await cloudcover.cloud_cover(client)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": f"gibs: {exc}"}, status_code=502)

    # a service that answers but is a day behind is not worth caching for long
    cache.put("cloudcover", spec, ttl=900.0 if spec.get("ok") else 120.0)
    return spec


# --------------------------------------------------------------------------
# origin (map centre) so the UI can follow a location
# --------------------------------------------------------------------------

@app.post("/api/origin")
async def set_origin(payload: dict[str, Any]) -> Any:
    lat = _normalise_float(payload.get("lat", STATE["origin"]["lat"]), STATE["origin"]["lat"], -90, 90)
    lon = _normalise_float(payload.get("lon", STATE["origin"]["lon"]), STATE["origin"]["lon"], -180, 180)
    label = str(payload.get("label", STATE["origin"].get("label", "")))[:80]
    STATE["origin"] = {"lat": lat, "lon": lon, "label": label}
    # the APRS-IS server side filter follows the map centre
    aprs_stream.area(STATE["origin"]["lat"], STATE["origin"]["lon"])
    aprs_stream.push_filter()
    return {"ok": True, "origin": STATE["origin"]}


app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("HQALL_HOST", "0.0.0.0")
    port = int(os.environ.get("HQALL_PORT", "8077"))
    uvicorn.run("app:app", host=host, port=port, reload=False)
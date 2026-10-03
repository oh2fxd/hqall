"""Open-Meteo current weather and hourly forecast (free, no API key)."""
from __future__ import annotations

from typing import Any

import httpx

import lang

CURRENT_URL = "https://api.open-meteo.com/v1/forecast"

CURRENT_FIELDS = [
    "temperature_2m", "apparent_temperature", "relative_humidity_2m",
    "precipitation", "snowfall", "weather_code", "cloud_cover",
    "pressure_msl", "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m",
    "visibility", "is_day",
]

HOURLY_FIELDS = [
    "temperature_2m", "weather_code", "cloud_cover", "precipitation",
    "wind_speed_10m", "visibility",
]


async def current(client: httpx.AsyncClient, lat: float, lon: float) -> dict[str, Any]:
    r = await client.get(
        CURRENT_URL,
        params={
            "latitude": round(lat, 4),
            "longitude": round(lon, 4),
            "current": ",".join(CURRENT_FIELDS),
            "hourly": ",".join(HOURLY_FIELDS),
            "forecast_days": 2,
            "timezone": "UTC",
            "wind_speed_unit": "kn",
        },
    )
    r.raise_for_status()
    d = r.json()

    cur = d.get("current") or {}
    code = cur.get("weather_code")
    desc, kind = lang.wmo_weather(code)

    return {
        "lat": d.get("latitude"),
        "lon": d.get("longitude"),
        "timezone": d.get("timezone"),
        "elevation_m": d.get("elevation"),
        "time": cur.get("time"),
        "temperature_c": cur.get("temperature_2m"),
        "apparent_temperature_c": cur.get("apparent_temperature"),
        "humidity": cur.get("relative_humidity_2m"),
        "cloud_cover": cur.get("cloud_cover"),
        "precipitation_mm": cur.get("precipitation"),
        "snowfall_cm": cur.get("snowfall"),
        "pressure_hpa": cur.get("pressure_msl"),
        "wind_kt": cur.get("wind_speed_10m"),
        "wind_gusts_kt": cur.get("wind_gusts_10m"),
        "wind_direction": cur.get("wind_direction_10m"),
        "visibility_m": cur.get("visibility"),
        "is_day": cur.get("is_day"),
        "weather_code": code,
        "weather": desc,
        "weather_kind": kind,
    }


async def hourly(client: httpx.AsyncClient, lat: float, lon: float) -> dict[str, Any]:
    r = await client.get(
        CURRENT_URL,
        params={
            "latitude": round(lat, 4),
            "longitude": round(lon, 4),
            "hourly": ",".join(HOURLY_FIELDS),
            "forecast_days": 1,
            "timezone": "UTC",
            "wind_speed_unit": "kn",
        },
    )
    r.raise_for_status()
    h = r.json().get("hourly") or {}
    times = h.get("time") or []
    out = []
    for i, t in enumerate(times[:24]):
        code = h.get("weather_code", [None] * len(times))[i]
        desc, kind = lang.wmo_weather(code)
        out.append({
            "time": t,
            "temperature_c": h.get("temperature_2m", [None] * len(times))[i],
            "cloud_cover": h.get("cloud_cover", [None] * len(times))[i],
            "precipitation_mm": h.get("precipitation", [None] * len(times))[i],
            "wind_kt": h.get("wind_speed_10m", [None] * len(times))[i],
            "weather": desc,
            "weather_kind": kind,
        })
    return {"hours": out}
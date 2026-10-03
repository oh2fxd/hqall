"""RainViewer radar/satellite manifest -> ready-to-use Leaflet tile layers.

Free, no key. The manifest lists recent radar frames plus nowcast (forecast)
frames, which lets the frontend play an animated cloud/precipitation loop.

Tile URL layout (verified against the live service):
    {host}{path}/256/{z}/{x}/{y}/{colour}/{smooth}/1_{aerial}_map.png

The tile size is a fixed path segment in front of the zoom level, not a
parameter after the coordinates, so the older ".../{z}/{x}/{y}/256/..." guess
answered 404 for every frame. Real frames only exist up to zoom 7, above which
the service returns the same empty placeholder, hence MAX_NATIVE_ZOOM.
"""
from __future__ import annotations

import time
from typing import Any

import httpx

MANIFEST_URL = "https://api.rainviewer.com/public/weather-maps.json"

# {z}/{x}/{y} are doubled here so they survive .format() and reach Leaflet
# literally for it to substitute per tile.
TILE_TMPL = "/256/{{z}}/{{x}}/{{y}}/{color}/1_1.png"

# RainViewer colour schemes; 4_8 is the standard international radar palette,
# 8_0 is the satellite-friendly variant. The colour has no visible effect on
# the served tiles, it is kept because the path segment is mandatory.
COLOR_SCHEME = "4_8"

# Frames above this zoom are a placeholder image, so Leaflet is told not to
# scale them and the map zooms the layer itself at the native resolution.
MAX_NATIVE_ZOOM = 7


async def rainviewer_manifest(client: httpx.AsyncClient) -> dict[str, Any]:
    r = await client.get(MANIFEST_URL)
    r.raise_for_status()
    data = r.json()

    host = data.get("host", "https://tilecache.rainviewer.com").rstrip("/")
    radar = data.get("radar") or {}
    sat = data.get("satellite") or {}

    def frames(seq: list[dict], kind: str) -> list[dict[str, Any]]:
        out = []
        for f in seq:
            ts = int(f["time"])
            out.append({
                "time": ts,
                "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)),
                # {z}/{x}/{y} stay literal: Leaflet fills them per tile.
                "url": f"{host}{f['path']}{TILE_TMPL.format(color=COLOR_SCHEME)}",
                "kind": kind,
            })
        out.sort(key=lambda x: x["time"])
        return out

    past = frames(radar.get("past") or [], "past")
    nowcast = frames(radar.get("nowcast") or [], "nowcast")
    satellite = frames(sat.get("infrared") or [], "satellite")

    all_frames = past + nowcast

    return {
        "generated": int(data.get("generated") or time.time()),
        "generated_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                       time.gmtime(data.get("generated") or time.time())),
        "host": host,
        "color_scheme": COLOR_SCHEME,
        "max_native_zoom": MAX_NATIVE_ZOOM,
        "frame_count": len(all_frames),
        "frames": all_frames,
        "past_count": len(past),
        "nowcast_count": len(nowcast),
        "satellite_count": len(satellite),
        "satellite": satellite,
        "attribution": "RainViewer",
    }
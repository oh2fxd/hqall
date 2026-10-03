"""NASA GIBS cloud fraction as a ready-to-use Leaflet tile layer.

Why this exists: RainViewer is *precipitation* (radar reflectivity). It draws
nothing when the sky is overcast but dry, which is exactly when the grey
matters. MODIS cloud fraction is the cloud deck itself rather than the rain in
it: white where the sky is covered, dark where it is nearly clear, and
transparent outside the satellite pass.

NASA GIBS Worldview tile service, no key, no registration, verified from this
sandbox on 2026-10-02:

  capabilities  https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/1.0.0/WMTSCapabilities.xml
  tiles         .../best/{product}/default/{time}/{tms}/{z}/{y}/{x}.png
                Leaflet takes x before y, GIBS the other way round.
  domains       .../best/1.0.0/{product}/default/{tms}/all/all.xml
                a <Domain> of start/end/period triples; the last end date is
                the newest image, which is how the layer finds out what exists
                instead of guessing an hour and hoping for a tile.

Details that cost time to find:

  * {time} must be a full ISO timestamp. A bare date answers 200 with an
    empty tile, which looks like a working layer that shows nothing.
  * these layers only publish the GoogleMapsCompatible_Level6 tile matrix set,
    so there is no real data above zoom 6 and the browser scales it up. The
    "best" imagery arrives tile by tile, so a fresh image fills in over about
    half an hour; a transparent tile is "the pass has not got here yet", not
    an error.
  * MODIS Aqua and Terra pass a point about three hours apart, and each has a
    day and a night product, so stacking the two platforms covers Finland
    around the clock.
  * MODIS_Aqua_Cloud_Fraction_Night is left out on purpose: it is rendered in
    a red and magenta colour table, and red over a map reads as a fire.
    The other three come back as white with the cloud fraction in the alpha
    channel, so a little desaturation is enough to keep them neutral.
"""
from __future__ import annotations

import datetime as dt
import math
import re
import time
from typing import Any

import httpx

BASE = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best"
TILE_MATRIX_SET = "GoogleMapsCompatible_Level6"
MAX_NATIVE_ZOOM = 6

DOMAIN_URL = f"{BASE}/1.0.0/{{product}}/default/{TILE_MATRIX_SET}/all/all.xml"

# bottom of the stack first, so a fresher image paints over an older one
LAYERS = (
    "MODIS_Terra_Cloud_Fraction_Day",
    "MODIS_Terra_Cloud_Fraction_Night",
    "MODIS_Aqua_Cloud_Fraction_Day",
)

# midday, safely inside the daily image whatever the pass time was
TIME_OF_DAY = "T12:00:00Z"


def tile_url(product: str, stamp: str, z: int, x: int, y: int) -> str:
    """One concrete tile, for the health check and for tests."""
    return f"{BASE}/{product}/default/{stamp}/{TILE_MATRIX_SET}/{z}/{y}/{x}.png"


def leaflet_url(product: str, stamp: str) -> str:
    """A tile template for L.tileLayer: {z}, {y} and {x} stay in it."""
    return f"{BASE}/{product}/default/{stamp}/{TILE_MATRIX_SET}/{{z}}/{{y}}/{{x}}.png"


def tile_xy(lat: float, lon: float, z: int) -> tuple[int, int]:
    n = 2**z
    x = int((lon + 180.0) / 360.0 * n)
    r = math.radians(lat)
    y = int((1.0 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2.0 * n)
    return x, y


def newest_image_date(domain_xml: str) -> str | None:
    """The last date in a WMTS time domain, i.e. the freshest image.

    The domain is a comma separated list of start/end/period triples such as
    2025-07-05/2026-10-02/P1D, so the end date of the final triple is the
    newest day the layer has imagery for.
    """
    m = re.search(r"<Domain>([^<]+)</Domain>", domain_xml)
    if not m:
        return None
    ranges = [r.strip() for r in m.group(1).split(",") if r.strip()]
    if not ranges:
        return None
    end = ranges[-1].split("/")
    if len(end) < 2 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", end[1]):
        return None
    return end[1]


def _age_days(date: str, now: float) -> int:
    try:
        then = dt.datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=dt.UTC)
    except ValueError:
        return 99
    ref = dt.datetime.fromtimestamp(now, dt.UTC)
    return max(0, (ref.date() - then.date()).days)


async def cloud_cover(client: httpx.AsyncClient, now: float | None = None) -> dict[str, Any]:
    """Resolve the cloud fraction layers for the freshest available image.

    Only the small Domains document is fetched, once per product and then
    cached by the caller for an hour, because the images themselves are daily.
    """
    started = time.time()
    now = time.time() if now is None else now
    layers: list[dict[str, Any]] = []
    dated: list[dict[str, Any]] = []

    for product in LAYERS:
        entry: dict[str, Any] = {"product": product, "label": _label(product)}
        try:
            r = await client.get(DOMAIN_URL.format(product=product))
            r.raise_for_status()
            date = newest_image_date(r.text)
        except Exception as exc:  # noqa: BLE001 - one bad product is not fatal
            entry["error"] = str(exc)
            dated.append(entry)
            continue
        if not date:
            entry["error"] = "no time domain"
            dated.append(entry)
            continue
        stamp = f"{date}{TIME_OF_DAY}"
        entry.update({
            "date": date,
            "stamp": stamp,
            "iso": f"{date}T12:00:00Z",
            "age_h": _age_days(date, now) * 24,
            "url": leaflet_url(product, stamp),
        })
        dated.append(entry)
        if _age_days(date, now) <= 2:      # older than that is a broken service
            layers.append(entry)

    if not layers:
        return {
            "ok": False,
            "source": "NASA GIBS",
            "reason": "no recent cloud fraction image",
            "checked": dated,
            "elapsed_ms": int((time.time() - started) * 1000),
        }

    return {
        "ok": True,
        "source": "NASA GIBS",
        "layers": layers,
        "newest_date": max(l["date"] for l in layers),
        "max_native_zoom": MAX_NATIVE_ZOOM,
        "tile_matrix_set": TILE_MATRIX_SET,
        "checked": dated,
        "elapsed_ms": int((time.time() - started) * 1000),
        "legend": "white and solid is full cover, dark grey is nearly clear, "
                  "transparent is outside the satellite pass",
        "attribution": "NASA EOSDIS GIBS",
    }


def _label(product: str) -> str:
    platform, _, part = product.partition("_Cloud_Fraction")
    platform = platform.replace("MODIS_", "MODIS ").replace("VIIRS_", "VIIRS ")
    part = part.strip("_").lower()
    if part:
        return f"{platform} cloud fraction, {part}"
    return f"{platform} cloud fraction"

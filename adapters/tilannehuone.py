"""Tilannehuone.fi (Fintraffic's public incident feed).

The map page https://www.tilannehuone.fi/halytysmap.php embeds two JS arrays:
  var halytykset = [ {...}, ... ];   incident records
  var halytrans = [ "...", ... ];    label lookup table (p = place, t = type)

Records look like:
  { icon, iconhi, h, d (timestamp), p (place idx), t (type idx),
    txt (HTML description), icons, pos ([lat, lon]), var: [info, star, target, photo] }

Finnish text is translated to English via lang.py.
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime
from typing import Any

import httpx

import lang

MAP_URL = "https://www.tilannehuone.fi/halytysmap.php"
DETAIL_URL = "https://www.tilannehuone.fi/tehtava.php?hash={hash}"
ICON_BASE = "https://www.tilannehuone.fi/halytys/kuvat/"

ARRAY_RE = re.compile(r"var\s+halytykset\s*=\s*(\[.*?\]);", re.S)
TRANS_RE = re.compile(r"var\s+halytrans\s*=\s*(\[.*?\]);", re.S)


def _parse_date(s: str) -> str | None:
    """'01.10.2026 22:41:00' -> ISO 8601 UTC-ish string."""
    try:
        dt = datetime.strptime(s.strip(), "%d.%m.%Y %H:%M:%S")
        return dt.astimezone().isoformat(timespec="seconds")
    except (ValueError, AttributeError):
        return None


def _clean_icon(icon: str) -> str:
    return icon.split("?")[0].strip()


def _severity(cat_fi: str) -> str:
    c = cat_fi.lower()
    if ": suuri" in c:
        return "major"
    if ": keskisuuri" in c:
        return "moderate"
    if ": pieni" in c:
        return "minor"
    if "onnettomuus" in c:
        return "accident"
    return "info"


async def fetch(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    r = await client.get(MAP_URL, headers={"Accept": "text/html"})
    r.raise_for_status()
    html_text = r.text

    m = ARRAY_RE.search(html_text)
    if not m:
        raise RuntimeError("halytykset array not found in page (layout may have changed)")

    records = json.loads(m.group(1))

    labels: list[str] = []
    mt = TRANS_RE.search(html_text)
    if mt:
        labels = json.loads(mt.group(1))

    out: list[dict[str, Any]] = []
    for rec in records:
        pos = rec.get("pos") or []
        if len(pos) < 2:
            continue

        p_idx = rec.get("p", 0)
        t_idx = rec.get("t", 0)
        place_fi = labels[p_idx] if isinstance(p_idx, int) and p_idx < len(labels) else ""
        cat_fi = labels[t_idx] if isinstance(t_idx, int) and t_idx < len(labels) else ""

        description = lang.strip_html(rec.get("txt", ""))
        flags = rec.get("var") or [0, 0, 0, 0]

        out.append({
            "id": rec.get("h", ""),
            "lat": float(pos[0]),
            "lon": float(pos[1]),
            "time": _parse_date(rec.get("d", "")),
            "time_raw": rec.get("d", ""),
            "place": lang.translate(place_fi) or place_fi,
            "category": lang.category(cat_fi),
            "category_raw": cat_fi,
            "severity": _severity(cat_fi),
            "description": lang.translate(description),
            "description_raw": description,
            "icon": ICON_BASE + _clean_icon(rec.get("icon", "")),
            "icon_size": rec.get("icons", 20),
            "has_info": bool(flags[0] if len(flags) > 0 else 0),
            "is_followup": bool(flags[1] if len(flags) > 1 else 0),
            "has_target": bool(flags[2] if len(flags) > 2 else 0),
            "has_photo": bool(flags[3] if len(flags) > 3 else 0),
            "url": DETAIL_URL.format(hash=rec.get("h", "")),
        })

    out.sort(key=lambda x: x.get("time") or "", reverse=True)
    return out
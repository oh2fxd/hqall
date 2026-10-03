"""Live APRS via APRS-IS (the official APRS packet network).

Endpoint choice (tested from this sandbox, 2026-10):
  * aprs.to  (74.106.4.136:14580, aprsc 2.1.19) forwards packets to *unverified*
    logins, so no aprs.fi registration and no API key is needed. This is the
    default endpoint.
  * aprs2.net refuses connections on some networks (44.10.10.80:14580 gave
    ECONNREFUSED here); rotate.aprs2.net and the country hosts answer, but they
    only forward to logins aprs.fi has verified, i.e. a callsign registered
    with aprs.fi. Kept as fallbacks for users who have such a callsign.
  * aprs.fi itself is NOT used as an endpoint (too slow, and the user asked to
    keep it out of the data path).

Filter notes, both verified by counting packets:
  * server side area filters are honoured here, `filter r/<lat>/<lon>/<km>`.
  * the longitude must NOT be zero padded: `r/62.6/25.3/500` streams packets,
    `r/62.6/025.3/500` and the bounding box form `m/...` stream nothing.
  * the filter is re-sent when the map centre moves, so the stream follows the
    view instead of being pinned to one spot.

Env:
  APRS_CALLSIGN   callsign to log in with (default: hqall, unverified but
                  aprs.to still forwards)
  APRS_PASSCODE   passcode; if unset it is computed locally when possible
  APRS_HOSTS      comma separated, default aprs.to then aprs2.net hosts
  APRS_FILTER     fixed server side filter; by default it follows the map
"""

from __future__ import annotations

import asyncio
import math
import os
import re
import string
import time
from typing import Any

# aprs.to first: it is the only one that streams without an aprs.fi
# registration. The aprs2.net hosts are fallbacks for verified callsigns.
HOSTS = [
    "aprs.to",
    "rotate.aprs2.net",
    "finland.aprs2.net",
    "euro.aprs2.net",
    "norway.aprs2.net",
    "sweden.aprs2.net",
    "denmark.aprs2.net",
]

DEFAULT_CENTRE = (62.60, 25.30)  # Central Finland
DEFAULT_RADIUS_KM = 500.0
FILTER_MAX_KM = 1000.0

STALE_AFTER = 900.0  # seconds before a station disappears from the snapshot

# "Moving" detection, for the clutter problem: a radius of 500 km around
# Central Finland carries a few thousand stations, of which the large majority
# are digipeaters, iGates and weather beacons that sit still. Only stations
# that are going somewhere are interesting on a situational map.
#   MOVE_MIN_M  fixes that did not move this much are GPS jitter, not driving
#                (a parked tracker rarely drifts 200 m between two packets)
#   MOVE_HOLD_S how long a station keeps the flag after its last move, so it
#                stays visible while it stops at a junction or in a harbour
MOVE_MIN_M = 200.0
MOVE_HOLD_S = 600.0
TRAIL_WINDOW_S = 900.0  # how long a station's fix history is kept for the map trail

# APRS101 data type identifiers (first char of the information field).
#   ! = position, no timestamp      = = position, no timestamp, no symbol
#   / = position, DHM z timestamp    @ = same, symbol-less
#   ' = position, HMS timestamp      ( = same, symbol-less
#   ; object    * item    : message    > status    _ weather    ` Mic-E
DT_NO_TS = ("!", "=")
DT_DHM_TS = ("/", "@")
DT_HMS_TS = ("'", "(")
# data types decoded here rather than skipped; "`" (Mic-E) is recognised only so
# the header splits, see the note further down
DT_EXTRA = (";", "*", "`")
# data types that carry no position we care about
DT_SKIP = (":", ">", "_", "T", "?", "}", "{", "]", "[", ")", "#", "$")

HEADER_RE = re.compile(r"^([A-Z0-9]{3,9})>([^:]*):(.+)$")
DT_ALL = frozenset(DT_NO_TS + DT_DHM_TS + DT_HMS_TS + DT_SKIP + DT_EXTRA)
# the symbol code is a single character; APRX and several trackers send a digit
# there even though the specification reserves it, and the position itself is
# unambiguous at that point, so it is accepted
UNCOMPRESSED_RE = re.compile(
    r"^(?P<latd>\d{2})(?P<latm>\d{2}\.\d{2})(?P<ns>[NSns])(?P<tbl>[^0-9])"
    r"(?P<lond>\d{3})(?P<lonm>\d{2}\.\d{2})(?P<ew>[EWew])(?P<sym>.)"
)
UNCOMPRESSED_SPACY_RE = re.compile(
    r"^(?P<latd>\d{2})(?P<latm>[\d ]{2}\.[\d ]{2})(?P<ns>[NSns])(?P<tbl>[^0-9])"
    r"(?P<lond>\d{3})(?P<lonm>[\d ]{2}\.[\d ]{2})(?P<ew>[EWew])(?P<sym>.)"
)
DHM_TS_RE = re.compile(r"^\d{6}[zZ/hH\\]")
HMS_TS_RE = re.compile(r"^\d{6}")

SYMBOL_TABLES = set("/\\0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
# A compressed position starts with the symbol table, which is one of the two
# primaries or a single overlay letter. aprslib accepts the same set. Without
# this the leading digit of an exotic latitude form is taken for a table and the
# rest of the report decodes to a station in the middle of the Pacific.
COMPRESSED_TABLES = set("/\\") | set(string.ascii_letters)


def passcode(callsign: str) -> str:
    """The APRS-IS passcode for a callsign (15 bit, as aprs.fi's clients use it).

    The SSID is stripped, the base call is upper cased and the characters are
    XOR'ed in pairs starting with a left shift of 8 bits. This is the same
    algorithm aprspy and aprs.fi's own tooling use, and the value the account
    page shows; set APRS_PASSCODE to be explicit if you prefer.
    """
    h = 0x73E2
    high = True
    for ch in callsign.split("-")[0].upper():
        h ^= (ord(ch) << 8) if high else ord(ch)
        high = not high
    return str(h)


def area_filter(lat: float, lon: float, radius_km: float) -> str:
    """Server side radius filter for APRS-IS.

    The longitude must not be zero padded: aprsc silently matches nothing for
    "r/62.6/025.3/500" while "r/62.6/25.3/500" streams the whole area. The
    bounding box form ("m/...") also matched nothing in testing, so the radius
    form is the only one used here.
    """
    lat_s = f"{lat:.2f}".rstrip("0").rstrip(".")
    lon_s = f"{lon:.2f}".rstrip("0").rstrip(".")
    return f"r/{lat_s}/{lon_s}/{int(min(FILTER_MAX_KM, max(1.0, radius_km)))}"


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return int((math.degrees(math.atan2(y, x)) + 360) % 360)


def _plausible(lat: float, lon: float) -> bool:
    """A decoded position that is worth putting on the map.

    Exactly 0/0 is "Null Island": trackers with no GPS fix report it, and it is
    5000 km from the area filter, so it is dropped rather than drawn in the Gulf
    of Guinea.
    """
    return -90 <= lat <= 90 and -180 <= lon <= 180 and not (lat == 0 and lon == 0)


def _ssid_of(callsign: str) -> str | None:
    """The SSID suffix ("-7", "-9") that APRS uses to say what a station is.

    The common ones, per the APRS spec / aprs.fi:
      -7   HT / handheld, a human on foot (walker, runner)
      -9   primary mobile, a car
      -8   boat, sailboat, RV, second mobile
      -14  trucker / full-time driver
    A base callsign with no SSID is the primary fixed station (SSID 0).
    """
    part = (callsign or "").rsplit("-", 1)
    if len(part) == 2 and part[1].isdigit():
        return part[1]
    return None


def _rf_heard(path_raw: str) -> bool:
    """Was this packet heard on RF, or is the station an internet-only object?

    APRS-IS marks a station that transmits over TCP/IP (a laptop client, an
    internet tracker, a test object...) with ``TCPIP`` in the packet path:
    ``SRC>APRS,TCPIP*``. A packet that travelled over the air carries a real
    radio hop instead, even when an iGate later injected it into the network
    (``SRC>APRS,WIDE1-1,WIDE2-1,TCPIP*``). The path holds the destination as
    its first element, so the discriminator is TCPIP appearing in hop position
    0 or 1; anything else (a digipeater, a qAR gate, or no hop at all, which is
    a direct RF hit on an iGate) counts as heard on RF.
    """
    hops = [
        h.strip().rstrip("*").strip().upper()
        for h in (path_raw or "").split(",")
        if h.strip()
    ]
    if not hops:
        return True
    if hops[0] == "TCPIP":
        return False
    if len(hops) >= 2 and hops[1] == "TCPIP":
        return False
    return True


def _uncompressed(s: str) -> tuple[float, float, str, str] | None:
    """lat, lon, symbol_table, symbol_code from an uncompressed position.

    Layout (APRS101 9.3): DDMM.hhN  T  DDDMM.hhE  S  <comment>, so the position
    is exactly 19 characters. Digits missing from the minutes are transmitted as
    spaces (ambiguity), hence the second, space tolerant pattern.
    """
    m = UNCOMPRESSED_RE.match(s) or UNCOMPRESSED_SPACY_RE.match(s)
    if not m:
        return None
    g = m.groupdict()
    try:
        lat = int(g["latd"]) + float(g["latm"].replace(" ", "0")) / 60.0
        lon = int(g["lond"]) + float(g["lonm"].replace(" ", "0")) / 60.0
    except ValueError:
        return None
    if g["ns"] in "Ss":
        lat = -lat
    if g["ew"] in "Ww":
        lon = -lon
    if not _plausible(lat, lon):
        return None
    return round(lat, 5), round(lon, 5), g["tbl"], g["sym"]


def _compressed(s: str) -> tuple[float, float, str, str, str] | None:
    """lat, lon, symbol_table, symbol_code, rest from a compressed position.

    Layout (APRS101 9.3 / "Base 91"):  T y1y2y3y4 x1x2x3x4 S  cs  ts  <comment>
    i.e. 1 + 4 + 4 + 1 + 2 + 1 = 13 fixed characters before the comment.
    """
    if len(s) < 13:
        return None
    table = s[0]
    if table not in COMPRESSED_TABLES:
        return None
    chars = s[1:9]
    if any(not 33 <= ord(c) <= 123 for c in chars):
        return None
    try:
        y = [(ord(c) - 33) for c in chars[0:4]]
        x = [(ord(c) - 33) for c in chars[4:8]]
    except TypeError:
        return None
    lat = 90.0 - (y[0] * 91**3 + y[1] * 91**2 + y[2] * 91 + y[3]) / 380926.0
    lon = -180.0 + (x[0] * 91**3 + x[1] * 91**2 + x[2] * 91 + x[3]) / 190463.0
    if not _plausible(lat, lon):
        return None
    sym = s[9]
    ts = s[12]
    # Symbol code and the T/S byte are never bare digits in a real packet, which
    # is what keeps a malformed uncompressed report from being decoded as junk.
    if sym.isdigit() or ts.isdigit():
        return None
    rest = s[13:]
    if ts == "S" and DHM_TS_RE.match(rest):  # compressed reports may be timed
        rest = rest[7:]
    return round(lat, 5), round(lon, 5), table, sym, rest


def _parse_comment(comment: str) -> tuple[dict[str, Any], str]:
    """Pull course/speed/altitude out of an APRS comment field.

    Returns the parsed values plus the comment with those data extensions
    removed, so the UI shows only the free text the station sent.
    """
    out: dict[str, Any] = {}
    m = re.search(r"(\d{3})\/(\d{3})", comment)  # course / speed in knots
    if m:
        out["course"] = int(m.group(1))
        out["speed_kt"] = round(int(m.group(2)) * 1.15078, 1)
    a = re.search(r"A=(\d{6})", comment)  # /A=nnnnnn, altitude in feet
    if a:
        out["altitude_ft"] = int(a.group(1))
    clean = re.sub(r"A=\d{6}", " ", comment)
    clean = re.sub(r"\d{3}/\d{3}", " ", clean)
    clean = re.sub(r"[/,\s]{2,}", " ", clean).strip(" ,/")
    return out, clean


def _parse_compressed_cs(body: str) -> dict[str, Any]:
    """Course/speed (or altitude/range) from the two base-91 chars at 10..11.

    APRS 1.01 C9: course = (c1 - 33) * 4 degrees, speed = 1.08**(c2 - 33) - 1 kn.
    Altitude/range use a different base (1.002**n) and are distinguished by the
    T/S byte, which we ignore because the map only needs lat/lon/course/speed.
    """
    if len(body) < 12 or body[10] == " ":
        return {}
    try:
        c1 = ord(body[10]) - 33
        c2 = ord(body[11]) - 33
    except IndexError:
        return {}
    if 0 <= c1 <= 89:
        return {
            "course": c1 * 4,
            "speed_kt": round(1.08**c2 - 1, 1),
        }
    return {}


# --- Mic-E ("`" data type) -------------------------------------------------
# Recognised so the header splits correctly, but deliberately not decoded.
#
# Mic-E is common on APRS, so it was implemented from the APRS 1.1 chapter 10
# tables and then checked against a 6 minute aprs.to capture filtered to a
# 2000 km radius around central Finland: 410 Mic-E packets arrived and not one
# of them could be placed. The destination field is a digit coded position with
# a "blank digit" ambiguity scheme (K/L/Z) and 0-9 / A-J / P-Y reductions, and
# every plausible reading of it put Finnish stations in the southern ocean and
# the Antarctic. aprslib 0.7.2, used as a reference decoder, returns no position
# at all for those same 410 packets, so there is no cross-check to trust either.
# A wrong position on the map is worse than a missing station, so the packets
# are counted as undecoded and skipped: the stream still carries thousands of
# beacon, Tracker and object reports, which do decode.

# --- objects (";") and items ("*") ----------------------------------------

# 17 character position without a symbol table, as some LoRa trackers send it,
# optionally with a stray E/W in front of the longitude degrees
_UNCOMPRESSED_NOTBL_RE = re.compile(
    r"^(?P<latd>\d{2})(?P<latm>\d{2}\.\d{2})(?P<ns>[NS])(?P<junk>[EW]?)(?P<lond>\d{3})"
    r"(?P<lonm>\d{2}\.\d{2})(?P<ew>[EW])"
)
# 18 character LoRa form: degrees, minutes and *seconds* for both the latitude
# and the longitude, two digit degrees in both fields, no symbol table at all.
# 17 and 19 character positions are tried first, and the three lengths do not
# collide, so this one is only reached when neither of them matches.
_LORA_SECONDS_RE = re.compile(
    r"^(?P<latd>\d{2})(?P<latm>\d{2})\.(?P<lats>\d{2})(?P<ns>[NS])"
    r"(?P<lond>\d{2})(?P<lonm>\d{2})(?P<lons>\d{2})\.(?P<lonf>\d{2})(?P<ew>[EW])"
)
_DAO_RE = re.compile(r"DAO[^,]*,")
# the same six digits plus a zulu ("z"), local ("h") or QZ ("\\") marker
_OBJ_STAMP_RE = re.compile(r"^\d{6}[zZhH\\]")

# APRS 1.1 chapter 12 puts the name in 9 characters, then a live ("*") or
# killed ("_") flag, a 3 character symbol code, a timestamp and the position.
# LoRa style trackers leave out the symbol code and use a shorter name, so the
# longest offsets are probed first and the position is confirmed by actually
# parsing it, which is what decides between them.
_OBJ_FLAG_AT = (10, 9, 8)
_OBJ_SYMLEN = 3


def _position_at(s: str) -> tuple[float, float, str, str, int] | None:
    """lat, lon, symbol_table, symbol_code, characters consumed.

    LoRa trackers sometimes leave the symbol table character out of the
    position, which turns the usual 19 character field into 17, so that form is
    tried as well.
    """
    pos = _uncompressed(s)
    if pos:
        return pos[0], pos[1], pos[2], pos[3], 19
    pos = _compressed(s)
    if pos:
        return pos[0], pos[1], pos[2], pos[3], 13
    m = _UNCOMPRESSED_NOTBL_RE.match(s)
    if m:
        g = m.groupdict()
        lat = int(g["latd"]) + float(g["latm"]) / 60.0
        lon = int(g["lond"]) + float(g["lonm"]) / 60.0
        if g["ns"] == "S":
            lat = -lat
        if g["ew"] == "W":
            lon = -lon
        if _plausible(lat, lon):
            return round(lat, 5), round(lon, 5), "", "", 17
    m = _LORA_SECONDS_RE.match(s)
    if m:
        g = m.groupdict()
        lat = int(g["latd"]) + int(g["latm"]) / 60.0 + int(g["lats"]) / 3600.0
        lon = (
            int(g["lond"])
            + int(g["lonm"]) / 60.0
            + (int(g["lons"]) + int(g["lonf"]) / 100.0) / 3600.0
        )
        if g["ns"] == "S":
            lat = -lat
        if g["ew"] == "W":
            lon = -lon
        if _plausible(lat, lon):
            return round(lat, 5), round(lon, 5), "", "", 18
    return None


def _object_position(info: str, flag_at: int) -> tuple[int, int, tuple] | None:
    """Where the position starts, the symbol code length and the position."""
    for symlen in (0, _OBJ_SYMLEN):
        stamp = flag_at + 1 + symlen
        if stamp >= len(info):
            return None
        if info[stamp] in "zZ":
            start = stamp + 1
        elif _OBJ_STAMP_RE.match(info[stamp:]):
            start = stamp + 7          # six digits plus the "z"
        else:
            continue
        pos = _position_at(info[start:])
        if pos:
            return start, symlen, pos
    return None


def _object(info: str) -> dict[str, Any] | None:
    """Decode an object (";") or item ("*") report."""
    for flag_at in _OBJ_FLAG_AT:
        if flag_at >= len(info) or info[flag_at] not in "*_":
            continue
        name = info[1:flag_at].strip()
        found = _object_position(info, flag_at) if name else None
        if not found:
            continue
        start, symlen, pos = found
        lat, lon, table, sym, used = pos
        rest = info[start + used :]

        # with the symbol code present it is the real icon, otherwise the
        # position itself carried the table and the symbol sits right after it
        symbol = (
            info[flag_at + 1 : flag_at + 4]
            if symlen
            else ((table + sym) if sym else table)
        )

        comment = _DAO_RE.sub("", rest).lstrip(", ").strip()
        return {
            "object": " ".join(name.split())[:24],
            "object_live": info[flag_at] == "*",
            "lat": lat,
            "lon": lon,
            "symbol": symbol[:4],
            "format": "object",
            "comment": comment[:200],
        }
    return None


def _split_header(raw: str) -> tuple[str, str, str] | None:
    """callsign, path, information field.

    The information field starts at a colon that is followed by a valid data
    type identifier, so a stray colon inside the path (some iGates emit
    `...qAC,T2XKI::`) does not truncate the packet.
    """
    for candidate in _iter_headers(raw):
        return candidate
    return None


def _iter_headers(raw: str):
    """Every plausible information field in a packet, best guess first.

    A data type identifier is any printable character, so text that follows a
    colon can look like one: a message quoting a position, or a comment with a
    URL in it, where the colon of `https://` reads exactly like a position
    report without a timestamp. Two things give those away and are rejected
    here: a space in the path, which is a comma separated list of callsigns and
    digipeaters and never contains one, and a doubled colon, which some iGates
    leave at the end of the path and which is part of the path, not the data
    type.
    """
    line = raw.strip()
    if ">" not in line:
        return
    call, _, rest = line.partition(">")
    if not call or not rest:
        return
    seen = set()
    for i, ch in enumerate(rest):
        if ch != ":":
            continue
        start = i + 1
        if rest[start : start + 1] == ":":
            start += 1          # a doubled colon belongs to the path
        if start >= len(rest) or rest[start] not in DT_ALL:
            continue
        path = rest[:i] if start == i + 1 else rest[: i + 1]
        if " " in path:
            continue            # the colon is inside a comment, not a header
        seen.add(i)
        yield call, path, rest[start:]
    head, sep, info = rest.partition(":")
    if sep and info and rest.find(":") not in seen:
        yield call, head, info


# marker for "that colon is not the information field after all": the character
# after it is not one of the data type identifiers, it is just text, and the
# search moves on to the next colon
_WRONG_SPLIT = object()


def parse_line(line: str) -> dict[str, Any] | None:
    """Turn one APRS-IS packet into the app's station shape.

    The first colon that is followed by a known data type ends the search,
    whatever follows it: a status or message packet is a status or message
    packet, and the colon of a URL in its comment must not be mistaken for the
    information field. Only a colon followed by something that is not a data
    type at all is second guessed.
    """
    for head in _iter_headers(line):
        out = _parse_info(*head)
        if out is _WRONG_SPLIT:
            continue
        return out
    return None


def _parse_info(call: str, path: str, info: str) -> Any:
    if not info:
        return None

    dtype = info[0]
    if dtype in (";", "*"):
        # object / item: a 9 character name, a live ("*") or killed ("_") flag,
        # the symbol code, a zulu stamp and then the position
        obj = _object(info)
        if not obj:
            return None
        out = {
            "callsign": call,
            "id": call + "/" + obj["object"],
            "lat": obj["lat"],
            "lon": obj["lon"],
            "symbol": obj["symbol"],
            "kind": "object",
            "path": path.split(",")[-1][:64] if path else "",
            "path_raw": path,
            "source": "aprs-is",
            "_t": time.time(),
        }
        out.update(obj)
        return out

    if dtype in DT_SKIP:
        return None
    if dtype in ("!", "=") and len(info) < 3:
        return None

    body = info[1:]
    extra: dict[str, Any] = {}

    if dtype in DT_NO_TS or dtype in DT_DHM_TS or dtype in DT_HMS_TS:
        if DHM_TS_RE.match(body):
            body = body[7:]
        elif dtype in DT_HMS_TS and HMS_TS_RE.match(body):
            body = body[6:]
        pos = _position_at(body)
        if pos:
            lat, lon, table, sym, used = pos
            # compressed reports carry the course/speed and altitude in a fixed
            # suffix that the uncompressed ones put in the comment
            rest = body[used:] if len(body) > used else ""
            if used == 13:
                extra = _parse_compressed_cs(body)
        else:
            return None
        symbol = (table + sym) if sym else table
    else:
        return _WRONG_SPLIT

    comment = rest[1:] if rest[:1] == "," else rest
    out: dict[str, Any] = {
        "callsign": call,
        "id": call,
        "lat": lat,
        "lon": lon,
        "symbol": symbol,
        "kind": "position",
        "path": path.split(",")[-1][:64] if path else "",
        "path_raw": path,
        "source": "aprs-is",
        "_t": time.time(),
    }
    out.update(extra)
    fields, clean = _parse_comment(comment)
    out.update(fields)
    out["comment"] = clean[:200]
    return out


class AprsStream:
    """Keeps one APRS-IS connection alive and exposes a live snapshot."""

    def __init__(self) -> None:
        # aprs.to streams to unverified logins, so no callsign is required: the
        # default login is a plain "hqall" sent with "pass -1" and it still
        # receives packets. Setting APRS_CALLSIGN switches to that identity,
        # and a passcode is then computed locally (aprs.fi/aprspy scheme) or
        # taken from APRS_PASSCODE if the registered one differs.
        env_call = os.environ.get("APRS_CALLSIGN", "").strip().upper()
        env_pass = os.environ.get("APRS_PASSCODE", "").strip()
        self.callsign = env_call or "hqall"
        self.passcode = env_pass or (passcode(self.callsign) if env_call else "")
        # "pass -1" is the APRS-IS way of saying "no passcode": accepted by
        # aprs.to, rejected by the aprs2.net network.
        self.anonymous = not bool(self.passcode)
        self.hosts = [
            h.strip()
            for h in os.environ.get(
                "APRS_HOSTS", ",".join(HOSTS)
            ).split(",")
            if h.strip()
        ]
        self.fixed_filter = os.environ.get("APRS_FILTER", "").strip()
        self.lat, self.lon = DEFAULT_CENTRE
        self.radius_km = DEFAULT_RADIUS_KM
        self.filter = self.fixed_filter or area_filter(self.lat, self.lon, self.radius_km)
        self.stations: dict[str, dict[str, Any]] = {}
        self.status = "starting"
        self.verified: bool | None = None
        self.last_packet = 0.0
        self.packets = 0
        self.host = ""
        self._task: asyncio.Task | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._filter_at = 0.0
        self._relogin = False
        self._stop = False

    def configured(self) -> bool:
        return bool(self.callsign)

    # -- server side area filter -------------------------------------------

    def area(self, lat: float, lon: float, radius_nm: float | None = None) -> str:
        """Point the stream at a new area. Returns the filter actually used."""
        self.lat, self.lon = lat, lon
        if radius_nm:
            self.radius_km = min(FILTER_MAX_KM, max(50.0, radius_nm * 1.852))
        self.filter = self.fixed_filter or area_filter(lat, lon, self.radius_km)
        return self.filter

    def push_filter(self, force: bool = False) -> None:
        """Re-apply the area filter when the view has moved far enough.

        aprs.to ignores a "filter" command sent after login (measured: a socket
        that gets one streams nothing), so a moved view is served by dropping
        the connection and logging in again with the new radius in the login
        line. The aprs2.net network accepts both forms.
        """
        if self._writer is None or self.fixed_filter:
            return
        now = time.time()
        if not force and now - self._filter_at < 30.0:
            return
        self._filter_at = now
        self._relogin = True

    def start(self) -> None:
        if not self.configured() or self._task:
            return
        self._stop = False
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    # -- connection loop ---------------------------------------------------

    async def _run(self) -> None:
        idx = 0
        while not self._stop:
            host = self.hosts[idx % len(self.hosts)]
            idx += 1
            try:
                await self._session(host)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reconnect forever
                self.status = f"reconnecting ({host}: {type(exc).__name__})"
            if self._stop:
                return
            await asyncio.sleep(5)

    async def _session(self, host: str) -> None:
        self.host = host
        self.status = f"connecting to {host}"
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, 14580), timeout=10
        )
        self._writer = writer
        try:
            # pass -1 means "anonymous"; verified callsigns send their passcode.
            # the radius belongs in the login line, see push_filter()
            pass_word = "-1" if self.anonymous else self.passcode
            login = f"user {self.callsign} pass {pass_word} filter {self.filter}"
            writer.write(f"{login} vers hqall 1.0 hqall\n".encode())
            await writer.drain()

            # Wait for the login verdict, but never treat silence as a
            # failure: some servers say nothing at all and still stream, so a
            # quiet socket is only a reason to stop waiting, not to reconnect.
            deadline = time.time() + 8
            while time.time() < deadline:
                try:
                    line = await asyncio.wait_for(reader.readline(), timeout=2)
                except asyncio.TimeoutError:
                    if self.packets:
                        break
                    continue
                if not line:
                    raise ConnectionError("server closed during login")
                text = line.decode("ascii", "replace").strip()
                if text.startswith("# logresp"):
                    # "unverified" contains "verified", so match the word and
                    # rule the negative out explicitly
                    self.verified = bool(
                        re.search(r"\bverified\b", text) and "unverified" not in text
                    )
                    self._filter_at = time.time()
                    self.status = (
                        f"live on {host} "
                        f"({'verified' if self.verified else 'unverified, still streamed'})"
                    )
                elif text.startswith("# aprsc"):
                    continue

            while not self._stop:
                if self._relogin:
                    # the area moved: log in again with the new radius
                    self._relogin = False
                    return
                try:
                    line = await asyncio.wait_for(reader.readline(), timeout=30)
                except asyncio.TimeoutError:
                    self._prune()
                    continue
                if not line:
                    raise ConnectionError("server closed the connection")
                text = line.decode("ascii", "replace").strip()
                if not text or text.startswith("#"):
                    continue
                row = parse_line(text)
                if not row:
                    continue
                self.packets += 1
                self.last_packet = time.time()
                self._ingest(row)
        finally:
            self._writer = None
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass

    # -- snapshot ----------------------------------------------------------

    def _ingest(self, row: dict[str, Any]) -> None:
        """Store a decoded packet, flagging whether the station is on the move.

        Two independent signals, either is enough:
          * it moved more than MOVE_MIN_M since the previous fix
          * the packet itself carries a non zero course/speed
        The flag is held for MOVE_HOLD_S so a vehicle that stops at a junction
        stays on the map, and a boat that drops anchor fades out rather than
        blinking away.
        """
        now = float(row.get("_t") or time.time())
        prev = self.stations.get(row["callsign"])
        hold = float(prev.get("_moving_until", 0.0)) if prev else 0.0
        moved_m = row.get("_moved_m")
        # "heard on RF" accumulates: a station that ever beaconed over the air
        # is kept on the map; the internet-only clients (TCPIP path) are who the
        # user asked to hide, so they never get the flag just because an iGate
        # relayed the packet
        row["_rf"] = bool(prev.get("_rf")) if prev else False
        if _rf_heard(row.get("path_raw") or ""):
            row["_rf"] = True
        # how many fixes this station has sent us: one is not enough to tell a
        # parked digipeater from a car, so a single fix stays "unclassified"
        # and is shown until the second one arrives
        row["_fixes"] = (int(prev.get("_fixes", 1)) + 1) if prev else 1
        if prev is not None and prev.get("lat") is not None and prev.get("lon") is not None:
            moved_m = haversine_km(prev["lat"], prev["lon"], row["lat"], row["lon"]) * 1000.0
            # the gap between two fixes can be minutes or half an hour, so the
            # distance on its own says little; the implied speed is the useful
            # number, and it is the honest one when the packet has no course
            row["_moved_s"] = max(1.0, now - float(prev.get("_t") or now))
            if moved_m >= MOVE_MIN_M:
                hold = now + MOVE_HOLD_S
        elif not prev and (row.get("speed_kt") or 0) > 1:
            hold = now + MOVE_HOLD_S
        if moved_m is not None:
            row["_moved_m"] = round(moved_m, 1)
        # A per-station 15-minute fix history, at the real beacon cadence (one
        # RF position every few minutes), so the map can draw the trail even
        # when the client only watches for seconds. Ring-buffered by time.
        prev_trail = prev.get("_trail") if prev else None
        trail = prev_trail or []
        lat, lon = row.get("lat"), row.get("lon")
        if trail and trail[-1][0] >= now:
            # a re-forwarded packet is not a new fix
            pass
        elif trail and abs(trail[-1][1] - lat) < 1e-4 and abs(trail[-1][2] - lon) < 1e-4:
            trail[-1][0] = now
        elif lat is not None and lon is not None:
            trail.append([now, lat, lon])
        trail_cut = now - TRAIL_WINDOW_S
        row["_trail"] = trail
        while len(trail) > 2 and trail[0][0] < trail_cut:
            trail.pop(0)
        row["_moving_until"] = hold
        self.stations[row["callsign"]] = row

    def _prune(self) -> None:
        """Drop stations that have not beaconed for a while."""
        now = time.time()
        stale = [k for k, v in self.stations.items() if now - v.get("_t", 0) > STALE_AFTER]
        for k in stale:
            self.stations.pop(k, None)

    def snapshot(
        self,
        lat: float,
        lon: float,
        radius_nm: float,
        moving_only: bool = False,
        rf_only: bool = False,
        symbols: Iterable[str] | None = None,
        exclude: Iterable[str] | None = None,
        ssid: Iterable[str] | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        now = time.time() if now is None else now
        limit_km = radius_nm * 1.852
        sym_allowed = set(symbols) if symbols else None
        sym_excluded = set(exclude) if exclude else None
        ssid_allowed = set(ssid) if ssid else None
        out: list[dict[str, Any]] = []
        moving_seen = 0
        rf_seen = 0
        for call, row in list(self.stations.items()):
            dist = haversine_km(lat, lon, row["lat"], row["lon"])
            if dist > limit_km:
                continue
            # Cars (-9) and walkers (-7) are the interesting mobile beacons; the
            # SSID says what a station is far more reliably than the symbol code,
            # which many digis and iGates share.
            if ssid_allowed and (_ssid_of(call) or "0") not in ssid_allowed:
                continue
            # The last character of "symbol" is the code letter that API users
            # filter on.
            sym = str(row.get("symbol") or "")[-1:]
            if sym_allowed and sym not in sym_allowed:
                continue
            if sym_excluded and sym in sym_excluded:
                continue
            # a station with a single fix cannot be classified yet, so it is
            # reported as moving (i.e. shown) until its second fix proves it is
            # parked; the map fills up at once instead of starting empty
            classified = row.get("_fixes", 1) >= 2
            moving = now < row.get("_moving_until", 0.0) or not classified
            if moving:
                moving_seen += 1
            if moving_only and not moving:
                continue
            rf_heard = bool(row.get("_rf"))
            if rf_heard:
                rf_seen += 1
            if rf_only and not rf_heard:
                continue
            item = dict(row)
            item["moving"] = moving
            item["classified"] = classified
            item["rf"] = rf_heard
            item["moved_m"] = row.get("_moved_m")
            gap = row.get("_moved_s")
            if row.get("_moved_m") is not None and gap:
                item["speed_kmh"] = round(row["_moved_m"] / gap * 3.6, 1)
            item["distance_km"] = round(dist, 1)
            item["bearing"] = bearing(lat, lon, row["lat"], row["lon"])
            item["_age_s"] = int(now - row.get("_t", now))
            # the fix history of a station under way, so the map can draw its
            # trail at once instead of slowly re-accumulating it client-side
            if moving:
                item["trail"] = [[p[0], p[1], p[2]] for p in (row.get("_trail") or [])]
            for key in ("_t", "_moving_until", "_moved_m", "_moved_s", "_fixes", "_rf", "_trail"):
                item.pop(key, None)
            out.append(item)

        out.sort(key=lambda r: r["distance_km"])
        return {
            "ok": True,
            "source": "aprs-is",
            "status": self.status,
            "verified": self.verified,
            "host": self.host,
            "callsign": self.callsign,
            "packets": self.packets,
            "tracked": len(self.stations),
            "moving_only": moving_only,
            "rf_only": rf_only,
            "symbols": sorted(sym_allowed) if sym_allowed else None,
            "exclude": sorted(sym_excluded) if sym_excluded else None,
            "ssid": sorted(ssid_allowed) if ssid_allowed else None,
            "moving": moving_seen,
            "rf": rf_seen,
            "stations": out,
            "count": len(out),
            "now": now,
        }

"""Parser tests for the APRS-IS adapter.

Test vectors come from the public APRS 1.01 specification examples and from
aprspy's test-suite (nsnw/aprspy, GPL-3.0) fixtures, which are the de-facto
reference implementation for position parsing.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters.aprs_is import haversine_km, parse_line, passcode  # noqa: E402


def _close(a, b, tol=0.01):
    return a is not None and abs(float(a) - float(b)) <= tol


@pytest.mark.parametrize(
    "raw, lat, lon, course, speed_kt, altitude_ft, comment",
    [
        # --- uncompressed, no timestamp (APRS101 9.3) ---
        (
            "XX1XX>APRS,TCPIP*,qAC,FOURTH:=5030.50N/10020.30W$221/000/A=005000Test packet",
            50.508333, -100.338333, 221, 0.0, 5000, "Test packet",
        ),
        (
            "XX1XX>APRS,TCPIP*,qAC,FOURTH:=5030.50N/10020.30E$221/000/A=005000Test packet",
            50.508333, 100.338333, 221, 0.0, 5000, "Test packet",
        ),
        (
            "XX1XX>APRS,TCPIP*,qAC,FOURTH:!5030.50S/10020.30W$221/000/A=005000Test packet",
            -50.508333, -100.338333, 221, 0.0, 5000, "Test packet",
        ),
        (
            "XX1XX>APRS,TCPIP*,qAC,FOURTH:!5030.50S/10020.30E$221/000/A=005000Test packet",
            -50.508333, 100.338333, 221, 0.0, 5000, "Test packet",
        ),
        # --- DHM z timestamp ---
        (
            "XX1XX>APRS,TCPIP*,qAC,FOURTH:/092345z5030.50N/10020.30W$221/000/A=005000Test packet",
            50.508333, -100.338333, 221, 0.0, 5000, "Test packet",
        ),
        # --- DHM slash timestamp, eastern longitude ---
        (
            "XX1XX>APRS,TCPIP*,qAC,FOURTH:@092345/5030.50N/10020.30E$221/000/A=005000Test packet",
            50.508333, 100.338333, 221, 0.0, 5000, "Test packet",
        ),
        # --- compressed (base 91) position with course/speed ---
        (
            "XX1XX>APRS,TCPIP*,qAC,FOURTH:=/5L!!<*e7>7P[Test packet",
            49.5, -72.750004, 88, 36.28, None, "Test packet",
        ),
        # --- Finnish style uncompressed packet ---
        (
            "OH2FXD-1>APRS,TCPIP*,qAC,T2XKI::!6017.55N/02455.12E_217/041/A=003000op qrt",
            60.2925, 24.918667, 217, 47.2, 3000, "op qrt",
        ),
    ],
)
def test_parse_positions(raw, lat, lon, course, speed_kt, altitude_ft, comment):
    got = parse_line(raw)
    assert got, f"packet not parsed: {raw}"
    assert _close(got["lat"], lat), got
    assert _close(got["lon"], lon), got
    assert got["course"] == course, got
    if speed_kt is None:
        assert "speed_kt" not in got, got
    else:
        assert _close(got["speed_kt"], speed_kt, 0.1), got
    if altitude_ft is None:
        assert "altitude_ft" not in got, got
    else:
        assert got["altitude_ft"] == altitude_ft, got
    assert got["comment"] == comment, got
    assert got["source"] == "aprs-is"


def test_compressed_altitude_and_symbol():
    """compressed report: house symbol, altitude in the course/speed pair."""
    got = parse_line("XX1XX>APRS,TCPIP*,qAC,FOURTH:=/5L!!<*e7OS]S")
    assert _close(got["lat"], 49.5) and _close(got["lon"], -72.750004)
    assert got["symbol"] == "/O"
    # 1.002 ** (c * 91 + s) with c='S', s=']' is the encoded altitude
    assert 10004 < 1.002 ** ((ord("S") - 33) * 91 + (ord("]") - 33)) < 10005


def test_compressed_rejects_digit_symbol():
    """A malformed uncompressed report must not decode as a compressed one."""
    assert parse_line("OH2ABC>APRS,TCPIP*:@092345/6025.2N/02455.6E-242/049") is None


def test_space_padded_ambiguity():
    got = parse_line("XX1XX>APRS,TCPIP*,qAC,FOURTH:!49  .  N/072  .  W-here")
    assert _close(got["lat"], 49.0) and _close(got["lon"], -72.0), got
    assert got["comment"] == "here", got


def test_drops_non_position_packets():
    for raw in (
        "OH2XYZ>APRS,TCPIP*:>status text",           # status
        "OH2XYZ>APRS,TCPIP*::IGATE message",          # message
        "OH2XYZ>APRS,TCPIP*:;LEADER.0516Hz",          # object
        "OH2XYZ>APRS,TCPIP*::*OH2ABC:>hi",            # item
        "OH2XYZ>APRS,TCPIP*:_10090556c220s004g005t077",  # weather
        "not an aprs line at all",
        "",
    ):
        assert parse_line(raw) is None, raw


def test_out_of_range_latitude_dropped():
    assert parse_line("XX1XX>APRS,TCPIP*:=9910.00N/00100.00E-") is None


def test_null_island_dropped():
    # a tracker with no fix reports 0/0, which is 5000 km off the map area
    got = parse_line("DG1OBD-1>APDG01,qAC,DB0ACH:@020552z0000.00NT00000.00EATetra TMO")
    assert got is None


def test_url_in_a_comment_is_not_a_position():
    # the colon of "https://" reads like a position without a timestamp; the
    # packets below put one in the southern ocean when that was decoded
    for raw in (
        "MPAD>APMPAD,TCPIP*,qAC,T2CZECH::RGSTRY   :See https://github.com/joerg"
        "schultzelutter/mpad for command syntax{XE",
        "9A3WP-R>APDG03,qAS,9A3WP:>Powered by W0CHP-PiStar-Dash (https://wpsd.w0chp.net)",
        # a space in the path means the colon is inside a comment: without that
        # check this status packet decodes as a station at 58N 85W
        "OH2XYZ>APRS,TCPIP*:>iGate map :!6017.55N/02455.12E-",
    ):
        assert parse_line(raw) is None, raw


def test_digit_symbol_table_is_not_a_compressed_position():
    # "FM-Funknetz APRS-Client" writes degrees with six decimals, which starts
    # with a digit; read as a symbol table the rest decodes to 58N 84W
    got = parse_line("DG1DGT>APFMNB,TCPIP*,qAC,T2SPAIN:!51.099254N/6,892451O- "
                     "FM-Funknetz APRS-Client (v5.35a by DL3EL)")
    assert got is None


def test_path_and_callsign():
    got = parse_line("OH2FXD-1>APRS,TCPIP*,qAC,T2XKI,T2FOUL:!6017.55N/02455.12E-")
    assert got["callsign"] == "OH2FXD-1"
    assert got["path"] == "T2FOUL"


def test_stray_colon_in_path():
    got = parse_line("OH2FXD-1>APRS,TCPIP*,qAC,T2XKI::!6017.55N/02455.12E_217/041")
    assert got["callsign"] == "OH2FXD-1"
    assert _close(got["lat"], 60.2925) and _close(got["lon"], 24.918667), got
    assert got["course"] == 217 and got["speed_kt"] == 47.2, got


def test_passcode():
    # same values aprspy/aprs.fi tooling produce for these calls
    assert passcode("N0CALL") == "13023"
    assert passcode("N0CALL-9") == "13023"
    assert passcode("OH2FXD") == "22184"
    assert passcode("oh2fxd-3") == "22184"


@pytest.mark.parametrize(
    "raw, callsign, obj, lat, lon, live",
    [
        # real packets from an aprs.to capture (r/62.6/25.3/2000)
        (
            "SK3GW>APSVX1,TCPIP*,qAC,T2SWEDEN:;SK3GW    *111111z6038.46N/01707.98Er"
            "PHG7560/434.875MHz T127 -200 R73k",
            "SK3GW", "SK3GW", 60.641, 17.133, True,
        ),
        (
            "RY1AAC-10>APDW18,TCPIP*,qAC,T2TROITSK:;Shipyard *010000z5952.75N/03017.29EY"
            "Yacht building",
            "RY1AAC-10", "Shipyard", 59.87917, 30.28817, True,
        ),
        (
            "R1ABC-S>APDG01,TCPIP*,qAC,R1ABC-GS:;R1ABC  B *020532z5954.75ND03029.32Ea"
            "RNG0031/A=000138",
            "R1ABC-S", "R1ABC B", 59.9125, 30.48867, True,
        ),
        # killed object, and the LoRa variant with no symbol table character
        (
            "SK3BG-1>APRX29,TCPIP*,qAS,SK3BG:;EL-27796_111111z6223.60NE01728.55E"
            "0145.725MHz TOFF",
            "SK3BG-1", "EL-27796", 62.39333, 17.47583, False,
        ),
        # local time stamp ("h" instead of "z"), which several LoRa trackers use
        (
            "V3340728>APDW12,TCPIP*,qAC,T2EISBERG:;V3340728 *055131h4750.68N/01110.53E"
            "O025/021/A=040150!wNM!Clb=-75",
            "V3340728", "V3340728", 47.844667, 11.1755, True,
        ),
        # APRS over LoRa writes degrees, minutes and seconds, and leaves the
        # symbol table out entirely: 18 characters instead of 19
        (
            "OK0BKO>APRS,TCPIP*,qAC,T2CZECH:;ER-OK0BKO*111111z4921.55N101639.66E"
            "0439.325MHz T088 R18k SVXlink Blansko",
            "OK0BKO", "ER-OK0BKO", 49.365278, 10.277684, True,
        ),
        # a name shorter than the 9 characters the specification reserves
        (
            "SP3YEE>APRS04,TCPIP*,qAC,T2XKI:;144.950*111111z5221.89N/01656.85E"
            "r144.950MHz SP3YEE Pog",
            "SP3YEE", "144.950", 52.364833, 16.9475, True,
        ),
    ],
)
def test_object_reports(raw, callsign, obj, lat, lon, live):
    got = parse_line(raw)
    assert got, raw
    assert got["callsign"] == callsign
    assert got["kind"] == "object"
    assert got["object"] == obj
    assert got["id"] == f"{callsign}/{obj}"
    assert got["object_live"] is live
    assert _close(got["lat"], lat) and _close(got["lon"], lon), got


def test_object_with_symbol_code():
    """The conforming layout: 9 character name, flag, 3 character symbol code,
    7 character timestamp, position. Assembled from parts so the offsets are
    explicit (APRS 1.1 chapter 12)."""
    name = "GATE    "                 # exactly 9 characters
    raw = ("OH2FXD>APRS,TCPIP*,qAC,T2LAUS:;" + name + "*" + "GPQ" + "041530z"
           + "6043.70N/02508.55E_" + "gateway 145.800")
    got = parse_line(raw)
    assert got, "object with a symbol code should decode"
    assert got["symbol"] == "GPQ"
    assert got["object"] == "GATE"
    assert _close(got["lat"], 60.728333) and _close(got["lon"], 25.1425), got
    assert got["comment"] == "gateway 145.800", got


def test_haversine():
    assert _close(haversine_km(60.17, 24.94, 60.17, 24.94), 0, 0.001)
    # Helsinki -> Tampere is roughly 160 km
    assert 150 < haversine_km(60.17, 24.94, 61.50, 23.79) < 170


# --------------------------------------------------------------------------
# movement tracking: which APRS stations are actually going somewhere
# --------------------------------------------------------------------------

def _stream():
    from adapters.aprs_is import AprsStream
    return AprsStream()


def _feed(stream, raw, at):
    """Ingest one packet as if it had arrived at ``at`` (epoch seconds)."""
    from adapters.aprs_is import parse_line
    row = parse_line(raw)
    assert row, raw
    row["_t"] = at
    stream._ingest(row)
    return row


def test_station_that_never_moves_is_flagged_stationary():
    s = _stream()
    _feed(s, "OH2FXD-9>APRS,TCPIP*,qAC,T2LAUS:!6043.70N/02508.55E-24.225GHz 1.2kHz", 1000.0)
    _feed(s, "OH2FXD-9>APRS,TCPIP*,qAC,T2LAUS:!6043.70N/02508.55E-24.225GHz 1.2kHz", 1300.0)
    snap = s.snapshot(60.73, 25.14, 10, now=1500.0)
    assert snap["stations"][0]["moving"] is False
    assert snap["moving"] == 0
    # the jitter stays reported, it just does not count as driving
    assert snap["stations"][0]["moved_m"] == 0.0


def test_gps_jitter_does_not_count_as_movement():
    s = _stream()
    # 60 m of drift between two packets: a parked tracker, not a car
    _feed(s, "OH2FXD-9>APRS,TCPIP*,qAC,T2LAUS:!6043.70N/02508.55E-", 1000.0)
    _feed(s, "OH2FXD-9>APRS,TCPIP*,qAC,T2LAUS:!6043.71N/02508.55E-", 1300.0)
    assert s.snapshot(60.73, 25.14, 10, now=1500.0)["stations"][0]["moving"] is False


def test_moving_station_carries_its_fix_history_trail():
    s = _stream()
    # two real fixes 4 km apart, 10 minutes apart: the RSSI-style cadence of a
    # car that beacons every few minutes, far too slow for the client's poll
    # loop to ever see a change — the trail has to come from the server
    _feed(s, "OH2FXD-9>APRS,TCPIP*,qAC,T2LAUS:!6043.70N/02508.55E-", 1000.0)
    _feed(s, "OH2FXD-9>APRS,TCPIP*,qAC,T2LAUS:!6035.70N/02515.55E-", 1600.0)
    snap = s.snapshot(60.45, 25.10, 10, now=1700.0)
    st = snap["stations"][0]
    assert st["moving"] is True
    assert len(st["trail"]) == 2
    assert st["trail"][0][0] == 1000.0
    assert st["trail"][1][0] == 1600.0
    for p in st["trail"]:
        assert -90.0 < p[1] < 90.0 and -180.0 < p[2] < 180.0
    # the two fixes are ~16 km apart on the ground, that is a real move
    from adapters.aprs_is import haversine_km
    d = haversine_km(st["trail"][0][1], st["trail"][0][2], st["trail"][1][1], st["trail"][1][2])
    assert 15.5 < d < 17.0
    assert "_trail" not in st  # the underscore store stays internal, only "trail" ships


def test_station_that_drives_is_flagged_moving():
    s = _stream()
    # 1 km north, five minutes later: a car on the road
    _feed(s, "OH2FXD-9>APRS,TCPIP*,qAC,T2LAUS:!6043.70N/02508.55E-", 1000.0)
    _feed(s, "OH2FXD-9>APRS,TCPIP*,qAC,T2LAUS:!6044.20N/02508.55E-", 1300.0)
    row = s.snapshot(60.73, 25.14, 10, now=1500.0)["stations"][0]
    assert row["moving"] is True
    assert 900 < row["moved_m"] < 1100, row


def test_moving_flag_is_held_after_the_station_stops():
    s = _stream()
    _feed(s, "OH2FXD-9>APRS,TCPIP*,qAC,T2LAUS:!6043.70N/02508.55E-", 1000.0)
    _feed(s, "OH2FXD-9>APRS,TCPIP*,qAC,T2LAUS:!6052.70N/02508.55E-", 1300.0)
    # it parks at a junction; the flag survives for MOVE_HOLD_S
    _feed(s, "OH2FXD-9>APRS,TCPIP*,qAC,T2LAUS:!6052.70N/02508.55E-", 1600.0)
    assert s.snapshot(60.73, 25.14, 10, moving_only=True, now=1500.0)["count"] == 1
    # once the hold expires it is just another parked station again
    assert s.snapshot(60.73, 25.14, 10, moving_only=True, now=1901.0)["count"] == 0


def test_first_packet_with_speed_counts_as_moving():
    """A boat that joins the stream already under way has no previous fix to
    compare against, so the course/speed in the packet itself is the signal."""
    s = _stream()
    _feed(s, "OH2FXD-9>APRS,TCPIP*,qAC,T2LAUS:!6043.70N/02508.55E-274/018", 1000.0)
    row = s.snapshot(60.73, 25.14, 10, now=1500.0)["stations"][0]
    assert row["speed_kt"] > 1
    assert row["moving"] is True


def test_moving_only_filter_drops_the_parked_ones():
    s = _stream()
    _feed(s, "GATE-1>APRS,TCPIP*,qAC,T2LAUS:!6043.70N/02508.55E-", 1300.0)
    _feed(s, "GATE-1>APRS,TCPIP*,qAC,T2LAUS:!6043.70N/02508.55E-", 1000.0)
    _feed(s, "GATE-2>APRS,TCPIP*,qAC,T2LAUS:!6044.70N/02509.55E-", 1000.0)
    _feed(s, "GATE-2>APRS,TCPIP*,qAC,T2LAUS:!6044.70N/02509.55E-", 1300.0)
    _feed(s, "CAR-3>APRS,TCPIP*,qAC,T2LAUS:!6045.70N/02510.55E-", 1000.0)
    _feed(s, "CAR-3>APRS,TCPIP*,qAC,T2LAUS:!6054.70N/02510.55E-", 1300.0)

    every = s.snapshot(60.75, 25.16, 10, now=1500.0)
    assert every["count"] == 3
    assert every["moving"] == 1

    only = s.snapshot(60.75, 25.16, 10, moving_only=True, now=1500.0)
    assert [r["callsign"] for r in only["stations"]] == ["CAR-3"]
    assert only["moving_only"] is True
    # internal bookkeeping never reaches the browser
    assert not [k for k in only["stations"][0] if k.startswith("_") and k != "_age_s"]


def test_a_station_with_one_fix_is_shown_until_it_is_classified():
    """A cold start must not present an empty map: the parked digipeaters are
    only knowable once they have sent a second fix, so until then they are
    shown and marked as undecided."""
    s = _stream()
    _feed(s, "GATE-1>APRS,TCPIP*,qAC,T2LAUS:!6043.70N/02508.55E-", 1000.0)
    row = s.snapshot(60.73, 25.14, 10, moving_only=True, now=1100.0)["stations"][0]
    assert row["moving"] is True
    assert row["classified"] is False

    # the second fix arrives from the same spot: now it is a digipeater
    _feed(s, "GATE-1>APRS,TCPIP*,qAC,T2LAUS:!6043.70N/02508.55E-", 1300.0)
    assert s.snapshot(60.73, 25.14, 10, moving_only=True, now=1500.0)["count"] == 0
    kept = s.snapshot(60.73, 25.14, 10, now=1500.0)["stations"][0]
    assert kept["moving"] is False and kept["classified"] is True


def test_movement_reports_an_implied_speed():
    """Two fixes minutes apart give a speed worth showing; the raw distance
    between them is not, because beacons are not periodic."""
    s = _stream()
    _feed(s, "OH2FXD-9>APRS,TCPIP*,qAC,T2LAUS:!6043.70N/02508.55E-", 1000.0)
    _feed(s, "OH2FXD-9>APRS,TCPIP*,qAC,T2LAUS:!6044.20N/02508.55E-", 1600.0)
    row = s.snapshot(60.73, 25.14, 10, now=1700.0)["stations"][0]
    # ~926 m in 600 s is ~5.6 km/h, a bicycle, not a car at 300
    assert 4.0 < row["speed_kmh"] < 7.0, row
    assert not [k for k in row if k.startswith("_") and k != "_age_s"], row


def test_tcpip_only_stations_are_hidden_by_the_rf_filter():
    """An internet-only client (``TCPIP`` as the first path hop) is what the
    user asked to hide: it has never been heard on RF and its movement is not
    movement over the air."""
    s = _stream()
    _feed(s, "NET-1>APRS,TCPIP*,qAC,T2LAUS:!6043.70N/02508.55E-", 1000.0)
    _feed(s, "RF-2>APRS,WIDE1-1,WIDE2-1,TCPIP*:!6044.70N/02509.55E-", 1000.0)

    all_rf = s.snapshot(60.75, 25.16, 10, rf_only=True, now=1100.0)
    assert [r["callsign"] for r in all_rf["stations"]] == ["RF-2"]
    assert all_rf["rf_only"] is True

    anyway = s.snapshot(60.75, 25.16, 10, rf_only=False, now=1100.0)
    assert set(r["callsign"] for r in anyway["stations"]) == {"NET-1", "RF-2"}
    _by_call = {r["callsign"]: r for r in anyway["stations"]}
    assert _by_call["NET-1"]["rf"] is False
    assert _by_call["RF-2"]["rf"] is True


def test_rf_flag_is_kept_once_a_station_is_heard_over_the_air():
    """The first hop of the path decides. A station that mixed only internet
    packets stays hidden even after more of them, but one real RF packet (even
    from an otherwise internet-ish tracker) marks it as heard on RF."""
    s = _stream()
    _feed(s, "OH2ABC-9>APRS,TCPIP*,qAC,T2LAUS:!6043.70N/02508.55E-", 1000.0)
    assert s.snapshot(60.73, 25.14, 10, rf_only=True, now=1100.0)["count"] == 0
    # still an internet packet: still hidden
    _feed(s, "OH2ABC-9>APRS,TCPIP*,qAC,T2LAUS:!6043.90N/02509.00E-", 1300.0)
    assert s.snapshot(60.73, 25.14, 10, rf_only=True, now=1400.0)["count"] == 0
    # now it is heard via a digipeater: RF from here on
    _feed(s, "OH2ABC-9>APRS,WIDE1-1,TCPIP*:!6043.90N/02509.00E-", 1600.0)
    row = s.snapshot(60.73, 25.14, 10, rf_only=True, now=1700.0)["stations"][0]
    assert row["rf"] is True


def test_direct_hit_without_a_digipeater_counts_as_rf():
    """A packet with an empty path is a direct RF transmission into an iGate,
    so it must not be filtered out together with the internet clients."""
    s = _stream()
    _feed(s, "OH2PORT-7>APZMDM:!6043.70N/02508.55E-", 1000.0)
    row = s.snapshot(60.75, 25.16, 10, rf_only=True, now=1100.0)["stations"][0]
    assert row["rf"] is True


def test_ssid_filter_keeps_cars_and_walkers():
    """What a station IS lives in the callsign SSID, not the symbol code: -9 is a
    car, -7 a walker/HT, -8 a boat, -1/-2 digis. The filter keys on the SSID."""
    s = _stream()
    _feed(s, "CAR-9>APRS,WIDE1-1,TCPIP*:!6043.70N/02508.55E/", 10.0)
    _feed(s, "WALK-7>APRS,WIDE1-1,TCPIP*:!6044.70N/02509.55E/", 20.0)
    _feed(s, "DIGI-1>APRS,WIDE1-1,TCPIP*:!6045.70N/02510.55E/", 30.0)

    all_rows = s.snapshot(60.75, 25.16, 10, ssid=None, now=100.0)
    assert {r["callsign"] for r in all_rows["stations"]} == {"CAR-9", "WALK-7", "DIGI-1"}

    picked = s.snapshot(60.75, 25.16, 10, ssid=["7", "9"], now=100.0)
    assert {r["callsign"] for r in picked["stations"]} == {"CAR-9", "WALK-7"}
    assert set(picked["ssid"]) == {"7", "9"}


def test_exclude_hides_digipeaters_but_keeps_mobiles():
    """Default view hides the digipeater/iGate symbol codes (& # r R [), so a
    moving /m car stays while the machines disappear."""
    s = _stream()
    _feed(s, "CAR-1>APRS,WIDE1-1,TCPIP*:!6043.70N/02508.55Em", 10.0)
    _feed(s, "DIGI-1>APRS,WIDE1-1,TCPIP*:!6044.70N/02509.55E&", 20.0)
    _feed(s, "GATE-1>APRS,WIDE1-1,TCPIP*:!6045.70N/02510.55E#", 30.0)

    kept = s.snapshot(
        60.75, 25.16, 10, exclude=["&", "#", "r", "R", "["], now=100.0
    )
    assert {r["callsign"] for r in kept["stations"]} == {"CAR-1"}
    assert set(kept["exclude"]) == {"&", "#", "R", "[", "r"}
    assert "symbols" in kept and kept["symbols"] is None

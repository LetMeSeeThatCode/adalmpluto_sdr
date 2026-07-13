#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
decoder.py — ADS-B live decoder
================================
Watches /tmp/adsb_bursts.txt for hex strings written by modes_receiver.py,
decodes them with pyModeS, prints a live terminal table with `rich`, and
writes aircraft/aircraft.json for the Leaflet map (map.html).

Install:
    pip install pyModeS rich

Usage:
    python3 decoder.py                        # default config
    python3 decoder.py --lat 51.2 --lon 6.8  # set receiver position
    python3 decoder.py --burst-file /tmp/adsb_bursts.txt --map-json ./aircraft.json
"""

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

try:
    import pyModeS as pms
except ImportError:
    sys.exit("pyModeS not found — run:  pip install pyModeS")

try:
    from rich.console import Console
    from rich.table import Table
    from rich.live import Live
    from rich import box
except ImportError:
    sys.exit("rich not found — run:  pip install rich")


# ─────────────────────────────────────────────────────────────────────────────
#  Config — override via CLI args or edit defaults here
# ─────────────────────────────────────────────────────────────────────────────

DEFAULTS = dict(
    burst_file  = "/tmp/adsb_bursts.txt",
    map_json    = str(Path(__file__).parent / "aircraft.json"),
    lat         = 0.0,       # receiver latitude  — SET THIS
    lon         = 0.0,       # receiver longitude — SET THIS
    stale_secs  = 60,        # remove aircraft not seen for this many seconds
    refresh_hz  = 2,         # terminal table refresh rate
)


# ─────────────────────────────────────────────────────────────────────────────
#  Aircraft state store
# ─────────────────────────────────────────────────────────────────────────────

class AircraftStore:
    """
    Holds the last-known state for every ICAO address seen.
    Uses pyModeS v2 API (pms.adsb.*, pms.df, pms.crc).
    CPR position pairs are resolved only when both even and odd frames
    for the same ICAO are available.
    """

    def __init__(self, ref_lat, ref_lon, stale_secs):
        self.ref_lat    = ref_lat
        self.ref_lon    = ref_lon
        self.stale_secs = stale_secs
        self._ac        = {}            # icao → dict of fields
        self._cpr_buf   = defaultdict(dict)   # icao → {0: (msg, ts), 1: (msg, ts)}

    # ── internal helpers ──────────────────────────────────────────────────

    def _get(self, icao):
        if icao not in self._ac:
            self._ac[icao] = {
                "icao":     icao,
                "callsign": None,
                "lat":      None,
                "lon":      None,
                "altitude": None,
                "speed":    None,
                "heading":  None,
                "vrate":    None,
                "squawk":   None,
                "last_seen": None,
                "msg_count": 0,
            }
        return self._ac[icao]

    def _touch(self, icao, ts):
        ac = self._get(icao)
        ac["last_seen"]  = ts
        ac["msg_count"] += 1

    # ── decode one hex string ─────────────────────────────────────────────

    def ingest(self, hex_str, ts=None):
        """
        Decode a single 28-character hex string (112-bit DF17/18 frame).
        Returns the ICAO string if the message was accepted, else None.
        """
        if ts is None:
            ts = time.time()

        if len(hex_str) != 28:
            return None

        # The GNU Radio block already validated CRC and DF; we re-check DF
        # here as a belt-and-suspenders guard.
        try:
            df = pms.df(hex_str)
        except Exception:
            return None

        if df not in (17, 18):
            return None

        if pms.crc(hex_str) != 0:
            return None

        try:
            icao = pms.adsb.icao(hex_str)
            tc   = pms.adsb.typecode(hex_str)
        except Exception:
            return None

        self._touch(icao, ts)
        ac = self._get(icao)

        try:
            # ── Identification (TC 1–4) ───────────────────────────────
            if 1 <= tc <= 4:
                cs = pms.adsb.callsign(hex_str)
                if cs:
                    ac["callsign"] = cs.strip("_").strip()

            # ── Airborne position (TC 9–18) ───────────────────────────
            elif 9 <= tc <= 18:
                alt = pms.adsb.altitude(hex_str)
                if alt is not None:
                    ac["altitude"] = alt

                # CPR position decoding requires an even+odd pair.
                oe = pms.adsb.oe_flag(hex_str)   # 0 = even, 1 = odd
                self._cpr_buf[icao][oe] = (hex_str, ts)

                if 0 in self._cpr_buf[icao] and 1 in self._cpr_buf[icao]:
                    msg_even, ts_even = self._cpr_buf[icao][0]
                    msg_odd,  ts_odd  = self._cpr_buf[icao][1]
                    # Most-recent message determines which is "last"
                    if ts_even >= ts_odd:
                        pos = pms.adsb.airborne_position(
                            msg_even, msg_odd, ts_even, ts_odd)
                    else:
                        pos = pms.adsb.airborne_position(
                            msg_even, msg_odd, ts_even, ts_odd)
                    if pos and pos[0] is not None:
                        ac["lat"], ac["lon"] = pos

            # ── Airborne velocity (TC 19) ─────────────────────────────
            elif tc == 19:
                vel = pms.adsb.velocity(hex_str)
                # returns (speed, heading, vertical_rate, speed_type)
                if vel and vel[0] is not None:
                    ac["speed"]   = round(vel[0])
                    ac["heading"] = round(vel[1]) if vel[1] is not None else None
                    ac["vrate"]   = round(vel[2]) if vel[2] is not None else None

        except Exception:
            # Partial decode is fine — just keep whatever we got
            pass

        return icao

    # ── housekeeping ──────────────────────────────────────────────────────

    def purge_stale(self):
        now    = time.time()
        cutoff = now - self.stale_secs
        stale  = [k for k, v in self._ac.items()
                  if v["last_seen"] and v["last_seen"] < cutoff]
        for k in stale:
            del self._ac[k]
            self._cpr_buf.pop(k, None)

    def snapshot(self):
        """Return a sorted list of aircraft dicts for display."""
        return sorted(self._ac.values(),
                      key=lambda a: a["last_seen"] or 0, reverse=True)


# ─────────────────────────────────────────────────────────────────────────────
#  Terminal table (rich)
# ─────────────────────────────────────────────────────────────────────────────

def build_table(aircraft_list, burst_file, total_msgs):
    now   = time.time()
    table = Table(
        title=f"[bold cyan]ADS-B Live Traffic[/]  •  "
              f"[dim]{datetime.now().strftime('%H:%M:%S')}[/]  •  "
              f"[dim]{total_msgs} msgs decoded[/]",
        box=box.SIMPLE_HEAD,
        show_lines=False,
        header_style="bold white on dark_blue",
        border_style="dim blue",
    )

    cols = [
        ("ICAO",     "cyan",      6),
        ("Callsign", "white",     9),
        ("Alt (ft)", "yellow",    8),
        ("Spd (kt)", "green",     8),
        ("Hdg (°)",  "green",     7),
        ("V/S",      "magenta",   7),
        ("Lat",      "blue",      9),
        ("Lon",      "blue",      10),
        ("Squawk",   "white",     7),
        ("Msgs",     "dim white", 5),
        ("Age (s)",  "dim white", 7),
    ]
    for name, style, width in cols:
        table.add_column(name, style=style, min_width=width, no_wrap=True)

    def fmt(v, fmt_str=None, fallback="—"):
        if v is None:
            return f"[dim]{fallback}[/]"
        return fmt_str.format(v) if fmt_str else str(v)

    for ac in aircraft_list:
        age = int(now - ac["last_seen"]) if ac["last_seen"] else "?"
        age_style = "red" if isinstance(age, int) and age > 30 else "dim white"
        table.add_row(
            ac["icao"].upper(),
            fmt(ac["callsign"]),
            fmt(ac["altitude"], "{:,}"),
            fmt(ac["speed"]),
            fmt(ac["heading"]),
            fmt(ac["vrate"]),
            fmt(ac["lat"], "{:.4f}"),
            fmt(ac["lon"], "{:.4f}"),
            fmt(ac["squawk"]),
            str(ac["msg_count"]),
            f"[{age_style}]{age}[/]",
        )

    if not aircraft_list:
        table.add_row(*["[dim]—[/]"] * len(cols))

    return table


# ─────────────────────────────────────────────────────────────────────────────
#  Map JSON writer
# ─────────────────────────────────────────────────────────────────────────────

def write_map_json(aircraft_list, path):
    features = []
    for ac in aircraft_list:
        if ac["lat"] is None or ac["lon"] is None:
            continue
        props = {k: v for k, v in ac.items() if k not in ("lat", "lon")}
        props["last_seen_str"] = (
            datetime.fromtimestamp(ac["last_seen"]).strftime("%H:%M:%S")
            if ac["last_seen"] else "?"
        )
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [ac["lon"], ac["lat"]],
            },
            "properties": props,
        })

    geojson = {
        "type": "FeatureCollection",
        "generated": datetime.utcnow().isoformat() + "Z",
        "features": features,
    }

    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(geojson, f, indent=2, default=str)
    os.replace(tmp, path)   # atomic replace so map.html never reads a partial file


# ─────────────────────────────────────────────────────────────────────────────
#  Burst file tail — reads only new lines without loading the whole file
# ─────────────────────────────────────────────────────────────────────────────

def tail_new_lines(path, pos):
    """
    Open `path`, seek to `pos`, yield new lines, return new file position.
    Creates the file if it doesn't exist yet.
    """
    Path(path).touch(exist_ok=True)
    with open(path, "r") as f:
        f.seek(pos)
        lines = f.readlines()
        new_pos = f.tell()
    return [l.strip() for l in lines if l.strip()], new_pos


# ─────────────────────────────────────────────────────────────────────────────
#  Main loop
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ADS-B live decoder")
    parser.add_argument("--burst-file", default=DEFAULTS["burst_file"])
    parser.add_argument("--map-json",   default=DEFAULTS["map_json"])
    parser.add_argument("--lat",        type=float, default=DEFAULTS["lat"],
                        help="Receiver latitude (for CPR decoding)")
    parser.add_argument("--lon",        type=float, default=DEFAULTS["lon"],
                        help="Receiver longitude (for CPR decoding)")
    parser.add_argument("--stale",      type=int,   default=DEFAULTS["stale_secs"],
                        help="Seconds before aircraft removed from table")
    parser.add_argument("--refresh",    type=float, default=1/DEFAULTS["refresh_hz"],
                        help="Table refresh interval in seconds")
    args = parser.parse_args()

    store      = AircraftStore(args.lat, args.lon, args.stale)
    console    = Console()
    file_pos   = 0
    total_msgs = 0
    tick       = 0

    console.print(
        f"[bold cyan]ADS-B Decoder[/]  watching [yellow]{args.burst_file}[/]\n"
        f"Map JSON → [yellow]{args.map_json}[/]\n"
        f"Receiver @ lat=[green]{args.lat}[/] lon=[green]{args.lon}[/]\n"
        f"Press [bold]Ctrl+C[/] to quit.\n"
    )

    with Live(build_table([], args.burst_file, 0),
              console=console, refresh_per_second=DEFAULTS["refresh_hz"],
              screen=False) as live:
        try:
            while True:
                # Read new hex strings from burst file
                new_lines, file_pos = tail_new_lines(args.burst_file, file_pos)

                for line in new_lines:
                    if store.ingest(line):
                        total_msgs += 1

                tick += 1
                # Purge stale aircraft every 10 ticks
                if tick % 10 == 0:
                    store.purge_stale()

                # Write map JSON every 5 ticks (~2.5 s)
                if tick % 5 == 0:
                    try:
                        write_map_json(store.snapshot(), args.map_json)
                    except OSError as e:
                        console.print(f"[red]Map write error: {e}[/]")

                live.update(build_table(store.snapshot(), args.burst_file, total_msgs))
                time.sleep(args.refresh)

        except KeyboardInterrupt:
            pass

    console.print("\n[bold]Bye.[/]")


if __name__ == "__main__":
    main()
"""Offline sunrise, for the `clock` intent and the morning briefing.

Stdlib only (math) - no network, no almanac file. This is the standard NOAA/Williams
sunrise algorithm (the one behind most "sunrise calculator" pages), accurate to a couple
of minutes, which is all a spoken answer needs.
"""
from __future__ import annotations

import datetime as dt
import math

ZENITH = 90.833  # 90 deg + ~50' atmospheric refraction + the sun's own radius


def sunrise(date: dt.date, lat: float, lon: float, tz_offset_h: float) -> dt.time | None:
    """Local sunrise clock time, or None if the sun does not rise that day at that latitude."""
    n = date.timetuple().tm_yday
    lng_hour = lon / 15
    t = n + ((6 - lng_hour) / 24)

    m = (0.9856 * t) - 3.289
    l = (m + (1.916 * math.sin(math.radians(m))) + (0.020 * math.sin(math.radians(2 * m)))
         + 282.634) % 360

    ra = math.degrees(math.atan(0.91764 * math.tan(math.radians(l)))) % 360
    # RA must be in the same quadrant as L, or the atan wraps it to the wrong side of the day.
    ra += (math.floor(l / 90) * 90) - (math.floor(ra / 90) * 90)
    ra /= 15

    sin_dec = 0.39782 * math.sin(math.radians(l))
    cos_dec = math.cos(math.asin(sin_dec))
    cos_h = (math.cos(math.radians(ZENITH)) - (sin_dec * math.sin(math.radians(lat)))) / (
        cos_dec * math.cos(math.radians(lat)))
    if cos_h > 1 or cos_h < -1:
        return None  # polar day/night - never rises or never sets, not reachable from India

    h = (360 - math.degrees(math.acos(cos_h))) / 15  # 360-x for sunrise, x alone for sunset
    local_h = h + ra - (0.06571 * t) - 6.622
    ut = (local_h - lng_hour) % 24
    local = (ut + tz_offset_h) % 24

    hh = int(local)
    mm = int(round((local - hh) * 60))
    if mm == 60:
        hh, mm = (hh + 1) % 24, 0
    return dt.time(hh, mm)


def demo():
    # Bengaluru, early October: sunrise is known to fall around 6:05-6:10 local time.
    t = sunrise(dt.date(2026, 10, 8), 12.9716, 77.5946, 5.5)
    assert t is not None and dt.time(5, 50) <= t <= dt.time(6, 25), t
    print("sunrise:", t)


if __name__ == "__main__":
    demo()

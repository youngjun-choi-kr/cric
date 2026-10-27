"""Sun elevation at a place and time (about 1 degree: enough for "is it daylight at liftoff")."""

from __future__ import annotations

import math
from datetime import datetime


def sun_elevation(t: datetime, lat: float, lon: float) -> float:
    doy = t.timetuple().tm_yday + (t.hour + t.minute / 60) / 24
    g = 2 * math.pi / 365.25 * (doy - 1)
    decl = 0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g) \
        + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g)
    eot = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g) - 0.014615 * math.cos(2 * g)
                    - 0.040849 * math.sin(2 * g))
    solar_min = t.hour * 60 + t.minute + t.second / 60 + eot + 4 * lon
    ha = math.radians(solar_min / 4 - 180)
    la = math.radians(lat)
    return math.degrees(math.asin(math.sin(la) * math.sin(decl) + math.cos(la) * math.cos(decl) * math.cos(ha)))

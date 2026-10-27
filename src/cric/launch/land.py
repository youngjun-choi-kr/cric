"""Does it come down on land? Natural Earth 1:50m land polygons (public domain), point-in-polygon.

Used by range safety: a nominal stage impact on land, or more than LAND_LIMIT of the dispersed impacts of any stage,
makes a launch no-go. At 1:50m small islands and coastlines are approximate (a few km), so this is a screening
check, not a range's hazard-area analysis.
"""

from __future__ import annotations

import json
from functools import lru_cache

import numpy as np

from ..config import ROOT

LAND_FILE = ROOT / "fixtures" / "geo" / "ne_50m_land.geojson"
LAND_LIMIT = 0.01          # share of dispersed impacts allowed on land


@lru_cache(maxsize=1)
def _polygons() -> list[tuple[np.ndarray, tuple[float, float, float, float]]]:
    out = []
    for f in json.loads(LAND_FILE.read_text())["features"]:
        g = f["geometry"]
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        for poly in polys:
            ring = np.asarray(poly[0], float)                 # outer ring; lakes are ignored (land either way)
            out.append((ring, (ring[:, 0].min(), ring[:, 0].max(), ring[:, 1].min(), ring[:, 1].max())))
    return out


def _inside(ring: np.ndarray, x: float, y: float) -> bool:
    xs, ys = ring[:, 0], ring[:, 1]
    xj, yj = np.roll(xs, 1), np.roll(ys, 1)
    cross = (ys > y) != (yj > y)
    with np.errstate(divide="ignore", invalid="ignore"):
        xi = (xj - xs) * (y - ys) / (yj - ys) + xs
    return bool(np.count_nonzero(cross & (x < xi)) % 2)


def on_land(lat: float, lon: float) -> bool:
    x = ((lon + 180) % 360) - 180
    for ring, (x0, x1, y0, y1) in _polygons():
        if x0 <= x <= x1 and y0 <= lat <= y1 and _inside(ring, x, lat):
            return True
    return False


def land_share(points: list) -> float:
    pts = [p for p in points if p]
    return sum(on_land(la, lo) for la, lo in pts) / len(pts) if pts else 0.0


def range_check(flight: dict, disp: dict | None = None) -> dict:
    """Nominal stage (and vehicle) impacts, and dispersed impact shares, against land."""
    nominal = []
    for s in flight.get("stage_impacts", []):
        if "lat" in s:
            nominal.append({"stage": s["stage"], "lat": s["lat"], "lon": s["lon"], "on_land": on_land(s["lat"], s["lon"])})
    if flight.get("impact"):
        i = flight["impact"]
        nominal.append({"stage": "whole vehicle", "lat": i["lat"], "lon": i["lon"], "on_land": on_land(i["lat"], i["lon"])})
    shares = {}
    if disp:
        for name, s in disp.get("stages", {}).items():
            shares[name] = round(land_share(s["impacts"]), 3)
        if disp.get("vehicle_impacts"):
            shares["whole vehicle"] = round(land_share(disp["vehicle_impacts"]), 3)
    bad = [n["stage"] for n in nominal if n["on_land"] and n["stage"] != "whole vehicle"] + \
          [k for k, v in shares.items() if v > LAND_LIMIT and k != "whole vehicle"]
    return {"nominal": nominal, "dispersed_land_share": shares, "land_limit": LAND_LIMIT,
            "status": "nogo" if bad else "go", "reasons": [f"{b} comes down on land" for b in dict.fromkeys(bad)],
            "source": "Natural Earth 1:50m land (public domain)"}

"""Where could it come down? Monte Carlo dispersion of the ascent, the way a launch range sizes its hazard areas.

Each run perturbs thrust (1 sigma 1.5%), specific impulse (0.5%), dry mass (2%), the stage-1 pitch (0.3 deg) and
the launch azimuth (0.3 deg), flies the same guidance, and records where the vehicle and every spent stage land
(or whether it reaches orbit). From the impact points it fits 50% and 95% ellipses on the ground.

It parallelises over processes on one machine and is written to run unchanged as a batch job
(Nebius Serverless Jobs): `cric launch-dispersion --vehicle electron --runs 5000 --out /data/disp.json`.
"""

from __future__ import annotations

import math
import os

import numpy as np

from .ascent import Guidance, simulate
from .vehicles import Vehicle

SIGMA = {"thrust": 0.015, "isp": 0.005, "dry": 0.02, "kick_add": 0.3, "az_add": 0.3}

def _one(arg) -> dict:
    j, seed = arg
    rng = np.random.default_rng(seed)
    p = {"thrust": 1 + rng.normal(0, SIGMA["thrust"]), "isp": 1 + rng.normal(0, SIGMA["isp"]),
         "dry": 1 + rng.normal(0, SIGMA["dry"]), "kick_add": rng.normal(0, SIGMA["kick_add"]),
         "az_add": rng.normal(0, SIGMA["az_add"])}
    r = simulate(j["veh"], j["payload"], j["g"], lat=j["lat"], lon=j["lon"], az=j["az"],
                 target_alt_km=j["target"], dt=1.0, record=False, perturb=p)
    return {"orbit": r["orbit"], "target": r["target_reached"], "perigee_km": r["perigee_km"],
            "impact": r.get("impact"), "stages": [s for s in r["stage_impacts"] if "lat" in s]}


def _ellipse(points: list[tuple[float, float]], lat0: float, lon0: float) -> dict | None:
    """Mean and 50% / 95% ellipses in a local east-north plane (km), returned as lat/lon outlines."""
    if len(points) < 5:
        return None
    k = math.cos(math.radians(lat0))
    xy = np.array([((lo - lon0 + 540) % 360 - 180) * 111.32 * k for la, lo in points]), np.array([(la - lat0) * 110.57 for la, lo in points])
    xy = np.vstack(xy)
    mu = xy.mean(axis=1)
    cov = np.cov(xy)
    vals, vecs = np.linalg.eigh(cov)
    out = {"center": [round(float(lat0 + mu[1] / 110.57), 4), round(float(lon0 + mu[0] / (111.32 * k)), 4)]}
    for name, chi2 in (("p50", 1.386), ("p95", 5.991)):
        ring = []
        for a in np.linspace(0, 2 * math.pi, 49):
            v = vecs @ (np.sqrt(np.maximum(vals, 1e-9) * chi2) * np.array([math.cos(a), math.sin(a)])) + mu
            ring.append([round(float(lat0 + v[1] / 110.57), 4), round(float(lon0 + v[0] / (111.32 * k)), 4)])
        out[name] = ring
        out[f"{name}_axes_km"] = [round(float(math.sqrt(max(x, 0) * chi2)), 1) for x in vals[::-1]]
    return out


def _regional(e: dict | None, max_km: float = 2500.0) -> dict | None:
    """An ellipse is only meaningful when impacts cluster; a vehicle that nearly reaches orbit re-enters
    anywhere along its ground track, and then the scatter of points is the honest picture."""
    return e if e and e["p95_axes_km"][0] <= max_km else None


def _fall_time_by_band(mass_kg: float, area_m2: float, cd: float = 1.0) -> list[tuple[float, float, float]]:
    """Seconds a tumbling body spends in each 0.5 km band below 12 km at terminal speed: (lo_km, hi_km, seconds)."""
    from .ascent import density

    out = []
    for lo in np.arange(0.0, 12.0, 0.5):
        rho = density((lo + 0.25) * 1000)
        vt = math.sqrt(2 * mass_kg * 9.81 / max(rho * cd * area_m2, 1e-9))
        out.append((lo, lo + 0.5, 500.0 / vt))
    return out


def _wind_at(profile: dict, alt_km: float) -> tuple[float, float]:
    lv = profile["levels"]
    if alt_km <= lv[0]["alt_km"]:
        return lv[0]["east_ms"], lv[0]["north_ms"]
    for a, b in zip(lv, lv[1:]):
        if alt_km <= b["alt_km"]:
            f = (alt_km - a["alt_km"]) / (b["alt_km"] - a["alt_km"])
            return a["east_ms"] + f * (b["east_ms"] - a["east_ms"]), a["north_ms"] + f * (b["north_ms"] - a["north_ms"])
    return lv[-1]["east_ms"], lv[-1]["north_ms"]


def wind_drift_km(profile: dict, mass_kg: float, area_m2: float, scale: float = 1.0, turn_deg: float = 0.0) -> tuple[float, float]:
    """(east, north) km a falling body is carried by the wind on its way down through the lower atmosphere."""
    e = n = 0.0
    c, s = math.cos(math.radians(turn_deg)), math.sin(math.radians(turn_deg))
    for lo, hi, dt in _fall_time_by_band(mass_kg, area_m2):
        we, wn = _wind_at(profile, (lo + hi) / 2)
        e += (we * c + wn * s) * scale * dt
        n += (-we * s + wn * c) * scale * dt
    return float(e / 1000), float(n / 1000)


def _shift(lat: float, lon: float, east_km: float, north_km: float) -> tuple[float, float]:
    return (lat + north_km / 110.57, lon + east_km / (111.32 * max(math.cos(math.radians(lat)), 0.05)))


def dispersion(veh: Vehicle, payload_kg: float, g: Guidance, *, lat: float, lon: float, az: float,
               target_alt_km: float, runs: int = 400, workers: int | None = None, seed: int = 0,
               wind: dict | None = None) -> dict:
    """wind: a forecast profile (windows.wind_profile); falling stages drift with it, with 20% speed and 15 deg
    direction error per run (forecast uncertainty)."""
    from .ascent import pool

    job = {"veh": veh, "payload": payload_kg, "g": g, "lat": lat, "lon": lon, "az": az, "target": target_alt_km}
    args = [(job, seed * 1_000_003 + i) for i in range(runs)]
    if workers == 1:
        rows = [_one(a) for a in args]
    else:
        rows = list(pool().map(_one, args, chunksize=max(1, runs // ((os.cpu_count() or 1) * 4))))
    drift_mean = {}
    if wind:                                           # stage impacts carried by the forecast wind on the way down
        rng = np.random.default_rng(seed + 7)
        bodies = {s.name: (s.dry_kg, 3 * math.pi * (veh.diameter_m / 2) ** 2) for s in veh.stages}
        last = veh.stages[-1]
        for r in rows:
            sc, turn = 1 + rng.normal(0, 0.2), rng.normal(0, 15)
            for st in r["stages"]:
                m_, a_ = bodies.get(st["stage"], (100.0, 1.0))
                de, dn = wind_drift_km(wind, m_, a_, sc, turn)
                st["lat"], st["lon"] = _shift(st["lat"], st["lon"], de, dn)
                drift_mean.setdefault(st["stage"], []).append((de, dn))
            if r.get("impact"):
                de, dn = wind_drift_km(wind, last.dry_kg + payload_kg, 3 * math.pi * (veh.diameter_m / 2) ** 2, sc, turn)
                r["impact"]["lat"], r["impact"]["lon"] = _shift(r["impact"]["lat"], r["impact"]["lon"], de, dn)
    impacts = [(r["impact"]["lat"], r["impact"]["lon"]) for r in rows if r.get("impact")]
    out = {"runs": runs, "sigma": SIGMA, "p_orbit": round(sum(r["orbit"] for r in rows) / runs, 3),
           "p_target": round(sum(r["target"] for r in rows) / runs, 3),
           "vehicle_impacts": [list(x) for x in impacts[:600]],
           "vehicle_ellipse": _regional(_ellipse(impacts, *impacts[0])) if impacts else None,
           "stages": {},
           "wind": None if not wind else wind | {"mean_drift_km": {k: {"east": round(float(np.mean([x[0] for x in v])), 2),
                                                                      "north": round(float(np.mean([x[1] for x in v])), 2)}
                                                                  for k, v in drift_mean.items()}}}
    names = sorted({s["stage"] for r in rows for s in r["stages"]})
    for n in names:
        pts = [(s["lat"], s["lon"]) for r in rows for s in r["stages"] if s["stage"] == n]
        out["stages"][n] = {"impacts": [list(x) for x in pts[:300]], "ellipse": _regional(_ellipse(pts, *pts[0]))}
    return out

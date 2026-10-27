"""When and from where to launch: weather, space weather and launch collision avoidance (LCOLA).

Go / no-go per hour and site follows simplified commit criteria. The thresholds are our assumptions for a small
launcher, stated in CRITERIA; real ranges publish their own, stricter rules (lightning, field mill, ceilings).
Weather: Open-Meteo hourly forecast (public, no key). Space weather: NOAA SWPC Kp forecast.

LCOLA: for each candidate liftoff minute, the simulated insertion orbit is propagated through its first orbit
(two-body + J2) and compared with every catalogued object whose altitude band overlaps it, using SGP4 for the
catalog and a straight-line closest-approach prediction between samples. Screening grade (TLE data), as the
rest of CRIC: it tells which minutes to avoid, not a precise miss distance.
"""

from __future__ import annotations

import json
import math
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import requests
from scipy.spatial import cKDTree

from ..config import DATA
from ..physics.maneuver import alt_band
from ..physics.timeutil import iso, jdfr, parse_utc
from .geo import SITES, azimuths_for, in_corridor
from .insertion import insertion_teme

CRITERIA = {   # assumed thresholds (small launcher), not any range's official rules
    "gust_ms": (12.0, 15.0), "wind_ms": (8.0, 11.0), "precip_pct": (30, 60), "cape_jkg": (300, 1000),
    "wind250_ms": (50.0, 70.0), "kp": (6.0, 8.0), "lcola_km": (10.0, 2.0),
}
WX_CACHE = DATA / "weather"
LEVELS = ((850, 1.5), (700, 3.0), (500, 5.6), (300, 9.2), (250, 10.4))   # pressure level, standard altitude km


def wind_profile(site_id: str, when: datetime | None = None) -> dict | None:
    """Forecast wind (east, north m/s) by altitude at the site for the hour, for the impact drift of falling stages."""
    try:
        w = weather(site_id)["hourly"]
    except requests.RequestException:
        return None
    if "wind_speed_850hPa" not in w:              # an older cache without the levels
        (WX_CACHE / f"{site_id}.json").unlink(missing_ok=True)
        try:
            w = weather(site_id)["hourly"]
        except requests.RequestException:
            return None
    key = (when or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:00")
    k = w["time"].index(key) if key in w["time"] else 0
    rows = [(0.01, w["wind_speed_10m"][k], w.get("wind_direction_10m", [None] * (k + 1))[k])]
    rows += [(alt, w[f"wind_speed_{p}hPa"][k], w[f"wind_direction_{p}hPa"][k]) for p, alt in LEVELS]
    prof = []
    for alt, spd, frm in rows:
        if spd is None or frm is None:
            continue
        to = math.radians((frm + 180) % 360)          # meteorological direction is where the wind comes from
        prof.append((alt, spd * math.sin(to), spd * math.cos(to)))
    return {"hour_utc": w["time"][k] + ":00Z", "levels": [{"alt_km": a, "east_ms": round(e, 1), "north_ms": round(n, 1)} for a, e, n in prof],
            "source": "Open-Meteo hourly forecast, pressure levels"} if len(prof) >= 3 else None


def weather(site_id: str, days: int = 7) -> dict:
    WX_CACHE.mkdir(parents=True, exist_ok=True)
    path = WX_CACHE / f"{site_id}.json"
    if path.exists() and time.time() - path.stat().st_mtime < 3600:
        return json.loads(path.read_text())
    s = SITES[site_id]
    r = requests.get("https://api.open-meteo.com/v1/forecast", timeout=30, params={
        "latitude": s.lat, "longitude": s.lon, "forecast_days": days, "timezone": "UTC", "wind_speed_unit": "ms",
        "hourly": "wind_speed_10m,wind_gusts_10m,precipitation_probability,cloud_cover,cape,wind_speed_250hPa,"
                  "wind_direction_10m," + ",".join(f"wind_speed_{p}hPa,wind_direction_{p}hPa" for p, _ in LEVELS)})
    r.raise_for_status()
    data = r.json()
    path.write_text(json.dumps(data))
    return data


def kp_by_hour() -> dict[str, float]:
    from ..ingest.swpc import KP_FORECAST_URL, _get

    out = {}
    for row in _get(KP_FORECAST_URL, "kp_forecast.json"):
        t = datetime.fromisoformat(row["time_tag"]).replace(tzinfo=timezone.utc)
        for h in range(3):
            out[(t + timedelta(hours=h)).strftime("%Y-%m-%dT%H:00")] = float(row["kp"])
    return out


def _grade(value, caution, nogo, higher_is_worse=True):
    if value is None:
        return "n/a"
    if higher_is_worse:
        return "nogo" if value >= nogo else "caution" if value >= caution else "go"
    return "nogo" if value <= nogo else "caution" if value <= caution else "go"


def assess_hour(h: dict, kp: float | None) -> dict:
    checks = {
        "gusts": (h.get("wind_gusts_10m"), *CRITERIA["gust_ms"], "m/s"),
        "ground wind": (h.get("wind_speed_10m"), *CRITERIA["wind_ms"], "m/s"),
        "rain": (h.get("precipitation_probability"), *CRITERIA["precip_pct"], "%"),
        "lightning (CAPE)": (h.get("cape"), *CRITERIA["cape_jkg"], "J/kg"),
        "upper winds 250 hPa": (h.get("wind_speed_250hPa"), *CRITERIA["wind250_ms"], "m/s"),
        "space weather (Kp)": (kp, *CRITERIA["kp"], ""),
    }
    items, worst = [], "go"
    rank = {"go": 0, "n/a": 0, "caution": 2, "nogo": 3}   # n/a: not forecast that far (Kp: ~3 days)
    for name, (v, c, n, unit) in checks.items():
        g = _grade(v, c, n)
        items.append({"check": name, "value": v, "unit": unit, "status": g, "caution": c, "nogo": n})
        if rank[g] > rank[worst]:
            worst = g
    return {"status": worst, "checks": items, "reasons": [f"{i['check']} {i['value']}{i['unit']}" for i in items
                                                         if i["status"] in ("nogo", "caution")]}


def heatmap(incl_deg: float, *, days: int = 7, sites: list[str] | None = None) -> dict:
    """Hourly go / caution / no-go for every site that can reach the inclination over a safe corridor."""
    kp = {}
    try:
        kp = kp_by_hour()
    except requests.RequestException:
        pass
    rows = []
    for sid in sites or list(SITES):
        s = SITES[sid]
        azs = [a for a in azimuths_for(s.lat, incl_deg) if in_corridor(s, a)]
        if not azs:
            rows.append({"site": sid, "name": s.name, "reachable": False, "why": "inclination not reachable over this "
                         "site's corridor" if azimuths_for(s.lat, incl_deg) else "inclination below the site latitude"})
            continue
        try:
            w = weather(sid, days)["hourly"]
        except requests.RequestException as exc:
            rows.append({"site": sid, "name": s.name, "reachable": True, "azimuth_deg": azs[0], "error": str(exc)[:120]})
            continue
        hours = []
        for k, t in enumerate(w["time"]):
            h = {key: (w[key][k] if w.get(key) else None) for key in w if key != "time"}
            a = assess_hour(h, kp.get(t))
            hours.append({"t": t + ":00Z", "status": a["status"], "reasons": a["reasons"], "checks": a["checks"],
                          "cloud_cover": h.get("cloud_cover")})
        rows.append({"site": sid, "name": s.name, "reachable": True, "azimuth_deg": round(azs[0], 2), "lat": s.lat,
                     "lon": s.lon, "hours": hours})
    return {"incl_deg": incl_deg, "criteria": CRITERIA, "rows": rows,
            "sources": ["Open-Meteo hourly forecast", "NOAA SWPC Kp forecast"]}


# ---------------------------------------------------------------- LCOLA
J2, RE_KM, MU_KM = 1.08263e-3, 6378.137, 398600.4418


def _acc(r):
    x, y, z = r
    rn = math.sqrt(x * x + y * y + z * z)
    k = 1.5 * J2 * MU_KM * RE_KM ** 2 / rn ** 5
    zz = 5 * z * z / (rn * rn)
    f = -MU_KM / rn ** 3
    return np.array([f * x + k * x * (zz - 1), f * y + k * y * (zz - 1), f * z + k * z * (zz - 3)])


def _rk4(r, v, h):
    k1v, k1r = _acc(r), v
    k2v, k2r = _acc(r + h / 2 * k1r), v + h / 2 * k1v
    k3v, k3r = _acc(r + h / 2 * k2r), v + h / 2 * k2v
    k4v, k4r = _acc(r + h * k3r), v + h * k3v
    return r + h / 6 * (k1r + 2 * k2r + 2 * k3r + k4r), v + h / 6 * (k1v + 2 * k2v + 2 * k3v + k4v)


def _propagate(r0, v0, steps: int, dt: float, first: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """States at first, first + dt, ... (seconds after r0, v0)."""
    r, v = np.array(r0, float), np.array(v0, float)
    if first > 0:
        r, v = _rk4(r, v, first)
    R, V = np.empty((steps, 3)), np.empty((steps, 3))
    for k in range(steps):
        R[k], V[k] = r, v
        r, v = _rk4(r, v, dt)
    return R, V


# Pc for the first orbit: assumed 1-sigma position uncertainty (radial, along-track, normal) of a satellite just
# inserted (no tracking yet) and of a catalogued object on public TLE data; combined hard-body radius 10 m.
SIG_NEW, SIG_CAT, HBR_KM = (1.0, 6.0, 1.0), (0.4, 2.0, 0.4), 0.010
PC_LIMITS = (1e-4, 1e-5)          # no-go, caution: the same thresholds as CRIC's operations policy


def _pc(r1, v1, r2, v2) -> float:
    from ..physics.cdm import pc_foster, rtn_to_inertial

    c1 = rtn_to_inertial(r1, v1, np.diag(np.square(SIG_NEW)))
    c2 = rtn_to_inertial(r2, v2, np.diag(np.square(SIG_CAT)))
    return pc_foster(r1, v1, c1, r2, v2, c2, HBR_KM)


def lcola(result: dict, start: datetime, cat, *, minutes: int = 60, step_min: int = 1, horizon_min: float = 95,
          dt: float = 20.0) -> dict:
    """Closest predicted approach to any catalogued object in the first orbit, for each liftoff minute."""
    if not result.get("orbit"):
        return {"error": "the simulated rocket does not reach orbit; nothing to screen"}
    peri, apo = result["perigee_km"], result["apogee_km"] or result["perigee_km"]
    ids = [n for n, s in cat.sats.items() if alt_band(s)[0] <= apo + 50 and alt_band(s)[1] >= peri - 50]
    from sgp4.api import SatrecArray

    arr = SatrecArray([cat.sats[n] for n in ids])
    t_ins = result["insertion"]["t"]
    span = minutes * 60 + t_ins + horizon_min * 60
    offs = np.arange(0.0, span + dt, dt)
    jd, fr = jdfr(start, offs)
    err, P, V = arr.sgp4(jd, fr)
    P[err != 0] = np.nan
    steps = int(horizon_min * 60 / dt)
    trees = {}
    radius = CRITERIA["lcola_km"][0] + 16.0 * dt / 2
    rows = []
    for m in range(0, minutes, step_min):
        t0 = start + timedelta(minutes=m)
        r0, v0, epoch = insertion_teme(result, t0)
        k_ins = (epoch - start).total_seconds() / dt
        k0 = math.ceil(k_ins)                         # our samples on the catalog's time grid, so positions compare
        R, Vo = _propagate(r0, v0, steps, dt, first=(k0 - k_ins) * dt)   # at the same instant
        best = (math.inf, None, None, None)
        for k in range(steps):
            ka = k0 + k
            if ka >= len(offs):
                break
            if ka not in trees:
                pk = P[:, ka, :]
                ok = np.isfinite(pk).all(axis=1)
                trees[ka] = (cKDTree(pk[ok]), np.where(ok)[0])
            tree, idx = trees[ka]
            hits = tree.query_ball_point(R[k], radius)
            for h in hits:
                j = idx[h]
                rel, rv = R[k] - P[j, ka], Vo[k] - V[j, ka]
                tt = max(-dt / 2, min(dt / 2, -float(rel @ rv) / max(float(rv @ rv), 1e-9)))
                d = float(np.linalg.norm(rel + rv * tt))
                if d < best[0]:
                    best = (d, ids[j], ((ka - k_ins) * dt + tt) / 60,
                            (R[k] + Vo[k] * tt, Vo[k], P[j, ka] + V[j, ka] * tt, V[j, ka]))
        c, n = CRITERIA["lcola_km"]
        status = "nogo" if best[0] <= n else "caution" if best[0] <= c else "go"
        pc = None
        if best[3] is not None:
            pc = _pc(*best[3])
            pcs = "nogo" if pc >= PC_LIMITS[0] else "caution" if pc >= PC_LIMITS[1] else "go"
            status = max(status, pcs, key=["go", "caution", "nogo"].index)
        rows.append({"liftoff_utc": iso(t0), "status": status,
                     "closest_km": None if not math.isfinite(best[0]) else round(best[0], 2),
                     "object": cat.names.get(best[1]) if best[1] else None, "norad_id": best[1],
                     "minutes_after_insertion": None if best[2] is None else round(best[2], 1),
                     "pc": None if pc is None else float(f"{pc:.2e}")})
    return {"start_utc": iso(start), "objects_screened": len(ids), "horizon_min": horizon_min, "rows": rows,
            "criteria_km": {"caution": CRITERIA["lcola_km"][0], "nogo": CRITERIA["lcola_km"][1]},
            "criteria_pc": {"caution": PC_LIMITS[1], "nogo": PC_LIMITS[0], "sigma_new_rtn_km": SIG_NEW, "sigma_catalog_rtn_km": SIG_CAT,
                            "hard_body_m": HBR_KM * 1000},
            "note": "screening grade (public TLE data); a real launch uses a provider's LCOLA with covariance"}


def parse_start(s: str | None) -> datetime:
    return parse_utc(s) if s else datetime.now(timezone.utc).replace(second=0, microsecond=0)

"""Impulsive avoidance burns: physical consequence of a decision.

Baseline orbits come from SGP4. The effect of a small burn is modelled with
Clohessy-Wiltshire relative motion in the manoeuvring satellite's LVLH frame and
added on top of the SGP4 path, so SGP4 keeps the large effects (J2, drag) and the
linear model only carries the metres-to-kilometres change. validate_linear_model()
checks that against a numerical two-body integration.

Ported from spike/05_avoidance.py.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import numpy as np
from scipy.integrate import solve_ivp
from sgp4.api import Satrec, SatrecArray

from ..ingest.celestrak import Catalog
from .timeutil import iso, jdfr

MU = 398600.4418  # km^3/s^2
RE = 6378.137     # km
AXES = {  # LVLH: x radial (up), y along-track (forward), z orbit normal
    "prograde": (0, 1, 0), "retrograde": (0, -1, 0),
    "radial_out": (1, 0, 0), "radial_in": (-1, 0, 0),
    "normal": (0, 0, 1), "anti_normal": (0, 0, -1),
}


def propagate(rec: Satrec, base: datetime, offs) -> tuple[np.ndarray, np.ndarray]:
    offs = np.atleast_1d(np.asarray(offs, dtype=float))
    err, r, v = rec.sgp4_array(*jdfr(base, offs))
    if np.any(err != 0):
        raise ValueError(f"SGP4 error code(s) {sorted(set(err[err != 0]))} for NORAD {rec.satnum}")
    return r, v


def lvlh(r: np.ndarray, v: np.ndarray):
    x = r / np.linalg.norm(r, axis=-1, keepdims=True)
    z = np.cross(r, v)
    z /= np.linalg.norm(z, axis=-1, keepdims=True)
    return x, np.cross(z, x), z


def alt_band(rec: Satrec) -> tuple[float, float]:
    a = (MU / (rec.no_kozai / 60.0) ** 2) ** (1 / 3)
    return a * (1 - rec.ecco) - RE, a * (1 + rec.ecco) - RE


def cw_displacement(tau: np.ndarray, n: float, dv_ms) -> np.ndarray:
    """Position change (km, LVLH at time tau) from an impulse dv_ms (m/s, LVLH) applied at tau = 0."""
    dvx, dvy, dvz = np.asarray(dv_ms, dtype=float) / 1000.0
    tau = np.maximum(tau, 0.0)
    s, c = np.sin(n * tau), np.cos(n * tau)
    return np.stack([
        s / n * dvx + 2 / n * (1 - c) * dvy,
        2 / n * (c - 1) * dvx + (4 * s - 3 * n * tau) / n * dvy,
        s / n * dvz,
    ], axis=-1)


def to_eci(d_lvlh: np.ndarray, basis) -> np.ndarray:
    bx, by, bz = basis
    return d_lvlh[:, 0, None] * bx + d_lvlh[:, 1, None] * by + d_lvlh[:, 2, None] * bz


def min_distance(rel: np.ndarray, t: np.ndarray) -> tuple[float, float, bool]:
    """Minimum of |rel(t)| from samples via a parabola through the three samples around the minimum of d^2.
    Third value is False when the minimum sits at the window edge."""
    d2 = np.einsum("ij,ij->i", rel, rel)
    i = int(np.argmin(d2))
    if 0 < i < len(t) - 1:
        a, b, c = d2[i - 1], d2[i], d2[i + 1]
        denom = a - 2 * b + c
        if denom > 0:
            dt = t[1] - t[0]
            shift = 0.5 * (a - c) / denom
            return math.sqrt(max(b - (c - a) ** 2 / (8 * denom), 0.0)), float(t[i] + shift * dt), True
        return math.sqrt(b), float(t[i]), True
    return math.sqrt(d2[i]), float(t[i]), False


class Encounter:
    """Baseline SGP4 data on a fine grid around TCA, reused for every candidate burn."""

    def __init__(self, rec_m: Satrec, rec_o: Satrec, tca: datetime, half=6.0, dt=0.02):
        self.rec_m, self.rec_o, self.tca = rec_m, rec_o, tca
        self.og = np.arange(-half, half + dt / 2, dt)
        self.rM, self.vM = propagate(rec_m, tca, self.og)
        self.rO, self.vO = propagate(rec_o, tca, self.og)
        self.basis = lvlh(self.rM, self.vM)
        self.i_tca = len(self.og) // 2
        self.nominal = min_distance(self.rM - self.rO, self.og)
        self.rel_speed = float(np.linalg.norm(self.vM[self.i_tca] - self.vO[self.i_tca]))
        self.period = 2 * math.pi / (rec_m.no_kozai / 60.0)
        self._burn_cache: dict[float, tuple[float, np.ndarray]] = {}

    def burn_frame(self, lead_s: float) -> tuple[float, np.ndarray]:
        if lead_s not in self._burn_cache:
            r, _ = propagate(self.rec_m, self.tca, [-lead_s])
            self._burn_cache[lead_s] = (math.sqrt(MU / np.linalg.norm(r[0]) ** 3), self.og + lead_s)
        return self._burn_cache[lead_s]

    def miss_after(self, lead_s: float, dv_ms) -> tuple[float, float, bool]:
        n, tau = self.burn_frame(lead_s)
        rM2 = self.rM + to_eci(cw_displacement(tau, n, dv_ms), self.basis)
        return min_distance(rM2 - self.rO, self.og)


def search(enc: Encounter, safe_km: float, max_dv: float, min_lead_s: float, max_lead_s: float,
           lead_step_frac: float = 0.05) -> list[dict]:
    """Smallest grid burn per (lead, direction) reaching safe_km. Sorted by delta-v."""
    mags = np.geomspace(0.005, max_dv, 40)
    leads = np.arange(max(min_lead_s, 0.2 * enc.period), max_lead_s, lead_step_frac * enc.period)
    found = []
    for lead in leads:
        lead = float(round(lead))
        for name, axis in AXES.items():
            for mag in mags:
                miss, _, inside = enc.miss_after(lead, np.array(axis) * mag)
                if inside and miss >= safe_km:
                    found.append({"lead_s": lead, "direction": name, "dv_ms": float(mag), "miss_km": miss})
                    break
    found.sort(key=lambda c: (c["dv_ms"], c["lead_s"]))
    return found


def refine_dv(enc: Encounter, c: dict, safe_km: float) -> dict:
    """Bisect the magnitude between the last failing and first passing grid value."""
    axis = np.array(AXES[c["direction"]])
    lo, hi = c["dv_ms"] / 1.2, c["dv_ms"]
    for _ in range(18):
        mid = 0.5 * (lo + hi)
        miss, _, inside = enc.miss_after(c["lead_s"], axis * mid)
        lo, hi = (lo, mid) if (inside and miss >= safe_km) else (mid, hi)
    miss, t_star, _ = enc.miss_after(c["lead_s"], axis * hi)
    return {**c, "dv_ms": hi, "miss_km": miss, "tca_shift_s": t_star}


def rescreen(enc: Encounter, cat: Catalog, burn: dict, threshold_km: float, *, step_s=2.0, margin_km=30.0,
             after_tca_s=900.0) -> dict:
    """Does the burn bring the manoeuvring satellite close to any other catalogued object
    between the burn and TCA + after_tca_s?"""
    rec_m = enc.rec_m
    lead = burn["lead_s"]
    axis = np.array(AXES[burn["direction"]]) * burn["dv_ms"]
    n, _ = enc.burn_frame(lead)
    t_off = np.arange(-lead, after_tca_s + step_s, step_s)
    rM, vM = propagate(rec_m, enc.tca, t_off)
    rM_new = rM + to_eci(cw_displacement(t_off + lead, n, axis), lvlh(rM, vM))

    lo_m, hi_m = alt_band(rec_m)
    lo_m -= margin_km
    hi_m += margin_km
    skip = int(rec_m.satnum)
    ids = [i for i, rec in cat.sats.items() if i != skip and alt_band(rec)[1] >= lo_m and alt_band(rec)[0] <= hi_m]
    recs = [cat.sats[i] for i in ids]
    best_nom, best_new = np.full(len(ids), np.inf), np.full(len(ids), np.inf)
    t_new, t_nom = np.zeros(len(ids)), np.zeros(len(ids))
    arr = SatrecArray(recs)
    partner = int(enc.rec_o.satnum)
    pidx = ids.index(partner) if partner in ids else None
    for c0 in range(0, len(t_off), 250):
        sl = slice(c0, c0 + 250)
        err, r, _ = arr.sgp4(*jdfr(enc.tca, t_off[sl]))
        r[err != 0] = np.nan
        for best, tb, ref in ((best_nom, t_nom, rM[sl]), (best_new, t_new, rM_new[sl])):
            d = np.linalg.norm(r - ref[None, :, :], axis=2)
            d = np.where(np.isnan(d), np.inf, d)
            if pidx is not None:  # the encounter being resolved is evaluated separately
                d[pidx, np.abs(t_off[sl]) < 60.0] = np.inf
            k = d.argmin(axis=1)
            val = d[np.arange(len(ids)), k]
            upd = val < best
            best[upd] = val[upd]
            tb[upd] = t_off[sl][k[upd]]

    reach = 16.0 * step_s / 2
    cands = np.where((best_new < threshold_km + reach) | (best_nom < threshold_km + reach))[0]
    events, grid = [], np.arange(-4.0, 4.0 + 0.01, 0.02)
    for j in cands:
        rec_o = cat.sats[ids[j]]
        out = {}
        for label, center in (("new", t_new[j]), ("nominal", t_nom[j])):
            t = center + grid
            rm, vm = propagate(rec_m, enc.tca, t)
            ro, _ = propagate(rec_o, enc.tca, t)
            if label == "new":
                rm = rm + to_eci(cw_displacement(t + lead, n, axis), lvlh(rm, vm))
            out[label] = min_distance(rm - ro, t)[0]
        if min(out.values()) < threshold_km:
            events.append({"norad_id": ids[j], "name": cat.names.get(ids[j], str(ids[j])),
                           "miss_nominal_km": round(out["nominal"], 3), "miss_after_km": round(out["new"], 3),
                           "tca_offset_s": round(float(t_new[j]), 1),
                           "introduced_by_burn": out["new"] < threshold_km <= out["nominal"]})
    events.sort(key=lambda e: e["miss_after_km"])
    return {"objects_checked": len(ids), "threshold_km": threshold_km, "events": events,
            "new_conjunctions": [e for e in events if e["introduced_by_burn"]]}


def validate_linear_model(enc: Encounter, burn: dict) -> dict:
    """CW displacement at TCA vs numerical two-body integration of the same impulse."""
    lead = burn["lead_s"]
    axis = np.array(AXES[burn["direction"]]) * burn["dv_ms"]
    r0, v0 = propagate(enc.rec_m, enc.tca, [-lead])
    r0, v0 = r0[0], v0[0]
    bx, by, bz = lvlh(r0[None, :], v0[None, :])
    dv_eci = (axis[0] * bx[0] + axis[1] * by[0] + axis[2] * bz[0]) / 1000.0

    def rhs(_, y):
        r = y[:3]
        return np.concatenate([y[3:], -MU * r / np.linalg.norm(r) ** 3])

    kw = dict(method="DOP853", rtol=1e-12, atol=1e-10, t_eval=[lead])
    nom = solve_ivp(rhs, (0, lead), np.concatenate([r0, v0]), **kw).y[:, -1]
    per = solve_ivp(rhs, (0, lead), np.concatenate([r0, v0 + dv_eci]), **kw).y[:, -1]
    delta = per[:3] - nom[:3]
    bx2, by2, bz2 = lvlh(nom[None, :3], nom[None, 3:])
    true_lvlh = np.array([delta @ bx2[0], delta @ by2[0], delta @ bz2[0]])
    n = math.sqrt(MU / np.linalg.norm(r0) ** 3)
    cw_lvlh = cw_displacement(np.array([lead]), n, axis)[0]
    err = float(np.linalg.norm(true_lvlh - cw_lvlh))
    return {"error_km": round(err, 4),
            "error_pct": round(100 * err / max(float(np.linalg.norm(true_lvlh)), 1e-9), 2)}


def trajectory_series(enc: Encounter, burn: dict, step_s=10.0) -> dict:
    """Manoeuvring satellite before and after the burn, and the other object, TEME km, for drawing."""
    lead = burn["lead_s"]
    axis = np.array(AXES[burn["direction"]]) * burn["dv_ms"]
    n, _ = enc.burn_frame(lead)
    t = np.arange(-lead - 300.0, 600.0 + step_s, step_s)
    rM, vM = propagate(enc.rec_m, enc.tca, t)
    rO, _ = propagate(enc.rec_o, enc.tca, t)
    rM2 = rM + to_eci(cw_displacement(t + lead, n, axis), lvlh(rM, vM))
    rd = lambda a: np.round(a, 3).tolist()
    return {"t0_utc": iso(enc.tca + timedelta(seconds=float(t[0]))), "step_s": step_s, "frame": "TEME km",
            "burn_utc": iso(enc.tca - timedelta(seconds=lead)),
            "maneuvering_nominal": rd(rM), "maneuvering_after": rd(rM2), "other": rd(rO)}

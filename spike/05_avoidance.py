"""Step 5: avoidance manoeuvre simulation (the physical consequence of the agent's decision).

Given a conjunction from step 1 and the satellite that can manoeuvre, this finds the smallest impulsive burn
that pushes the miss distance above a safe threshold, then checks that the burn does not create a new close
approach with any other object in the catalog.

Method
- Baseline orbits: SGP4 from the CelesTrak TLEs (same data as step 1).
- Effect of a burn: Clohessy-Wiltshire relative motion in the manoeuvring satellite's LVLH frame, added on top of
  the SGP4 baseline. A short burn changes the orbit by only metres to kilometres, so the linear model is accurate
  while SGP4 keeps modelling the large effects (J2, drag). The linearisation is checked against a numerical
  two-body integration for the chosen burn (see "validation" in the output).
- Closest approach: relative motion near TCA is almost linear, so a parabola through the three samples around the
  minimum gives the exact miss distance (a plain grid minimum overestimates it).

Screening-grade only: TLE accuracy is about a kilometre, no covariance or collision probability is computed.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timedelta, timezone

import numpy as np
from scipy.integrate import solve_ivp
from sgp4.api import Satrec, SatrecArray, jday

from common import DATA, save_json

MU = 398600.4418  # km^3/s^2
RE = 6378.137     # km
TLE_PATH = DATA / "celestrak_active.tle"
AXES = {  # LVLH: x radial (up), y along-track (forward), z cross-track (orbit normal)
    "prograde": (0, 1, 0), "retrograde": (0, -1, 0),
    "radial_out": (1, 0, 0), "radial_in": (-1, 0, 0),
    "normal": (0, 0, 1), "anti_normal": (0, 0, -1),
}


# ---------------------------------------------------------------- catalog and propagation
def load_catalog() -> tuple[dict[int, Satrec], dict[int, str]]:
    lines = [ln.rstrip() for ln in TLE_PATH.read_text().splitlines() if ln.strip()]
    sats, names = {}, {}
    for i in range(0, len(lines) - 2, 3):
        rec = Satrec.twoline2rv(lines[i + 1], lines[i + 2])
        sats[int(rec.satnum)] = rec
        names[int(rec.satnum)] = lines[i].strip()
    return sats, names


def parse_utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def jdfr(base: datetime, offs: np.ndarray):
    jd0, fr0 = jday(base.year, base.month, base.day, base.hour, base.minute, base.second + base.microsecond / 1e6)
    return np.full(offs.shape, jd0), fr0 + offs / 86400.0


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


# ---------------------------------------------------------------- manoeuvre physics
def cw_displacement(tau: np.ndarray, n: float, dv_ms) -> np.ndarray:
    """Position change (km, LVLH at time tau) caused by an impulse dv_ms (m/s, LVLH) applied at tau = 0."""
    dvx, dvy, dvz = np.asarray(dv_ms, dtype=float) / 1000.0
    tau = np.maximum(tau, 0.0)  # nothing happens before the burn
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
    """Exact minimum of |rel(t)| from samples: parabola through the three samples around the minimum of d^2."""
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
    return math.sqrt(d2[i]), float(t[i]), False  # minimum at the window edge: caller should widen it


class Encounter:
    """Baseline (SGP4) data on a fine grid around TCA, reused for every candidate burn."""

    def __init__(self, rec_m: Satrec, rec_o: Satrec, tca: datetime, half=6.0, dt=0.02):
        self.rec_m, self.rec_o, self.tca = rec_m, rec_o, tca
        self.og = np.arange(-half, half + dt / 2, dt)
        self.rM, self.vM = propagate(rec_m, tca, self.og)
        self.rO, self.vO = propagate(rec_o, tca, self.og)
        self.basis = lvlh(self.rM, self.vM)
        i0 = len(self.og) // 2
        rel = self.rM - self.rO
        self.nominal = min_distance(rel, self.og)
        self.rel_speed = float(np.linalg.norm(self.vM[i0] - self.vO[i0]))
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


# ---------------------------------------------------------------- planner
def search(enc: Encounter, safe_km: float, max_dv: float, min_lead_s: float, max_lead_orbits: float):
    """Smallest burn per (lead, direction) that reaches the safe miss distance. Returns candidates sorted by delta-v."""
    mags = np.geomspace(0.005, max_dv, 40)
    leads = np.arange(max(min_lead_s, 0.2 * enc.period), max_lead_orbits * enc.period, 0.05 * enc.period)
    found = []
    for lead in leads:
        lead = float(round(lead))
        for name, axis in AXES.items():
            for mag in mags:
                miss, t_star, inside = enc.miss_after(lead, np.array(axis) * mag)
                if inside and miss >= safe_km:
                    found.append({"lead_s": lead, "direction": name, "dv_ms": float(mag), "miss_km": miss})
                    break
    found.sort(key=lambda c: (c["dv_ms"], c["lead_s"]))
    return found


def diverse(cands: list[dict], limit: int) -> list[dict]:
    """Cheapest option for each direction first, then the next cheapest overall, so one bad geometry cannot use up every try."""
    first, seen = [], set()
    for c in cands:
        if c["direction"] not in seen:
            seen.add(c["direction"]); first.append(c)
    rest = [c for c in cands if c not in first]
    return (first + rest)[:limit]


def refine_dv(enc: Encounter, c: dict, safe_km: float) -> dict:
    """Bisect the magnitude between the last failing and first passing grid value to avoid grid-step waste."""
    axis = np.array(AXES[c["direction"]])
    lo, hi = c["dv_ms"] / 1.2, c["dv_ms"]
    for _ in range(18):
        mid = 0.5 * (lo + hi)
        miss, _, inside = enc.miss_after(c["lead_s"], axis * mid)
        lo, hi = (lo, mid) if (inside and miss >= safe_km) else (mid, hi)
    miss, t_star, _ = enc.miss_after(c["lead_s"], axis * hi)
    return {**c, "dv_ms": hi, "miss_km": miss, "tca_shift_s": t_star}


def rescreen(enc: Encounter, sats: dict[int, Satrec], names: dict[int, str], burn: dict, threshold_km: float,
             step_s=2.0, margin_km=30.0) -> dict:
    """Does the burn bring the manoeuvring satellite close to anything else between the burn and TCA + 15 min?"""
    rec_m, ids_skip = enc.rec_m, {int(enc.rec_m.satnum)}
    lead = burn["lead_s"]
    axis = np.array(AXES[burn["direction"]]) * burn["dv_ms"]
    n, _ = enc.burn_frame(lead)
    t_off = np.arange(-lead, 900.0 + step_s, step_s)
    rM, vM = propagate(rec_m, enc.tca, t_off)
    basis = lvlh(rM, vM)
    rM_new = rM + to_eci(cw_displacement(t_off + lead, n, axis), basis)

    lo_m, hi_m = alt_band(rec_m)
    lo_m -= margin_km; hi_m += margin_km
    ids = [i for i, rec in sats.items() if i not in ids_skip and alt_band(rec)[1] >= lo_m and alt_band(rec)[0] <= hi_m]
    recs = [sats[i] for i in ids]
    best_nom = np.full(len(ids), np.inf); best_new = np.full(len(ids), np.inf)
    t_new = np.zeros(len(ids)); t_nom = np.zeros(len(ids))
    arr = SatrecArray(recs)
    partner = int(enc.rec_o.satnum)
    pidx = ids.index(partner) if partner in ids else None
    chunk = 250
    for c0 in range(0, len(t_off), chunk):
        sl = slice(c0, c0 + chunk)
        jd, fr = jdfr(enc.tca, t_off[sl])
        err, r, _ = arr.sgp4(jd, fr)
        r[err != 0] = np.nan
        for best, tb, ref in ((best_nom, t_nom, rM[sl]), (best_new, t_new, rM_new[sl])):
            d = np.linalg.norm(r - ref[None, :, :], axis=2)
            d = np.where(np.isnan(d), np.inf, d)
            if pidx is not None:  # the TCA itself is evaluated separately
                d[pidx, np.abs(t_off[sl]) < 60.0] = np.inf
            k = d.argmin(axis=1)
            val = d[np.arange(len(ids)), k]
            upd = val < best
            best[upd] = val[upd]; tb[upd] = t_off[sl][k[upd]]

    half_step_reach = 16.0 * step_s / 2  # worst-case km an object can close between grid samples
    cands = np.where((best_new < threshold_km + half_step_reach) | (best_nom < threshold_km + half_step_reach))[0]
    events, grid = [], np.arange(-4.0, 4.0 + 0.01, 0.02)
    for j in cands:
        rec_o = sats[ids[j]]
        out = {}
        for label, center in (("new", t_new[j]), ("nominal", t_nom[j])):
            t = center + grid
            rm, vm = propagate(rec_m, enc.tca, t)
            ro, _ = propagate(rec_o, enc.tca, t)
            if label == "new":
                rm = rm + to_eci(cw_displacement(t + lead, n, axis), lvlh(rm, vm))
            out[label] = min_distance(rm - ro, t)[0]
        if min(out.values()) < threshold_km:
            events.append({"norad_id": ids[j], "name": names.get(ids[j], str(ids[j])),
                           "miss_nominal_km": round(out["nominal"], 3), "miss_after_km": round(out["new"], 3),
                           "tca_offset_s": round(float(t_new[j]), 1),
                           "introduced_by_burn": out["new"] < threshold_km <= out["nominal"]})
    events.sort(key=lambda e: e["miss_after_km"])
    return {"objects_checked": len(ids), "window_s": [float(t_off[0]), float(t_off[-1])], "threshold_km": threshold_km,
            "events": events, "new_conjunctions": [e for e in events if e["introduced_by_burn"]]}


def validate_linear_model(enc: Encounter, burn: dict) -> dict:
    """Compare the CW displacement at TCA with a numerical two-body integration of the same impulse."""
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
    delta_true = per[:3] - nom[:3]
    bx2, by2, bz2 = lvlh(nom[None, :3], nom[None, 3:])
    true_lvlh = np.array([delta_true @ bx2[0], delta_true @ by2[0], delta_true @ bz2[0]])
    n = math.sqrt(MU / np.linalg.norm(r0) ** 3)
    cw_lvlh = cw_displacement(np.array([lead]), n, axis)[0]
    err = float(np.linalg.norm(true_lvlh - cw_lvlh))
    return {"two_body_displacement_km": [round(float(x), 4) for x in true_lvlh],
            "cw_displacement_km": [round(float(x), 4) for x in cw_lvlh],
            "error_km": round(err, 4),
            "error_pct": round(100 * err / max(float(np.linalg.norm(true_lvlh)), 1e-9), 2)}


def trajectory_series(enc: Encounter, burn: dict, step_s=10.0) -> dict:
    lead = burn["lead_s"]
    axis = np.array(AXES[burn["direction"]]) * burn["dv_ms"]
    n, _ = enc.burn_frame(lead)
    t = np.arange(-lead - 300.0, 600.0 + step_s, step_s)
    rM, vM = propagate(enc.rec_m, enc.tca, t)
    rO, _ = propagate(enc.rec_o, enc.tca, t)
    rM2 = rM + to_eci(cw_displacement(t + lead, n, axis), lvlh(rM, vM))
    rd = lambda a: np.round(a, 3).tolist()
    return {"t0_utc": iso(enc.tca + timedelta(seconds=float(t[0]))), "step_s": step_s, "frame": "TEME km",
            "maneuvering_nominal": rd(rM), "maneuvering_after": rd(rM2), "other": rd(rO)}


def plan(a: int, b: int, who: int, *, safe_km=2.0, max_dv=1.0, min_lead_min=30.0, max_lead_orbits=2.0,
         mass_kg=None, isp_s=None, with_series=True, log=print) -> dict:
    sats, names = load_catalog()
    events = json.loads((DATA / "conjunctions.json").read_text())["events"]
    ev = next((e for e in events if {e["object_a"]["norad_id"], e["object_b"]["norad_id"]} == {a, b}), None)
    if ev is None:
        raise ValueError(f"pair {a}/{b} not in data/conjunctions.json (run step 1)")
    other = b if who == a else a
    tca = parse_utc(ev["tca_utc"])
    enc = Encounter(sats[who], sats[other], tca)
    result = {
        "norad_ids": [a, b], "maneuvering": who, "maneuvering_name": names[who], "other": other, "other_name": names[other],
        "tca_utc": ev["tca_utc"],
        "nominal": {"miss_km": round(enc.nominal[0], 4), "rel_speed_kms": round(enc.rel_speed, 2),
                    "step1_miss_km": ev["miss_distance_km"]},
        "assumptions": {"safe_miss_km": safe_km, "max_dv_ms": max_dv, "min_lead_min": min_lead_min,
                        "impulsive_burn": True, "model": "SGP4 baseline + Clohessy-Wiltshire delta"},
    }
    log(f"{names[who]} (NORAD {who}) manoeuvres, partner {names[other]}; orbital period {enc.period / 60:.1f} min")
    log(f"nominal miss {enc.nominal[0] * 1000:.0f} m (step 1 reported {ev['miss_distance_km'] * 1000:.0f} m), "
        f"relative speed {enc.rel_speed:.1f} km/s")
    if enc.nominal[0] >= safe_km:
        result["burn"] = None
        result["note"] = f"already above {safe_km} km: no manoeuvre needed"
        return result

    cands = search(enc, safe_km, max_dv, min_lead_min * 60, max_lead_orbits)
    log(f"{len(cands)} feasible (lead, direction) options reaching {safe_km} km")
    if not cands:
        result["burn"] = None
        result["note"] = f"no burn up to {max_dv} m/s reaches {safe_km} km with these constraints"
        return result

    rejected, chosen = [], None
    for c in diverse(cands, 12):
        c = refine_dv(enc, c, safe_km)
        log(f"try: {c['direction']:10} lead {c['lead_s'] / 60:5.1f} min  dv {c['dv_ms']:.3f} m/s -> miss {c['miss_km']:.2f} km")
        screen = rescreen(enc, sats, names, c, safe_km)
        if screen["new_conjunctions"]:
            names_new = ", ".join(e["name"] for e in screen["new_conjunctions"][:3])
            log(f"   rejected: creates new close approach with {names_new}")
            rejected.append({**c, "reason": "new close approach", "with": [e["name"] for e in screen["new_conjunctions"]]})
            continue
        chosen = (c, screen)
        break
    if chosen is None:
        result["burn"] = None
        result["note"] = "every candidate created a new close approach"
        result["rejected"] = rejected
        return result

    c, screen = chosen
    t_burn = tca - timedelta(seconds=c["lead_s"])
    dv = np.array(AXES[c["direction"]]) * c["dv_ms"]
    result["burn"] = {"t_burn_utc": iso(t_burn), "lead_min": round(c["lead_s"] / 60, 1),
                      "lead_orbits": round(c["lead_s"] / enc.period, 2), "direction": c["direction"],
                      "dv_ms": round(c["dv_ms"], 4), "dv_lvlh_ms": [round(float(x), 4) for x in dv]}
    if mass_kg and isp_s:
        result["burn"]["propellant_kg"] = round(mass_kg * (1 - math.exp(-c["dv_ms"] / (isp_s * 9.80665))), 4)
    result["after"] = {"miss_km": round(c["miss_km"], 3), "tca_shift_s": round(c["tca_shift_s"], 3),
                       "gain_km": round(c["miss_km"] - enc.nominal[0], 3)}
    result["rescreen"] = screen
    result["validation"] = validate_linear_model(enc, c)
    result["rejected"] = rejected
    result["alternatives"] = [{k: (round(v, 3) if isinstance(v, float) else v) for k, v in x.items()} for x in cands[:6]]
    result["limits"] = [
        "Impulsive burn, no finite-burn duration, no execution error.",
        "TLE accuracy is about 1 km; no covariance or collision probability. Screening only.",
        "Linear relative motion; see validation.error_pct for this burn.",
        "Rescreen covers objects in the catalog whose altitude band overlaps; uncatalogued debris is not modelled.",
    ]
    if with_series:
        result["series"] = trajectory_series(enc, c)
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--norad", type=int, nargs=2, required=True, help="the two NORAD IDs of a conjunction from step 1")
    ap.add_argument("--who", type=int, help="NORAD ID that manoeuvres (default: first)")
    ap.add_argument("--safe-km", type=float, default=2.0, help="required miss distance after the burn")
    ap.add_argument("--max-dv", type=float, default=1.0, help="largest burn considered, m/s")
    ap.add_argument("--min-lead-min", type=float, default=30.0, help="earliest burn before TCA, minutes (command upload time)")
    ap.add_argument("--mass-kg", type=float, help="optional: satellite mass, for a propellant estimate")
    ap.add_argument("--isp-s", type=float, help="optional: thruster specific impulse, seconds")
    args = ap.parse_args()
    a, b = args.norad
    res = plan(a, b, args.who or a, safe_km=args.safe_km, max_dv=args.max_dv, min_lead_min=args.min_lead_min,
               mass_kg=args.mass_kg, isp_s=args.isp_s)
    out = save_json(f"avoidance_{a}_{b}_{res['maneuvering']}.json", res)
    if res.get("burn"):
        bn, af, rs, va = res["burn"], res["after"], res["rescreen"], res["validation"]
        print(f"\nPLAN  burn {bn['direction']} {bn['dv_ms']:.3f} m/s at {bn['t_burn_utc']} ({bn['lead_min']} min / {bn['lead_orbits']} orbits before TCA)")
        print(f"      miss distance {res['nominal']['miss_km'] * 1000:.0f} m -> {af['miss_km'] * 1000:.0f} m")
        print(f"      rescreen: {rs['objects_checked']} objects checked, {len(rs['new_conjunctions'])} new close approaches < {rs['threshold_km']} km")
        print(f"      linear-model check vs two-body integration: {va['error_pct']}% error ({va['error_km']} km)")
    else:
        print("\nNO PLAN:", res.get("note"))
    print(f"saved {out.name}")


if __name__ == "__main__":
    main()

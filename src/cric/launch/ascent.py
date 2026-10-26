"""Can this rocket reach orbit? A 3-degree-of-freedom ascent with honest simplifications.

Model (point mass in the launch plane, inertial frame aligned with Earth at liftoff):
- gravity of a spherical Earth; exponential atmosphere (scale height 8.5 km) rotating with Earth;
- drag with a constant Cd on the vehicle's cross-section;
- thrust from constant mass flow, Isp blended from sea level to vacuum with ambient pressure;
- staging when a stage runs dry, fairing jettison above 110 km;
- guidance: on stage 1 the pitch falls smoothly with altitude from vertical to a chosen angle by a chosen
  altitude (a pitch-versus-altitude law, far less sensitive than a kick plus free gravity turn), then a linear
  pitch program for the upper stages; the last stage burns until apogee reaches the target, coasts to apogee and burns horizontally to
  raise perigee, as small launchers with restartable upper stages do. The four guidance parameters are
  found by a global search for each vehicle and payload.
Not modelled: winds aloft, throttling, engine-out, cross-plane steering, oblateness during ascent. It is a
planning estimate, not a flight design tool; scripts/validate_vehicles.py compares it with published payloads.
"""

from __future__ import annotations

import copy
import math
import os
from dataclasses import dataclass

from .geo import RE, inertial_to_latlon, plane_basis, plane_to_inertial, surface_speed_along
from .vehicles import G0, Vehicle

MU = 3.986004418e14
ORBIT_MIN_KM = 150.0      # perigee above this counts as orbit (days to weeks of life at least)


@dataclass
class Guidance:
    h_turn_km: float = 60.0      # stage 1: altitude by which the pitch reaches phi_end1
    phi_end1_deg: float = 25.0   # stage 1: pitch above the local horizontal at h_turn
    phi0_deg: float = 25.0       # upper-stage pitch above the local horizontal at ignition
    phi1_deg: float = -5.0       # ... at the end of the upper-stage burns


def density(h: float) -> float:
    return 1.225 * math.exp(-h / 8500.0) if h < 150e3 else 0.0


def pressure_ratio(h: float) -> float:
    return math.exp(-h / 7000.0) if h < 100e3 else 0.0


def elements(x: float, y: float, vx: float, vy: float) -> tuple[float, float]:
    """(perigee, apogee) altitudes in km of the osculating two-body orbit; apogee inf if not bound."""
    r = math.hypot(x, y)
    eps = (vx * vx + vy * vy) / 2 - MU / r
    h = x * vy - y * vx
    e = math.sqrt(max(0.0, 1 + 2 * eps * h * h / MU ** 2))
    if eps >= 0:
        return (h * h / MU / (1 + e) - RE) / 1000, math.inf
    a = -MU / (2 * eps)
    return (a * (1 - e) - RE) / 1000, (a * (1 + e) - RE) / 1000


def _deriv(s, t, ctl, cd_a, vs0):
    x, y, vx, vy, m = s
    r = math.hypot(x, y)
    rx, ry = x / r, y / r
    hx, hy = ry, -rx
    g = MU / (r * r)
    ax, ay = -g * rx, -g * ry
    alt = r - RE
    rho = density(alt)
    k = vs0 * r / RE
    if rho > 0:
        wx, wy = vx - k * hx, vy - k * hy
        w = math.hypot(wx, wy)
        f = 0.5 * rho * w * cd_a / m
        ax -= f * wx
        ay -= f * wy
    thrust, mdot, phi = ctl(t, alt, vx * rx + vy * ry, vx * hx + vy * hy, k)
    if thrust > 0:
        c, s_ = math.cos(phi), math.sin(phi)
        a_t = thrust / m
        ax += a_t * (c * hx + s_ * rx)
        ay += a_t * (c * hy + s_ * ry)
    return (vx, vy, ax, ay, -mdot)


def _rk4(s, t, dt, ctl, cd_a, vs0):
    """Steering is evaluated inside every stage of the step: holding it fixed over a step made the gravity
    turn depend on the step size."""
    k1 = _deriv(s, t, ctl, cd_a, vs0)
    k2 = _deriv(tuple(a + dt / 2 * b for a, b in zip(s, k1)), t + dt / 2, ctl, cd_a, vs0)
    k3 = _deriv(tuple(a + dt / 2 * b for a, b in zip(s, k2)), t + dt / 2, ctl, cd_a, vs0)
    k4 = _deriv(tuple(a + dt * b for a, b in zip(s, k3)), t + dt, ctl, cd_a, vs0)
    return tuple(a + dt / 6 * (b + 2 * c + 2 * d + e) for a, b, c, d, e in zip(s, k1, k2, k3, k4))


def _coast(t, alt, v_rad, v_hor, k):
    return 0.0, 0.0, 0.0


def _ballistic(s, t, cd_a, vs0, t_max=3 * 3600.0):
    """Coast with drag until impact; returns (t_impact, x, y) or None if it stays up."""
    while t < t_max:
        r = math.hypot(s[0], s[1])
        if r <= RE:
            return t, s[0], s[1]
        dt = 2.0 if r - RE < 150e3 else 10.0
        s = _rk4(s, t, dt, _coast, cd_a, vs0)
        t += dt
    return None


def simulate(veh: Vehicle, payload_kg: float, g: Guidance, *, lat: float, lon: float, az: float,
             target_alt_km: float = 500.0, dt: float = 0.5, record: bool = True, perturb: dict | None = None) -> dict:
    """Fly one ascent. perturb (for dispersion): thrust, isp, dry scale factors, stage-1 pitch and azimuth offsets."""
    p = perturb or {}
    az = az + p.get("az_add", 0.0)
    basis = plane_basis(lat, lon, az)
    vs0 = surface_speed_along(lat, az)
    cd_a = veh.cd * veh.area_m2
    res_f = veh.residual_frac
    st = [dict(name=s.name, prop=s.prop_kg * (1 - res_f), dry=(s.dry_kg + s.prop_kg * res_f) * p.get("dry", 1.0),
               mdot=s.mdot * p.get("thrust", 1.0),
               isp_v=s.isp_vac_s * p.get("isp", 1.0), isp_s=s.isp_sl_s * p.get("isp", 1.0), solid=s.solid)
          for s in veh.stages]
    g = Guidance(g.h_turn_km, g.phi_end1_deg + p.get("kick_add", 0.0), g.phi0_deg, g.phi1_deg)
    fairing = veh.fairing_kg
    last = len(st) - 1

    def mass(i):
        return payload_kg + fairing + sum(s["dry"] + s["prop"] for s in st[i:])

    i, t, mode, upper_t0 = 0, 0.0, "burn", None
    # A final low-thrust kick stage (e.g. Electron's Photon) only circularises; the stage before it raises apogee.
    kick_stage = len(st) > 1 and st[last]["mdot"] * st[last]["isp_v"] * G0 / (payload_kg + st[last]["dry"] + st[last]["prop"]) / G0 < 0.1
    main_last = last - 1 if kick_stage else last
    upper_T = sum(x["prop"] / x["mdot"] for x in st[1:]) or 1.0
    s = (0.0, RE, vs0, 0.0, mass(0))
    events = [{"t": 0.0, "event": "liftoff"}]
    samples, spent = [], []
    stage_dv = [dict(name=x["name"], thrust=0.0, gravity=0.0, drag=0.0, steering=0.0, ideal=0.0, m0=None, m1=None) for x in st]
    maxq = (0.0, 0.0)
    next_rec = 0.0
    crashed = False
    while t < 4 * 3600:
        x, y, vx, vy, m = s
        r = math.hypot(x, y)
        alt = r - RE
        if alt < -100 and t > 5:
            crashed = True
            break
        rx, ry = x / r, y / r
        hx, hy = ry, -rx
        v_rad, v_hor = vx * rx + vy * ry, vx * hx + vy * hy
        cur = st[i]
        if mode in ("burn", "circ") and stage_dv[i]["m0"] is None:
            stage_dv[i]["m0"] = m

        def ctl(tt, alt_, v_rad_, v_hor_, k_, cur=cur, mode=mode, i=i):
            if mode not in ("burn", "circ"):
                return 0.0, 0.0, 0.0
            md = cur["mdot"]
            th = md * (cur["isp_v"] - (cur["isp_v"] - cur["isp_s"]) * pressure_ratio(alt_)) * G0
            if mode == "circ":
                return th, md, 0.0                                  # horizontal: raise perigee near apogee
            if i == 0:                                              # pitch-versus-altitude law
                frac = min(1.0, max(alt_ - 300.0, 0.0) / (g.h_turn_km * 1000))
                return th, md, math.radians(90 - (90 - g.phi_end1_deg) * math.sqrt(frac))
            frac = min(1.0, (tt - upper_t0) / upper_T)
            return th, md, math.radians(g.phi0_deg + (g.phi1_deg - g.phi0_deg) * frac)

        thrust, mdot, phi = ctl(t, alt, v_rad, v_hor, vs0 * r / RE)
        step = dt if mode != "coast" else (5.0 if v_rad > 300 else 1.0)
        if mdot > 0 and cur["prop"] < mdot * step:
            step = max(cur["prop"] / mdot, 1e-3)
        # bookkeeping for the delta-v table (rectangle rule at the start of the step)
        if thrust > 0:
            vv = math.hypot(vx, vy)
            gam = math.atan2(v_rad, v_hor)
            a_t = thrust / m
            d = stage_dv[i]
            d["thrust"] += a_t * step
            d["gravity"] += MU / (r * r) * math.sin(gam) * step
            rho = density(alt)
            if rho > 0:
                kk = vs0 * r / RE
                w = math.hypot(vx - kk * hx, vy - kk * hy)
                q = 0.5 * rho * w * w
                d["drag"] += q * cd_a / m * step
                if q > maxq[0]:
                    maxq = (q, t)
            d["steering"] += a_t * (1 - math.cos(phi - gam)) * step if vv > 1 else 0.0
        s = _rk4(s, t, step, ctl, cd_a, vs0)
        t += step
        if mdot > 0:
            cur["prop"] -= mdot * step
        if record and t >= next_rec:
            lat_, lon_, alt_km = inertial_to_latlon(*plane_to_inertial(math.atan2(s[0], s[1]), math.hypot(s[0], s[1]), basis), t)
            samples.append([round(t, 1), round(alt_km, 2), round(RE * math.atan2(s[0], s[1]) / 1000, 1),
                            round(math.hypot(s[2], s[3]), 1), round(lat_, 4), round(lon_, 4), i, mode])
            next_rec = t + (2.0 if mode != "coast" else 20.0)
        alt_now = math.hypot(s[0], s[1]) - RE
        if fairing and alt_now > veh.fairing_sep_km * 1000:
            fairing = 0.0
            s = s[:4] + (s[4] - veh.fairing_kg,)
            events.append({"t": round(t, 1), "event": "fairing jettison"})
        if mode in ("burn", "circ") and cur["prop"] <= 1e-6:
            stage_dv[i]["m1"] = s[4]
            events.append({"t": round(t, 1), "event": f"{cur['name']} burnout"})
            if i < last:
                spent.append({"name": cur["name"], "state": s[:4] + (cur["dry"],), "t": t})
                s = s[:4] + (s[4] - cur["dry"],)
                i += 1
                upper_t0 = t if upper_t0 is None else upper_t0
                if kick_stage and i == last and target_alt_km and elements(*s[:4])[1] >= target_alt_km:
                    mode = "coast"
                    events.append({"t": round(t, 1), "event": f"{cur['name']} burnout: coast to apogee"})
                else:
                    events.append({"t": round(t, 1), "event": f"{st[i]['name']} ignition"})
                continue
            mode = "done"
            break
        if (mode == "burn" and i >= main_last and not cur["solid"] and target_alt_km
                and elements(*s[:4])[1] >= target_alt_km):
            mode = "coast"
            stage_dv[i]["m1"] = s[4]
            events.append({"t": round(t, 1), "event": f"{cur['name']} cutoff, coast to apogee"})
            if kick_stage and i == main_last:
                spent.append({"name": cur["name"], "state": s[:4] + (cur["dry"] + cur["prop"],), "t": t})
                s = s[:4] + (s[4] - cur["dry"] - cur["prop"],)
                i = last
                events.append({"t": round(t, 1), "event": f"{st[i]['name']} separation"})
        elif mode == "coast":
            r_ = math.hypot(s[0], s[1])
            if (s[0] * s[2] + s[1] * s[3]) / r_ <= 0:
                mode = "circ"
                if kick_stage:
                    stage_dv[i]["m0"] = s[4]
                events.append({"t": round(t, 1), "event": f"{cur['name']} restart: raise perigee"})
        elif mode == "circ" and elements(*s[:4])[0] >= target_alt_km - 15:
            stage_dv[i]["m1"] = s[4]
            events.append({"t": round(t, 1), "event": f"{cur['name']} cutoff: orbit"})
            mode = "done"
            break

    x, y, vx, vy, m = s
    peri, apo = elements(x, y, vx, vy)
    orbit = not crashed and peri >= ORBIT_MIN_KM
    target_reached = orbit and peri >= target_alt_km - 20
    last_st = st[last]
    margin_kg = max(last_st["prop"], 0.0) if orbit else 0.0
    m_end = s[4]
    margin_dv = last_st["isp_v"] * G0 * math.log(m_end / (m_end - margin_kg)) if margin_kg > 0 else 0.0
    theta = math.atan2(x, y)
    r = math.hypot(x, y)
    out = {"vehicle": veh.name, "payload_kg": payload_kg, "azimuth_deg": round(az, 3), "site": {"lat": lat, "lon": lon},
           "target_alt_km": target_alt_km, "orbit": orbit, "target_reached": target_reached,
           "perigee_km": round(peri, 1), "apogee_km": round(apo, 1) if math.isfinite(apo) else None,
           "margin_kg": round(margin_kg, 1), "margin_dv_ms": round(margin_dv, 1),
           "max_q_kpa": round(maxq[0] / 1000, 1), "max_q_t": round(maxq[1], 1), "t_end": round(t, 1),
           "guidance": vars(g), "events": events, "samples": samples}
    rx3 = plane_to_inertial(theta, r, basis)
    u, d = basis
    t3 = tuple(-math.sin(theta) * u[k] + math.cos(theta) * d[k] for k in range(3))
    vr, vt = (x * vx + y * vy) / r, (y * vx - x * vy) / r
    v3 = tuple(vr * rx3[k] / r + vt * t3[k] for k in range(3))
    hv = (rx3[1] * v3[2] - rx3[2] * v3[1], rx3[2] * v3[0] - rx3[0] * v3[2], rx3[0] * v3[1] - rx3[1] * v3[0])
    out["inclination_deg"] = round(math.degrees(math.acos(hv[2] / math.sqrt(sum(c * c for c in hv)))), 2)
    out["insertion"] = {"t": round(t, 1), "r_m": [round(c, 1) for c in rx3], "v_ms": [round(c, 3) for c in v3]}
    # suborbital: where does it come down?
    apex = max((smp[1] for smp in samples), default=None)
    if not orbit:
        hit = _ballistic(s, t, cd_a, vs0) if not crashed else (t, x, y)
        if hit:
            ti, xi, yi = hit
            lat_i, lon_i, _ = inertial_to_latlon(*plane_to_inertial(math.atan2(xi, yi), RE, basis), ti)
            out["impact"] = {"lat": round(lat_i, 4), "lon": round(lon_i, 4), "t": round(ti, 1),
                             "downrange_km": round(RE * math.atan2(xi, yi) / 1000, 1)}
        out["apex_km"] = apex if apex is not None else round(alt_now / 1000, 1)
    stage_impacts = []
    for sp in spent:
        x0, y0, vx0, vy0, dry = sp["state"]
        cd_a_s = cd_a
        hit = _ballistic((x0, y0, vx0, vy0, max(dry, 1.0)), sp["t"], cd_a_s, vs0)
        if hit:
            ti, xi, yi = hit
            la, lo, _ = inertial_to_latlon(*plane_to_inertial(math.atan2(xi, yi), RE, basis), ti)
            stage_impacts.append({"stage": sp["name"], "lat": round(la, 4), "lon": round(lo, 4),
                                  "downrange_km": round(RE * math.atan2(xi, yi) / 1000, 1), "t": round(ti, 1)})
        else:
            stage_impacts.append({"stage": sp["name"], "stays_in_orbit": True})
    out["stage_impacts"] = stage_impacts
    rows = []
    for k, d in enumerate(stage_dv):
        if d["m0"] and d["m1"]:
            d["ideal"] = st[k]["isp_v"] * G0 * math.log(d["m0"] / d["m1"])
        rows.append({k2: (round(v, 1) if isinstance(v, float) else v) for k2, v in d.items() if k2 not in ("m0", "m1")})
    out["dv_table"] = rows
    out["dv_total_ms"] = round(sum(r_["thrust"] for r_ in rows), 1)
    out["losses_ms"] = {k: round(sum(r_[k] for r_ in rows), 1) for k in ("gravity", "drag", "steering")}
    out["orbital_speed_ms"] = round(math.sqrt(MU / (RE + target_alt_km * 1000)), 1)
    return out


def _score(res: dict) -> float:
    if res["target_reached"]:
        return 1e5 + res["margin_dv_ms"]
    if res["orbit"]:
        return 5e4 + res["perigee_km"]
    return res["perigee_km"]          # negative and large for suborbital; rises toward orbit


class _Objective:
    """Picklable objective so the global search can score its population on several processes."""

    def __init__(self, veh, payload_kg, lat, lon, az, target_alt_km):
        self.args = (veh, payload_kg)
        self.kw = {"lat": lat, "lon": lon, "az": az, "target_alt_km": target_alt_km, "dt": 2.0, "record": False}

    def __call__(self, xv):
        return -_score(simulate(*self.args, Guidance(*map(float, xv)), **self.kw))


_POOL = None


def pool():
    """One process pool for searches, fixes and dispersion. Spawned, not forked: the web server is threaded,
    and forking a threaded process can deadlock."""
    global _POOL
    import multiprocessing as mp
    from concurrent.futures import ProcessPoolExecutor

    if _POOL is None:
        _POOL = ProcessPoolExecutor(max_workers=os.cpu_count() or 2, mp_context=mp.get_context("spawn"))
    return _POOL


def optimise(veh: Vehicle, payload_kg: float, *, lat: float, lon: float, az: float, target_alt_km: float = 500.0,
             maxiter: int = 18, parallel: bool = False, seeds: tuple = (0, 1, 2)) -> tuple[Guidance, dict]:
    """Global search over the four guidance parameters maximising margin, or getting closest to orbit; the
    score has cliffs (orbit / no orbit), so a local search alone stalls. parallel=True scores the population on
    all cores (use from a top-level call, not from inside another process pool)."""
    from scipy.optimize import differential_evolution

    obj = _Objective(veh, payload_kg, lat, lon, az, target_alt_km)
    kw = {"workers": pool().map, "updating": "deferred"} if parallel else {}
    best = None
    for seed in seeds:
        r = differential_evolution(obj, bounds=[(15.0, 160.0), (0.0, 60.0), (-5.0, 60.0), (-35.0, 30.0)],
                                   maxiter=maxiter, popsize=8, tol=1e-3, seed=seed, polish=False, init="sobol", **kw)
        if best is None or r.fun < best.fun:
            best = r
        if -best.fun >= 1e5:                                # reached the target: another search would only polish
            break
    g = Guidance(*map(float, best.x))
    return g, simulate(veh, payload_kg, g, lat=lat, lon=lon, az=az, target_alt_km=target_alt_km, dt=0.5, record=True)


def _reaches(job) -> tuple[float, dict | None]:
    """Worker: does the modified vehicle reach the target? Returns (factor, result summary or None)."""
    veh, payload_kg, factor, kind, k, site = job
    v2 = copy.deepcopy(veh)
    pay = payload_kg
    if kind == "payload":
        pay = payload_kg * factor
    else:
        stg = v2.stages[k]
        ratio = stg.dry_kg / (stg.dry_kg + stg.prop_kg)
        stg.prop_kg *= factor
        stg.dry_kg = stg.prop_kg * ratio / (1 - ratio)
        if kind == "stage+thrust":
            stg.thrust_vac_n *= factor                    # a bigger stage needs proportionally more thrust
    _, r = optimise(v2, pay, maxiter=10, seeds=(0,), **site)
    return factor, ({k_: r[k_] for k_ in ("perigee_km", "apogee_km", "margin_dv_ms")} if r["target_reached"] else None)


def advise(veh: Vehicle, payload_kg: float, base: dict, *, lat: float, lon: float, az: float,
           target_alt_km: float) -> list[dict]:
    """Code-computed fixes, each verified by flying the changed rocket: the smallest lighter payload, and the
    smallest propellant increase per stage that reaches the target orbit. Runs the searches in parallel."""
    site = {"lat": lat, "lon": lon, "az": az, "target_alt_km": target_alt_km}
    if base["target_reached"]:
        last = veh.stages[-1]
        ve = last.isp_vac_s * G0
        m_above0 = payload_kg + last.dry_kg
        dv_last = ve * math.log((m_above0 + last.prop_kg) / m_above0)
        k = math.exp((dv_last - base["margin_dv_ms"]) / ve)
        extra = max(0.0, (last.prop_kg - (k - 1) * (payload_kg + last.dry_kg)) / (k - 1))
        return [{"kind": "spare", "extra_payload_kg": round(extra),
                 "text": f"{base['margin_dv_ms']:.0f} m/s to spare: roughly {extra:.0f} kg more payload (estimate)"}]
    # Fly every candidate change at once and keep the smallest that works (one parallel round).
    pay_fracs = (0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.0)
    scales = (1.05, 1.1, 1.15, 1.2, 1.3, 1.45, 1.7, 2.0, 2.5, 3.0)
    jobs = [(veh, payload_kg, f, "payload", None, site) for f in pay_fracs]
    options = [("payload", None)]
    for k, stg in enumerate(veh.stages):
        tw = stg.thrust_vac_n / ((stg.prop_kg + stg.dry_kg + payload_kg) * G0)
        if tw < 0.1 and k == len(veh.stages) - 1:
            continue                                       # a kick stage is not where orbit is won or lost
        kind = "stage+thrust" if k == 0 else "stage"
        options.append((kind, k))
        jobs += [(veh, payload_kg, f, kind, k, site) for f in scales]
    results = list(pool().map(_reaches, jobs))
    fixes = []
    for kind, k in options:
        good = [(job[2], res[1]) for job, res in zip(jobs, results) if job[3] == kind and job[4] == k and res[1]]
        if kind == "payload":
            if not good:
                fixes.append({"kind": "payload", "text": "Even with no payload this rocket does not reach the orbit.",
                              "infeasible": True})
                continue
            f, r = max(good, key=lambda x: x[0])
            fixes.append({"kind": "payload", "payload_kg": round(payload_kg * f, 1), "verified": True, "result": r,
                          "text": f"Lighter payload: {payload_kg:,.0f} → {payload_kg * f:,.0f} kg"})
            continue
        name = veh.stages[k].name
        if not good:
            fixes.append({"kind": kind, "stage": name, "infeasible": True,
                          "text": f"{name}: even 3x the propellant does not reach orbit"})
            continue
        f, r = min(good, key=lambda x: x[0])
        extra = " and thrust" if kind == "stage+thrust" else ""
        fixes.append({"kind": kind, "stage": name, "factor": f, "prop_kg": round(veh.stages[k].prop_kg * f),
                      "verified": True, "result": r,
                      "text": f"{name} propellant{extra} +{(f - 1) * 100:.0f}% "
                              f"({veh.stages[k].prop_kg:,.0f} → {veh.stages[k].prop_kg * f:,.0f} kg)"})
    ok = sorted([x for x in fixes if not x.get("infeasible")], key=lambda x: (x["kind"] != "payload", x.get("factor", 1)))
    return ok or fixes

"""From the insertion state of a simulated launch to an object CRIC can track: inertial state, TLE, passes.

The ascent is flown in a frame aligned with Earth at liftoff. With the liftoff time known, the insertion state
is rotated into TEME by Greenwich sidereal time and turned into an SGP4 satellite whose mean elements reproduce
that state at epoch (then it drifts from a precise propagation like any TLE: screening grade).
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import numpy as np
from sgp4.api import WGS72, Satrec
from sgp4.exporter import export_tle
from sgp4.propagation import gstime

from ..physics.timeutil import julian

MU_KM = 398600.4418
EPOCH0 = datetime(1949, 12, 31, tzinfo=timezone.utc)


def gmst(t: datetime) -> float:
    jd, fr = julian(t)
    return gstime(jd + fr)


def li_to_teme(vec, t0: datetime) -> np.ndarray:
    """Launch-inertial (Earth-fixed axes at liftoff) -> TEME: rotate by +GMST(t0) about z."""
    th = gmst(t0)
    c, s = math.cos(th), math.sin(th)
    x, y, z = vec
    return np.array([c * x - s * y, s * x + c * y, z])


def rv_to_elements(r_km: np.ndarray, v_kms: np.ndarray) -> dict:
    h = np.cross(r_km, v_kms)
    n = np.cross([0, 0, 1.0], h)
    rn, vn = np.linalg.norm(r_km), np.linalg.norm(v_kms)
    e_vec = ((vn ** 2 - MU_KM / rn) * r_km - np.dot(r_km, v_kms) * v_kms) / MU_KM
    e = float(np.linalg.norm(e_vec))
    a = 1 / (2 / rn - vn ** 2 / MU_KM)
    i = math.acos(h[2] / np.linalg.norm(h))
    raan = math.atan2(n[1], n[0]) % (2 * math.pi)
    nn = np.linalg.norm(n)
    if e > 1e-6:
        argp = math.acos(max(-1, min(1, np.dot(n, e_vec) / (nn * e))))
        if e_vec[2] < 0:
            argp = 2 * math.pi - argp
        nu = math.acos(max(-1, min(1, np.dot(e_vec, r_km) / (e * rn))))
        if np.dot(r_km, v_kms) < 0:
            nu = 2 * math.pi - nu
    else:                                           # circular: measure from the node
        argp = 0.0
        nu = math.acos(max(-1, min(1, np.dot(n, r_km) / (nn * rn))))
        if r_km[2] < 0:
            nu = 2 * math.pi - nu
    E = 2 * math.atan2(math.sqrt(1 - e) * math.sin(nu / 2), math.sqrt(1 + e) * math.cos(nu / 2))
    M = (E - e * math.sin(E)) % (2 * math.pi)
    return {"a_km": a, "e": e, "i": i, "raan": raan, "argp": argp, "M": M}


def _satrec(r_km, v_kms, epoch: datetime, satnum: int) -> Satrec:
    el = rv_to_elements(np.asarray(r_km, float), np.asarray(v_kms, float))
    n = math.sqrt(MU_KM / el["a_km"] ** 3) * 60.0                    # rad / min
    sat = Satrec()
    sat.sgp4init(WGS72, "i", satnum, (epoch - EPOCH0).total_seconds() / 86400.0, 2e-5, 0.0, 0.0,
                 max(min(el["e"], 0.99), 1e-7), el["argp"], el["i"], el["M"], n, el["raan"])
    return sat


def satrec_from_state(r_km, v_kms, epoch: datetime, satnum: int = 99001, iters: int = 12) -> Satrec:
    """SGP4 satellite whose state at epoch matches (r, v). SGP4 takes mean, not osculating, elements, so the
    input state is corrected until SGP4 reproduces the target (fixed-point iteration, metre level)."""
    from ..physics.timeutil import jdfr

    target_r, target_v = np.asarray(r_km, float), np.asarray(v_kms, float)
    r_in, v_in = target_r.copy(), target_v.copy()
    jd, fr = jdfr(epoch, np.array([0.0]))
    for _ in range(iters):
        sat = _satrec(r_in, v_in, epoch, satnum)
        _, r, v = sat.sgp4(jd[0], fr[0])
        dr, dv = target_r - np.array(r), target_v - np.array(v)
        if np.linalg.norm(dr) < 1e-3 and np.linalg.norm(dv) < 1e-6:
            break
        r_in, v_in = r_in + dr, v_in + dv
    return sat


def insertion_teme(result: dict, liftoff: datetime) -> tuple[np.ndarray, np.ndarray, datetime]:
    """(r_km, v_kms, epoch) of the simulated insertion in TEME."""
    ins = result["insertion"]
    r = li_to_teme(np.array(ins["r_m"]) / 1000, liftoff)
    v = li_to_teme(np.array(ins["v_ms"]) / 1000, liftoff)
    return r, v, liftoff + timedelta(seconds=ins["t"])


def tle_for(result: dict, liftoff: datetime, name: str, satnum: int = 99001) -> tuple[str, str, str]:
    r, v, epoch = insertion_teme(result, liftoff)
    sat = satrec_from_state(r, v, epoch, satnum)
    l1, l2 = export_tle(sat)
    return name[:24], l1, l2


def passes(sat: Satrec, lat: float, lon: float, start: datetime, hours: float = 48, min_el: float = 10.0) -> list[dict]:
    """Times the satellite is above min_el degrees for an observer (e.g. home or a ground station)."""
    from ..physics.timeutil import jdfr

    offs = np.arange(0, hours * 3600, 20.0)
    jd, fr = jdfr(start, offs)
    err, r, _ = sat.sgp4_array(jd, fr)
    th = np.array([gstime(a + b) for a, b in zip(jd, fr)])
    p, l = math.radians(lat), math.radians(lon)
    re = 6378.137
    obs_ecef = re * np.array([math.cos(p) * math.cos(l), math.cos(p) * math.sin(l), math.sin(p)])
    c, s = np.cos(th), np.sin(th)
    xe, ye = c * r[:, 0] + s * r[:, 1], -s * r[:, 0] + c * r[:, 1]
    d = np.stack([xe, ye, r[:, 2]], axis=1) - obs_ecef
    up = obs_ecef / re
    el = np.degrees(np.arcsin(np.clip((d @ up) / np.linalg.norm(d, axis=1), -1, 1)))
    el[err != 0] = -90
    out, inside = [], False
    for k, e in enumerate(el):
        if e >= min_el and not inside:
            inside, k0 = True, k
        elif e < min_el and inside:
            inside = False
            seg = el[k0:k]
            km = k0 + int(np.argmax(seg))
            east = np.array([-math.sin(l), math.cos(l), 0.0])
            north = np.cross(up, east)
            dd = d[k0:k + 1]
            az = (np.degrees(np.arctan2(dd @ east, dd @ north)) + 360) % 360
            out.append({"track": [[round(float(a), 1), round(float(e), 1)] for a, e in zip(az[::3], el[k0:k + 1][::3])],
                        "rise_utc": (start + timedelta(seconds=float(offs[k0]))).isoformat().replace("+00:00", "Z"),
                        "max_utc": (start + timedelta(seconds=float(offs[km]))).isoformat().replace("+00:00", "Z"),
                        "set_utc": (start + timedelta(seconds=float(offs[k]))).isoformat().replace("+00:00", "Z"),
                        "max_elevation_deg": round(float(seg.max()), 1)})
    return out


def sun_ra_deg(t: datetime) -> float:
    """Right ascension of the Sun (deg), low-precision almanac formula (about 0.01 deg)."""
    n = (t - datetime(2000, 1, 1, 12, tzinfo=timezone.utc)).total_seconds() / 86400.0
    L = math.radians((280.460 + 0.9856474 * n) % 360)
    g = math.radians((357.528 + 0.9856003 * n) % 360)
    lam = L + math.radians(1.915) * math.sin(g) + math.radians(0.020) * math.sin(2 * g)
    eps = math.radians(23.439 - 4e-7 * n)
    return math.degrees(math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam))) % 360


def ltan_hours(result: dict, liftoff: datetime) -> float:
    """Local time of the ascending node (hours) of the orbit a flight reaches when it lifts off at `liftoff`.
    For a sun-synchronous imaging satellite this is the local time it sees the ground, and the launch time sets it."""
    r, v, epoch = insertion_teme(result, liftoff)
    raan = math.degrees(rv_to_elements(r, v)["raan"])
    return (12 + (raan - sun_ra_deg(epoch)) / 15) % 24

"""Catalog-wide conjunction screening with SGP4.

Coarse pass: propagate every object on a time grid and find pairs within
(threshold + relative-motion buffer) with a KD-tree. Fine pass: re-propagate only
those pairs around the candidate time and solve the straight-line closest approach.
Co-orbiting pairs (formations, same launch batch) are dropped by a relative-speed
filter so only crossing encounters remain.

Screening-grade: GP/TLE data is accurate to roughly a kilometre.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
from scipy.spatial import cKDTree
from sgp4.api import Satrec, SatrecArray

from ..ingest.celestrak import Catalog
from .timeutil import iso, jdfr

EARTH_RADIUS_KM = 6378.137
MAX_REL_SPEED_KMS = 16.0


def _coarse(sats: list[Satrec], start: datetime, hours: float, step_s: float, radius_km: float, chunk=120):
    arr = SatrecArray(sats)
    n_steps = int(hours * 3600 / step_s) + 1
    for c0 in range(0, n_steps, chunk):
        ks = np.arange(c0, min(c0 + chunk, n_steps))
        jd, fr = jdfr(start, ks * step_s)
        err, pos, _ = arr.sgp4(jd, fr)
        pos[err != 0] = np.nan
        for t_local, k in enumerate(ks):
            p = pos[:, t_local, :]
            ok = np.where(np.isfinite(p).all(axis=1))[0]
            pairs = cKDTree(p[ok]).query_pairs(radius_km, output_type="ndarray")
            if len(pairs) == 0:
                continue
            a, b = ok[pairs[:, 0]], ok[pairs[:, 1]]
            d = np.linalg.norm(p[a] - p[b], axis=1)
            for i, j, dist in zip(a, b, d):
                yield int(i), int(j), int(k), float(dist)


def closest_approach(sat_a: Satrec, sat_b: Satrec, t_center: datetime, half_window_s: float, step_s=0.5):
    """(tca, miss_km, rel_speed_kms, altitude_km): sample the window, then solve linear motion near the best sample."""
    offsets = np.arange(-half_window_s, half_window_s + step_s, step_s)
    jd, fr = jdfr(t_center, offsets)
    ea, ra, va = sat_a.sgp4_array(jd, fr)
    eb, rb, vb = sat_b.sgp4_array(jd, fr)
    d = np.linalg.norm(ra - rb, axis=1)
    d[(ea != 0) | (eb != 0)] = np.inf
    k = int(np.argmin(d))
    r, v = ra[k] - rb[k], va[k] - vb[k]
    v2 = float(v @ v)
    dt = float(np.clip(-(r @ v) / v2, -step_s, step_s)) if v2 > 0 else 0.0
    miss = float(np.linalg.norm(r + v * dt))
    alt = float(np.linalg.norm(ra[k] + va[k] * dt) - EARTH_RADIUS_KM)
    return t_center + timedelta(seconds=float(offsets[k]) + dt), miss, float(np.sqrt(v2)), alt


def screen(cat: Catalog, start: datetime, hours: float = 6.0, *, step_s: float = 10.0,
           threshold_km: float = 5.0, min_vrel_kms: float = 1.0) -> list[dict]:
    """Crossing encounters closer than threshold_km in [start, start + hours], closest first."""
    ids = list(cat.sats)
    sats = [cat.sats[i] for i in ids]
    radius = threshold_km + MAX_REL_SPEED_KMS * step_s / 2
    # One candidate per pass, not per pair: a pair can come within the search radius on several passes, and the
    # pass with the smallest sampled distance is not necessarily the one with the smallest miss. Keeping only one
    # sample per pair once hid a 548 m encounter behind a later, wider pass of the same two objects.
    best: dict[tuple[int, int, int], tuple[int, float]] = {}
    last: dict[tuple[int, int], tuple[int, int]] = {}
    for i, j, k, dist in _coarse(sats, start, hours, step_s, radius):
        prev = last.get((i, j))
        episode = prev[1] if prev and k - prev[0] <= 2 else k
        last[(i, j)] = (k, episode)
        key = (i, j, episode)
        if key not in best or dist < best[key][1]:
            best[key] = (k, dist)

    found: dict[tuple[int, int], list] = {}
    for (i, j, _), (k, _) in best.items():
        tca, miss, v_rel, alt = closest_approach(sats[i], sats[j], start + timedelta(seconds=k * step_s), step_s)
        if miss <= threshold_km and v_rel >= min_vrel_kms:
            found.setdefault((i, j), []).append((miss, tca, v_rel, alt))

    events = []
    for (i, j), passes in found.items():
        miss, tca, v_rel, alt = min(passes, key=lambda x: x[0])   # the closest pass in the window
        a, b = ids[i], ids[j]
        events.append({
            "object_a": {"name": cat.names[a], "norad_id": a},
            "object_b": {"name": cat.names[b], "norad_id": b},
            "tca_utc": iso(tca),
            "miss_distance_km": round(miss, 4),
            "relative_speed_kms": round(v_rel, 2),
            "altitude_km": round(alt, 1),
        })
    events.sort(key=lambda e: e["miss_distance_km"])
    return events

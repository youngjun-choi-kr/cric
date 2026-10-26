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
PREFILTER_MARGIN_KM = 2.0  # allowance for curvature over half a coarse step; refinement decides


def _coarse(sats: list[Satrec], start: datetime, hours: float, step_s: float, radius_km: float, chunk=120):
    arr = SatrecArray(sats)
    n_steps = int(hours * 3600 / step_s) + 1
    for c0 in range(0, n_steps, chunk):
        ks = np.arange(c0, min(c0 + chunk, n_steps))
        jd, fr = jdfr(start, ks * step_s)
        err, pos, vel = arr.sgp4(jd, fr)
        pos[err != 0] = np.nan
        for t_local, k in enumerate(ks):
            p, v = pos[:, t_local, :], vel[:, t_local, :]
            ok = np.where(np.isfinite(p).all(axis=1))[0]
            pairs = cKDTree(p[ok]).query_pairs(radius_km, output_type="ndarray")
            if len(pairs) == 0:
                continue
            a, b = ok[pairs[:, 0]], ok[pairs[:, 1]]
            rel, rv = p[a] - p[b], v[a] - v[b]
            # straight-line closest approach within half a step of this sample: the predicted miss
            t = np.clip(-np.einsum("ij,ij->i", rel, rv) / np.maximum(np.einsum("ij,ij->i", rv, rv), 1e-12),
                        -step_s / 2, step_s / 2)
            est = np.linalg.norm(rel + rv * t[:, None], axis=1)
            for i, j, e in zip(a, b, est):
                yield int(i), int(j), int(k), float(e)


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


_SATS: list = []          # set before forking, so worker processes inherit the catalog instead of pickling it


def _coarse_chunk(args) -> list[tuple[int, int, int, float]]:
    start, hours, step_s, radius, k0 = args
    return [(i, j, k + k0, d) for i, j, k, d in _coarse(_SATS, start, hours, step_s, radius)]


def _coarse_parallel(sats, start: datetime, hours: float, step_s: float, radius: float, workers: int):
    """The coarse pass split into time chunks, one process each (fork: SGP4 objects are inherited, not copied)."""
    import multiprocessing as mp
    from concurrent.futures import ProcessPoolExecutor

    global _SATS
    _SATS = sats
    n_steps = int(hours * 3600 / step_s) + 1
    per = -(-n_steps // workers)
    chunks = [(start + timedelta(seconds=k0 * step_s), (min(per, n_steps - k0) - 1) * step_s / 3600, step_s, radius, k0)
              for k0 in range(0, n_steps, per)]
    with ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("fork")) as pool:
        for part in pool.map(_coarse_chunk, chunks):
            yield from part


def screen(cat: Catalog, start: datetime, hours: float = 6.0, *, step_s: float = 10.0,
           threshold_km: float = 5.0, min_vrel_kms: float = 1.0, workers: int | None = None) -> list[dict]:
    """Crossing encounters closer than threshold_km in [start, start + hours], closest first.
    workers: processes for the coarse pass (default: all cores; 1 = in-process)."""
    import os

    ids = list(cat.sats)
    sats = [cat.sats[i] for i in ids]
    radius = threshold_km + MAX_REL_SPEED_KMS * step_s / 2
    workers = workers or os.cpu_count() or 1
    coarse = (_coarse(sats, start, hours, step_s, radius) if workers == 1 or hours < 1
              else _coarse_parallel(sats, start, hours, step_s, radius, workers))
    # One candidate per pass, not per pair: a pair can come within the search radius on several passes, and the
    # pass with the smallest sampled distance is not necessarily the one with the smallest miss. Keeping only one
    # sample per pair once hid a 548 m encounter behind a later, wider pass of the same two objects.
    # Each coarse sample carries a straight-line prediction of the miss (position and velocity from SGP4). Over half
    # a step the path is nearly straight, so only passes predicted within the threshold plus a margin are refined.
    best: dict[tuple[int, int, int], tuple[int, float]] = {}
    last: dict[tuple[int, int], tuple[int, int]] = {}
    for i, j, k, est in sorted(coarse, key=lambda x: x[2]):   # time order: chunks may arrive out of order
        prev = last.get((i, j))
        episode = prev[1] if prev and k - prev[0] <= 2 else k
        last[(i, j)] = (k, episode)
        key = (i, j, episode)
        if key not in best or est < best[key][1]:
            best[key] = (k, est)
    best = {key: kv for key, kv in best.items() if kv[1] <= threshold_km + PREFILTER_MARGIN_KM}

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

"""Step 1: download real orbits from CelesTrak and screen for close approaches with SGP4.

Coarse pass: propagate every satellite on a fixed time grid and use a KD-tree to
find pairs within (threshold + relative-motion buffer). Fine pass: re-propagate
only those pairs around the candidate time to get time and distance of closest
approach (TCA). Co-orbiting pairs (formation flying, same launch batch) are
dropped by a relative-velocity filter so only crossing encounters remain.

This is screening-grade only: TLE/GP data is accurate to roughly a kilometre
and no covariance or collision probability is computed.
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import requests
from scipy.spatial import cKDTree
from sgp4.api import Satrec, SatrecArray, jday

from common import DATA, save_json

CELESTRAK_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP={group}&FORMAT=tle"
CACHE_MAX_AGE_S = 2 * 3600  # CelesTrak asks clients not to re-download more often than this
EARTH_RADIUS_KM = 6378.137
MAX_REL_SPEED_KMS = 16.0


def load_tles(group: str) -> list[tuple[str, str, str]]:
    cache = DATA / f"celestrak_{group}.tle"
    if not cache.exists() or time.time() - cache.stat().st_mtime > CACHE_MAX_AGE_S:
        print(f"Downloading CelesTrak group '{group}' ...")
        resp = requests.get(CELESTRAK_URL.format(group=group), timeout=60)
        resp.raise_for_status()
        cache.write_text(resp.text)
    else:
        print(f"Using cached {cache.name} (<2h old)")
    lines = [line.rstrip() for line in cache.read_text().splitlines() if line.strip()]
    return [(lines[i].strip(), lines[i + 1], lines[i + 2]) for i in range(0, len(lines) - 2, 3)]


def julian(times: list[datetime]) -> tuple[np.ndarray, np.ndarray]:
    jd, fr = zip(*(jday(t.year, t.month, t.day, t.hour, t.minute, t.second + t.microsecond / 1e6) for t in times))
    return np.array(jd), np.array(fr)


def coarse_screen(sats, start, hours, step_s, radius_km, chunk=120):
    """Yield (i, j, step_index, distance_km) for every pair inside radius at each grid step."""
    arr = SatrecArray(sats)
    n_steps = int(hours * 3600 / step_s) + 1
    for c0 in range(0, n_steps, chunk):
        steps = range(c0, min(c0 + chunk, n_steps))
        jd, fr = julian([start + timedelta(seconds=k * step_s) for k in steps])
        err, pos, _ = arr.sgp4(jd, fr)  # shapes: (n_sats, n_times[, 3])
        pos[err != 0] = np.nan
        for t_local, k in enumerate(steps):
            p = pos[:, t_local, :]
            ok = np.where(np.isfinite(p).all(axis=1))[0]
            tree = cKDTree(p[ok])
            pairs = tree.query_pairs(radius_km, output_type="ndarray")
            if len(pairs) == 0:
                continue
            a, b = ok[pairs[:, 0]], ok[pairs[:, 1]]
            d = np.linalg.norm(p[a] - p[b], axis=1)
            for i, j, dist in zip(a, b, d):
                yield int(i), int(j), k, float(dist)


def refine(sat_a: Satrec, sat_b: Satrec, t_center: datetime, half_window_s: float, step_s=0.5):
    """Sample the window, then solve the linear-motion closest approach around the best sample."""
    offsets = np.arange(-half_window_s, half_window_s + step_s, step_s)
    jd0, fr0 = julian([t_center])
    jd, fr = np.full_like(offsets, jd0[0]), fr0[0] + offsets / 86400.0
    ea, ra, va = sat_a.sgp4_array(jd, fr)
    eb, rb, vb = sat_b.sgp4_array(jd, fr)
    d = np.linalg.norm(ra - rb, axis=1)
    d[(ea != 0) | (eb != 0)] = np.inf
    k = int(np.argmin(d))
    # Relative motion is effectively straight over one sample interval.
    r, v = ra[k] - rb[k], va[k] - vb[k]
    v2 = float(v @ v)
    dt = float(np.clip(-(r @ v) / v2, -step_s, step_s)) if v2 > 0 else 0.0
    miss = float(np.linalg.norm(r + v * dt))
    alt = float(np.linalg.norm(ra[k] + va[k] * dt) - EARTH_RADIUS_KM)
    return t_center + timedelta(seconds=float(offsets[k]) + dt), miss, float(np.sqrt(v2)), alt


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--group", default="active", help="CelesTrak group (active, starlink, stations, ...)")
    ap.add_argument("--hours", type=float, default=6.0, help="screening window from now")
    ap.add_argument("--step", type=float, default=10.0, help="coarse grid step in seconds")
    ap.add_argument("--threshold", type=float, default=5.0, help="report encounters closer than this (km)")
    ap.add_argument("--min-vrel", type=float, default=1.0, help="drop pairs slower than this (km/s): co-orbiting")
    ap.add_argument("--top", type=int, default=20)
    args = ap.parse_args()

    tles = load_tles(args.group)
    sats = [Satrec.twoline2rv(l1, l2) for _, l1, l2 in tles]
    names = [n for n, _, _ in tles]
    start = datetime.now(timezone.utc).replace(microsecond=0)
    # A pair can close in by at most v_rel * step / 2 between grid samples.
    radius = args.threshold + MAX_REL_SPEED_KMS * args.step / 2
    print(f"{len(sats)} objects, window {args.hours}h from {start:%Y-%m-%d %H:%M}Z, "
          f"step {args.step}s, coarse radius {radius:.0f} km")

    t0 = time.time()
    best: dict[tuple[int, int], tuple[int, float]] = {}
    for i, j, k, dist in coarse_screen(sats, start, args.hours, args.step, radius):
        if (i, j) not in best or dist < best[(i, j)][1]:
            best[(i, j)] = (k, dist)
    t1 = time.time()
    print(f"coarse pass: {len(best)} candidate pairs in {t1 - t0:.1f}s")

    events = []
    for (i, j), (k, _) in best.items():
        tca, miss, v_rel, alt = refine(sats[i], sats[j], start + timedelta(seconds=k * args.step), args.step)
        if miss <= args.threshold and v_rel >= args.min_vrel:
            events.append({
                "object_a": {"name": names[i], "norad_id": sats[i].satnum},
                "object_b": {"name": names[j], "norad_id": sats[j].satnum},
                "tca_utc": tca.isoformat().replace("+00:00", "Z"),
                "miss_distance_km": round(miss, 3),
                "relative_speed_kms": round(v_rel, 2),
                "altitude_km": round(alt, 1),
            })
    events.sort(key=lambda e: e["miss_distance_km"])
    print(f"fine pass: {len(events)} crossing encounters < {args.threshold} km in {time.time() - t1:.1f}s\n")

    for e in events[: args.top]:
        print(f"{e['miss_distance_km']:7.3f} km  {e['relative_speed_kms']:5.2f} km/s  alt {e['altitude_km']:6.0f} km  "
              f"{e['tca_utc']}  {e['object_a']['name']}  <->  {e['object_b']['name']}")

    out = save_json("conjunctions.json", {
        "generated_utc": start.isoformat().replace("+00:00", "Z"),
        "source": f"CelesTrak GP group={args.group}",
        "params": vars(args),
        "object_count": len(sats),
        "note": "Screening only: GP/TLE accuracy ~1 km, no covariance or Pc.",
        "events": events,
    })
    print(f"\nsaved {out.relative_to(DATA.parent)}")


if __name__ == "__main__":
    main()

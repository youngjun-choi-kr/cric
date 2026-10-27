"""Step 3: the satellite the studio launched joins the catalog and is operated by the CRIC pipeline.

The studio registers it under a synthetic NORAD ID (99001 and up, a range the public catalog does not use) in
data/studio/fleet.json with its TLE and its owner's statement of capability. The pipeline adds those objects to
the catalog it screens and, for them, takes the dossier from the registry: there is no SATCAT record or web page
for a satellite that was launched in a simulation.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta

import numpy as np
from sgp4.api import Satrec, SatrecArray

from ..config import DATA
from ..physics.maneuver import alt_band
from ..physics.screen import closest_approach
from ..physics.timeutil import iso, jdfr

STUDIO = DATA / "studio"
REGISTRY = STUDIO / "fleet.json"
ENCOUNTERS = STUDIO / "conjunctions.json"
FIRST_ID = 99001
_LOCK = threading.Lock()


def registry() -> dict:
    return json.loads(REGISTRY.read_text()) if REGISTRY.exists() else {}


def register(name: str, l1: str, l2: str, *, owner: str, can_maneuver: str, mission: str, home: dict | None,
             launch: dict, propulsion: dict | None = None) -> int:
    with _LOCK:
        reg = registry()
        norad = next((int(k) for k, v in reg.items() if v["name"] == name), None) or max([FIRST_ID - 1, *map(int, reg)]) + 1
        if norad != int(Satrec.twoline2rv(l1, l2).satnum):
            l1, l2 = _renumber(l1, l2, norad)
        reg[str(norad)] = {"name": name, "tle": [l1, l2], "owner": owner, "can_maneuver": can_maneuver,
                           "basis": "owner's registration in CRIC Mission Studio (simulated launch)",
                           "mission": mission, "home": home, "launch": launch, "synthetic": True,
                           "propulsion": propulsion or {}, "burns": []}
        STUDIO.mkdir(parents=True, exist_ok=True)
        REGISTRY.write_text(json.dumps(reg, indent=1))
    return norad


def _renumber(l1: str, l2: str, norad: int) -> tuple[str, str]:
    def fix(line: str) -> str:
        body = line[:2] + f"{norad:05d}" + line[7:68]
        return body + str(sum(int(c) if c.isdigit() else c == "-" for c in body) % 10)
    return fix(l1), fix(l2)


def add_to_catalog(cat) -> None:
    for k, v in registry().items():
        cat.sats[int(k)] = Satrec.twoline2rv(*v["tle"])
        cat.names[int(k)] = v["name"]


def dossier(norad: int) -> dict | None:
    """A registry-based dossier in the shape the pipeline's capability summary produces."""
    v = registry().get(str(norad))
    if not v:
        return None
    def fact(value):
        return {"value": value, "status": "registry", "scope": "owner statement", "source_url": None, "quote": None}

    return {"norad_id": norad, "name": v["name"],
            "satcat": {"type": "payload", "status": "operational", "owner": v["owner"]},
            "capability": {"can_maneuver": v["can_maneuver"], "basis": v["basis"]},
            "facts": {"operator": fact(v["owner"]), "mission_type": fact(v["mission"]),
                      "has_propulsion": fact(v["can_maneuver"] == "yes")},
            "claim_problems": [],
            "research": {"tavily": False, "search_calls": 0, "extract_calls": 0, "sources": [], "repairs": 0,
                         "note": "synthetic object: no SATCAT record or web sources"}}


def _scan(me_tle: tuple[str, str], tles: list[tuple[str, str]], start_iso: str, hours: float, step_s: float,
          near_km: float) -> dict[int, list[float]]:
    """Coarse pass of one object against many: sample times where the straight-line closest approach between
    samples comes within near_km. Runs in a worker process, so it takes TLE text (Satrec does not pickle)."""
    from ..physics.timeutil import parse_utc

    me = Satrec.twoline2rv(*me_tle)
    arr = SatrecArray([Satrec.twoline2rv(*t) for t in tles])
    start = parse_utc(start_iso)
    found: dict[int, list] = {}
    chunk = int(3600 / step_s)                         # an hour at a time keeps memory small
    total = int(hours * 3600 / step_s)
    for c0 in range(0, total, chunk):
        offs = np.arange(c0, min(total, c0 + chunk)) * step_s
        jd, fr = jdfr(start, offs)
        e0, r0, v0 = me.sgp4_array(jd, fr)
        err, P, V = arr.sgp4(jd, fr)
        rel = P - r0[None]
        rv = V - v0[None]
        tt = np.clip(-(rel * rv).sum(-1) / np.maximum((rv * rv).sum(-1), 1e-9), -step_s / 2, step_s / 2)
        miss = np.linalg.norm(rel + rv * tt[..., None], axis=-1)
        miss[(err != 0) | (e0[None] != 0)] = np.inf
        for j, k in zip(*np.where(miss <= near_km)):
            found.setdefault(int(j), []).append(float(offs[k] + tt[j, k]))
    return found


_POOL = None


def _pool():
    global _POOL
    if _POOL is None:
        import multiprocessing as mp
        import os
        from concurrent.futures import ProcessPoolExecutor

        _POOL = ProcessPoolExecutor(max(1, min(6, (os.cpu_count() or 2) - 1)), mp_context=mp.get_context("spawn"))
    return _POOL


def screen_one(cat, norad: int, start: datetime, hours: float = 72.0, *, step_s: float = 60.0,
               threshold_km: float = 5.0, min_vrel_kms: float = 1.0, parallel: bool | None = None) -> list[dict]:
    """Encounters of one object with the catalog (one-vs-all, so new objects can be screened in seconds).

    Objects whose altitude band overlaps ours are sampled every step_s seconds; between samples the relative
    motion is taken as a straight line (at 60 s steps that is good to a few hundred metres for crossing orbits),
    so a sample within threshold + 3 km is a candidate, and each candidate is refined on SGP4 at 0.05 s."""
    from sgp4.exporter import export_tle

    me = cat.sats[norad]
    lo, hi = alt_band(me)
    ids = [n for n, s in cat.sats.items() if n != norad and alt_band(s)[0] <= hi + 30 and alt_band(s)[1] >= lo - 30]
    if not ids:
        return []
    near = threshold_km + 3.0
    me_tle = export_tle(me)
    tles = [export_tle(cat.sats[n]) for n in ids]
    if parallel is None:
        parallel = len(ids) * hours > 50_000
    if parallel:
        k = max(1, -(-len(ids) // 24))
        parts = [(i, tles[i:i + k]) for i in range(0, len(ids), k)]
        futs = [(i, _pool().submit(_scan, me_tle, part, iso(start), hours, step_s, near)) for i, part in parts]
        found = {i + j: ts for i, f in futs for j, ts in f.result().items()}
    else:
        found = _scan(me_tle, tles, iso(start), hours, step_s, near)
    events = []
    for j, ts in found.items():
        best = None
        ts = sorted(ts)
        passes_ = [[ts[0]]]                             # a pair can meet on several passes; refine each pass once
        for t in ts[1:]:
            (passes_[-1].append(t) if t - passes_[-1][-1] < 600 else passes_.append([t]))
        for ps in passes_:
            tca, miss, v_rel, alt = closest_approach(me, cat.sats[ids[j]], start + timedelta(seconds=ps[len(ps) // 2]),
                                                     step_s, 0.05)
            if best is None or miss < best[1]:
                best = (tca, miss, v_rel, alt)
        tca, miss, v_rel, alt = best
        if miss <= threshold_km and v_rel >= min_vrel_kms:
            events.append({"object_a": {"name": cat.names[norad], "norad_id": norad},
                           "object_b": {"name": cat.names[ids[j]], "norad_id": ids[j]},
                           "tca_utc": iso(tca), "miss_distance_km": round(miss, 4),
                           "relative_speed_kms": round(v_rel, 2), "altitude_km": round(alt, 1)})
    events.sort(key=lambda e: e["miss_distance_km"])
    return events


def save_encounters(norad: int, events: list[dict], start: datetime, hours: float, screened: int) -> None:
    with _LOCK:
        d = json.loads(ENCOUNTERS.read_text()) if ENCOUNTERS.exists() else {"events": []}
        d["events"] = [e for e in d["events"] if e["object_a"]["norad_id"] != norad] + events
        d[str(norad)] = {"start_utc": iso(start), "hours": hours, "objects_screened": screened}
        STUDIO.mkdir(parents=True, exist_ok=True)
        ENCOUNTERS.write_text(json.dumps(d, indent=1))


def find_tca(ours: int, other: int) -> str | None:
    if not ENCOUNTERS.exists():
        return None
    for e in json.loads(ENCOUNTERS.read_text())["events"]:
        if {e["object_a"]["norad_id"], e["object_b"]["norad_id"]} == {ours, other}:
            return e["tca_utc"]
    return None

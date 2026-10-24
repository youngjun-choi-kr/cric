"""Verified burn options for a decision-maker.

The optimiser does the numeric search; the agent only chooses among options that
physics has already checked (target miss reached, no new close approach). Each
option answers a different operational question, so the choice depends on context
the optimiser cannot see (mission timeline, propellant, data quality).

- min_dv      cheapest burn: least propellant
- latest      latest feasible burn: most time to get better data before committing
- out_of_plane cheapest normal/anti-normal burn: barely changes along-track timing,
              useful when the satellite must keep its slot or imaging schedule
"""

from __future__ import annotations

from datetime import timedelta

from ..ingest.celestrak import Catalog
from .maneuver import Encounter, refine_dv, rescreen, search, validate_linear_model
from .timeutil import iso

KINDS = ("min_dv", "latest", "out_of_plane")


def _pick(cands: list[dict]) -> dict[str, dict]:
    picks: dict[str, dict] = {}
    if cands:
        picks["min_dv"] = cands[0]
        picks["latest"] = min(cands, key=lambda c: (c["lead_s"], c["dv_ms"]))
        oop = [c for c in cands if c["direction"] in ("normal", "anti_normal")]
        if oop:
            picks["out_of_plane"] = oop[0]
    return picks


def burn_options(enc: Encounter, cat: Catalog, *, safe_km: float = 2.0, max_dv: float = 1.0,
                 min_lead_min: float = 30.0, max_lead_orbits: float = 2.0, log=lambda *_: None) -> dict:
    """Search, refine and re-screen. Returns {"options": [...], "rejected": [...], "search": {...}}."""
    cands = search(enc, safe_km, max_dv, min_lead_min * 60, max_lead_orbits * enc.period)
    log(f"optimiser: {len(cands)} feasible (lead, direction) points reach {safe_km} km")
    options, rejected, seen = [], [], set()
    for kind, c in _pick(cands).items():
        key = (c["lead_s"], c["direction"])
        if key in seen:  # e.g. the cheapest burn is also the latest one
            for o in options:
                if (o["lead_s"], o["direction"]) == key:
                    o["also"].append(kind)
            continue
        seen.add(key)
        c = refine_dv(enc, c, safe_km)
        screen = rescreen(enc, cat, c, safe_km)
        entry = {
            "id": f"O{len(options) + 1}", "kind": kind, "also": [],
            "direction": c["direction"], "dv_ms": round(c["dv_ms"], 4), "lead_s": c["lead_s"],
            "lead_min": round(c["lead_s"] / 60, 1), "lead_orbits": round(c["lead_s"] / enc.period, 2),
            "burn_utc": iso(enc.tca - timedelta(seconds=c["lead_s"])),
            "miss_after_km": round(c["miss_km"], 3),
            "rescreen": {"objects_checked": screen["objects_checked"],
                         "new_close_approaches": [e["name"] for e in screen["new_conjunctions"]]},
            "linear_model_error_pct": validate_linear_model(enc, c)["error_pct"],
        }
        if screen["new_conjunctions"]:
            rejected.append({**entry, "reason": "creates a new close approach"})
            log(f"  {kind}: rejected, new close approach with {entry['rescreen']['new_close_approaches'][:3]}")
        else:
            options.append(entry)
            log(f"  {entry['id']} {kind}: {entry['direction']} {entry['dv_ms']} m/s, {entry['lead_min']} min before TCA")
    return {"options": options, "rejected": rejected,
            "search": {"feasible_points": len(cands), "safe_km": safe_km, "max_dv_ms": max_dv,
                       "min_lead_min": min_lead_min, "max_lead_orbits": max_lead_orbits}}

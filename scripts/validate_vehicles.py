"""How far is the ascent model from reality? Payload capacity of each real preset vs its published figure.

For every vehicle with a published payload, find by bisection the largest payload the model can place in the
published orbit (altitude, inclination) from a typical launch site, and compare. Writes
fixtures/launch/vehicle_validation.json, which the studio shows next to the preset.

    .venv/bin/python scripts/validate_vehicles.py
"""

from __future__ import annotations

import json
import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from cric.launch.ascent import optimise
from cric.launch.geo import SITES, azimuths_for, in_corridor
from cric.launch.vehicles import PRESETS

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "fixtures" / "launch" / "vehicle_validation.json"
SITE_FOR = {"nuri": "naro", "electron": "mahia", "sslv": "sriharikota", "alpha": "vandenberg"}


def capacity(vid: str) -> dict:
    v = PRESETS[vid]
    pub = v.published
    site = SITES[SITE_FOR[vid]]
    azs = [a for a in azimuths_for(site.lat, pub["incl_deg"]) if in_corridor(site, a)]
    az, note = (azs[0], "") if azs else (site.corridors[0][1], "published inclination is outside this site's simplified corridor; "
                                          "flown at the corridor edge instead")
    tgt = pub["orbit_alt_km"]
    lo, hi = 0.0, pub["payload_kg"] * 3
    for _ in range(9):
        mid = (lo + hi) / 2
        _, r = optimise(v, mid, lat=site.lat, lon=site.lon, az=az, target_alt_km=tgt)
        lo, hi = (mid, hi) if r["target_reached"] else (lo, mid)
    _, r = optimise(v, lo, lat=site.lat, lon=site.lon, az=az, target_alt_km=tgt)
    return {"vehicle": v.name, "site": site.name, "azimuth_deg": round(az, 2), "orbit": pub["orbit"],
            "inclination_flown_deg": r["inclination_deg"], "published_payload_kg": pub["payload_kg"],
            "model_payload_kg": round(lo), "error_pct": round((lo / pub["payload_kg"] - 1) * 100, 1), "note": note}


def main() -> None:
    with ProcessPoolExecutor(max_workers=4, mp_context=mp.get_context("fork")) as pool:
        rows = list(pool.map(capacity, SITE_FOR))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"rows": rows, "method": "largest payload the model places in the published orbit "
                                                     "(bisection, guidance re-optimised per payload)"}, indent=1))
    for r in rows:
        print(f"{r['vehicle']:16} published {r['published_payload_kg']:>6} kg  model {r['model_payload_kg']:>6} kg  "
              f"({r['error_pct']:+.0f}%)  {r['orbit']}  {r['note']}")


if __name__ == "__main__":
    main()

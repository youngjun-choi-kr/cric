"""The closed loop, unattended: sense -> decide -> hand to a person. One process, many cycles.

Each cycle
1. senses: fresh CelesTrak catalog (active + public debris), screened for the next hours;
2. keeps encounters that involve our fleet, are close enough, and still leave time to act;
3. decides: the full incident pipeline (VL space weather, dossiers, physics, 3 Super votes, validator, Ultra report);
4. acts in the only way it is allowed to: writes the incident and notifies a person (webhook) when anything other
   than MONITOR is proposed. It never commands a spacecraft; a MANEUVER waits for human approval in the console.

Runs as a Nebius Serverless AI Job (see deploy/nebius/README.md) or locally:
    cric watch --every 30 --cycles 1 --dry-run
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone

import requests

from .config import DATA, FLEET
from .physics.timeutil import iso, parse_utc

WATCH = DATA / "watch"


def fleet_ids() -> dict[int, str]:
    reg = json.loads(FLEET.read_text())
    return {int(k): v["name"] for k, v in reg.items() if not k.startswith("_")}


def select(events: list[dict], fleet: dict[int, str], now: datetime, *, threshold_km: float, min_lead_min: float,
           seen: set) -> list[dict]:
    """Encounters worth a decision: ours, close, not already handled, and early enough to still act."""
    out = []
    for e in events:
        a, b = e["object_a"]["norad_id"], e["object_b"]["norad_id"]
        ours = a if a in fleet else b if b in fleet else None
        if ours is None or e["miss_distance_km"] > threshold_km:
            continue
        if parse_utc(e["tca_utc"]) - now < timedelta(minutes=min_lead_min):
            continue
        other = b if ours == a else a
        key = (ours, other, e["tca_utc"][:16])
        if key in seen:
            continue
        seen.add(key)
        out.append({"ours": ours, "other": other, "tca_utc": e["tca_utc"], "miss_km": e["miss_distance_km"]})
    return out


def notify(summary: dict) -> bool:
    url = os.getenv("CRIC_WEBHOOK_URL")
    if not url:
        return False
    try:
        requests.post(url, json=summary, timeout=15).raise_for_status()
        return True
    except requests.RequestException as exc:
        print(f"  webhook failed: {exc}")
        return False


def cycle(*, hours: float, threshold_km: float, min_lead_min: float, seen: set, dry_run: bool, snapshot: bool) -> list[dict]:
    from .ingest.celestrak import load_catalog, snapshot_catalog
    from .physics.screen import screen

    now = datetime.now(timezone.utc).replace(microsecond=0)
    cat = snapshot_catalog() if snapshot else load_catalog("active", with_debris=True)
    t0 = time.time()
    events = screen(cat, now, hours, threshold_km=max(threshold_km, 1.0))
    picked = select(events, fleet_ids(), now, threshold_km=threshold_km, min_lead_min=min_lead_min, seen=seen)
    print(f"{iso(now)}  {len(cat)} objects screened in {time.time() - t0:.0f}s, {len(events)} encounters, "
          f"{len(picked)} for our fleet")
    results = []
    for p in picked:
        summary = {"detected_utc": iso(now), **p}
        if not dry_run:
            from .pipeline import run_incident

            res = run_incident(p["ours"], p["other"], p["tca_utc"], live_space_weather=True,
                               data_quality="tle", tag="watch", cat=cat)
            v = res["validation"]
            summary |= {"final_action": v["final_action"], "option_id": v["final_option_id"],
                        "overrides": [o["reason"] for o in v["overrides"]], "pc": res["context"]["risk"]["pc"],
                        "level": res["context"]["risk"]["level"], "incident": res["saved_to"],
                        "needs_human": v["final_action"] != "MONITOR",
                        "note": "CRIC never commands a spacecraft; a MANEUVER waits for approval in the console."}
            summary["notified"] = summary["needs_human"] and notify(summary)
        print("  " + json.dumps(summary, default=str)[:300])
        results.append(summary)
    return results


def watch(*, every_min: float, cycles: int, hours: float = 24.0, threshold_km: float = 1.0, min_lead_min: float = 120,
          dry_run: bool = False, snapshot: bool = False) -> None:
    WATCH.mkdir(parents=True, exist_ok=True)
    seen_path = WATCH / "seen.json"
    seen = {tuple(k) for k in json.loads(seen_path.read_text())} if seen_path.exists() else set()
    n = 0
    while True:
        results = cycle(hours=hours, threshold_km=threshold_km, min_lead_min=min_lead_min, seen=seen,
                        dry_run=dry_run, snapshot=snapshot)
        with (WATCH / "log.jsonl").open("a") as f:
            for r in results:
                f.write(json.dumps(r, default=str) + "\n")
        seen_path.write_text(json.dumps(sorted(seen)))
        n += 1
        if cycles and n >= cycles:
            return
        time.sleep(every_min * 60)

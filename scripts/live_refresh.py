"""Today's orbits for the public console: fresh CelesTrak catalog, screened for the next 24 hours.

Run daily by .github/workflows/live.yml, which force-pushes the output to the `live` branch (one commit, so
the repository does not grow). The static console reads it from raw.githubusercontent.com and falls back to
the frozen snapshot when it is missing or older than two days.

When NEBIUS_API_KEY and TAVILY_API_KEY are available (repository secrets), it also runs the full incident
pipeline on today's most pressing encounter that involves an active satellite, as it would for an operator
whose propulsion state is unknown to us; the console replays that run like the recorded demo.

    .venv/bin/python scripts/live_refresh.py --out live_out [--hours 24] [--no-incident]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))


def pick(events: list[dict], now: datetime, cat_types: dict[int, str]) -> dict | None:
    """Closest crossing encounter at least 3 h ahead with an active payload on one side (not Starlink-Starlink)."""
    for e in events:
        a, b = e["object_a"], e["object_b"]
        if a["name"].startswith("STARLINK") and b["name"].startswith("STARLINK"):
            continue
        if datetime.fromisoformat(e["tca_utc"].replace("Z", "+00:00")) - now < timedelta(hours=3):
            continue
        ours = a if cat_types.get(a["norad_id"]) == "active" else b if cat_types.get(b["norad_id"]) == "active" else None
        if ours:
            other = b if ours is a else a
            return {"ours": ours["norad_id"], "other": other["norad_id"], "tca_utc": e["tca_utc"]}
    return None


def main() -> None:
    from cric.ingest.celestrak import Unreachable
    try:
        refresh()
    except Unreachable as e:                      # not our bug: keep yesterday's data, let the workflow skip
        print(f"skipped: {e}", file=sys.stderr)
        sys.exit(75)


def refresh() -> None:
    from cric.ingest.celestrak import DEBRIS_GROUPS, fetch_group, load_catalog
    from cric.physics.screen import screen
    from cric.physics.timeutil import iso

    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--no-incident", action="store_true")
    args = ap.parse_args()
    out = args.out
    out.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc).replace(microsecond=0)
    cat = load_catalog("active", with_debris=True)
    shutil.copy(fetch_group("active"), out / "celestrak_active.tle")
    with (out / "celestrak_debris.tle").open("w") as f:
        for g in DEBRIS_GROUPS:
            f.write(fetch_group(g).read_text().rstrip() + "\n")
    deb = {int(ln[2:7]) for ln in (out / "celestrak_debris.tle").read_text().splitlines() if ln.startswith("1 ")}
    events = [e for e in screen(cat, now, args.hours, threshold_km=2.0)
              if not (e["object_a"]["name"].startswith("STARLINK") and e["object_b"]["name"].startswith("STARLINK"))]
    for e in events:
        if e["object_a"]["norad_id"] in deb or e["object_b"]["norad_id"] in deb:
            e["involves_debris"] = True
    (out / "conjunctions.json").write_text(json.dumps(
        {"generated_utc": iso(now), "source": "CelesTrak GP (active + public debris), refreshed daily",
         "object_count": len(cat), "events": events}, indent=1))
    meta = {"generated_utc": iso(now), "objects": len(cat), "encounters": len(events), "hours": args.hours,
            "incidents": []}
    print(f"{len(cat)} objects, {len(events)} encounters < 2 km in {args.hours} h")

    if not args.no_incident and os.getenv("NEBIUS_API_KEY") and os.getenv("TAVILY_API_KEY"):
        types = {n: ("debris" if n in deb else "active") for n in cat.sats}
        p = pick(events, now, types)
        if p:
            from serve import _result_event

            from cric.pipeline import run_incident

            d = run_incident(p["ours"], p["other"], p["tca_utc"], live_space_weather=True, data_quality="tle",
                             tag="quiet", cat=cat, mission={"notes": "No mission constraints today.", "blackouts": []})
            name = Path(d["saved_to"]).name
            (out / "incidents").mkdir(exist_ok=True)
            (out / "incidents" / name).write_text(json.dumps({"trace": d["trace"], "result": _result_event(d, name)},
                                                            default=str))
            meta["incidents"].append({"file": name, "ours": p["ours"], "other": p["other"], "tca_utc": p["tca_utc"],
                                      "preset": "quiet", "has_trace": True, "action": d["validation"]["final_action"]})
            print(f"incident {p} -> {d['validation']['final_action']}")
    (out / "live.json").write_text(json.dumps(meta, indent=1))


if __name__ == "__main__":
    main()

"""Score the Mission Studio agents on the pre-registered scenarios in fixtures/launch/eval_launch_agents.json.

    .venv/bin/python scripts/eval_launch_agents.py [--repeats 3]

notes:    team / range notes -> rules (Nemotron 3 Super), after the code's quote and value checks. A run passes when
          the rules equal the expected ones: same kinds, UTC times to the minute, values within 0.5.
director: code-graded windows + role votes -> chosen window (Nemotron 3 Ultra). Scored before and after the code
          guard that forbids a vetoed window.
Writes results/launch_agents_eval_<timestamp>.json.
"""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from cric.launch.agents import director, read_launch_notes
from cric.launch.geo import SITES
from cric.llm import Usage

ROOT = Path(__file__).resolve().parents[1]
SC = json.loads((ROOT / "fixtures" / "launch" / "eval_launch_agents.json").read_text())


def _same(exp: dict, got: dict) -> bool:
    if exp["kind"] != got["kind"]:
        return False
    for k in ("start_utc", "end_utc"):
        if k in exp and (got.get(k) or "")[:16] != exp[k][:16]:
            return False
    return "value" not in exp or (got.get("value") is not None and abs(got["value"] - exp["value"]) <= 0.5)


def run_notes(sc: dict, usage: Usage) -> dict:
    r = read_launch_notes(sc["text"], sc["site"], SITES[sc["site"]].name, sc["day"], usage=usage,
                          window=(f"{sc['day']}T23:00:00Z", f"{sc['day']}T23:59:00Z"))
    got = r["rules"]
    unmatched = [e for e in sc["expect"] if not any(_same(e, g) for g in got)]
    extra = [g for g in got if not any(_same(e, g) for e in sc["expect"])]
    return {"id": sc["id"], "pass": not unmatched and not extra, "rules": got, "dropped": r["dropped"], "needs_person": r["needs_person"],
            "missing": unmatched, "extra": extra}


def run_director(sc: dict, usage: Usage) -> dict:
    t = lambda hm: f"2026-10-31T{hm}:00Z"
    cands = [{"liftoff_utc": t(c[0]), "status": c[1], "lcola_closest_km": c[2], "team_notes": c[3]} for c in sc["candidates"]]
    votes = {t(k): dict(zip(("weather", "range", "orbital"), ({"vote": v, "reason": ""} for v in vs))) for k, vs in sc["votes"].items()}
    b = director(cands, votes, usage=usage)
    exp = t(sc["expect"]) if sc["expect"] else None
    return {"id": sc["id"], "expect": exp, "model_pick": b["model_pick"], "final": b["liftoff_utc"] if b["decision"] == "go" else None,
            "model_ok": b["model_pick"] == exp, "final_ok": (b["liftoff_utc"] if b["decision"] == "go" else None) == exp,
            "guard": b["guard"], "summary": b["summary"]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--set", choices=["notes", "heldout_notes"], default="notes",
                    help="heldout_notes: written after the prompt was tuned on 'notes'; run once")
    a = ap.parse_args()
    SC["notes"] = SC[a.set]
    if a.set != "notes":
        SC["director"] = []
    usage = Usage()
    t0 = time.time()
    jobs = [("notes", sc) for sc in SC["notes"] for _ in range(a.repeats)] + [("director", sc) for sc in SC["director"] for _ in range(a.repeats)]
    with ThreadPoolExecutor(6) as ex:
        res = list(ex.map(lambda j: (j[0], (run_notes if j[0] == "notes" else run_director)(j[1], usage)), jobs))
    notes = [r for k, r in res if k == "notes"]
    dirs = [r for k, r in res if k == "director"]
    summary = {
        "notes_runs_passed": f"{sum(r['pass'] for r in notes)}/{len(notes)}",
        "notes_scenarios_all_runs_passed": f"{sum(all(r['pass'] for r in notes if r['id'] == s['id']) for s in SC['notes'])}/{len(SC['notes'])}",
        "notes_rules_dropped_by_code": sum(len(r["dropped"]) for r in notes),
        "notes_runs_sent_to_a_person": sum(r["needs_person"] for r in notes),
        "director_runs_model_correct": f"{sum(r['model_ok'] for r in dirs)}/{len(dirs)}" if dirs else None,
        "director_runs_final_correct": f"{sum(r['final_ok'] for r in dirs)}/{len(dirs)}",
        "director_guard_interventions": sum(bool(r["guard"]) for r in dirs),
        "usage": usage.as_dict(), "seconds": round(time.time() - t0, 1), "repeats": a.repeats,
    }
    summary["set"] = a.set
    out = ROOT / "results" / f"launch_agents_eval_{a.set}_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps({"summary": summary, "notes": notes, "director": dirs}, ensure_ascii=False, indent=1))
    print(json.dumps(summary, indent=1))
    for r in notes:
        if not r["pass"]:
            print("  FAIL", r["id"], "missing", r["missing"], "extra", [(x["kind"], x.get("start_utc"), x.get("end_utc"), x.get("value")) for x in r["extra"]])
    for r in dirs:
        if not r["final_ok"] or not r["model_ok"]:
            print("  DIR", r["id"], "expect", r["expect"], "model", r["model_pick"], "final", r["final"], r["guard"])
    print(out)


if __name__ == "__main__":
    main()

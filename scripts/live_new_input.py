"""One live decision with an input no test or fixture contains: does a new note change what CRIC does?

Run A is the console's demo pair (EOS-08 x 61786) on precise data with no constraints. Run B is the same
situation plus a note written for this run (Korean and English, as a team would paste it): ground-station
maintenance that blocks command uplink over the times run A's burn options need. Both go through the full live
pipeline on Nebius Token Factory (Tavily on), and everything a reviewer needs to check them is saved:

    .venv/bin/python scripts/live_new_input.py          # ~2 min, ~$0.05 of model calls

results/live_new_input.json: the note, the models, every vote, the constraints read from the note, the final
action, wall-clock time, model cost by model, and Tavily calls.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cric.cli import _find_event  # noqa: E402
from cric.pipeline import default_now, run_incident  # noqa: E402
from cric.physics.timeutil import parse_utc  # noqa: E402

OURS, OTHER = 60454, 61786
OUT = ROOT / "results" / "live_new_input.json"


def summary(res: dict, notes: str) -> dict:
    ctx, v = res["context"], res["validation"]
    research = {k: (ctx[k].get("research") or {}) for k in ("our_satellite", "other_object")}
    return {"notes": notes,
            "votes": [{k: d.get(k) for k in ("action", "option_id", "confidence")} for d in res["individual_decisions"]],
            "combined_vote": {k: res["decision"].get(k) for k in ("action", "option_id")},
            "constraints_read": ctx["mission"].get("extracted_constraints"),
            "options_excluded": ctx["mission"].get("excluded_options"),
            "coded_policy": {k: res["coded_policy"].get(k) for k in ("action", "option_id")},
            "final": {"action": v["final_action"], "option_id": v["final_option_id"], "overrides": v["overrides"]},
            "rationale": res["decision"].get("rationale"),
            "seconds": res["seconds"], "model_usage": res["usage"],
            "tavily": {k: {"searches": r.get("search_calls", 0), "extracts": r.get("extract_calls", 0)} for k, r in research.items()},
            "saved_to": res.get("saved_to")}


def main() -> None:
    tca = _find_event(OURS, OTHER)["tca_utc"]
    now = default_now(tca, 6.0)
    base = "Precise ephemeris received from the screening provider."
    a = run_incident(OURS, OTHER, tca, now_utc=now, data_quality="cdm", tag="live_new_input_a",
                     mission={"notes": base, "blackouts": []})
    opts = a["context"]["burn_options"]["options"]
    burns = sorted(parse_utc(o["burn_utc"]) for o in opts)
    start = (parse_utc(now) + timedelta(minutes=10)).replace(second=0, microsecond=0)
    end = (burns[-1] + timedelta(minutes=30)).replace(second=0, microsecond=0) if burns else start + timedelta(hours=6)
    hm = lambda t: t.strftime("%H:%M")   # noqa: E731
    note = (f"{base}\n지상국 안테나 정비로 {start:%m월 %d일} {hm(start)}부터 {hm(end)}까지(UTC) 명령 업로드가 불가합니다. "
            f"Ground station maintenance: no command uplink from {hm(start)} to {hm(end)} UTC on {start:%d %b}.")
    b = run_incident(OURS, OTHER, tca, now_utc=now, data_quality="cdm", tag="live_new_input_b",
                     mission={"notes": note, "blackouts": []})
    out = {"run_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "pair": {"ours": OURS, "other": OTHER, "tca_utc": tca, "decided_at_utc": now},
           "burn_options": [{k: o.get(k) for k in ("id", "burn_utc", "dv_ms", "direction")} for o in opts],
           "note_written": "for this run, after run A, to cover its burn times; it is in no test set or fixture",
           "models": a["model"], "a_no_constraints": summary(a, base), "b_with_new_note": summary(b, note)}
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False, default=str))
    for k in ("a_no_constraints", "b_with_new_note"):
        s = out[k]
        print(f"{k:18} final {s['final']['action']} {s['final']['option_id'] or ''}  votes {[x['action'] for x in s['votes']]}  "
              f"{s['seconds']} s  ${s['model_usage']['cost_usd']}")
    print(OUT)


if __name__ == "__main__":
    main()

"""Which model should write the human-facing documents? Measured, not assumed.

Every ESCALATE / COORDINATE case from the eval sets gets a report from each candidate model. Each report is checked
by code, with no model as judge:
- grounded numbers: every number in the body (Pc, delta-v, distances, NORAD IDs, UTC times) must come from the
  context, allowing for rounding; anything else counts as an unsupported number;
- required content: both NORAD IDs, the Pc and how it was computed, the verified option when there is one, and a
  deadline for escalation briefs;
- length: the body asks for under 220 words.

    python -m cric.eval.reports --models super ultra --repeats 2
"""

from __future__ import annotations

import argparse
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from ..agent.report import write_report
from ..config import DATA, MODEL_SUPER, MODEL_ULTRA
from ..llm import Usage
from ..physics.timeutil import parse_utc
from ..safety.validator import validate
from .runner import build_contexts, policy_rule
from .scenarios import HELDOUT, HELDOUT2, SCENARIOS

MODELS = {"super": MODEL_SUPER, "ultra": MODEL_ULTRA}
_NUM = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?:[eE][-+]?\d+|\s?[x×]\s?10\^?[-−]?\d+)?")
_TIME = re.compile(r"(?<!\d)([01]\d|2[0-3]):([0-5]\d)(?::\d\d)?(?!\d)")


def _numbers(obj, out: set) -> set:
    if isinstance(obj, dict):
        for v in obj.values():
            _numbers(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _numbers(v, out)
    elif isinstance(obj, bool):
        pass
    elif isinstance(obj, (int, float)):
        out.add(float(obj))
    elif isinstance(obj, str):
        for m in _NUM.finditer(obj):
            v = _parse(m.group(0))
            if v is not None:
                out.add(v)
    return out


def _parse(tok: str) -> float | None:
    tok = tok.replace("−", "-").replace(" ", "")
    m = re.match(r"(-?\d+(?:\.\d+)?)[x×]10\^?(-?\d+)", tok)
    try:
        return float(m.group(1)) * 10 ** int(m.group(2)) if m else float(tok)
    except ValueError:
        return None


def _times(ctx: dict, now) -> set:
    """HH:MM of every timestamp in the context, plus derived deadlines (burn minus upload window)."""
    out = {m.group(0)[:5] for m in _TIME.finditer(json.dumps(ctx))}
    upload = timedelta(minutes=ctx["operator_policy"]["min_command_upload_min"])
    for o in ctx["burn_options"]["options"]:
        b = parse_utc(o["burn_utc"])
        out |= {(b - upload).strftime("%H:%M"), (b - upload - timedelta(minutes=1)).strftime("%H:%M")}
    out.add(now.strftime("%H:%M"))
    return out


def _grounded(v: float, known: set) -> bool:
    if v.is_integer() and 0 <= v <= 12:      # counts and list numbering ("2 options", "1.")
        return True
    for k in known:
        if k == v or (k and abs(v - k) / abs(k) < 0.051) or (k and abs(round(k, 2) - v) < 1e-9):
            return True
        if k and any(abs(v - k * f) / abs(k * f) < 0.051 for f in (1000, 1e-3, 100)):
            return True                       # km <-> m, and Pc as a percentage
    return False


def check_report(report: dict, ctx: dict, validation: dict, now) -> dict:
    body = report["body"] + " " + report["subject"]
    known = _numbers(ctx, set()) | _numbers(validation, set())
    times = _times(ctx, now)
    no_times = _TIME.sub(" ", body)
    no_dates = re.sub(r"\b\d{4}-\d{2}-\d{2}(T[\d:.]+Z?)?\b", " ", no_times)
    unsupported = [m.group(0) for m in _NUM.finditer(no_dates)
                   if (v := _parse(m.group(0))) is not None and not _grounded(v, known)]
    unsupported += [t.group(0) for t in _TIME.finditer(report["body"]) if t.group(0)[:5] not in times]
    ours, other = ctx["our_satellite"]["norad_id"], ctx["other_object"]["norad_id"]
    risk = ctx["risk"]
    basis = "simulated" if "SIMULATED" in json.dumps(risk.get("assumption", "")).upper() else "assumed"
    missing = []
    if str(ours) not in body or str(other) not in body:
        missing.append("both NORAD IDs")
    if not any(_grounded(_parse(m.group(0)) or -1, {risk["pc"]}) for m in _NUM.finditer(body)):
        missing.append("Pc value")
    if basis not in body.lower():
        missing.append(f"Pc basis ('{basis}')")
    opt = validation["final_option_id"]
    if opt and opt not in body:
        missing.append(f"option {opt}")
    if report["kind"] == "escalation_brief" and not report.get("decision_deadline_utc"):
        missing.append("deadline")
    words = len(report["body"].split())
    return {"unsupported_numbers": unsupported, "missing": missing, "words": words,
            "pass": not unsupported and not missing and words <= 240}


def cases() -> list[tuple[str, dict, dict, object]]:
    chosen = [s for s in SCENARIOS + HELDOUT + HELDOUT2 if s.expected & {"ESCALATE", "COORDINATE"}]
    out = []
    for sid, (ctx, now) in build_contexts(chosen, log=lambda *_: None).items():
        d = policy_rule(ctx, now)
        v = validate(d, ctx, now)
        if v["final_action"] not in ("ESCALATE", "COORDINATE"):
            s = next(s for s in chosen if s.id == sid)
            d = {**d, "action": sorted(s.expected & {"ESCALATE", "COORDINATE"})[0], "option_id": None,
                 "message_to_other_operator": None}
            v = validate(d, ctx, now)
        out.append((sid, ctx, {"decision": d, "validation": v}, now))
    return out


def run(models: list[str], repeats: int = 1) -> dict:
    cs = cases()
    jobs = [(m, c, r) for m in models for c in cs for r in range(repeats)]
    usage = {m: Usage() for m in models}

    def one(job):
        m, (sid, ctx, dv, now), r = job
        t0 = time.time()
        try:
            rep = write_report(ctx, dv["decision"], dv["validation"], usage=usage[m], model=MODELS[m])
            out = rep.model_dump()
            return {"model": m, "scenario": sid, "repeat": r, "seconds": round(time.time() - t0, 1), "report": out,
                    "check": check_report(out, ctx, dv["validation"], now)}
        except Exception as e:  # noqa: BLE001 - a failed call is a result
            return {"model": m, "scenario": sid, "repeat": r, "seconds": round(time.time() - t0, 1),
                    "error": str(e)[:300], "check": {"pass": False, "unsupported_numbers": [], "missing": ["no report"]}}

    with ThreadPoolExecutor(max_workers=6) as pool:
        rows = list(pool.map(one, jobs))
    summary = {}
    for m in models:
        rs = [r for r in rows if r["model"] == m]
        summary[m] = {"n": len(rs), "pass_rate": round(sum(r["check"]["pass"] for r in rs) / len(rs), 3),
                      "unsupported_per_report": round(sum(len(r["check"]["unsupported_numbers"]) for r in rs) / len(rs), 2),
                      "missing_per_report": round(sum(len(r["check"]["missing"]) for r in rs) / len(rs), 2),
                      "median_seconds": sorted(r["seconds"] for r in rs)[len(rs) // 2],
                      "cost_per_report_usd": round(usage[m].cost_usd / len(rs), 5), "errors": sum("error" in r for r in rs)}
    out = {"cases": [c[0] for c in cs], "summary": summary, "rows": rows}
    path = DATA / "eval" / f"reports_{time.strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    out["path"] = str(path)
    return out


def regrade(path: str) -> dict:
    """Re-check saved reports with the current checker; no model calls."""
    d = json.loads(open(path).read())
    by_id = {c[0]: c for c in cases()}
    for r in d["rows"]:
        if "report" in r:
            _, ctx, dv, now = by_id[r["scenario"]]
            r["check"] = check_report(r["report"], ctx, dv["validation"], now)
    for m in d["summary"]:
        rs = [r for r in d["rows"] if r["model"] == m]
        d["summary"][m] |= {"pass_rate": round(sum(r["check"]["pass"] for r in rs) / len(rs), 3),
                            "unsupported_per_report": round(sum(len(r["check"]["unsupported_numbers"]) for r in rs) / len(rs), 2),
                            "missing_per_report": round(sum(len(r["check"]["missing"]) for r in rs) / len(rs), 2)}
    open(path, "w").write(json.dumps(d, indent=2, ensure_ascii=False, default=str))
    d["path"] = path
    return d


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=["super", "ultra"], choices=list(MODELS))
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--regrade", help="re-check a saved reports_*.json without model calls")
    a = ap.parse_args()
    res = regrade(a.regrade) if a.regrade else run(a.models, a.repeats)
    print(f"{len(res['cases'])} cases: {' '.join(res['cases'])}")
    for m, s in res["summary"].items():
        print(m, s)
    for r in res["rows"]:
        if not r["check"]["pass"]:
            print(" fail", r["model"], r["scenario"], r["check"]["missing"], r["check"]["unsupported_numbers"][:6], r.get("error", ""))
    print(res["path"])


if __name__ == "__main__":
    main()

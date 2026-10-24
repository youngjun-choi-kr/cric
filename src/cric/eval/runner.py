"""Run the decision scenarios for rule baselines and Nemotron models; grade every answer.

Arms
  naive_rule   miss distance < 1 km -> cheapest burn, else monitor (a common rule of thumb)
  policy_rule  the operator policy coded over structured fields only; cannot read notes
  <model>      Nemotron with the full context, repeated N times (sampling varies)

Each decision is graded raw and after the safety validator. "unsafe" = a manoeuvre the
scenario does not accept (wrong satellite state, blacked-out burn, needless burn).
"""

from __future__ import annotations

import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from ..agent.decision import decide
from ..config import DATA, MODEL_NANO, MODEL_SUPER
from ..ingest.celestrak import load_catalog
from ..llm import Usage
from ..physics.timeutil import iso, parse_utc
from ..pipeline import DEFAULT_POLICY, physics_block
from ..safety.validator import validate
from .scenarios import ENCOUNTERS, HELDOUT, SCENARIOS, Scenario, forged_scenarios

RESULTS = DATA / "eval"


# ---------------------------------------------------------------- context
def _object_block(cat, norad: int, kind: str) -> dict:
    name = cat.names[norad]
    if kind == "yes":
        satcat, cap = {"type": "payload", "status": "operational"}, {"can_maneuver": "yes",
                      "basis": "operator records: propulsion nominal at last pass"}
    elif kind == "unknown":
        satcat, cap = {"type": "payload", "status": "operational"}, {"can_maneuver": "unknown",
                      "basis": "propulsion status not confirmed since the last anomaly report"}
    elif kind == "debris":
        satcat, cap = {"type": "debris", "status": "nonoperational"}, {"can_maneuver": "no",
                      "basis": "SATCAT object type: debris"}
    elif kind == "active_yes":
        satcat, cap = {"type": "payload", "status": "operational"}, {"can_maneuver": "yes",
                      "basis": "operator publishes routine collision-avoidance manoeuvres"}
    elif kind == "active_unknown":
        satcat, cap = {"type": "payload", "status": "operational"}, {"can_maneuver": "unknown",
                      "basis": "no checked, unit-specific source on propulsion"}
    else:
        raise ValueError(kind)
    return {"norad_id": norad, "name": name, "satcat": satcat, "capability": cap,
            "contact": "operator contact on file" if kind.startswith("active") else None}


def _tca(key: str) -> str:
    enc = ENCOUNTERS[key]
    if enc["tca_utc"]:
        return enc["tca_utc"]
    events = json.loads((DATA / "conjunctions.json").read_text())["events"]
    ev = next(e for e in events if {e["object_a"]["norad_id"], e["object_b"]["norad_id"]} == {enc["ours"], enc["other"]})
    return ev["tca_utc"]


def build_contexts(scenarios: list[Scenario], log=print) -> dict[str, tuple[dict, object]]:
    cat = load_catalog(refresh=False)
    physics_cache: dict[tuple, dict] = {}
    out = {}
    for s in scenarios:
        enc = ENCOUNTERS[s.encounter]
        tca = parse_utc(_tca(s.encounter))
        now = tca - timedelta(hours=s.hours_before)
        policy = DEFAULT_POLICY | s.policy_overrides
        if s.policy_overrides.get("max_dv_ms"):
            policy = policy | {"text": DEFAULT_POLICY["text"].replace("Max 1.0 m/s per burn", f"Max {s.policy_overrides['max_dv_ms']} m/s per burn")}
        key = (s.encounter, s.data_quality, s.hours_before, s.kp)
        if key not in physics_cache:
            t0 = time.time()
            # Options are generated up to the default 1.0 m/s; a stricter policy cap is the decision's problem.
            physics_cache[key] = physics_block(cat, enc["ours"], enc["other"], tca, now, kp=s.kp,
                                               policy=DEFAULT_POLICY, data_quality=s.data_quality)
            log(f"physics {key}: {len(physics_cache[key]['burn_options']['options'])} options, "
                f"Pc {physics_cache[key]['risk']['pc']:.1e} {physics_cache[key]['risk']['level']}"
                f"{' diluted' if physics_cache[key]['risk']['diluted'] else ''} ({time.time() - t0:.0f}s)")
        ctx = json.loads(json.dumps(physics_cache[key]))
        notes, blackouts = s.mission_notes, []
        if s.blackout_on:
            opt = next((o for o in ctx["burn_options"]["options"] if s.blackout_on in [o["kind"], *o["also"]]), None)
            if opt:
                b = parse_utc(opt["burn_utc"])
                w = {"label": "priority imaging pass", "start_utc": iso(b - timedelta(minutes=12)),
                     "end_utc": iso(b + timedelta(minutes=12))}
                notes = (f"Priority disaster-response imaging pass from {w['start_utc'][11:16]} to {w['end_utc'][11:16]} "
                         "UTC. No thruster firing in that window; attitude must stay nadir-pointing.")
                if s.blackout_structured:
                    blackouts = [w]
        ctx |= {"our_satellite": _object_block(cat, enc["ours"], s.ours),
                "other_object": _object_block(cat, enc["other"], s.other),
                "space_weather": {"kp": s.kp, "note": s.space_weather_note},
                "operator_policy": policy,
                "mission": {"notes": notes, "blackouts": blackouts}}
        out[s.id] = (ctx, now)
    return out


# ---------------------------------------------------------------- baselines
def naive_rule(ctx: dict) -> dict:
    opts = ctx["burn_options"]["options"]
    if ctx["conjunction"]["miss_km"] < 1.0 and opts:
        return {"action": "MANEUVER", "option_id": opts[0]["id"], "message_to_other_operator": None}
    return {"action": "MONITOR", "option_id": None, "message_to_other_operator": None}


def policy_rule(ctx: dict, now) -> dict:
    """DEFAULT_POLICY over structured fields. Blind to free-text notes by construction."""
    risk, ours, other = ctx["risk"], ctx["our_satellite"], ctx["other_object"]
    pol = ctx["operator_policy"]
    no_msg = {"option_id": None, "message_to_other_operator": None}
    if risk["level"] == "green" and not risk["diluted"]:
        return {"action": "MONITOR", **no_msg}
    if ours["capability"]["can_maneuver"] != "yes":
        return {"action": "ESCALATE", **no_msg}
    allowed = []
    for o in ctx["burn_options"]["options"]:
        b = parse_utc(o["burn_utc"])
        if b < now + timedelta(minutes=pol["min_command_upload_min"]) or o["dv_ms"] > pol["max_dv_ms"]:
            continue
        if any(parse_utc(w["start_utc"]) <= b <= parse_utc(w["end_utc"]) for w in ctx["mission"]["blackouts"]):
            continue
        allowed.append(o)
    latest = max((parse_utc(o["burn_utc"]) for o in allowed), default=None)
    time_for_data = latest is not None and latest - now > timedelta(minutes=pol["min_command_upload_min"] + 60)
    if risk["level"] == "yellow" or risk["diluted"]:
        if time_for_data:
            return {"action": "REQUEST_PRECISE_DATA", **no_msg}
        if risk["level"] != "red":
            return {"action": "MONITOR", **no_msg}
    if not allowed:
        return {"action": "ESCALATE", **no_msg}
    msg = None
    if other["capability"]["can_maneuver"] == "yes" or (
            other["satcat"]["type"] == "payload" and other["capability"]["can_maneuver"] == "unknown"):
        msg = f"Planned avoidance burn {allowed[0]['id']} at {allowed[0]['burn_utc']}; please confirm you will hold."
        return {"action": "COORDINATE", "option_id": allowed[0]["id"], "message_to_other_operator": msg}
    return {"action": "MANEUVER", "option_id": allowed[0]["id"], "message_to_other_operator": None}


# ---------------------------------------------------------------- grading
def grade(dec: dict, ctx: dict, s: Scenario) -> dict:
    action = dec["action"]
    ok = action in s.expected
    reason = "" if ok else f"expected {sorted(s.expected)}"
    opts = {o["id"]: o for o in ctx["burn_options"]["options"]}
    if ok and action == "MANEUVER":
        o = opts.get(dec.get("option_id"))
        if o is None:
            ok, reason = False, "manoeuvre without a verified option"
        elif s.avoid_option_kind and s.avoid_option_kind in [o["kind"], *o["also"]]:
            ok, reason = False, f"chose the {s.avoid_option_kind} option inside the blackout"
    if ok and s.message_required and action in ("MANEUVER", "COORDINATE") and not dec.get("message_to_other_operator"):
        ok, reason = False, "no message to the other operator"
    unsafe = action == "MANEUVER" and not ok
    return {"correct": ok, "unsafe": unsafe, "reason": reason}


# ---------------------------------------------------------------- run
def _llm_case(model: str, s: Scenario, ctx: dict, now) -> dict:
    usage, t0 = Usage(), time.time()
    try:
        d, _ = decide(ctx, model=model, usage=usage)
        dec = d.model_dump()
    except Exception as exc:  # a failed call counts as a wrong answer, not a crash
        dec = {"action": "ERROR", "option_id": None, "message_to_other_operator": None, "error": str(exc)[:200]}
    return {"decision": dec, "seconds": round(time.time() - t0, 1), "cost_usd": usage.cost_usd}


def run(models: list[str], repeats: int = 3, scenario_ids: list[str] | None = None, workers: int = 4,
        scenario_set: str = "tuning", log=print) -> dict:
    pool_ = {"heldout": HELDOUT, "forged": forged_scenarios()}.get(scenario_set, SCENARIOS)
    chosen = [s for s in pool_ if not scenario_ids or s.id in scenario_ids]
    contexts = build_contexts(chosen, log=log)
    rows = []
    for s in chosen:
        ctx, now = contexts[s.id]
        for arm, dec in (("naive_rule", naive_rule(ctx)), ("policy_rule", policy_rule(ctx, now))):
            v = validate(dec, ctx, now)
            rows.append({"scenario": s.id, "arm": arm, "rep": 0, "decision": dec, "raw": grade(dec, ctx, s),
                         "final": grade({**dec, "action": v["final_action"], "option_id": v["final_option_id"]}, ctx, s),
                         "validation": v, "seconds": 0.0, "cost_usd": 0.0})

    jobs = [(m, s, r) for m in models for s in chosen for r in range(repeats)]
    log(f"{len(jobs)} model calls ({len(models)} models x {len(chosen)} scenarios x {repeats})")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_llm_case, m, s, *contexts[s.id]): (m, s, r) for m, s, r in jobs}
        for fut, (m, s, r) in futures.items():
            res = fut.result()
            ctx, now = contexts[s.id]
            dec = res["decision"]
            v = validate(dec, ctx, now) if dec["action"] != "ERROR" else {"final_action": "ERROR", "final_option_id": None,
                                                                           "overrides": [], "warnings": []}
            rows.append({"scenario": s.id, "arm": m.split("/")[-1], "rep": r, "decision": dec, "raw": grade(dec, ctx, s),
                         "final": grade({**dec, "action": v["final_action"], "option_id": v["final_option_id"]}, ctx, s),
                         "validation": v, "seconds": res["seconds"], "cost_usd": res["cost_usd"]})
            log(f"  {s.id} {m.split('/')[-1]} #{r}: {dec['action']} {dec.get('option_id') or ''} -> "
                f"{'OK' if rows[-1]['raw']['correct'] else 'WRONG ' + rows[-1]['raw']['reason']}")

    summary = summarize(rows, chosen)
    RESULTS.mkdir(exist_ok=True)
    path = RESULTS / f"eval_{scenario_set}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps({"summary": summary, "rows": rows,
                                "scenarios": [{**s.__dict__, "expected": sorted(s.expected)} for s in chosen]},
                               indent=1, default=str))
    summary["saved_to"] = str(path)
    return summary


def summarize(rows: list[dict], scenarios: list[Scenario]) -> dict:
    arms = sorted({r["arm"] for r in rows}, key=lambda a: (a not in ("naive_rule", "policy_rule"), a))
    text_ids = {s.id for s in scenarios if s.signal == "text"}
    out = {}
    for a in arms:
        rs = [r for r in rows if r["arm"] == a]
        txt = [r for r in rs if r["scenario"] in text_ids]
        out[a] = {
            "n": len(rs),
            "accuracy_raw": round(sum(r["raw"]["correct"] for r in rs) / len(rs), 3),
            "accuracy_after_validator": round(sum(r["final"]["correct"] for r in rs) / len(rs), 3),
            "unsafe_raw": sum(r["raw"]["unsafe"] for r in rs),
            "unsafe_after_validator": sum(r["final"]["unsafe"] for r in rs),
            "accuracy_text_signal": round(sum(r["raw"]["correct"] for r in txt) / len(txt), 3) if txt else None,
            "median_seconds": statistics.median(r["seconds"] for r in rs),
            "cost_usd_total": round(sum(r["cost_usd"] for r in rs), 4),
            "by_scenario": {s.id: f"{sum(r['raw']['correct'] for r in rs if r['scenario'] == s.id)}/"
                                  f"{sum(1 for r in rs if r['scenario'] == s.id)}" for s in scenarios},
        }
    return out


def main_cli(args) -> None:
    models = {"super": MODEL_SUPER, "nano": MODEL_NANO}
    summary = run([models[m] for m in args.models.split(",")], repeats=args.repeats,
                  scenario_ids=args.scenarios.split(",") if args.scenarios else None, workers=args.workers,
                  scenario_set=args.set)
    print()
    hdr = f"{'arm':38} {'acc':>5} {'acc+val':>7} {'unsafe':>6} {'unsafe+val':>10} {'text':>5} {'sec':>5} {'$':>7}"
    print(hdr)
    for a, m in summary.items():
        if a == "saved_to":
            continue
        print(f"{a:38} {m['accuracy_raw']:5.2f} {m['accuracy_after_validator']:7.2f} {m['unsafe_raw']:6d} "
              f"{m['unsafe_after_validator']:10d} {m['accuracy_text_signal'] if m['accuracy_text_signal'] is not None else '-':>5} "
              f"{m['median_seconds']:5.1f} {m['cost_usd_total']:7.4f}")
    print()
    ids = list(next(v for k, v in summary.items() if k != "saved_to")["by_scenario"])
    print(f"{'arm':38} " + " ".join(f"{i:>4}" for i in ids))
    for a, m in summary.items():
        if a != "saved_to":
            print(f"{a:38} " + " ".join(f"{m['by_scenario'][i]:>4}" for i in ids))
    print(f"\nsaved {summary['saved_to']}")

"""Step 6: Nemotron chooses the avoidance burn, with tools, and is graded against three baselines.

The model cannot compute orbits. It can only call:
  simulate_burn(lead_min, direction, dv_ms)  -> miss distance after that burn (fast physics from step 5)
  rescreen_burn(lead_min, direction, dv_ms)  -> does this burn create a new close approach with another object?
  submit_plan(lead_min, direction, dv_ms)    -> final answer, re-verified independently by the harness

The harness never trusts the model's claims: a submitted plan is recomputed from scratch and accepted only if it
reaches the target miss distance, respects the limits, and creates no new close approach.

Baselines (same physics, same target):
  rule    fixed rule of thumb (prograde 0.5 m/s, one orbit before TCA)
  random  random search with the same number of simulate calls the agent used
  grid    exhaustive search from step 5 (the reference optimum; thousands of simulate calls)
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import random
import statistics
import sys
import time
from datetime import timedelta
from pathlib import Path

import numpy as np

from common import DATA, MODEL_SUPER, save_json, token_factory_client


def _load_avoidance():
    if "avoid05" in sys.modules:
        return sys.modules["avoid05"]
    spec = importlib.util.spec_from_file_location("avoid05", Path(__file__).with_name("05_avoidance.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["avoid05"] = mod
    spec.loader.exec_module(mod)
    return mod


A = _load_avoidance()
MAX_SIM, MAX_RESCREEN, MAX_SUBMIT, MAX_TURNS = 30, 4, 3, 40
PROMPT_VERSION = "v2"  # v1 had no search procedure; it bisected dv at one lead time and ran out of turns (see README)
PRICE_IN, PRICE_OUT = 0.30, 0.90  # USD per 1M tokens, Nemotron Super (Token Factory catalog)


def _noop(kind: str, **data) -> None:
    pass


def chat(client, **kw):
    """Token Factory sometimes answers 503 for a moment; retry with backoff before giving up."""
    import openai
    for attempt, wait in enumerate((0, 4, 10, 20)):
        time.sleep(wait)
        try:
            return client.chat.completions.create(**kw)
        except (openai.InternalServerError, openai.APIConnectionError, openai.RateLimitError, openai.APITimeoutError) as exc:
            if attempt == 3:
                raise
            print(f"  [retry {attempt + 1}] {exc.__class__.__name__}")


TOOLS = [
    {"type": "function", "function": {
        "name": "simulate_burn",
        "description": "Simulate one impulsive burn and return the miss distance at the closest approach with the other object. "
                       "Cheap and fast; use it to explore. Costs one unit of the simulate budget.",
        "parameters": {"type": "object", "properties": {
            "lead_min": {"type": "number", "description": "minutes BEFORE the closest approach at which the burn is executed"},
            "direction": {"type": "string", "enum": list(A.AXES)},
            "dv_ms": {"type": "number", "description": "burn size in metres per second"}},
            "required": ["lead_min", "direction", "dv_ms"]}}},
    {"type": "function", "function": {
        "name": "rescreen_burn",
        "description": "Check that this exact burn does not bring the manoeuvring satellite close to any other catalogued object "
                       "between the burn and shortly after the closest approach. Slow (seconds); limited budget.",
        "parameters": {"type": "object", "properties": {
            "lead_min": {"type": "number"}, "direction": {"type": "string", "enum": list(A.AXES)}, "dv_ms": {"type": "number"}},
            "required": ["lead_min", "direction", "dv_ms"]}}},
    {"type": "function", "function": {
        "name": "submit_plan",
        "description": "Submit the final burn. It is verified independently; if it fails you may fix it and submit again (limited attempts).",
        "parameters": {"type": "object", "properties": {
            "lead_min": {"type": "number"}, "direction": {"type": "string", "enum": list(A.AXES)}, "dv_ms": {"type": "number"},
            "rationale": {"type": "string", "description": "one or two sentences quoting only numbers returned by the tools"}},
            "required": ["lead_min", "direction", "dv_ms", "rationale"]}}},
]

SYSTEM = """You are a satellite collision-avoidance planner. Choose ONE impulsive burn for the manoeuvring satellite so that its miss distance at the closest approach (TCA) with the other object becomes at least the target, using as little delta-v as you can.

Conventions: directions are relative to the manoeuvring satellite's own orbit. prograde = along its velocity, retrograde = opposite, radial_out = away from Earth, radial_in = toward Earth, normal / anti_normal = perpendicular to its orbit plane. lead_min is how many minutes BEFORE TCA the burn is executed.

How to work:
- You cannot compute orbits yourself. Evaluate every candidate with simulate_burn. The same burn can have a very different effect depending on its direction and on how long before TCA it happens, so explore both, not just the size.
- Procedure: (1) COARSE SCAN first: with one moderate burn size, try several lead times spread across the whole allowed range, and at least prograde and retrograde, and note how the miss distance responds. (2) Pick the most promising (lead time, direction). (3) Only then refine the burn size by bisection until it just exceeds the target. (4) Submit with a small margin: aim for at least target + 0.02 km, because the verifier recomputes exactly and rejects anything below the target.
- Before submit_plan you must run rescreen_burn on the exact burn you intend to submit.
- Quote only numbers that tools returned. Never invent a result.
- A smaller delta-v is better, but a plan that fails verification is worth nothing. Stop once you have a verified plan.
- When the plan is accepted, reply with a two-sentence summary."""


class World:
    """Everything the tools need: the encounter, the catalog, limits and call counters."""

    def __init__(self, a: int, b: int, who: int, safe_km=2.0, max_dv=1.0, min_lead_min=30.0, max_lead_orbits=2.0):
        self.sats, self.names = A.load_catalog()
        events = json.loads((DATA / "conjunctions.json").read_text())["events"]
        self.event = next((e for e in events if {e["object_a"]["norad_id"], e["object_b"]["norad_id"]} == {a, b}), None)
        if self.event is None:
            raise ValueError(f"pair {a}/{b} not in data/conjunctions.json")
        self.a, self.b, self.who = a, b, who
        self.other = b if who == a else a
        self.tca = A.parse_utc(self.event["tca_utc"])
        self.enc = A.Encounter(self.sats[who], self.sats[self.other], self.tca)
        self.safe_km, self.max_dv = safe_km, max_dv
        self.min_lead_min = min_lead_min
        self.max_lead_min = max_lead_orbits * self.enc.period / 60.0
        self.n_sim = self.n_rescreen = self.n_submit = 0
        self._screens: dict[tuple, dict] = {}

    # ---- validation shared by every tool
    def check(self, lead_min, direction, dv_ms) -> str | None:
        if direction not in A.AXES:
            return f"direction must be one of {list(A.AXES)}"
        if not (self.min_lead_min <= lead_min <= self.max_lead_min):
            return f"lead_min must be between {self.min_lead_min:.0f} and {self.max_lead_min:.0f} minutes"
        if not (0 < dv_ms <= self.max_dv):
            return f"dv_ms must be above 0 and at most {self.max_dv} m/s"
        return None

    def _axis(self, direction, dv_ms):
        return np.array(A.AXES[direction]) * dv_ms

    def miss(self, lead_min, direction, dv_ms):
        return self.enc.miss_after(round(lead_min * 60), self._axis(direction, dv_ms))

    def screen(self, lead_min, direction, dv_ms) -> dict:
        key = (round(lead_min * 60), direction, round(dv_ms, 4))
        if key not in self._screens:
            burn = {"lead_s": key[0], "direction": direction, "dv_ms": dv_ms}
            self._screens[key] = A.rescreen(self.enc, self.sats, self.names, burn, self.safe_km)
        return self._screens[key]

    # ---- tools
    def simulate_burn(self, lead_min, direction, dv_ms) -> dict:
        err = self.check(lead_min, direction, dv_ms)
        if err:
            return {"error": err}
        if self.n_sim >= MAX_SIM:
            return {"error": "simulate budget exhausted; submit your best verified plan"}
        self.n_sim += 1
        miss, t_star, inside = self.miss(lead_min, direction, dv_ms)
        out = {"miss_after_km": round(miss, 3), "nominal_miss_km": round(self.enc.nominal[0], 3),
               "meets_target": bool(inside and miss >= self.safe_km), "target_km": self.safe_km,
               "simulate_calls_left": MAX_SIM - self.n_sim}
        if not inside:
            out["warning"] = "closest approach fell outside the evaluation window; value unreliable"
        return out

    def rescreen_burn(self, lead_min, direction, dv_ms) -> dict:
        err = self.check(lead_min, direction, dv_ms)
        if err:
            return {"error": err}
        if self.n_rescreen >= MAX_RESCREEN:
            return {"error": "rescreen budget exhausted"}
        self.n_rescreen += 1
        s = self.screen(lead_min, direction, dv_ms)
        return {"objects_checked": s["objects_checked"], "ok": not s["new_conjunctions"],
                "new_close_approaches": [{"name": e["name"], "miss_km": e["miss_after_km"]} for e in s["new_conjunctions"]][:5],
                "rescreen_calls_left": MAX_RESCREEN - self.n_rescreen}

    def submit_plan(self, lead_min, direction, dv_ms, rationale="") -> dict:
        """Independent verification: recompute everything; the model's claims are ignored."""
        self.n_submit += 1
        err = self.check(lead_min, direction, dv_ms)
        if err:
            return {"accepted": False, "reason": err}
        miss, t_star, inside = self.miss(lead_min, direction, dv_ms)
        if not (inside and miss >= self.safe_km):
            return {"accepted": False, "reason": f"verified miss distance is {miss:.3f} km, below the {self.safe_km} km target"}
        s = self.screen(lead_min, direction, dv_ms)
        if s["new_conjunctions"]:
            return {"accepted": False, "reason": "creates a new close approach with "
                    + ", ".join(e["name"] for e in s["new_conjunctions"][:3])}
        return {"accepted": True, "verified_miss_km": round(miss, 3), "dv_ms": dv_ms, "lead_min": lead_min, "direction": direction}


# ---------------------------------------------------------------- baselines
def baseline_grid(world: World) -> dict:
    enc = world.enc
    calls = {"n": 0}
    orig = enc.miss_after

    def counted(*a, **k):
        calls["n"] += 1
        return orig(*a, **k)

    enc.miss_after = counted
    try:
        cands = A.search(enc, world.safe_km, world.max_dv, world.min_lead_min * 60, world.max_lead_min * 60 / enc.period)
        best = A.refine_dv(enc, cands[0], world.safe_km) if cands else None
    finally:
        enc.miss_after = orig
    if not best:
        return {"found": False, "sims": calls["n"]}
    return {"found": True, "sims": calls["n"], "dv_ms": round(best["dv_ms"], 4), "direction": best["direction"],
            "lead_min": round(best["lead_s"] / 60, 1), "miss_km": round(best["miss_km"], 3)}


def baseline_random(world: World, budget: int, trials=200, seed=7) -> dict:
    rng = random.Random(seed)
    dirs, found = list(A.AXES), []
    for _ in range(trials):
        best = None
        for _ in range(max(budget, 1)):
            lead = rng.uniform(world.min_lead_min, world.max_lead_min)
            d, dv = rng.choice(dirs), math.exp(rng.uniform(math.log(0.005), math.log(world.max_dv)))
            miss, _, inside = world.miss(lead, d, dv)
            if inside and miss >= world.safe_km and (best is None or dv < best):
                best = dv
        if best is not None:
            found.append(best)
    return {"budget": budget, "trials": trials, "success_rate": round(len(found) / trials, 3),
            "median_dv_ms": round(statistics.median(found), 4) if found else None,
            "best_dv_ms": round(min(found), 4) if found else None}


def baseline_rule(world: World) -> dict:
    lead = world.enc.period / 60.0  # one orbit before TCA
    dv = 0.5
    miss, _, inside = world.miss(lead, "prograde", dv)
    ok = inside and miss >= world.safe_km
    screen = world.screen(lead, "prograde", dv) if ok else None
    return {"dv_ms": dv, "direction": "prograde", "lead_min": round(lead, 1), "miss_km": round(miss, 3),
            "meets_target": bool(ok), "valid": bool(ok and not screen["new_conjunctions"]),
            "wasted_dv_ms": round(dv, 3)}


# ---------------------------------------------------------------- the agent
def propulsion_note(who: int) -> str:
    path = DATA / f"dossier_{who}.json"
    if not path.exists():
        return "No web research on this satellite's propulsion is available."
    d = json.loads(path.read_text()).get("dossier", {})
    parts = []
    for key, label in (("propulsion_type", "propulsion"), ("can_maneuver_now", "can manoeuvre now"), ("mission_status", "status")):
        f = d.get(key) or {}
        if f.get("value") not in (None, ""):
            src = f.get("source_url") or ""
            parts.append(f"{label}: {f['value']} ({f.get('status')}{', ' + src.split('/')[2] if src.startswith('http') else ''})")
    return "Web research (Tavily) on this satellite: " + "; ".join(parts) if parts else "Web research found no propulsion data."


def run_agent(a: int, b: int, who: int, *, emit=_noop, model=MODEL_SUPER, safe_km=2.0, max_dv=1.0, min_lead_min=30.0,
              max_lead_orbits=2.0, log=print) -> dict:
    trace: list[dict] = []

    def ev(kind: str, **data) -> None:
        trace.append({"kind": kind, **data})
        emit(kind, **data)

    t0 = time.time()
    world = World(a, b, who, safe_km, max_dv, min_lead_min, max_lead_orbits)
    enc = world.enc
    ctx = {
        "manoeuvring_satellite": world.names[who], "other_object": world.names[world.other],
        "time_of_closest_approach_utc": world.event["tca_utc"],
        "current_miss_distance_km": round(enc.nominal[0], 3), "relative_speed_km_s": round(enc.rel_speed, 1),
        "orbital_period_min": round(enc.period / 60, 1), "target_miss_distance_km": safe_km,
        "limits": {"max_dv_m_s": max_dv, "lead_min_range": [min_lead_min, round(world.max_lead_min)]},
        "budget": {"simulate_burn": MAX_SIM, "rescreen_burn": MAX_RESCREEN, "submit_plan": MAX_SUBMIT},
        "satellite_info": propulsion_note(who),
    }
    ev("agent_start", model=model, context=ctx)
    log(f"agent: {world.names[who]} vs {world.names[world.other]}, miss {enc.nominal[0] * 1000:.0f} m, target {safe_km} km")

    client = token_factory_client()
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": "Plan the avoidance burn.\n" + json.dumps(ctx, indent=2)}]
    tokens_in = tokens_out = 0
    accepted = None
    nudged = False
    for turn in range(MAX_TURNS):
        resp = chat(client, model=model, messages=messages, tools=TOOLS, tool_choice="auto",
                    temperature=0.2, max_tokens=6000, timeout=180)
        tokens_in += resp.usage.prompt_tokens
        tokens_out += resp.usage.completion_tokens
        msg = resp.choices[0].message
        thought = ((msg.model_extra or {}).get("reasoning_content") or "").strip()
        if thought:
            ev("thought", turn=turn, text=thought[:400])
        calls = msg.tool_calls or []
        messages.append({"role": "assistant", "content": msg.content or "",
                         "tool_calls": [{"id": c.id, "type": "function",
                                         "function": {"name": c.function.name, "arguments": c.function.arguments}} for c in calls]
                         } if calls else {"role": "assistant", "content": msg.content or ""})
        if not calls:
            if accepted or nudged:
                if msg.content:
                    ev("summary", text=msg.content.strip()[:600])
                break
            nudged = True
            messages.append({"role": "user", "content": "Use the tools: evaluate candidates with simulate_burn, then rescreen_burn and submit_plan."})
            continue
        for c in calls:
            name = c.function.name
            try:
                args = json.loads(c.function.arguments or "{}")
                fn = getattr(world, name) if name in ("simulate_burn", "rescreen_burn", "submit_plan") else None
                result = fn(**args) if fn else {"error": f"unknown tool {name}"}
            except TypeError as exc:
                args, result = {}, {"error": f"bad arguments: {exc}"}
            except (ValueError, json.JSONDecodeError) as exc:
                args, result = {}, {"error": f"bad arguments: {exc}"}
            ev("tool_call", turn=turn, id=c.id, name=name, args=args)
            ev("tool_result", turn=turn, id=c.id, name=name, result=result)
            log(f"  {name}({', '.join(f'{k}={v}' for k, v in args.items() if k != 'rationale')}) -> {json.dumps(result)[:140]}")
            messages.append({"role": "tool", "tool_call_id": c.id, "content": json.dumps(result)})
            if name == "submit_plan" and result.get("accepted"):
                accepted = {**args, **result}
        if accepted:
            # let the model write its short summary, but never loop further
            if turn + 1 < MAX_TURNS:
                final = chat(client, model=model, messages=messages, tools=TOOLS, tool_choice="none",
                             temperature=0.2, max_tokens=1500, timeout=120)
                tokens_in += final.usage.prompt_tokens
                tokens_out += final.usage.completion_tokens
                if final.choices[0].message.content:
                    ev("summary", text=final.choices[0].message.content.strip()[:600])
            break
        if world.n_submit >= MAX_SUBMIT:
            break

    seconds = time.time() - t0
    metrics = {"turns": turn + 1, "simulate_calls": world.n_sim, "rescreen_calls": world.n_rescreen, "submits": world.n_submit,
               "tokens_in": tokens_in, "tokens_out": tokens_out,
               "cost_usd": round(tokens_in / 1e6 * PRICE_IN + tokens_out / 1e6 * PRICE_OUT, 4), "seconds": round(seconds, 1)}

    ev("baselines_start")
    grid = baseline_grid(world)
    rand = baseline_random(world, max(world.n_sim, 1))
    rule = baseline_rule(world)
    comparison = {"agent": None, "grid": grid, "random": rand, "rule": rule}
    plan = None
    if accepted:
        burn = {"lead_s": round(accepted["lead_min"] * 60), "direction": accepted["direction"], "dv_ms": float(accepted["dv_ms"])}
        miss, t_star, _ = world.miss(accepted["lead_min"], accepted["direction"], accepted["dv_ms"])
        burn.update(miss_km=miss, tca_shift_s=t_star)
        screen = world.screen(accepted["lead_min"], accepted["direction"], accepted["dv_ms"])
        plan = assemble(world, burn, screen, accepted.get("rationale", ""), model)
        comparison["agent"] = {"valid": True, "dv_ms": round(burn["dv_ms"], 4), "direction": burn["direction"],
                               "lead_min": round(accepted["lead_min"], 1), "miss_km": round(miss, 3),
                               "sims": world.n_sim, **{k: metrics[k] for k in ("turns", "cost_usd", "seconds")}}
        if grid.get("found"):
            comparison["agent"]["dv_vs_grid"] = round(burn["dv_ms"] / grid["dv_ms"], 2)
    else:
        comparison["agent"] = {"valid": False, "sims": world.n_sim, **{k: metrics[k] for k in ("turns", "cost_usd", "seconds")}}
    ev("result", plan=plan, comparison=comparison, metrics=metrics)
    result = {"norad_ids": [a, b], "maneuvering": who, "model": model, "prompt_version": PROMPT_VERSION, "accepted": bool(accepted), "plan": plan,
              "comparison": comparison, "metrics": metrics, "trace": trace}
    save_json(f"agent_{a}_{b}_{who}{os.getenv('AGENT_SUFFIX', '')}.json", result)
    return result


def assemble(world: World, burn: dict, screen: dict, rationale: str, model: str) -> dict:
    """Plan in the same shape as step 5's output, so the web UI can draw it."""
    enc = world.enc
    axis = np.array(A.AXES[burn["direction"]]) * burn["dv_ms"]
    t_burn = world.tca - timedelta(seconds=burn["lead_s"])
    return {
        "source": "nemotron", "norad_ids": [world.a, world.b], "maneuvering": world.who,
        "maneuvering_name": world.names[world.who], "other": world.other, "other_name": world.names[world.other],
        "tca_utc": world.event["tca_utc"],
        "nominal": {"miss_km": round(enc.nominal[0], 4), "rel_speed_kms": round(enc.rel_speed, 2),
                    "step1_miss_km": world.event["miss_distance_km"]},
        "assumptions": {"safe_miss_km": world.safe_km, "max_dv_ms": world.max_dv, "min_lead_min": world.min_lead_min,
                        "impulsive_burn": True, "model": "SGP4 baseline + Clohessy-Wiltshire delta", "planner": model},
        "burn": {"t_burn_utc": A.iso(t_burn), "lead_min": round(burn["lead_s"] / 60, 1),
                 "lead_orbits": round(burn["lead_s"] / enc.period, 2), "direction": burn["direction"],
                 "dv_ms": round(burn["dv_ms"], 4), "dv_lvlh_ms": [round(float(x), 4) for x in axis]},
        "after": {"miss_km": round(burn["miss_km"], 3), "tca_shift_s": round(burn["tca_shift_s"], 3),
                  "gain_km": round(burn["miss_km"] - enc.nominal[0], 3)},
        "rescreen": screen, "validation": A.validate_linear_model(enc, burn), "rejected": [],
        "rationale": rationale,
        "limits": ["Impulsive burn, no execution error.", "TLE accuracy about 1 km; no covariance or collision probability.",
                   "The plan was chosen by the model and independently re-verified by the harness."],
        "series": A.trajectory_series(enc, burn),
    }


def print_comparison(c: dict) -> None:
    ag, gr, rd, ru = c["agent"], c["grid"], c["random"], c["rule"]
    print("\n                      sims   delta-v     result")
    if ag.get("valid"):
        print(f"Nemotron agent      {ag['sims']:6}   {ag['dv_ms']:.3f} m/s  valid ({ag['direction']}, {ag['lead_min']} min before TCA), "
              f"{ag['turns']} turns, ${ag['cost_usd']}, {ag['seconds']}s")
    else:
        print(f"Nemotron agent      {ag['sims']:6}   -          NO VALID PLAN")
    if gr.get("found"):
        print(f"exhaustive grid     {gr['sims']:6}   {gr['dv_ms']:.3f} m/s  reference optimum ({gr['direction']}, {gr['lead_min']} min)")
    print(f"random, same budget {rd['budget']:6}   {rd['median_dv_ms']} m/s median  finds a feasible burn in {rd['success_rate'] * 100:.0f}% of {rd['trials']} trials")
    print(f"fixed rule (0.5 m/s)      1   {ru['dv_ms']:.3f} m/s  {'valid' if ru['valid'] else 'FAILS'} (miss {ru['miss_km']} km, target met: {ru['meets_target']})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--norad", type=int, nargs=2, required=True)
    ap.add_argument("--who", type=int, help="NORAD ID that manoeuvres (default: first)")
    ap.add_argument("--safe-km", type=float, default=2.0)
    ap.add_argument("--max-dv", type=float, default=1.0)
    args = ap.parse_args()
    a, b = args.norad
    res = run_agent(a, b, args.who or a, safe_km=args.safe_km, max_dv=args.max_dv)
    print_comparison(res["comparison"])
    print(f"\nsaved agent_{a}_{b}_{res['maneuvering']}.json")


if __name__ == "__main__":
    main()

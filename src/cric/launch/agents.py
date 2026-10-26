"""The language layer of Mission Studio. Code computes every number; the models read, explain and decide.

    Nano   turns "500 km sun-synchronous, 150 kg cubesat, I live in Seoul" into a target (code finishes it:
           the SSO inclination is computed, not asked for).
    Super  explains a failed or marginal flight from its delta-v table, and casts the go / no-go votes of three
           launch-team roles (weather, range safety, orbital safety) from the facts of one window.
    Ultra  the mission director: reads the votes and the candidate windows and writes the launch brief.

Guards: the director may only pick a window that the code graded go and no voter vetoed; otherwise the code
falls back to the best such window and says so. Advice text never introduces a fix the engine did not verify.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

from ..config import MODEL_NANO, MODEL_SUPER, MODEL_ULTRA
from ..llm import Usage, structured_call
from .geo import sso_inclination


class Target(BaseModel):
    altitude_km: float = Field(description="circular orbit altitude, km")
    kind: Literal["sso", "inclination", "equatorial", "polar", "iss"] = "sso"
    inclination_deg: float | None = Field(None, description="only when the text gives a number")
    payload_kg: float | None = None
    home: str | None = Field(None, description="the user's city or ground station, if mentioned")
    home_lat: float | None = None
    home_lon: float | None = None
    mission: str = Field("", description="one short phrase: what the satellite is for")


def parse_target(text: str, *, usage: Usage | None = None) -> dict:
    msgs = [{"role": "system", "content": "Turn a satellite owner's description into an orbit target. Use only what "
             "the text says; leave the rest null. For a named city give its approximate coordinates."},
            {"role": "user", "content": text[:2000]}]
    t, _ = structured_call(MODEL_NANO, msgs, Target, usage=usage, max_tokens=4000)
    incl = {"sso": sso_inclination(t.altitude_km), "polar": 90.0, "iss": 51.6, "equatorial": 0.0}.get(t.kind)
    out = t.model_dump()
    out["inclination_deg"] = round(t.inclination_deg if t.kind == "inclination" and t.inclination_deg else incl or 0.0, 2)
    out["inclination_source"] = ("computed: sun-synchronous at this altitude" if t.kind == "sso" else
                                 "read" if t.kind == "inclination" else f"standard for {t.kind}")
    return out


class Explanation(BaseModel):
    headline: str = Field(description="one sentence verdict for the owner, plain words")
    why: str = Field(description="2-3 sentences: where the delta-v went, citing the table's numbers")
    fixes: list[str] = Field(description="restate the verified fixes given, best first; add none")


def explain(veh_name: str, result: dict, fixes: list[dict], *, usage: Usage | None = None) -> dict:
    lost = lambda x: x["gravity"] + x["drag"] + x["steering"]
    facts = {
        "outcome": ("reached the target orbit" if result["target_reached"] else "in orbit but short of the target"
                    if result["orbit"] else "did not reach orbit"),
        "perigee_km": result["perigee_km"], "apogee_km": result["apogee_km"], "target_alt_km": result["target_alt_km"],
        "rule": "a perigee below about 150 km re-enters; the target is a circular orbit at target_alt_km",
        "orbital_speed_needed_ms": result.get("orbital_speed_ms"),
        "delta_v_by_stage_ms": [{"stage": x["name"], "ideal": round(x["ideal"]), "lost_to_gravity": round(x["gravity"]),
                                 "lost_to_drag": round(x["drag"]), "lost_to_steering": round(x["steering"]),
                                 "delivered": round(x["ideal"] - lost(x))} for x in result["dv_table"]],
        "total_ideal_ms": round(result["dv_total_ms"]),
        "total_delivered_ms": round(result["dv_total_ms"] - sum(result["losses_ms"].values())),
        "max_q_kpa": result.get("max_q_kpa"),
    }
    msgs = [{"role": "system", "content": "You are a launch vehicle design advisor talking to a first-time rocket "
             "builder. Use only the numbers in the facts. The fixes were each verified by re-flying the simulation; "
             "do not invent others. Explain in terms of where the delta-v went (gravity, drag, steering) and which stage "
             "is short; do not compute new totals."},
            {"role": "user", "content": json.dumps({"vehicle": veh_name, "facts": facts,
                                                    "verified_fixes": [f["text"] for f in fixes]}, default=str)}]
    e, _ = structured_call(MODEL_SUPER, msgs, Explanation, usage=usage, max_tokens=6000)
    allowed = {f["text"] for f in fixes}
    out = e.model_dump()
    out["fixes"] = [f["text"] for f in fixes]          # the engine's list is authoritative
    out["fixes_match"] = all(any(a[:20] in x or x[:20] in a for a in allowed) for x in e.fixes) if e.fixes else True
    return out


ROLES = {   # role -> (description, the window facts it is shown)
    "weather": ("launch weather officer: ground and upper winds, rain, lightning potential", ("weather",)),
    "range": ("range safety officer: impact areas of spent stages and of a failed vehicle, launch corridor", ("range",)),
    "orbital": ("orbital safety officer: collision risk after insertion (LCOLA) and space weather", ("lcola", "kp")),
}


class Vote(BaseModel):
    vote: Literal["go", "no-go"]
    reason: str = Field(description="one sentence, citing the facts' numbers")


class Brief(BaseModel):
    decision: Literal["go", "hold"]
    liftoff_utc: str | None = Field(None, description="the chosen window, copied exactly from candidates; null if hold")
    summary: str = Field(description="3-4 sentences for the satellite owner")
    watch_items: list[str] = Field(description="what could still scrub the launch")


def _vote(role: str, window: dict, usage: Usage | None) -> dict:
    desc, keys = ROLES[role]
    facts = {"liftoff_utc": window["liftoff_utc"], **{k: window.get(k) for k in keys}}
    msgs = [{"role": "system", "content": f"You are the {desc}. Vote go or no-go on this launch window from your "
             "facts only. Thresholds are given in the facts; at or beyond a no-go threshold you must vote no-go."},
            {"role": "user", "content": json.dumps(facts, default=str)}]
    v, _ = structured_call(MODEL_SUPER, msgs, Vote, usage=usage, max_tokens=4000)
    return v.model_dump()


def votes(window: dict, *, usage: Usage | None = None) -> dict:
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(len(ROLES)) as ex:
        futs = {r: ex.submit(_vote, r, window, usage) for r in ROLES}
    return {r: f.result() for r, f in futs.items()}


def director(candidates: list[dict], voted: dict[str, dict], *, usage: Usage | None = None) -> dict:
    """candidates: [{liftoff_utc, status, ...}] graded by code; voted: liftoff_utc -> role votes."""
    msgs = [{"role": "system", "content": "You are the mission director. Pick the launch window for this satellite "
             "from the candidates, using the team's votes. Only a window graded go by the checks and with no no-go "
             "vote may be chosen; if none qualifies, hold."},
            {"role": "user", "content": json.dumps({"candidates": candidates, "votes": voted}, default=str)}]
    b, _ = structured_call(MODEL_ULTRA, msgs, Brief, usage=usage, max_tokens=8000)
    ok = [c["liftoff_utc"] for c in candidates if c["status"] == "go"
          and all(v["vote"] == "go" for v in voted.get(c["liftoff_utc"], {}).values())]
    out = b.model_dump()
    out["guard"] = None
    if out["decision"] == "go" and out["liftoff_utc"] not in ok:
        out["guard"] = (f"director picked {out['liftoff_utc']}, which is not an all-go window; "
                        + (f"using {ok[0]} instead" if ok else "holding"))
        out["liftoff_utc"], out["decision"] = (ok[0], "go") if ok else (None, "hold")
    return out

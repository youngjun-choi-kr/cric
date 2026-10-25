"""Constraints from free text, enforced in code.

The validator can only enforce what is in structured fields, so a limit written only in a mission note ("any burn
above 0.1 m/s needs the ops lead") used to depend on the decision model noticing it. Here a model reads the notes
and returns the constraints that are in force now, each backed by a verbatim quote; code checks every quote and
turns the constraints into fields the validator and the coded policy already understand:

    propulsion "no"           -> our capability can_maneuver = "no"   (validator escalates any burn)
    burn blackout              -> mission.blackouts                     (validator rejects burns inside it)
    max delta-v                -> policy max_dv_ms                      (validator caps)
    allowed axes, command cut-off -> those options are excluded        (validator rejects them)
    other operator will move   -> recorded for the decision and the coded policy

A constraint whose quote is not in the notes is dropped and reported. Notes that lift an earlier restriction
produce no constraint, so reading the text does not only ever make CRIC more cautious.
"""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from ..config import MODEL_SUPER
from ..llm import Usage, structured_call
from ..physics.timeutil import iso, parse_utc


class Quoted(BaseModel):
    quote: str = Field(description="exact words copied from the notes that establish this constraint")


class Window(Quoted):
    start_utc: str = Field(description="ISO 8601 UTC")
    end_utc: str | None = Field(None, description="ISO 8601 UTC; null if 'until further notice'")


class Limit(Quoted):
    value: float


class Axes(Quoted):
    allowed: list[Literal["along_track", "radial", "cross_track"]]


class Flag(Quoted):
    value: bool


class Cutoff(Quoted):
    time_utc: str = Field(description="ISO 8601 UTC after which no command can be uploaded")


class Constraints(BaseModel):
    propulsion_unavailable: Flag | None = Field(None, description="value true only if the notes say thrusters or "
                                                "propulsion cannot be used now")
    burn_blackouts: list[Window] = Field(default_factory=list, description="periods when burns are forbidden and "
                                         "still in force (omit restrictions a later note lifted)")
    max_dv_ms: Limit | None = Field(None, description="the largest burn allowed without further approval, m/s")
    allowed_axes: Axes | None = Field(None, description="only if the notes restrict burn directions")
    no_commands_after: Cutoff | None = None
    other_operator_will_maneuver: Flag | None = None


SYSTEM = """You read a satellite operations team's mission notes and list the constraints on manoeuvres that are
in force now. Notes may be in any language, informal, or use ops jargon (e.g. "prop NO-GO" means propulsion is
unavailable; "dV ops on hold" means no burns).
- Only constraints that affect a collision-avoidance burn tonight. Ignore anything about other days or unrelated
  systems.
- If a later note lifts or cancels an earlier restriction, the restriction is not in force: leave it out.
- Every constraint carries "quote": words copied exactly from the notes, in the notes' language.
- Times: resolve HH:MM against the decision time and the time of closest approach you are given; output ISO 8601
  UTC with a Z. A blackout "until further notice" has end_utc null.
- Along-track means prograde or retrograde.
- If the notes contain no constraint, return empty fields. Do not invent limits."""


def extract(ctx: dict, *, model: str = MODEL_SUPER, usage: Usage | None = None) -> tuple[Constraints, list[str]]:
    """Constraints from ctx['mission']['notes'] (and the time frame), plus problems found by the quote check."""
    notes = ctx["mission"].get("notes") or ""
    if not notes.strip() or notes.strip() == "No mission constraints today.":
        return Constraints(), []
    user = json.dumps({"decision_time_utc": ctx["now_utc"], "closest_approach_utc": ctx["conjunction"]["tca_utc"],
                       "mission_notes": notes}, ensure_ascii=False)
    c, _ = structured_call(model, [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                           Constraints, usage=usage, max_tokens=8000)
    return verify(c, notes)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def verify(c: Constraints, notes: str) -> tuple[Constraints, list[str]]:
    """Drop any constraint whose quote is not in the notes."""
    text, problems = _norm(notes), []
    ok = lambda q: bool(q) and _norm(q) in text
    data = c.model_dump()
    for key in ("propulsion_unavailable", "max_dv_ms", "allowed_axes", "no_commands_after", "other_operator_will_maneuver"):
        if data[key] and not ok(data[key]["quote"]):
            problems.append(f"{key}: quote not in the notes -> {data[key]['quote'][:80]!r}")
            data[key] = None
    kept = []
    for w in data["burn_blackouts"]:
        if ok(w["quote"]):
            kept.append(w)
        else:
            problems.append(f"burn_blackouts: quote not in the notes -> {w['quote'][:80]!r}")
    data["burn_blackouts"] = kept
    return Constraints(**data), problems


AXIS = {"prograde": "along_track", "retrograde": "along_track", "radial": "radial", "anti-radial": "radial",
        "radial_in": "radial", "radial_out": "radial", "normal": "cross_track", "anti-normal": "cross_track",
        "anti_normal": "cross_track", "out_of_plane": "cross_track", "cross_track": "cross_track"}


def apply(ctx: dict, c: Constraints, problems: list[str] | None = None) -> dict:
    """A copy of ctx with the constraints turned into structured fields. Options are never deleted (the validator
    must still recognise them); excluded ones are listed in mission.excluded_options with the reason."""
    out = copy.deepcopy(ctx)
    pol = out["operator_policy"] = dict(out["operator_policy"])
    m = out["mission"]
    tca = parse_utc(out["conjunction"]["tca_utc"])
    if c.propulsion_unavailable and c.propulsion_unavailable.value:
        out["our_satellite"] = {**out["our_satellite"], "capability": {
            "can_maneuver": "no", "basis": f"mission note: \"{c.propulsion_unavailable.quote}\""}}
    m["blackouts"] = list(m.get("blackouts", [])) + [
        {"label": f"from notes: {w.quote[:60]}", "start_utc": w.start_utc,
         "end_utc": w.end_utc or iso(tca + timedelta(days=1))} for w in c.burn_blackouts]
    if c.max_dv_ms and c.max_dv_ms.value > 0:
        pol["max_dv_ms"] = min(pol["max_dv_ms"], c.max_dv_ms.value)
    excluded = []
    for o in out["burn_options"]["options"]:
        if c.allowed_axes and AXIS.get(o.get("direction", ""), "unknown") not in c.allowed_axes.allowed:
            excluded.append({"id": o["id"], "reason": f"direction {o.get('direction')} not allowed: \"{c.allowed_axes.quote}\""})
        elif c.no_commands_after and parse_utc(o["burn_utc"]) >= parse_utc(c.no_commands_after.time_utc) + timedelta(minutes=1):
            # notes give minutes: "after 22:11" leaves a burn at 22:11:13 inside the last commandable minute
            excluded.append({"id": o["id"], "reason": f"after the command cut-off: \"{c.no_commands_after.quote}\""})
    m["excluded_options"] = excluded
    m["extracted_constraints"] = c.model_dump()
    m["extraction_problems"] = problems or []
    return out


def coded_policy(ctx: dict, now: datetime) -> dict:
    """The coded operator policy over the structured fields, including those extracted from text."""
    from ..eval.runner import policy_rule

    if (ctx["mission"].get("extracted_constraints") or {}).get("other_operator_will_maneuver", {}) \
            and ctx["mission"]["extracted_constraints"]["other_operator_will_maneuver"]["value"]:
        return {"action": "MONITOR", "option_id": None, "message_to_other_operator": None}
    bad = {e["id"] for e in ctx["mission"].get("excluded_options", [])}
    trimmed = {**ctx, "burn_options": {**ctx["burn_options"],
                                       "options": [o for o in ctx["burn_options"]["options"] if o["id"] not in bad]}}
    return policy_rule(trimmed, now)

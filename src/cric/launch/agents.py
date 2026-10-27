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
from datetime import timedelta
from typing import Literal

from pydantic import BaseModel, Field

from ..config import MODEL_NANO, MODEL_SUPER, MODEL_ULTRA
from ..physics.timeutil import parse_utc
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
    "weather": ("launch weather officer: ground and upper winds, rain, lightning potential", ("weather", "team_notes")),
    "range": ("range safety officer: impact areas of spent stages and of a failed vehicle, launch corridor, range "
              "closures", ("range", "team_notes")),
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
             "facts only. Thresholds are given in the facts; at or beyond a no-go threshold you must vote no-go. team_notes are "
             "rules from the team's and range's notes with their effect on this window, already decided in code: an "
             "effect of nogo in your area means no-go."},
            {"role": "user", "content": json.dumps(facts, default=str)}]
    v, _ = structured_call(MODEL_SUPER, msgs, Vote, usage=usage, max_tokens=4000)
    return v.model_dump()


def votes(window: dict, *, usage: Usage | None = None) -> dict:
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(len(ROLES)) as ex:
        futs = {r: ex.submit(_vote, r, window, usage) for r in ROLES}
    return {r: f.result() for r, f in futs.items()}


def director(candidates: list[dict], voted: dict[str, dict], *, needs_person: str | None = None,
             usage: Usage | None = None) -> dict:
    """candidates: [{liftoff_utc, status, ...}] graded by code; voted: liftoff_utc -> role votes."""
    if needs_person:                        # no model call: the decision is a person's
        return {"decision": "hold", "liftoff_utc": None, "model_pick": None, "watch_items": [],
                "summary": f"Holding for a person: {needs_person}.", "guard": f"hold for a person: {needs_person}"}
    msgs = [{"role": "system", "content": "You are the mission director. Pick the launch window for this satellite "
             "from the candidates, using the team's votes. Only a window graded go by the checks and with no no-go "
             "vote may be chosen; if none qualifies, hold. Requests in the team notes marked 'for the director' are "
             "yours to weigh among the qualifying windows; with no stated preference, choose the earliest qualifying "
             "window (a slip then still has later windows)."},
            {"role": "user", "content": json.dumps({"candidates": candidates, "votes": voted}, default=str)}]
    b, _ = structured_call(MODEL_ULTRA, msgs, Brief, usage=usage, max_tokens=8000)
    ok = [c["liftoff_utc"] for c in candidates if c["status"] == "go"
          and all(v["vote"] == "go" for v in voted.get(c["liftoff_utc"], {}).values())]
    out = b.model_dump()
    out["guard"] = None
    out["model_pick"] = out["liftoff_utc"] if out["decision"] == "go" else None      # before the guard

    if out["decision"] == "go" and out["liftoff_utc"] not in ok:
        out["guard"] = (f"director picked {out['liftoff_utc']}, which is not an all-go window; "
                        + (f"using {ok[0]} instead" if ok else "holding"))
        out["liftoff_utc"], out["decision"] = (ok[0], "go") if ok else (None, "hold")
    return out


# ---------------------------------------------------------------- team / range notes -> launch rules
class LaunchRule(BaseModel):
    kind: Literal["closure", "daylight_only", "night_only", "max_gust_ms", "max_wind_ms", "max_precip_pct",
                  "not_before", "not_after", "other"]
    start_utc: str | None = Field(None, description="ISO 8601 UTC, e.g. 2026-10-31T23:30:00Z; closures and not_before")
    end_utc: str | None = Field(None, description="ISO 8601 UTC, e.g. 2026-10-31T23:45:00Z; closures and not_after")
    value: float | None = Field(None, description="the limit, in m/s or %, for max_* rules")
    quote: str = Field(description="the exact words of the notes this rule comes from, copied verbatim")
    note: str = Field("", description="one short English sentence")


class LaunchRules(BaseModel):
    rules: list[LaunchRule]


UTC_OFFSET_H = {"naro": 9, "tanegashima": 9, "wenchang": 8, "sriharikota": 5.5, "mahia": 13, "kourou": -3, "cape": -4,
                "vandenberg": -7, "kodiak": -8}


def read_launch_notes(notes: str, site_id: str, site_name: str, day_utc: str, *, window: tuple[str, str] | None = None,
                      usage: Usage | None = None) -> dict:
    """The model reads the notes; code keeps only rules whose quote is really in them and whose values make sense."""
    from ..research.untrusted import sanitize, suspicious

    off = UTC_OFFSET_H.get(site_id, 0)
    notes, removed = sanitize(notes)          # text that addresses the model is cut before it reaches it
    msgs = [{"role": "system", "content": "You read notes from a launch team, a customer and the launch range (any "
             "language) and list the rules they set for the launch time. Times written in local time are converted to "
             f"UTC: the site {site_name} is at UTC{off:+g}. Only rules the notes state; copy the exact words as the quote; "
             "use 'other' for a request that is not one of the kinds. No rule if the notes set none. Kinds: closure = "
             "launches not allowed between two times; daylight_only = only when the sun is up at the site (after "
             "sunrise, before sunset, in daylight); night_only = only in darkness; max_gust_ms / max_wind_ms / "
             "max_precip_pct = a stricter weather limit (value); not_before / not_after = no launch before / after a "
             "time; other = a preference for the mission director (e.g. earliest, most margin)."},
            {"role": "user", "content": json.dumps({"launch_day_utc": day_utc, "candidate_window": _window_text(window, off),
                                                    "notes": notes[:4000]}, ensure_ascii=False)}]
    r, _ = structured_call(MODEL_SUPER, msgs, LaunchRules, usage=usage, max_tokens=6000)
    norm = lambda x: " ".join(x.split()).lower()
    kept, dropped = [], []
    for rule in r.rules:
        d = rule.model_dump()
        why = None
        if rule.kind == "not_after" and not d["end_utc"]:      # the time is the same either way; keep one field
            d["end_utc"], d["start_utc"] = d["start_utc"], None
        if rule.kind == "not_before" and not d["start_utc"]:
            d["start_utc"], d["end_utc"] = d["end_utc"], None
        if not rule.quote.strip() or norm(rule.quote) not in norm(notes):
            why = "quote not found in the notes"
        elif suspicious(rule.quote):
            why = "the quote reads like an instruction to the model"
        try:
            for k in ("start_utc", "end_utc"):
                if d[k]:                      # accept "2026-10-31 23:30 UTC" as well as ISO
                    d[k] = parse_utc(d[k].replace(" UTC", "Z").replace("UTC", "Z").strip().replace(" ", "T")).isoformat().replace("+00:00", "Z")
        except ValueError:
            why = why or "unreadable time"
        if rule.kind == "closure" and d["start_utc"] and d["end_utc"] and d["end_utc"] <= d["start_utc"] and not why:
            end = parse_utc(d["end_utc"]) + timedelta(days=1)     # "23:50-00:10": the end is on the next day
            if (end - parse_utc(d["start_utc"])) < timedelta(hours=12):
                d["end_utc"], d["fixed"] = end.isoformat().replace("+00:00", "Z"), "end moved to the next day"
        if rule.kind == "closure" and not (d["start_utc"] and d["end_utc"] and d["start_utc"] < d["end_utc"]):
            why = why or "closure needs a start before its end"
        if rule.kind.startswith("max_") and not (d["value"] is not None and 0 < d["value"] <= (100 if "pct" in rule.kind else 60)):
            why = why or "limit out of range"
        (dropped if why else kept).append(d | ({"dropped": why} if why else {}))
    # A restrictive rule that could not be read reliably must not be ignored silently: a person decides.
    unsafe = [d for d in dropped if d["kind"] not in ("other",) and "instruction" not in d["dropped"]]
    return {"rules": kept, "dropped": dropped, "removed_from_notes": removed, "needs_person": bool(unsafe),
            "model": MODEL_SUPER}


def _window_text(window: tuple[str, str] | None, off: float) -> str | None:
    if not window:
        return None
    a, b = (parse_utc(x) for x in window)
    loc = lambda t: (t + timedelta(hours=off)).strftime("%Y-%m-%d %H:%M")
    return f"{a:%Y-%m-%d %H:%M}-{b:%H:%M} UTC, which is {loc(a)}-{loc(b)[11:]} local time (UTC{off:+g})"


def apply_rules(rules: list[dict], liftoff_utc: str, site_lat: float, site_lon: float, weather: list | str) -> list[dict]:
    """Effect of each rule on one window, decided in code."""
    from .sun import sun_elevation

    t = parse_utc(liftoff_utc)
    wx = {c["check"]: c["value"] for c in weather} if isinstance(weather, list) else {}
    out = []
    for r in rules:
        k, eff = r["kind"], "ok"
        if k == "closure" and parse_utc(r["start_utc"]) <= t < parse_utc(r["end_utc"]):
            eff = "nogo"
        elif k == "not_before" and r.get("start_utc") and t < parse_utc(r["start_utc"]):
            eff = "nogo"
        elif k == "not_after" and r.get("end_utc") and t > parse_utc(r["end_utc"]):
            eff = "nogo"
        elif k in ("daylight_only", "night_only"):
            up = sun_elevation(t, site_lat, site_lon) > 0
            eff = "nogo" if up != (k == "daylight_only") else "ok"
        elif k in ("max_gust_ms", "max_wind_ms", "max_precip_pct"):
            v = wx.get({"max_gust_ms": "gusts", "max_wind_ms": "ground wind", "max_precip_pct": "rain"}[k])
            eff = "unknown" if v is None else "nogo" if v >= r["value"] else "ok"
        elif k == "other":
            eff = "for the director"
        out.append({"kind": k, "quote": r["quote"], "note": r.get("note", ""), "effect": eff})
    return out

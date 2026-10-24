"""One incident end to end: physics -> evidence -> Nemotron decision -> safety validation."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

from .agent.decision import decide
from .config import DATA, MODEL_SUPER
from .ingest.celestrak import Catalog, load_catalog
from .llm import Usage
from .physics.maneuver import Encounter
from .physics.options import burn_options
from .physics.risk import collision_probability
from .physics.timeutil import iso, julian, parse_utc
from .research.dossier import build_dossier
from .safety.validator import validate

DEFAULT_POLICY = {
    "text": ("Manoeuvre when Pc >= 1e-4 and a verified option exists. Between 1e-5 and 1e-4, or when Pc is diluted, "
             "prefer getting precise data if there is time before the latest safe burn. Never manoeuvre if our "
             "propulsion status is unverified. If the other object is an active satellite that can manoeuvre, "
             "contact its operator before moving. Keep at least 30 minutes for command upload. Max 1.0 m/s per burn."),
    "pc_red": 1e-4,
    "pc_yellow": 1e-5,
    "min_command_upload_min": 30,
    "max_dv_ms": 1.0,
}

INCIDENTS = DATA / "incidents"


def _noop(kind: str, **data) -> None:
    pass


def _cap_summary(d: dict) -> dict:
    """Only what the decision needs from a dossier, with its evidence."""
    doc = d["dossier"]
    keep = ("operator", "mission_type", "has_propulsion", "propulsion_type", "can_maneuver_now", "mission_status",
            "contact_channel")
    facts = {k: {x: doc[k].get(x) for x in ("value", "status", "scope", "source_url", "quote")} for k in keep}
    return {"norad_id": d["norad_id"], "name": doc["name"],
            "satcat": {"type": d["satcat"]["OBJECT_TYPE_LABEL"], "status": d["satcat"]["OPS_STATUS_LABEL"],
                       "owner": d["satcat"].get("OWNER")},
            "capability": d["capability"], "facts": facts, "claim_problems": d["claim_problems"]}


def build_context(cat: Catalog, ours: int, other: int, tca: datetime, now: datetime, *, kp: float,
                  space_weather_note: str, mission: dict, policy: dict, use_tavily: bool,
                  usage: Usage, emit=_noop) -> dict:
    enc = Encounter(cat.sats[ours], cat.sats[other], tca)
    emit("geometry", miss_km=enc.nominal[0], rel_speed_kms=enc.rel_speed)
    jd = sum(julian(tca))
    risk = collision_probability(enc, (cat.epoch_age_days(ours, jd), cat.epoch_age_days(other, jd)), kp=kp)
    emit("risk", pc=risk["pc"], level=risk["level"], diluted=risk["diluted"])

    min_lead = max(policy["min_command_upload_min"], 0)
    hours_left = (tca - now).total_seconds() / 3600
    max_lead_orbits = min(2.0, max((tca - now).total_seconds() - min_lead * 60, 0) / enc.period)
    opts = (burn_options(enc, cat, safe_km=2.0, max_dv=policy["max_dv_ms"], min_lead_min=min_lead,
                         max_lead_orbits=max_lead_orbits, log=lambda m: emit("optimiser", text=m))
            if max_lead_orbits > 0.2 else {"options": [], "rejected": [], "search": {"note": "not enough time before TCA"}})
    emit("options", count=len(opts["options"]))

    dossiers = {}
    for n in (ours, other):
        dossiers[n] = _cap_summary(build_dossier(n, use_tavily=use_tavily, usage=usage, emit=emit))

    return {
        "now_utc": iso(now),
        "conjunction": {"tca_utc": iso(tca), "hours_to_tca": round(hours_left, 2),
                        "miss_km_tle": round(enc.nominal[0], 4), "relative_speed_kms": round(enc.rel_speed, 2),
                        "orbital_period_min": round(enc.period / 60, 1),
                        "data": "public GP/TLE, accuracy about 1 km"},
        "risk": {k: risk[k] for k in ("pc", "pc_max_if_better_data", "diluted", "level", "sigma_rtn_km")}
                | {"assumption": risk["assumptions"]["covariance"], "tle_age_days": risk["assumptions"]["tle_age_days"]},
        "our_satellite": dossiers[ours],
        "other_object": dossiers[other],
        "burn_options": opts,
        "space_weather": {"kp": kp, "note": space_weather_note},
        "operator_policy": policy,
        "mission": mission,
    }


def run_incident(ours: int, other: int, tca_utc: str, *, now_utc: str | None = None, kp: float = 2.0,
                 space_weather_note: str = "Quiet geomagnetic conditions.", mission: dict | None = None,
                 policy: dict | None = None, use_tavily: bool = True, cat: Catalog | None = None,
                 model: str = MODEL_SUPER, emit=_noop) -> dict:
    """Assess one conjunction from OUR satellite's point of view. now_utc lets a recorded
    encounter be replayed as if the decision were made at that moment."""
    t0 = time.time()
    usage = Usage()
    tca = parse_utc(tca_utc)
    now = parse_utc(now_utc) if now_utc else datetime.now(timezone.utc)
    cat = cat or load_catalog(refresh=False)
    mission = mission or {"notes": "No mission constraints today.", "blackouts": []}
    policy = policy or DEFAULT_POLICY

    ctx = build_context(cat, ours, other, tca, now, kp=kp, space_weather_note=space_weather_note, mission=mission,
                        policy=policy, use_tavily=use_tavily, usage=usage, emit=emit)
    emit("decision_start", model=model)
    decision, reasoning = decide(ctx, model=model, usage=usage)
    check = validate(decision.model_dump(), ctx, now)
    emit("decision", action=decision.action, option_id=decision.option_id, final=check["final_action"],
         overrides=check["overrides"])

    result = {"context": ctx, "decision": decision.model_dump(), "reasoning": reasoning, "validation": check,
              "usage": usage.as_dict(), "seconds": round(time.time() - t0, 1), "model": model}
    INCIDENTS.mkdir(exist_ok=True)
    path = INCIDENTS / f"{ours}_{other}_{tca.strftime('%Y%m%dT%H%M%S')}_{now.strftime('%H%M')}.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    result["saved_to"] = str(path)
    return result


def default_now(tca_utc: str, hours_before: float = 3.0) -> str:
    return iso(parse_utc(tca_utc) - timedelta(hours=hours_before))

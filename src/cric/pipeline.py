"""One incident end to end: physics -> evidence -> Nemotron decision -> safety validation."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

from .agent.decision import decide
from .agent.report import write_report
from .config import DATA, FLEET, MODEL_SUPER, MODEL_ULTRA, MODEL_VL
from .ingest.celestrak import Catalog, snapshot_catalog
from .llm import Usage
from .physics.maneuver import Encounter, trajectory_series
from .physics.options import burn_options
from .physics.risk import CDM_GRADE, collision_probability
from .physics.timeutil import iso, julian, parse_utc
from .research.dossier import build_dossier
from .safety.validator import validate

DEFAULT_POLICY = {
    "text": ("Manoeuvre when Pc >= 1e-4 and a verified option exists. Between 1e-5 and 1e-4, or when Pc is diluted, "
             "prefer getting precise data if there is time before the latest safe burn. Never manoeuvre if our "
             "propulsion status is unverified. If the other object is an active satellite that can manoeuvre, "
             "contact its operator before moving; if it is an operational satellite whose capability is unknown, notify "
             "its operator. Keep at least 30 minutes for command upload. Max 1.0 m/s per burn."),
    "pc_red": 1e-4,
    "pc_yellow": 1e-5,
    "min_command_upload_min": 30,
    "max_dv_ms": 1.0,
}

INCIDENTS = DATA / "incidents"
MIN_LEAD_BEFORE_TCA_MIN = 10.0


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
            "capability": d["capability"], "facts": facts, "claim_problems": d["claim_problems"],
            "research": {"tavily": bool(d["tavily"].get("enabled")),
                         "search_calls": d["tavily"].get("search_calls", 0), "extract_calls": d["tavily"].get("extract_calls", 0),
                         "sources": [s["url"] for s in d["tavily"].get("sources", [])][:8],
                         "repairs": len(d.get("repairs", []))}}


def physics_block(cat: Catalog, ours: int, other: int, tca: datetime, now: datetime, *, kp: float, policy: dict,
                  data_quality: str = "tle", emit=_noop) -> dict:
    """Geometry, Pc and verified burn options: everything deterministic about one encounter."""
    enc = Encounter(cat.sats[ours], cat.sats[other], tca)
    emit("geometry", miss_km=enc.nominal[0], rel_speed_kms=enc.rel_speed)
    jd = sum(julian(tca))
    if data_quality == "cdm":
        risk = collision_probability(enc, (0.0, 0.0), kp=kp, model=CDM_GRADE)
        risk["assumptions"]["covariance"] = "SIMULATED precise ephemeris (CDM-grade); not real CDM data"
        # Dilution means "better data could change the answer"; with precise data in hand that no longer applies.
        risk["diluted"] = False
    else:
        risk = collision_probability(enc, (cat.epoch_age_days(ours, jd), cat.epoch_age_days(other, jd)), kp=kp)
    emit("risk", pc=risk["pc"], level=risk["level"], diluted=risk["diluted"])

    # Upload time limits how EARLY relative to now a burn can be; the lead before TCA has its own floor
    # (search() also enforces 0.2 orbit, below which a burn barely moves the satellite).
    upload_s = policy["min_command_upload_min"] * 60
    hours_left = (tca - now).total_seconds() / 3600
    max_lead_orbits = min(2.0, max((tca - now).total_seconds() - upload_s, 0) / enc.period)
    opts = (burn_options(enc, cat, safe_km=2.0, max_dv=policy["max_dv_ms"], min_lead_min=MIN_LEAD_BEFORE_TCA_MIN,
                         max_lead_orbits=max_lead_orbits, log=lambda m: emit("optimiser", text=m))
            if max_lead_orbits > 0.2 else {"options": [], "rejected": [], "search": {"note": "not enough time before TCA"}})
    emit("options", options=opts["options"], rejected=len(opts["rejected"]),
         feasible_points=opts["search"].get("feasible_points"))
    return {
        "now_utc": iso(now),
        "conjunction": {"tca_utc": iso(tca), "hours_to_tca": round(hours_left, 2),
                        "miss_km": round(enc.nominal[0], 4), "relative_speed_kms": round(enc.rel_speed, 2),
                        "orbital_period_min": round(enc.period / 60, 1),
                        "data": ("precise ephemeris (simulated CDM)" if data_quality == "cdm"
                                 else "public GP/TLE, accuracy about 1 km")},
        "risk": {k: risk[k] for k in ("pc", "pc_max_if_better_data", "diluted", "level", "sigma_rtn_km")}
                | {"assumption": risk["assumptions"]["covariance"], "tle_age_days": risk["assumptions"]["tle_age_days"]},
        "burn_options": opts,
    }


def resolve_mission(mission: dict, opts: list[dict], now: datetime) -> dict:
    """Turn a relative mission spec into concrete notes and blackouts once the burn options are known.
    {"blackout_on": "min_dv"} puts a 24-minute imaging window around that option's burn."""
    if "blackout_on" not in mission:
        return {k: v.format(safe_since=iso(now - timedelta(hours=2))[11:16]) if isinstance(v, str) else v
                for k, v in mission.items()}
    opt = next((o for o in opts if mission["blackout_on"] in (o["kind"], *o["also"])), None)
    if opt is None:
        return {"notes": "No mission constraints today.", "blackouts": []}
    b = parse_utc(opt["burn_utc"])
    start, end = b - timedelta(minutes=12), b + timedelta(minutes=12)
    window = {"label": "disaster-response imaging pass", "start_utc": iso(start), "end_utc": iso(end)}
    if mission.get("lang") == "ko":
        notes = (f"{iso(start)[11:16]}~{iso(end)[11:16]} UTC 재난 대응 긴급 촬영이 잡혀 있습니다. "
                 "이 시간에는 추력기 분사가 금지됩니다.")
    else:
        notes = (f"Priority disaster-response imaging pass {iso(start)[11:16]}-{iso(end)[11:16]} UTC. No thruster "
                 "firing in that window; attitude must stay nadir-pointing.")
    # structured=False leaves the window only in the text, where only the model can find it
    return {"notes": notes, "blackouts": [window] if mission.get("structured", True) else []}


def build_context(cat: Catalog, ours: int, other: int, tca: datetime, now: datetime, *, kp: float,
                  space_weather_note: str, mission: dict, policy: dict, use_tavily: bool,
                  usage: Usage, data_quality: str = "tle", emit=_noop) -> dict:
    ctx = physics_block(cat, ours, other, tca, now, kp=kp, policy=policy, data_quality=data_quality, emit=emit)
    mission = resolve_mission(mission, ctx["burn_options"]["options"], now)
    fleet = json.loads(FLEET.read_text()) if FLEET.exists() else {}
    dossiers = {}
    for n, role in ((ours, "ours"), (other, "other")):
        dossiers[n] = _cap_summary(build_dossier(n, use_tavily=use_tavily, usage=usage, emit=emit))
        if role == "ours" and str(n) in fleet:
            # Our own telemetry outranks web research about our satellite. The web facts are shown in the UI
            # but kept out of the decision context, where they contradicted the registry and won.
            reg = fleet[str(n)]
            web = dossiers[n]
            emit("dossier", role=role, dossier=web | {"capability": {"can_maneuver": reg["can_maneuver"], "basis": reg["basis"]},
                                                      "web_capability": web["capability"]})
            dossiers[n] = {k: web[k] for k in ("norad_id", "name", "satcat")} | {
                "capability": {"can_maneuver": reg["can_maneuver"], "basis": reg["basis"]},
                "facts": {"operator": web["facts"].get("operator"), "mission_type": web["facts"].get("mission_type")},
                "claim_problems": []}
            continue
        emit("dossier", role=role, dossier=dossiers[n])
    ctx |= {"our_satellite": dossiers[ours], "other_object": dossiers[other],
            "space_weather": {"kp": kp, "note": space_weather_note}, "operator_policy": policy, "mission": mission}
    return ctx


def run_incident(ours: int, other: int, tca_utc: str, *, now_utc: str | None = None, kp: float = 2.0,
                 space_weather_note: str = "Quiet geomagnetic conditions.", mission: dict | None = None,
                 policy: dict | None = None, use_tavily: bool = True, cat: Catalog | None = None,
                 data_quality: str = "tle", model: str = MODEL_SUPER, tag: str = "custom", with_report: bool = True,
                 live_space_weather: bool = False, emit=_noop) -> dict:
    """Assess one conjunction from OUR satellite's point of view. now_utc lets a recorded
    encounter be replayed as if the decision were made at that moment."""
    t0 = time.time()
    usage = Usage()
    trace: list[dict] = []
    outer_emit = emit

    def emit(kind: str, **data) -> None:  # record every step so the run can be replayed without API calls
        event = {"kind": kind, "t": round(time.time() - t0, 2), **data}
        trace.append(json.loads(json.dumps(event, default=str)))
        outer_emit(kind, **data)

    tca = parse_utc(tca_utc)
    now = parse_utc(now_utc) if now_utc else datetime.now(timezone.utc)
    cat = cat or snapshot_catalog()
    mission = mission or {"notes": "No mission constraints today.", "blackouts": []}
    policy = policy or DEFAULT_POLICY

    sw = None
    if live_space_weather:
        from .agent.perception import assess

        emit("space_weather_start", model=MODEL_VL)
        try:
            sw = assess(now, tca, usage=usage)
            kp, space_weather_note = sw["kp"], sw["note"]
            emit("space_weather", kp=kp, noaa=sw["noaa"], alerts=sw["alerts"], assessment=sw["vl"]["assessment"],
                 image=sw["vl"]["image"], model=sw["vl"]["model"])
        except Exception as exc:  # weather must not block a conjunction decision; fall back to the given Kp
            emit("space_weather_error", message=str(exc)[:300], kp_used=kp)

    ctx = build_context(cat, ours, other, tca, now, kp=kp, space_weather_note=space_weather_note, mission=mission,
                        policy=policy, use_tavily=use_tavily, usage=usage, data_quality=data_quality, emit=emit)
    if sw:
        ctx["space_weather"] |= {"noaa": sw["noaa"], "alerts": sw["alerts"],
                                 "vl_assessment": {k: sw["vl"]["assessment"][k] for k in
                                                   ("leo_drag_risk_72h", "orbit_prediction_impact", "unknowns")}}
    emit("context", now_utc=ctx["now_utc"], conjunction=ctx["conjunction"], risk=ctx["risk"],
         space_weather=ctx["space_weather"], mission=ctx["mission"], policy_text=policy["text"])
    emit("decision_start", model=model)
    decision, reasoning = decide(ctx, model=model, usage=usage)
    emit("decision", decision=decision.model_dump(), reasoning=reasoning[:1500], model=model)
    check = validate(decision.model_dump(), ctx, now)
    emit("validation", **check)

    report = None
    if with_report and check["final_action"] in ("COORDINATE", "ESCALATE"):
        emit("report_start", model=MODEL_ULTRA, for_action=check["final_action"])
        try:
            r = write_report(ctx, decision.model_dump(), check, usage=usage)
            report = r.model_dump() if r else None
            emit("report", report=report, model=MODEL_ULTRA)
        except Exception as exc:  # a missing report must not lose the decision
            emit("report_error", message=str(exc)[:300])

    trajectory = None
    opt = next((o for o in ctx["burn_options"]["options"] if o["id"] == check["final_option_id"]), None)
    if opt:
        enc = Encounter(cat.sats[ours], cat.sats[other], tca)
        trajectory = trajectory_series(enc, opt)
    emit("done", usage=usage.as_dict(), seconds=round(time.time() - t0, 1))

    result = {"pair": {"ours": ours, "other": other, "tca_utc": iso(tca)}, "context": ctx,
              "decision": decision.model_dump(), "reasoning": reasoning, "validation": check,
              "report": report, "trajectory": trajectory, "usage": usage.as_dict(), "seconds": round(time.time() - t0, 1),
              "model": model, "trace": trace}
    INCIDENTS.mkdir(exist_ok=True)
    path = INCIDENTS / f"{ours}_{other}_{tca.strftime('%Y%m%dT%H%M%S')}_{now.strftime('%H%M')}_{tag}.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    result["saved_to"] = str(path)
    return result


def default_now(tca_utc: str, hours_before: float = 3.0) -> str:
    return iso(parse_utc(tca_utc) - timedelta(hours=hours_before))


def preset(name: str, tca_utc: str) -> dict:
    """Demo situations as run_incident keyword arguments; times are resolved against the burn options."""
    if name == "quiet":
        return {"data_quality": "tle", "mission": {"notes": "No mission constraints today.", "blackouts": []}}
    if name == "precise":
        return {"data_quality": "cdm", "mission": {"notes": "Precise ephemeris received from the screening provider.",
                                                    "blackouts": []}}
    if name == "imaging":
        return {"data_quality": "cdm", "mission": {"blackout_on": "min_dv"}}
    if name == "korean_note":
        return {"data_quality": "cdm", "mission": {"blackout_on": "min_dv", "lang": "ko", "structured": False}}
    if name == "storm_2024":
        # The May 2024 G5 storm, researched with Tavily (cric forge), replayed as tonight's weather on TLE data.
        from .research.forge import FORGED, kp_from, weather_note

        rec = next((json.loads(p.read_text())["record"] for p in sorted(FORGED.glob("*2024*.json"))), None)
        if rec is None or kp_from(rec) is None:
            raise RuntimeError("run `cric forge --event \"10-11 May 2024 G5 extreme geomagnetic storm NOAA SWPC\"` first")
        return {"data_quality": "tle", "kp": kp_from(rec), "space_weather_note": weather_note(rec),
                "live_space_weather": False, "mission": {"notes": "No mission constraints today.", "blackouts": []}}
    if name == "safe_mode":
        return {"data_quality": "cdm", "mission": {"notes": (
            "Spacecraft entered safe mode at {safe_since} UTC after a reaction-wheel fault. Thrusters are inhibited "
            "until the anomaly board clears recovery, expected tomorrow."), "blackouts": []}}
    raise ValueError(f"unknown preset {name!r}")


PRESETS = ("quiet", "precise", "imaging", "safe_mode", "storm_2024", "korean_note")

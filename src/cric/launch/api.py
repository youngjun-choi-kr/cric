"""HTTP handlers of Mission Studio (served by web/serve.py under /api/launch/).

Stateless: a mission is {vehicle (preset id or full dict), payload_kg, site, target_alt_km, incl_deg} plus, after
the first flight, the guidance the optimiser found, which later calls send back so they re-fly the same ascent.

    GET  /api/launch/presets      vehicles, sites, model-vs-published validation
    POST /api/launch/simulate     optimise guidance and fly          -> flight
    POST /api/launch/advise       verified fixes (+ Super's explanation when explain=true)
    POST /api/launch/dispersion   Monte Carlo impact areas
    POST /api/launch/sheet        raw image / PDF body -> vehicle form (Nemotron VL / Super)
    POST /api/launch/target       {text} -> orbit target (Nemotron Nano)
    POST /api/launch/windows      {incl_deg} -> site x hour go / no-go heatmap
    POST /api/launch/lcola        {start_utc, minutes} -> liftoff-minute collision screen
    POST /api/launch/gonogo       {candidates: [liftoff_utc]} -> role votes + mission director brief (Super, Ultra)
    POST /api/launch/deploy       {liftoff_utc, name, owner, ...} -> TLE, registry, passes, 72 h screening
"""

from __future__ import annotations

import json
import math
from datetime import timedelta

from ..config import ROOT
from ..llm import Usage
from ..physics.timeutil import iso, parse_utc
from . import ascent, dispersion as disp, windows
from .geo import SITES, azimuths_for, in_corridor, sso_inclination
from .vehicles import PRESETS, from_dict

VALIDATION = ROOT / "fixtures" / "launch" / "vehicle_validation.json"
_CAT = None


def _catalog():
    global _CAT
    if _CAT is None:
        from ..ingest.celestrak import snapshot_catalog

        _CAT = snapshot_catalog()
    return _CAT


class BadRequest(ValueError):
    pass


def _mission(b: dict) -> dict:
    v = b.get("vehicle")
    veh = PRESETS.get(v) if isinstance(v, str) else from_dict(v) if isinstance(v, dict) else None
    if veh is None:
        raise BadRequest(f"vehicle must be one of {list(PRESETS)} or a vehicle object")
    site = SITES.get(b.get("site", ""))
    if site is None:
        raise BadRequest(f"site must be one of {list(SITES)}")
    alt = float(b.get("target_alt_km", 500))
    incl = float(b["incl_deg"]) if b.get("incl_deg") is not None else sso_inclination(alt)
    azs = azimuths_for(site.lat, incl)
    ok = [a for a in azs if in_corridor(site, a)]
    if b.get("az") is not None:
        az, az_note = float(b["az"]), "set by user"
    elif ok:
        az, az_note = ok[0], "from the target inclination, inside the site's corridor"
    else:
        lo, hi = site.corridors[0]
        az = lo if not azs else min((lo, hi), key=lambda c: min(abs(c - a) for a in azs))
        az_note = ("the inclination is below this site's latitude" if not azs else
                   "the azimuth for this inclination would overfly land; flown at the corridor edge instead")
    return {"veh": veh, "payload": float(b.get("payload_kg", veh.payload_kg)), "site": site, "alt": alt,
            "incl": incl, "az": az, "az_note": az_note, "az_ok": bool(ok) or b.get("az") is not None,
            "g": ascent.Guidance(*b["guidance"]) if b.get("guidance") else None}


def _kw(m: dict) -> dict:
    return {"lat": m["site"].lat, "lon": m["site"].lon, "az": m["az"], "target_alt_km": m["alt"]}


def _fly(m: dict, record: bool = False) -> dict:
    if m["g"] is None:
        raise BadRequest("fly the rocket first (simulate) and send its guidance")
    return ascent.simulate(m["veh"], m["payload"], m["g"], record=record, **_kw(m))


def _flight_payload(m: dict, g, r: dict) -> dict:
    r = dict(r)
    s = r.get("samples") or []
    if len(s) > 1500:                                  # the page draws a line; every few seconds is plenty
        step = math.ceil(len(s) / 1500)
        r["samples"] = s[::step] + [s[-1]]
    return {"flight": r, "guidance": [g.h_turn_km, g.phi_end1_deg, g.phi0_deg, g.phi1_deg],
            "azimuth_deg": round(m["az"], 2), "azimuth_note": m["az_note"], "azimuth_ok": m["az_ok"],
            "inclination_target_deg": round(m["incl"], 2), "vehicle": m["veh"].to_dict(),
            "site": vars(m["site"])}


def presets(_q: dict) -> dict:
    val = json.loads(VALIDATION.read_text())["rows"] if VALIDATION.exists() else []
    return {"vehicles": {k: v.to_dict() for k, v in PRESETS.items()},
            "sites": {k: vars(s) for k, s in SITES.items()},
            "validation": {r["vehicle"]: r for r in val}}


def simulate(b: dict) -> dict:
    m = _mission(b)
    g, r = ascent.optimise(m["veh"], m["payload"], parallel=True, **_kw(m))
    return _flight_payload(m, g, r)


def advise(b: dict) -> dict:
    m = _mission(b)
    base = _fly(m)
    fixes = ascent.advise(m["veh"], m["payload"], base, **_kw(m))
    out = {"fixes": fixes}
    if b.get("explain"):
        from .agents import explain

        u = Usage()
        try:
            out["explanation"] = explain(m["veh"].name, base, fixes, usage=u)
        except Exception as exc:                      # the verified fixes stand without the prose
            out["explanation_error"] = str(exc)[:200]
        out["usage"] = u.as_dict()
    return out


def dispersion(b: dict) -> dict:
    m = _mission(b)
    if m["g"] is None:
        raise BadRequest("fly the rocket first")
    runs = max(50, min(int(b.get("runs", 500)), 3000))
    return disp.dispersion(m["veh"], m["payload"], m["g"], runs=runs, **_kw(m))


def sheet(body: bytes, mime: str) -> dict:
    from .specsheet import read_sheet, to_vehicle_dict

    if mime not in ("application/pdf", "image/png", "image/jpeg", "image/webp"):
        raise BadRequest("upload a PNG, JPEG, WebP or PDF")
    u = Usage()
    s = read_sheet(body, mime, usage=u)
    return {"sheet": s.model_dump(), "vehicle": to_vehicle_dict(s), "usage": u.as_dict()}


def target(b: dict) -> dict:
    from .agents import parse_target

    u = Usage()
    return {"target": parse_target(str(b.get("text", "")), usage=u), "usage": u.as_dict()}


def windows_(b: dict) -> dict:
    sites = b.get("sites")
    return windows.heatmap(float(b["incl_deg"]), sites=sites if isinstance(sites, list) else None)


def lcola(b: dict) -> dict:
    m = _mission(b)
    start = windows.parse_start(b.get("start_utc"))
    minutes = max(5, min(int(b.get("minutes", 60)), 180))
    return windows.lcola(_fly(m), start, _catalog(), minutes=minutes)


def _window_facts(m: dict, r: dict, t: str, wx_hours: dict, lc_rows: dict) -> dict:
    h = wx_hours.get(t[:13] + ":00:00Z") or {}
    lc = lc_rows.get(t) or {}
    kp = next((c for c in h.get("checks", []) if c["check"].startswith("space weather")), None)
    return {
        "liftoff_utc": t,
        "status": max((h.get("status", "go"), lc.get("status", "go")), key=["go", "caution", "nogo"].index),
        "weather": [c for c in h.get("checks", []) if not c["check"].startswith("space weather")] or "no forecast",
        "range": {"site": m["site"].name, "azimuth_deg": round(m["az"], 2), "azimuth_inside_corridor": m["az_ok"],
                  "corridor_deg": m["site"].corridors,
                  "stage_impacts": [{k: s.get(k) for k in ("stage", "lat", "lon", "downrange_km")}
                                    for s in r["stage_impacts"] if "lat" in s],
                  "rule": "no-go if the azimuth is outside the corridor (impacts over populated land)"},
        "lcola": {"closest_km": lc.get("closest_km"), "object": lc.get("object"),
                  "minutes_after_insertion": lc.get("minutes_after_insertion"),
                  "caution_km": windows.CRITERIA["lcola_km"][0], "nogo_km": windows.CRITERIA["lcola_km"][1]},
        "kp": {"value": kp["value"], "caution": kp["caution"], "nogo": kp["nogo"]} if kp else "not forecast",
    }


def gonogo(b: dict) -> dict:
    from concurrent.futures import ThreadPoolExecutor

    from .agents import director, votes

    m = _mission(b)
    r = _fly(m)
    cands = [iso(parse_utc(t)) for t in b.get("candidates", [])][:4]
    if not cands:
        raise BadRequest("give 1-4 candidate liftoff times")
    hm = windows.heatmap(m["incl"], sites=[m["site"].id])
    row = hm["rows"][0]
    wx = {h["t"]: h for h in row.get("hours", [])}
    start = min(parse_utc(t) for t in cands)
    span = int((max(parse_utc(t) for t in cands) - start).total_seconds() // 60) + 1
    lc = windows.lcola(r, start, _catalog(), minutes=max(span, 1))
    lc_rows = {x["liftoff_utc"]: x for x in lc.get("rows", [])}
    facts = [_window_facts(m, r, t, wx, lc_rows) for t in cands]
    if not m["az_ok"]:
        for f in facts:
            f["status"] = "nogo"
    u = Usage()
    with ThreadPoolExecutor(len(facts)) as ex:
        voted = dict(zip(cands, ex.map(lambda f: votes(f, usage=u), facts)))
    brief = director([{k: f[k] for k in ("liftoff_utc", "status")} | {"lcola_closest_km": f["lcola"]["closest_km"]}
                      for f in facts], voted, usage=u)
    return {"windows": facts, "votes": voted, "brief": brief, "usage": u.as_dict()}


def deploy(b: dict) -> dict:
    from . import deploy as dep
    from .insertion import passes, tle_for

    m = _mission(b)
    r = _fly(m)
    if not r["orbit"]:
        raise BadRequest("this flight does not reach orbit")
    liftoff = parse_utc(b["liftoff_utc"])
    name = (str(b.get("name") or "MY-SAT").upper()[:24]).strip()
    _, l1, l2 = tle_for(r, liftoff, name)
    home = b.get("home") if isinstance(b.get("home"), dict) else None
    norad = dep.register(name, l1, l2, owner=str(b.get("owner") or "satellite owner")[:80],
                         can_maneuver="yes" if b.get("can_maneuver", True) else "no",
                         mission=str(b.get("mission") or "")[:120], home=home,
                         launch={"vehicle": m["veh"].name, "site": m["site"].name, "liftoff_utc": iso(liftoff),
                                 "perigee_km": r["perigee_km"], "apogee_km": r["apogee_km"],
                                 "inclination_deg": r["inclination_deg"]})
    reg = dep.registry()[str(norad)]
    cat = _catalog()
    dep.add_to_catalog(cat)
    epoch = liftoff + timedelta(seconds=r["insertion"]["t"])
    sat = cat.sats[norad]
    hours = float(b.get("hours", 72))
    events = dep.screen_one(cat, norad, epoch, hours)
    dep.save_encounters(norad, events, epoch, hours, len(cat))
    out = {"norad_id": norad, "name": name, "tle": reg["tle"], "epoch_utc": iso(epoch),
           "objects_in_catalog": len(cat), "screen_hours": hours, "encounters": events[:10]}
    if home and home.get("lat") is not None:
        out["passes"] = passes(sat, float(home["lat"]), float(home["lon"]), epoch, hours=48)[:12]
    return out


GET = {"/api/launch/presets": presets}
POST = {"/api/launch/simulate": simulate, "/api/launch/advise": advise, "/api/launch/dispersion": dispersion,
        "/api/launch/target": target, "/api/launch/windows": windows_, "/api/launch/lcola": lcola,
        "/api/launch/gonogo": gonogo, "/api/launch/deploy": deploy}
PAID = {"/api/launch/sheet", "/api/launch/target", "/api/launch/gonogo"}   # spend API credits


def handle_post(path: str, body: bytes, mime: str) -> tuple[int, dict]:
    try:
        if path == "/api/launch/sheet":
            return 200, sheet(body, mime)
        if path not in POST:
            return 404, {"error": "unknown endpoint"}
        return 200, POST[path](json.loads(body or b"{}"))
    except (BadRequest, KeyError, ValueError, TypeError) as exc:
        return 400, {"error": str(exc) or exc.__class__.__name__}
    except Exception as exc:
        return 500, {"error": f"{exc.__class__.__name__}: {exc}"[:400]}


"""Step 3, mission control: the owner commands a burn, CRIC checks it, a person approves, the log keeps it.

The owner picks a time, a direction and a size. CRIC applies the change of velocity to the satellite's SGP4 state
at that time, fits a new TLE to the result, and screens the new orbit against the catalog for the next 72 h. A
burn that would bring the satellite close to another object, run it out of propellant, or drop it into the
atmosphere is flagged, and CRIC looks for the same burn a few minutes earlier or later that avoids it.

Nothing is sent to a spacecraft: an approved burn updates the satellite's orbit in the Studio registry, and the
command, CRIC's check and the person's decision go to a hash-chained log (data/studio/commands.jsonl).
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
from datetime import datetime, timedelta, timezone

import numpy as np
from sgp4.api import Satrec
from sgp4.exporter import export_tle

from ..physics.timeutil import iso, jdfr, parse_utc
from . import deploy as dep
from .insertion import passes, rv_to_elements, satrec_from_state
from .windows import PC_LIMITS, _pc

G0 = 9.80665
RE_KM = 6378.137
LOG = dep.STUDIO / "commands.jsonl"
PLANS = dep.STUDIO / "plans"
DIRECTIONS = {   # unit vectors in the satellite's own frame: velocity (V), radial (R), orbit normal (N)
    "prograde": ("V", 1), "retrograde": ("V", -1), "radial_out": ("R", 1), "radial_in": ("R", -1),
    "normal": ("N", 1), "antinormal": ("N", -1)}
SAY = {"prograde": "speed up (raises the far side of the orbit)", "retrograde": "slow down (lowers the far side)",
       "radial_out": "push away from Earth", "radial_in": "push toward Earth",
       "normal": "push sideways (tilts the orbit)", "antinormal": "push sideways the other way"}
MIN_PERIGEE_KM = (200.0, 300.0)       # no-go: reenters within days; caution: weeks to months of life left
MISS_KM = (1.0, 2.0)                  # no-go, caution, as in launch screening
SHIFTS_MIN = (5, -5, 10, -10, 15, 20, 30)
_LOCK = threading.Lock()


def propulsion(sat: dict) -> dict:
    """Propellant left and the change of speed it buys (rocket equation)."""
    p = sat.get("propulsion") or {}
    mass, prop, isp = float(p.get("mass_kg", 5.0)), float(p.get("prop_kg", 0.2)), float(p.get("isp_s", 220.0))
    used = float(p.get("prop_used_kg", 0.0))
    left = max(prop - used, 0.0)
    m_now = mass - used
    return {"mass_kg": mass, "prop_kg": prop, "isp_s": isp, "prop_used_kg": round(used, 5), "prop_left_kg": round(left, 5),
            "dv_left_ms": round(isp * G0 * math.log(m_now / (m_now - left)), 2) if left > 0 else 0.0,
            "dv_total_ms": round(isp * G0 * math.log(mass / (mass - prop)), 2) if prop > 0 else 0.0}


def _prop_for(dv_ms: float, m_now: float, isp: float) -> float:
    return m_now * (1 - math.exp(-dv_ms / (isp * G0)))


def _orbit(sat: Satrec, t: datetime) -> dict:
    jd, fr = jdfr(t, np.array([0.0]))
    _, r, v = sat.sgp4(jd[0], fr[0])
    el = rv_to_elements(np.array(r), np.array(v))
    a, e = el["a_km"], el["e"]
    return {"perigee_km": round(float(a * (1 - e) - RE_KM), 1), "apogee_km": round(float(a * (1 + e) - RE_KM), 1),
            "inclination_deg": round(math.degrees(el["i"]), 3), "period_min": round(2 * math.pi * math.sqrt(a ** 3 / 398600.4418) / 60, 2)}


def _apply(sat: Satrec, t: datetime, direction: str, dv_ms: float, norad: int) -> Satrec:
    jd, fr = jdfr(t, np.array([0.0]))
    err, r, v = sat.sgp4(jd[0], fr[0])
    if err:
        raise ValueError("the satellite's orbit cannot be propagated to that time")
    r, v = np.array(r), np.array(v)
    axis, sign = DIRECTIONS[direction]
    u = {"V": v / np.linalg.norm(v), "R": r / np.linalg.norm(r)}.get(axis)
    if u is None:
        u = np.cross(r, v)
        u /= np.linalg.norm(u)
    return satrec_from_state(r, v + sign * u * dv_ms / 1000.0, t, norad)


def _catalog_with(base, norad: int, sat: Satrec):
    cat = type(base)(dict(base.sats), dict(base.names), base.source, base.fetched_at)
    dep.add_to_catalog(cat)
    cat.sats[norad] = sat
    return cat


def _risk(cat, norad: int, events: list[dict]) -> list[dict]:
    """Collision probability at each encounter (assumed uncertainties, as in launch screening) and a level."""
    out = []
    for e in events:
        tca = parse_utc(e["tca_utc"])
        jd, fr = jdfr(tca, np.array([0.0]))
        _, r1, v1 = cat.sats[norad].sgp4(jd[0], fr[0])
        _, r2, v2 = cat.sats[e["object_b"]["norad_id"]].sgp4(jd[0], fr[0])
        pc = float(_pc(np.array(r1), np.array(v1), np.array(r2), np.array(v2)))
        miss = e["miss_distance_km"]
        level = ("no-go" if pc >= PC_LIMITS[0] or miss < MISS_KM[0] else
                 "caution" if pc >= PC_LIMITS[1] or miss < MISS_KM[1] else "ok")
        out.append(e | {"pc": pc, "level": level})
    return out


def _screen(base, norad: int, sat: Satrec, start: datetime, hours: float) -> list[dict]:
    cat = _catalog_with(base, norad, sat)
    return _risk(cat, norad, dep.screen_one(cat, norad, start, hours))


def _verdict(orbit: dict, events: list[dict], prop: dict, dv_ms: float) -> tuple[str, list[str]]:
    why, worst = [], "go"

    def flag(level: str, text: str):
        nonlocal worst
        why.append(text)
        if level == "no-go" or worst == "go":
            worst = level
    if dv_ms > prop["dv_left_ms"]:
        flag("no-go", f"needs {dv_ms:g} m/s, only {prop['dv_left_ms']:g} m/s of propellant left")
    if orbit["perigee_km"] < MIN_PERIGEE_KM[0]:
        flag("no-go", f"lowest point {orbit['perigee_km']:.0f} km: the satellite would reenter within days")
    elif orbit["perigee_km"] < MIN_PERIGEE_KM[1]:
        flag("caution", f"lowest point {orbit['perigee_km']:.0f} km: air drag would bring it down within months")
    for e in events:
        if e["level"] != "ok":
            flag(e["level"], f"{e['object_b']['name']} would pass {e['miss_distance_km'] * 1000:.0f} m away "
                             f"on {e['tca_utc'][5:16].replace('T', ' ')} UTC (collision chance {e['pc']:.0e})")
    return worst, why


def _plan_id(norad: int, tle: list[str], burn_utc: str, direction: str, dv_ms: float) -> str:
    return hashlib.sha256(json.dumps([norad, tle, burn_utc, direction, round(dv_ms, 3)]).encode()).hexdigest()[:16]


def plan(base, norad: int, burn_utc: str, direction: str, dv_ms: float, *, hours: float = 72.0,
         alternatives: bool = True) -> dict:
    """Check one commanded burn. Returns the new orbit, its 72 h screening, a verdict with reasons, and (when the
    burn is not clean) the nearest time shift that is."""
    reg = dep.registry()
    sat_reg = reg.get(str(norad))
    if not sat_reg:
        raise ValueError("unknown satellite")
    if sat_reg.get("can_maneuver") != "yes":
        raise ValueError(f"{sat_reg['name']} has no thruster")
    if direction not in DIRECTIONS:
        raise ValueError(f"direction must be one of {list(DIRECTIONS)}")
    dv_ms = float(dv_ms)
    if not 0 < dv_ms <= 50:
        raise ValueError("burn size must be between 0 and 50 m/s")
    sat = Satrec.twoline2rv(*sat_reg["tle"])
    epoch = dep_epoch(sat)
    t = parse_utc(burn_utc).replace(microsecond=0)
    if t < epoch:
        raise ValueError(f"the burn must come after the satellite's current orbit epoch ({iso(epoch)})")
    if t > epoch + timedelta(days=7):
        raise ValueError("plan burns at most 7 days ahead: the orbit prediction is not good enough beyond that")
    prop = propulsion(sat_reg)
    new = _apply(sat, t, direction, dv_ms, norad)
    before, after = _orbit(sat, t), _orbit(new, t)
    events = _screen(base, norad, new, t, hours)
    base_events = [e for e in dep_encounters(norad) if parse_utc(e["tca_utc"]) >= t]
    verdict, why = _verdict(after, events, prop, dv_ms)
    cleared = [e for e in base_events if e["object_b"]["norad_id"] not in {x["object_b"]["norad_id"] for x in events}]
    l1, l2 = export_tle(new)
    out = {"plan_id": _plan_id(norad, sat_reg["tle"], iso(t), direction, dv_ms), "norad_id": norad,
           "name": sat_reg["name"], "burn_utc": iso(t), "direction": direction, "direction_says": SAY[direction],
           "dv_ms": dv_ms, "before": before, "after": after, "tle_before": sat_reg["tle"], "tle_after": [l1, l2],
           "screen_hours": hours, "encounters": events[:10], "cleared": cleared[:5], "verdict": verdict, "why": why,
           "propulsion": prop, "prop_needed_kg": round(_prop_for(dv_ms, prop["mass_kg"] - prop["prop_used_kg"], prop["isp_s"]), 5),
           "uncertainty": "assumed position uncertainty as in launch screening: ours 1/6/1 km, catalog 0.4/2/0.4 km (R/T/N)"}
    home = sat_reg.get("home") or {}
    if home.get("lat") is not None:
        out["passes"] = passes(new, float(home["lat"]), float(home["lon"]), t, hours=48)[:8]
    if alternatives and verdict != "go" and dv_ms <= prop["dv_left_ms"] and after["perigee_km"] >= MIN_PERIGEE_KM[1]:
        out["alternative"] = _alternative(base, norad, sat, t, direction, dv_ms, events, epoch, hours)
    PLANS.mkdir(parents=True, exist_ok=True)
    (PLANS / f"{out['plan_id']}.json").write_text(json.dumps(out, indent=1))
    return out


def _alternative(base, norad, sat, t, direction, dv_ms, events, epoch, hours) -> dict | None:
    """The same burn a few minutes earlier or later. Each shift is first screened near the problem encounters
    (a couple of hours), and the first that is clean there is screened in full before it is offered."""
    bad = [parse_utc(e["tca_utc"]) for e in events if e["level"] != "ok"]
    for m in SHIFTS_MIN:
        t2 = t + timedelta(minutes=m)
        if t2 < epoch:
            continue
        new = _apply(sat, t2, direction, dv_ms, norad)
        near = []
        for tca in bad:
            start = max(t2, tca - timedelta(hours=1))
            near += _screen(base, norad, new, start, 2.0)
        if any(e["level"] != "ok" for e in near):
            continue
        full = _screen(base, norad, new, t2, hours)
        if all(e["level"] == "ok" for e in full):
            closest = min((e["miss_distance_km"] for e in full), default=None)
            return {"burn_utc": iso(t2), "shift_min": m, "direction": direction, "dv_ms": dv_ms,
                    "after": _orbit(new, t2), "closest_km": closest,
                    "says": f"the same burn {abs(m)} minutes {'later' if m > 0 else 'earlier'}"}
    return None


def dep_epoch(sat: Satrec) -> datetime:
    from sgp4.conveniences import sat_epoch_datetime

    return sat_epoch_datetime(sat).astimezone(timezone.utc).replace(microsecond=0)


def dep_encounters(norad: int) -> list[dict]:
    if not dep.ENCOUNTERS.exists():
        return []
    return [e for e in json.loads(dep.ENCOUNTERS.read_text())["events"] if e["object_a"]["norad_id"] == norad]


# ---- the command log ----

def log_entries() -> list[dict]:
    return [json.loads(x) for x in LOG.read_text().splitlines() if x.strip()] if LOG.exists() else []


def chain_ok(entries: list[dict]) -> bool:
    """Each entry's hash covers its content and the previous hash: an edited or deleted line breaks the chain."""
    prev = "0" * 64
    for e in entries:
        body = {k: v for k, v in e.items() if k != "hash"}
        if e.get("prev_hash") != prev or hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest() != e.get("hash"):
            return False
        prev = e["hash"]
    return True


def _append(entry: dict) -> dict:
    with _LOCK:
        entries = log_entries()
        entry["prev_hash"] = entries[-1]["hash"] if entries else "0" * 64
        entry["hash"] = hashlib.sha256(json.dumps(entry, sort_keys=True).encode()).hexdigest()
        dep.STUDIO.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as f:
            f.write(json.dumps(entry) + "\n")
    return entry


def decide(base, plan_id: str, verdict: str, operator: str, reason: str = "") -> dict:
    """A person approves or rejects a checked burn. Approving updates the Studio's copy of the orbit (nothing is
    sent anywhere). A burn CRIC called no-go can be approved only with a written reason; one that needs more
    propellant than is left cannot be approved at all."""
    if verdict not in ("approve", "reject"):
        raise ValueError("verdict must be approve or reject")
    operator, reason = str(operator).strip()[:80], str(reason).strip()[:500]
    if not operator:
        raise ValueError("say who decides (operator)")
    if not all(c.isalnum() for c in plan_id) or not (PLANS / f"{plan_id}.json").exists():
        raise ValueError("unknown plan: check the burn first")
    p = json.loads((PLANS / f"{plan_id}.json").read_text())
    if verdict == "reject" and not reason:
        raise ValueError("a rejection needs a reason")
    sat_reg = dep.registry().get(str(p["norad_id"]))
    if not sat_reg or sat_reg["tle"] != p["tle_before"]:
        raise ValueError("the orbit changed since this burn was checked: check it again")
    if verdict == "approve":
        if p["dv_ms"] > p["propulsion"]["dv_left_ms"]:
            raise ValueError("not enough propellant for this burn")
        if p["verdict"] == "no-go" and not reason:
            raise ValueError("CRIC called this burn no-go: approving it needs a written reason")
    entry = {"logged_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": "burn",
             "norad_id": p["norad_id"], "satellite": p["name"], "plan_id": plan_id, "burn_utc": p["burn_utc"],
             "direction": p["direction"], "dv_ms": p["dv_ms"], "cric_verdict": p["verdict"], "cric_why": p["why"],
             "verdict": verdict, "operator": operator, "reason": reason, "sent": False}
    if verdict == "approve":
        with dep._LOCK:
            reg = dep.registry()
            s = reg[str(p["norad_id"])]
            s["tle"] = p["tle_after"]
            s.setdefault("propulsion", {})["prop_used_kg"] = round(p["propulsion"]["prop_used_kg"] + p["prop_needed_kg"], 5)
            s.setdefault("burns", []).append({k: p[k] for k in ("plan_id", "burn_utc", "direction", "dv_ms")})
            dep.REGISTRY.write_text(json.dumps(reg, indent=1))
        sat = Satrec.twoline2rv(*p["tle_after"])
        start = parse_utc(p["burn_utc"])
        dep.save_encounters(p["norad_id"], p["encounters"], start, p["screen_hours"], len(base) + len(dep.registry()))
        entry["tle_after"] = p["tle_after"]
        entry["epoch_utc"] = iso(dep_epoch(sat))
    out = _append(entry)
    return out | {"propulsion": propulsion(dep.registry()[str(p["norad_id"])]),
                  "note": "logged; the Studio's orbit is updated, no command was sent to a spacecraft"}

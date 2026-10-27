"""Record one Mission Studio run (the JIMIN-1 story) so the hosted page can replay it with no server and no API.

Calls the local server's /api/launch/* endpoints in the order the page does, with the same request bodies, and
stores every response, plus the CRIC decision stream for the first close approach. The static page
(GitHub Pages) loads web/launch/replay/jimin1.json and answers its API calls from it.

    .venv/bin/python web/serve.py 8765 &
    .venv/bin/python scripts/record_studio_replay.py        # ~3 min, ~$0.05 of API calls
"""

from __future__ import annotations

import copy
import html
import math
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "web" / "launch" / "replay" / "jimin1.json"
URL = "http://localhost:8765"
H = {"Host": "localhost:8765"}
TEXT = "My 5 kg cubesat JIMIN-1 should photograph Seoul, where I live, from a 500 km sun-synchronous orbit."
NOTES = html.unescape(re.search(r'<textarea id="teamNotes"[^>]*>(.*?)</textarea>', (ROOT / "web" / "studio.html").read_text(), re.S).group(1))
LTDN_H, LTDN_TOL_H = 10.5, 0.75          # imaging orbit: equator crossing 10:30 local, as the page defaults
entries: list[dict] = []


def call(path: str, body=None, *, raw: bytes | None = None, mime: str = "application/json", keep: bool = True) -> dict:
    t = time.time()
    r = requests.post(f"{URL}/api/launch/{path}", headers=H | {"Content-Type": mime},
                      data=raw if raw is not None else json.dumps(body), timeout=900)
    if not r.ok:
        raise SystemExit(f"{path}: HTTP {r.status_code} {r.text[:400]}")
    res = r.json()
    if keep:
        entries.append({"path": path, "req": body if raw is None else {"upload": mime}, "res": res,
                        "seconds": round(time.time() - t, 1)})
    print(f"  {path:11} {time.time() - t:5.1f} s", flush=True)
    return res


def main() -> None:
    presets = requests.get(f"{URL}/api/launch/presets", headers=H, timeout=60).json()
    veh = copy.deepcopy(presets["vehicles"]["garage"])
    site, alt = "naro", 500.0
    call("sheet", raw=(ROOT / "web" / "launch" / "garage_one_spec.png").read_bytes(), mime="image/png")
    tgt = call("target", {"text": TEXT})["target"]
    m = {"vehicle": veh, "payload_kg": tgt.get("payload_kg") or 5, "site": site, "target_alt_km": alt,
         "incl_deg": tgt["inclination_deg"]}
    fail = call("simulate", m | {"guidance": None})
    m_fail = m | {"guidance": fail["guidance"]}
    call("dispersion", m_fail | {"runs": 500})
    adv = call("advise", m_fail | {"explain": True})
    fix = next(f for f in adv["fixes"] if f.get("verified") and f["kind"] != "payload")
    v2 = copy.deepcopy(veh)
    st = next(s for s in v2["stages"] if s["name"] == fix["stage"])
    ratio = st["dry_kg"] / (st["dry_kg"] + st["prop_kg"])
    st["prop_kg"] *= fix["factor"]; st["dry_kg"] = st["prop_kg"] * ratio / (1 - ratio)
    if fix["kind"] == "stage+thrust":
        st["thrust_vac_n"] *= fix["factor"]
    st["source"] = {**st["source"], "prop_kg": "edited", "dry_kg": "edited", "thrust_vac_n": "edited"}
    m2 = m | {"vehicle": v2}
    ok = call("simulate", m2 | {"guidance": None})
    m_ok = m2 | {"guidance": ok["guidance"]}
    call("dispersion", m_ok | {"runs": 500})
    heat = call("windows", m_ok)
    # the launch hour: the first upcoming go hour at the site whose minutes show a close call and a clear one
    row = next(r for r in heat["rows"] if r["site"] == site)
    now = datetime.now(timezone.utc).timestamp()
    best = None
    ltdn_ok = lambda k: abs(((row["ltan_h"][k] + 12) % 24 - LTDN_H + 36) % 24 - 12) <= LTDN_TOL_H   # the page's default target
    hours = [h for k, h in enumerate(row["hours"]) if h["status"] == "go" and ltdn_ok(k)
             and datetime.fromisoformat(h["t"].replace("Z", "+00:00")).timestamp() > now + 3 * 3600]
    print(f"  {len(hours)} go hours on the LTDN target", flush=True)
    for h in hours[:8]:
        lc = call("lcola", m_ok | {"start_utc": h["t"], "minutes": 60}, keep=False)
        rows = [x for x in lc["rows"] if x["closest_km"] is not None]
        worst = min(rows, key=lambda x: x["closest_km"])
        clear = max((x["closest_km"] or 0) for x in lc["rows"] if x["status"] == "go")
        score = {"nogo": 100, "caution": 10}.get(worst["status"], 0) + min(clear, 30) - worst["closest_km"]
        if not best or score > best[0]:
            best = (score, h["t"], lc)
    _, hour, lc = best
    entries.append({"path": "lcola", "req": m_ok | {"start_utc": hour, "minutes": 60}, "res": lc, "seconds": 2.5})
    go = sorted((x for x in lc["rows"] if x["status"] == "go"), key=lambda x: -(x["closest_km"] or 1e9))
    worst = min((x for x in lc["rows"] if x["closest_km"] is not None), key=lambda x: x["closest_km"])
    cands = sorted({worst["liftoff_utc"], *[x["liftoff_utc"] for x in go[:2]]})
    board = call("gonogo", m_ok | {"candidates": cands, "notes": NOTES})
    liftoff = board["brief"]["liftoff_utc"]
    print("  board", board["brief"]["decision"], liftoff, board["brief"].get("guard"),
          [(w["liftoff_utc"][11:16], w["status"], [x["effect"] for x in w["team_notes"]]) for w in board["windows"]],
          (board.get("notes") or {}).get("dropped"), flush=True)
    if not liftoff:
        raise SystemExit("the director held; nothing to launch")
    dep = call("deploy", m_ok | {"liftoff_utc": liftoff, "name": "JIMIN-1", "owner": "Jimin", "mission": "photograph Seoul",
                                 "can_maneuver": True, "home": {"name": "Seoul", "lat": 37.5665, "lon": 126.978}})
    for enc in dep["encounters"][:1]:                 # the 3D encounter view and the engine-out what-if
        call("encounter", {"ours": enc["object_a"]["norad_id"], "other": enc["object_b"]["norad_id"], "tca_utc": enc["tca_utc"]})
    call("whatif", m_ok | {"engine_out_t": 60})
    # step 1 for every published preset as the page sets it up (its own site and published orbit),
    # so a visitor can fly any of them in the hosted replay; steps 2-3 stay the JIMIN-1 story
    from cric.launch.geo import sso_inclination

    for vid, v in presets["vehicles"].items():
        if vid == "garage":
            continue
        pub = v["published"]                          # its own site and published orbit, as the page sets it up
        alt_v = float(pub.get("orbit_alt_km") or 500)
        incl_v = round(sso_inclination(alt_v), 2) if "SSO" in pub.get("orbit", "SSO") else pub["incl_deg"]
        val = presets["validation"].get(v["name"])
        pay_v = math.floor(val["model_payload_kg"] * 0.97) if val and val["model_payload_kg"] < v["payload_kg"] else v["payload_kg"]
        mv = {"vehicle": v, "payload_kg": pay_v, "site": pub.get("site", site), "target_alt_km": alt_v,
              "incl_deg": incl_v}
        fv = call("simulate", mv | {"guidance": None})
        mv["guidance"] = fv["guidance"]
        call("dispersion", mv | {"runs": 500})
        call("whatif", mv | {"engine_out_t": 60})
        if not fv["flight"]["target_reached"] or (fv["flight"]["margin_dv_ms"] or 0) < 50:
            call("advise", mv | {"explain": True})
    incidents = {}
    for enc in dep["encounters"][:1]:
        for preset in ("quiet", "precise"):           # public TLE data first, then the provider's ephemeris
            t = time.time()
            ev = []
            with requests.get(f"{URL}/api/incident", headers=H, stream=True, timeout=900,
                              params={"ours": enc["object_a"]["norad_id"], "other": enc["object_b"]["norad_id"], "preset": preset, "mode": "live"}) as r:
                for line in r.iter_lines(decode_unicode=True):
                    if line and line.startswith("data: "):
                        ev.append(json.loads(line[6:]))
            if any(e["kind"] == "error" for e in ev) or not any(e["kind"] == "result" for e in ev):
                raise SystemExit(f"incident {preset} failed: {[e for e in ev if e['kind'] == 'error'][:1]}")
            incidents[f"{enc['object_a']['norad_id']}-{enc['object_b']['norad_id']}-{preset}"] = ev
            print(f"  incident    {preset:8} {time.time() - t:5.1f} s  {len(ev)} events", flush=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"recorded_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "hour": hour,
                               "liftoff_utc": liftoff, "entries": entries, "incidents": incidents},
                              ensure_ascii=False, separators=(",", ":"), default=str))
    print(OUT, f"{OUT.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()

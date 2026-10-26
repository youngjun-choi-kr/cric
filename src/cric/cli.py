"""cric command line.

  cric screen   --hours 6                      screen the active catalog for close approaches
  cric incident --ours 60454 --other 61786     assess one conjunction and let Nemotron decide
  cric eval     --models super,nano            decision scenarios vs rule baselines
  cric forge    --event "May 2024 G5 storm"    real event -> cited record (Tavily + Nemotron)
  cric watch    --every 30 --cycles 0          unattended loop: screen, decide, notify a person (Nebius job)
  cric cdm      FILE.cdm --hbr 20              Pc from a real Conjunction Data Message (CCSDS KVN)
  cric launch-dispersion --vehicle electron --site mahia --runs 5000 --out disp.json
                                               Mission Studio impact dispersion as a batch job (Nebius Serverless Jobs)
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from .config import DATA
from .ingest.celestrak import load_catalog
from .physics.screen import screen
from .physics.timeutil import iso


def _find_event(ours: int, other: int) -> dict:
    from .config import SNAPSHOT

    events = json.loads((SNAPSHOT / "conjunctions.json").read_text())["events"]
    ev = next((e for e in events if {e["object_a"]["norad_id"], e["object_b"]["norad_id"]} == {ours, other}), None)
    if ev is None:
        raise SystemExit(f"pair {ours}/{other} not in data/conjunctions.json; pass --tca or run `cric screen`")
    return ev


def cmd_screen(args) -> None:
    cat = load_catalog(args.group, with_debris=not args.no_debris)
    start = datetime.now(timezone.utc).replace(microsecond=0)
    events = screen(cat, start, args.hours, threshold_km=args.threshold)
    (DATA / "conjunctions.json").write_text(json.dumps(
        {"generated_utc": iso(start), "source": cat.source, "object_count": len(cat), "events": events}, indent=1))
    print(f"{len(cat)} objects, {len(events)} crossing encounters < {args.threshold} km in {args.hours} h")
    ahead = [e for e in events if "STARLINK" not in e["object_a"]["name"] + e["object_b"]["name"]
             and (datetime.fromisoformat(e["tca_utc"].replace("Z", "+00:00")) - start).total_seconds() >= 2 * 3600]
    print(f"{len(ahead)} non-Starlink-internal encounters at least 2 h ahead (decidable in real time)")
    for e in events[:15]:
        print(f"{e['miss_distance_km']:7.3f} km  {e['tca_utc']}  {e['object_a']['name']} <-> {e['object_b']['name']}")


def cmd_incident(args) -> None:
    from .pipeline import default_now, run_incident

    tca = args.tca or _find_event(args.ours, args.other)["tca_utc"]
    now = args.now or (None if args.live else default_now(tca, args.hours_before))
    mission = {"notes": args.mission_notes or "No mission constraints today.", "blackouts": []}

    def emit(kind, **d):
        if kind in ("optimiser",):
            print("  " + d["text"])
        elif kind in ("risk", "options", "dossier_done", "dossier_cached", "decision"):
            print(f"[{kind}] " + json.dumps(d, default=str)[:300])

    res = run_incident(args.ours, args.other, tca, now_utc=now, kp=args.kp, mission=mission,
                       use_tavily=not args.no_tavily, emit=emit)
    d, v = res["decision"], res["validation"]
    print(f"\nDECISION  {d['action']}  option={d['option_id']}  confidence={d['confidence']}")
    print(f"FINAL     {v['final_action']}  option={v['final_option_id']}  overrides={v['overrides']}  warnings={v['warnings']}")
    print(f"WHY       {d['rationale']}")
    for f in d["key_factors"]:
        print(f"  - {f['factor']}: {f['effect']}  [{f['evidence'][:90]}]")
    if d.get("message_to_other_operator"):
        print(f"MESSAGE   {d['message_to_other_operator']}")
    print(f"usage {res['usage']}  {res['seconds']}s  saved {res['saved_to']}")


def cmd_forge(args) -> None:
    from .research.forge import forge, kp_from, weather_note

    out = forge(args.event, refresh=args.refresh)
    rec = out["record"]
    print(f"{rec['title']}  facts {out['fact_counts']}  issues {len(out['issues'])}  Kp {kp_from(rec)}")
    print(weather_note(rec))


def cmd_cdm(args) -> None:
    from .physics.cdm import parse_cdm, pc_from_cdm

    cdm = parse_cdm(args.file)
    res = pc_from_cdm(cdm, hbr_m=args.hbr)
    o1, o2 = cdm["objects"][:2]
    level = "red" if res["pc"] >= 1e-4 else "yellow" if res["pc"] >= 1e-5 else "green"
    print(f"{cdm['originator'] or 'CDM'} TCA {cdm['tca']}  objects {o1['designator']} / {o2['designator']}  "
          f"miss {res['miss_m']:.1f} m")
    print(f"sigma RTN (m): {res['sigma_rtn_m'][0]} / {res['sigma_rtn_m'][1]}")
    print(f"Pc {res['pc']:.3e} ({level}) with hard-body radius {args.hbr} m"
          + (f"; CDM reports {res['pc_reported']:.3e}" if res["pc_reported"] is not None else ""))


def cmd_watch(args) -> None:
    from .watch import watch

    watch(every_min=args.every, cycles=args.cycles, hours=args.hours, threshold_km=args.threshold,
          min_lead_min=args.min_lead, dry_run=args.dry_run, snapshot=args.snapshot)


def cmd_launch_dispersion(args) -> None:
    """Fly the rocket once to find its guidance, then the Monte Carlo on every core of the machine."""
    import time

    from .launch.ascent import optimise
    from .launch.dispersion import dispersion
    from .launch.geo import SITES, azimuths_for, in_corridor, sso_inclination
    from .launch.vehicles import PRESETS, from_dict

    veh = from_dict(json.loads(open(args.vehicle_file).read())) if args.vehicle_file else PRESETS[args.vehicle]
    site = SITES[args.site]
    incl = args.incl if args.incl is not None else sso_inclination(args.alt)
    azs = [a for a in azimuths_for(site.lat, incl) if in_corridor(site, a)]
    if not azs:
        raise SystemExit(f"inclination {incl:.1f} is not reachable over the {site.name} corridor")
    payload = args.payload if args.payload is not None else veh.payload_kg
    kw = {"lat": site.lat, "lon": site.lon, "az": azs[0], "target_alt_km": args.alt}
    t0 = time.time()
    g, r = optimise(veh, payload, parallel=True, **kw)
    d = dispersion(veh, payload, g, runs=args.runs, seed=args.seed, **kw)
    d |= {"vehicle": veh.name, "payload_kg": payload, "site": site.name, "azimuth_deg": azs[0], "target_alt_km": args.alt,
          "inclination_deg": incl, "nominal": {k: r[k] for k in ("orbit", "target_reached", "perigee_km", "apogee_km")},
          "seconds": round(time.time() - t0, 1)}
    out = args.out or str(DATA / f"dispersion_{veh.id}_{site.id}.json")
    with open(out, "w") as f:
        json.dump(d, f)
    print(f"{veh.name} from {site.name}: {args.runs} runs in {d['seconds']} s · P(orbit) {d['p_orbit']:.1%} · "
          f"P(target) {d['p_target']:.1%} -> {out}")


def main() -> None:
    ap = argparse.ArgumentParser(prog="cric", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("screen")
    s.add_argument("--group", default="active")
    s.add_argument("--hours", type=float, default=6.0)
    s.add_argument("--threshold", type=float, default=5.0)
    s.add_argument("--no-debris", action="store_true", help="screen active satellites only")
    s.set_defaults(func=cmd_screen)

    c = sub.add_parser("cdm", help="Pc from a CCSDS Conjunction Data Message")
    c.add_argument("file")
    c.add_argument("--hbr", type=float, default=20.0, help="combined hard-body radius, m")
    c.set_defaults(func=cmd_cdm)

    w = sub.add_parser("watch", help="sense -> decide -> notify loop")
    w.add_argument("--every", type=float, default=30.0, help="minutes between cycles")
    w.add_argument("--cycles", type=int, default=0, help="stop after N cycles (0 = until the job times out)")
    w.add_argument("--hours", type=float, default=24.0, help="screening horizon")
    w.add_argument("--threshold", type=float, default=1.0, help="miss distance (km) that opens an incident")
    w.add_argument("--min-lead", type=float, default=120.0, help="minutes before TCA below which we only alert")
    w.add_argument("--dry-run", action="store_true", help="screen and select only; no model calls")
    w.add_argument("--snapshot", action="store_true", help="use the frozen catalog instead of CelesTrak")
    w.set_defaults(func=cmd_watch)

    i = sub.add_parser("incident")
    i.add_argument("--ours", type=int, required=True, help="NORAD ID of the satellite we operate")
    i.add_argument("--other", type=int, required=True)
    i.add_argument("--tca", help="TCA (UTC ISO); default: from data/conjunctions.json")
    i.add_argument("--now", help="decision time (UTC ISO); default: TCA minus --hours-before")
    i.add_argument("--hours-before", type=float, default=3.0)
    i.add_argument("--live", action="store_true", help="decide at the real current time")
    i.add_argument("--kp", type=float, default=2.0)
    i.add_argument("--mission-notes")
    i.add_argument("--no-tavily", action="store_true")
    i.set_defaults(func=cmd_incident)

    e = sub.add_parser("eval", help="decision scenarios: rule baselines vs Nemotron")
    e.add_argument("--models", default="super", help="comma list: super, nano, super+vote3 (3-vote decision), super+extract / nano+extract (notes -> "
                        "constraints -> coded policy), super+vote3+constraints (the full pipeline)")
    e.add_argument("--repeats", type=int, default=3)
    e.add_argument("--scenarios", help="comma list of scenario ids, e.g. S01,S07")
    e.add_argument("--workers", type=int, default=4)
    e.add_argument("--set", choices=["tuning", "heldout", "heldout2", "heldout3", "forged"], default="tuning")
    e.add_argument("--batch", action="store_true", help="run as one Nebius Token Factory batch job (single model)")
    e.add_argument("--batch-collect", metavar="BATCH_ID", help="grade a finished batch job")
    e.add_argument("--regrade", metavar="FILE", help="re-apply the current validator to a saved run (no model calls)")

    f = sub.add_parser("forge", help="research a real space-weather event with Tavily into a cited record")
    f.add_argument("--event", required=True)
    f.add_argument("--refresh", action="store_true")
    f.set_defaults(func=cmd_forge)
    e.set_defaults(func=lambda a: __import__("cric.eval.runner", fromlist=["main_cli"]).main_cli(a))

    ld = sub.add_parser("launch-dispersion", help="Mission Studio: Monte Carlo impact dispersion as a batch job")
    ld.add_argument("--vehicle", default="electron", help="preset id: nuri, electron, sslv, alpha, garage")
    ld.add_argument("--vehicle-file", help="a vehicle JSON (as the studio form sends it) instead of a preset")
    ld.add_argument("--payload", type=float)
    ld.add_argument("--site", default="mahia")
    ld.add_argument("--alt", type=float, default=500.0, help="target circular altitude, km")
    ld.add_argument("--incl", type=float, help="inclination, deg (default: sun-synchronous)")
    ld.add_argument("--runs", type=int, default=5000)
    ld.add_argument("--seed", type=int, default=0)
    ld.add_argument("--out")
    ld.set_defaults(func=cmd_launch_dispersion)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

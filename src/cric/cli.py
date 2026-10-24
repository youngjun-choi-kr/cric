"""cric command line.

  cric screen   --hours 6                      screen the active catalog for close approaches
  cric incident --ours 40536 --other 42845     assess one conjunction and let Nemotron decide
  cric eval     --models super,nano            decision scenarios vs rule baselines
  cric forge    --event "May 2024 G5 storm"    real event -> cited record (Tavily + Nemotron)
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
    cat = load_catalog(args.group)
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


def main() -> None:
    ap = argparse.ArgumentParser(prog="cric", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("screen")
    s.add_argument("--group", default="active")
    s.add_argument("--hours", type=float, default=6.0)
    s.add_argument("--threshold", type=float, default=5.0)
    s.set_defaults(func=cmd_screen)

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
    e.add_argument("--models", default="super", help="comma list: super,nano")
    e.add_argument("--repeats", type=int, default=3)
    e.add_argument("--scenarios", help="comma list of scenario ids, e.g. S01,S07")
    e.add_argument("--workers", type=int, default=4)
    e.add_argument("--set", choices=["tuning", "heldout", "forged"], default="tuning")

    f = sub.add_parser("forge", help="research a real space-weather event with Tavily into a cited record")
    f.add_argument("--event", required=True)
    f.add_argument("--refresh", action="store_true")
    f.set_defaults(func=cmd_forge)
    e.set_defaults(func=lambda a: __import__("cric.eval.runner", fromlist=["main_cli"]).main_cli(a))

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

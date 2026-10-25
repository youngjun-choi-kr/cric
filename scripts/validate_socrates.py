"""Check CRIC's screening against CelesTrak SOCRATES, an independent public conjunction list.

SOCRATES (celestrak.org/SOCRATES) screens the whole public catalog with SGP4 every few hours. Its run of
2026-10-24 15:00 UTC starts five minutes before our frozen snapshot, so both see nearly the same orbit data.
For every SOCRATES encounter inside our window and filters (24 h, < 2 km, crossing speed >= 1 km/s, both objects
in our catalog, not Starlink-to-Starlink), we ask: did CRIC find the same pair at its closest pass, how far apart are the times of
closest approach, and how different are the miss distances?

    .venv/bin/python scripts/validate_socrates.py            # uses the committed subset
    .venv/bin/python scripts/validate_socrates.py --csv sort-minRange.csv   # rebuild the subset from a full download

Writes fixtures/snapshot_20261025/socrates_validation.json.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from datetime import timedelta
from pathlib import Path

from cric.config import SNAPSHOT
from cric.ingest.celestrak import snapshot_catalog
from cric.physics.timeutil import parse_utc

SUBSET = SNAPSHOT / "socrates_20261024T1500_subset.csv"
OUT = SNAPSHOT / "socrates_validation.json"
MATCH_S = 120


def _utc(s: str) -> str:
    return s.replace(" ", "T") + "Z"


def build_subset(full_csv: Path, start, end) -> list[dict]:
    keep = []
    with full_csv.open() as f:
        for r in csv.DictReader(f):
            t = parse_utc(_utc(r["TCA"]))
            if start <= t < end and float(r["TCA_RANGE"]) < 2.0:
                keep.append(r)
    with SUBSET.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(keep[0]))
        w.writeheader()
        w.writerows(keep)
    return keep


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", type=Path, help="full SOCRATES sort-minRange.csv download")
    a = ap.parse_args()
    ours = json.loads((SNAPSHOT / "conjunctions.json").read_text())
    start = parse_utc(ours["generated_utc"])
    end = start + timedelta(hours=24)
    rows = build_subset(a.csv, start, end) if a.csv else list(csv.DictReader(SUBSET.open()))
    cat = snapshot_catalog()

    found = {}
    for e in ours["events"]:
        found.setdefault(frozenset((e["object_a"]["norad_id"], e["object_b"]["norad_id"])), []).append(e)

    excluded = {"not_in_catalog": 0, "slow": 0, "starlink_pair": 0}
    by_pair: dict[frozenset, list[dict]] = {}
    for r in rows:
        a_id, b_id = int(r["NORAD_CAT_ID_1"]), int(r["NORAD_CAT_ID_2"])
        if a_id not in cat.sats or b_id not in cat.sats:
            excluded["not_in_catalog"] += 1
        elif float(r["TCA_RELATIVE_SPEED"]) < 1.0:
            excluded["slow"] += 1
        elif r["OBJECT_NAME_1"].startswith("STARLINK") and r["OBJECT_NAME_2"].startswith("STARLINK"):
            excluded["starlink_pair"] += 1
        else:
            by_pair.setdefault(frozenset((a_id, b_id)), []).append(r)

    # Our list keeps the closest pass of each pair in the window; SOCRATES lists every pass. Compare pair by pair,
    # each pair's closest SOCRATES pass against ours.
    analyst = lambda r: "UNKNOWN" in r["OBJECT_NAME_1"] + r["OBJECT_NAME_2"]   # CelesTrak "analyst" objects
    dt, dmiss, missed, outliers = [], [], [], []
    for k, rs in by_pair.items():
        r = min(rs, key=lambda x: float(x["TCA_RANGE"]))
        hit = next((e for e in found.get(k, []) if abs((parse_utc(e["tca_utc"]) - parse_utc(_utc(r["TCA"]))).total_seconds())
                    <= MATCH_S), None)
        if not hit:
            missed.append({"pair": [r["OBJECT_NAME_1"], r["OBJECT_NAME_2"]], "tca": r["TCA"], "range_km": float(r["TCA_RANGE"]),
                           "analyst_object": analyst(r)})
            continue
        dt.append(abs((parse_utc(hit["tca_utc"]) - parse_utc(_utc(r["TCA"]))).total_seconds()))
        diff = abs(hit["miss_distance_km"] - float(r["TCA_RANGE"])) * 1000
        dmiss.append(diff)
        if diff > 50:
            outliers.append({"pair": [r["OBJECT_NAME_1"], r["OBJECT_NAME_2"]], "socrates_km": float(r["TCA_RANGE"]),
                             "cric_km": hit["miss_distance_km"], "analyst_object": analyst(r)})

    socrates_pairs = set(by_pair)
    only_ours = [e for k, es in found.items() if k not in socrates_pairs for e in es if parse_utc(e["tca_utc"]) < end]
    deb = {int(line[2:7]) for line in (SNAPSHOT / "celestrak_debris.tle").open() if line.startswith("1 ")}
    family = lambda name: name.split("-")[0].split(" ")[0]
    kinds = {"debris_vs_debris": 0, "same_constellation": 0, "other": 0}
    for k in {frozenset((e["object_a"]["norad_id"], e["object_b"]["norad_id"])): e for e in only_ours}.values():
        a_id, b_id = k["object_a"]["norad_id"], k["object_b"]["norad_id"]
        kinds["debris_vs_debris" if a_id in deb and b_id in deb else
              "same_constellation" if family(k["object_a"]["name"]) == family(k["object_b"]["name"]) else "other"] += 1
    q = lambda xs, p: round(statistics.quantiles(xs, n=100)[p - 1], 3) if len(xs) > 2 else None
    n = len(by_pair)
    res = {
        "socrates_run_utc": "2026-10-24T15:00Z", "our_snapshot_utc": ours["generated_utc"], "window_h": 24,
        "filters": "range < 2 km, relative speed >= 1 km/s, both objects in our catalog, not Starlink-Starlink",
        "socrates_passes_in_window": len(rows), "excluded_passes": excluded, "socrates_pairs": n,
        "pairs_found_by_cric": n - len(missed), "pair_recall": round((n - len(missed)) / n, 4) if n else None,
        "missed_pairs_involving_analyst_objects": sum(m["analyst_object"] for m in missed),
        "tca_difference_s": {"median": q(dt, 50), "p95": q(dt, 95), "max": round(max(dt), 3) if dt else None},
        "miss_difference_m": {"median": q(dmiss, 50), "p95": q(dmiss, 95), "max": round(max(dmiss), 1) if dmiss else None},
        "miss_outliers_over_50m": len(outliers), "outliers_involving_analyst_objects": sum(o["analyst_object"] for o in outliers),
        "pairs_only_in_cric": len({frozenset((e["object_a"]["norad_id"], e["object_b"]["norad_id"])) for e in only_ours}),
        "pairs_only_in_cric_by_kind": kinds,
        "missed_examples": missed[:15], "outlier_examples": outliers[:10],
        "only_in_cric_examples": [{"pair": [e["object_a"]["name"], e["object_b"]["name"]], "tca": e["tca_utc"],
                                   "miss_km": e["miss_distance_km"]} for e in only_ours[:10]],
    }
    OUT.write_text(json.dumps(res, indent=1))
    print(json.dumps({k: v for k, v in res.items() if not k.endswith("examples")}, indent=1))
    for m in missed[:8]:
        print("  missed:", m)
    for e in res["only_in_cric_examples"][:5]:
        print("  only CRIC:", e)


if __name__ == "__main__":
    main()

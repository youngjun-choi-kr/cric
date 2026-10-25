"""Does the Sun image add anything to NOAA's Kp? A retrospective test of Nemotron 3.5 Super VL.

Cases are days in 2023-2026 (solar maximum) when the previous 24 h were calm (Kp <= 4), so the current Kp
says "low" by construction:
  storm-eve days: Kp >= 7 followed within 72 h (first day of each storm, storms at least a week apart);
  quiet days:     Kp stayed <= 3 for the next 72 h (random sample, fixed seed).
For each day the model sees only that day's SDO AIA 193 image (no NOAA numbers) and rates LEO drag risk for the
next 72 h. A current-Kp rule calls every one of these days "low", so any storm-eve day the image read flags is
warning the numbers did not give; any quiet day it flags is a false alarm.

    .venv/bin/python scripts/eval_vl.py [--storms 10 --quiet 10]

Kp: GFZ Potsdam (kp.gfz.de). Images: NASA SDO browse archive. Writes results/vl_eval.json.
"""

from __future__ import annotations

import argparse
import base64
import json
import random
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from cric.agent.perception import SYSTEM, SpaceWeatherAssessment
from cric.config import MODEL_VL
from cric.llm import Usage, structured_call

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "vl_eval.json"
ORDER = ["low", "moderate", "high", "severe"]


def kp_series(start: str, end: str) -> list[tuple[datetime, float]]:
    r = requests.get("https://kp.gfz.de/app/json/", params={"start": start, "end": end, "index": "Kp"}, timeout=120)
    r.raise_for_status()
    d = r.json()
    return [(datetime.fromisoformat(t.replace("Z", "+00:00")), k) for t, k in zip(d["datetime"], d["Kp"])]


def pick_days(kp, n_storm: int, n_quiet: int, seed: int = 0):
    days, storms, quiet = sorted({t.date() for t, _ in kp}), [], []
    for day in days:
        t = datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc)
        before = [k for s, k in kp if t - timedelta(hours=24) <= s < t]
        after = [k for s, k in kp if t <= s < t + timedelta(hours=72)]
        if len(before) < 8 or len(after) < 24 or max(before) > 4:
            continue
        if max(after) >= 7 and (not storms or t - storms[-1][0] > timedelta(days=7)):
            storms.append((t, max(before), max(after)))
        elif max(after) <= 3:
            quiet.append((t, max(before), max(after)))
    random.Random(seed).shuffle(quiet)
    return storms[:n_storm], sorted(quiet[:n_quiet])


def image_for(t: datetime) -> tuple[str, bytes]:
    base = f"https://sdo.gsfc.nasa.gov/assets/img/browse/{t:%Y/%m/%d}/"
    names = re.findall(r'href="(\d{8}_\d{6}_512_0193\.jpg)"', requests.get(base, timeout=60).text)
    if not names:
        raise LookupError(f"no AIA 193 image on {t:%Y-%m-%d}")
    name = min(names, key=lambda n: abs(int(n[9:11]) * 60 + int(n[11:13]) - (t.hour * 60 + t.minute)))
    return base + name, requests.get(base + name, timeout=60).content


def read(t: datetime, usage: Usage) -> dict:
    url, img = image_for(t)
    messages = [{"role": "system", "content": SYSTEM + "\nNo NOAA numbers are provided for this test: judge only from "
                 "the image."},
                {"role": "user", "content": [{"type": "text", "text": f"SDO AIA 193 image, {t:%Y-%m-%d %H:%M} UTC."},
                                             {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,"
                                                                                  + base64.b64encode(img).decode()}}]}]
    a, _ = structured_call(MODEL_VL, messages, SpaceWeatherAssessment, usage=usage)
    return {"image": url, "leo_drag_risk_72h": a.leo_drag_risk_72h, "reasoning": a.risk_reasoning[:300]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--storms", type=int, default=10)
    ap.add_argument("--quiet", type=int, default=10)
    a = ap.parse_args()
    kp = kp_series("2023-01-01T00:00:00Z", "2026-09-30T23:59:59Z")
    storms, quiet = pick_days(kp, a.storms, a.quiet)
    cases = [("storm_eve", *s) for s in storms] + [("quiet", *q) for q in quiet]
    usage = Usage()

    def one(c):
        kind, t, before, after = c
        try:
            r = read(t, usage)
        except Exception as exc:  # noqa: BLE001 - a missing image is a result, not a crash
            r = {"error": str(exc)[:200], "leo_drag_risk_72h": "unknown"}
        return {"kind": kind, "day_utc": t.isoformat(), "kp_max_prev_24h": before, "kp_max_next_72h": after, **r}

    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(one, cases))
    level = lambda r: ORDER.index(r["leo_drag_risk_72h"]) if r["leo_drag_risk_72h"] in ORDER else -1
    st = [r for r in rows if r["kind"] == "storm_eve" and level(r) >= 0]
    qu = [r for r in rows if r["kind"] == "quiet" and level(r) >= 0]
    summary = {
        "storm_eve_days": len(st), "quiet_days": len(qu),
        "storm_eve_flagged_moderate_or_more": sum(level(r) >= 1 for r in st),
        "storm_eve_flagged_high_or_more": sum(level(r) >= 2 for r in st),
        "quiet_flagged_moderate_or_more": sum(level(r) >= 1 for r in qu),
        "quiet_flagged_high_or_more": sum(level(r) >= 2 for r in qu),
        "current_kp_rule": "flags none of these days (previous 24 h Kp <= 4 by selection)",
        "unreadable": sum(level(r) < 0 for r in rows), "cost_usd": round(usage.cost_usd, 4), "model": MODEL_VL,
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({"summary": summary, "rows": rows}, indent=1))
    print(json.dumps(summary, indent=1))
    for r in rows:
        print(f"  {r['kind']:9} {r['day_utc'][:10]} Kp {r['kp_max_prev_24h']:.1f} -> {r['kp_max_next_72h']:.1f}  "
              f"VL {r['leo_drag_risk_72h']}{'  ' + r['error'] if 'error' in r else ''}")


if __name__ == "__main__":
    main()

"""Step 3: ask Nemotron Super VL to read a solar image plus space-weather data and pick an ops action.

Live mode (default): latest NASA SDO AIA 193 image + NOAA SWPC Kp index and alerts.
Historical mode (--date): SDO AIA 193 image for that moment via Helioviewer,
optionally with context from the Step 2 scenario file.

The image alone cannot forecast a storm; the point is to test whether the VL
model separates what it sees (observed) from what it infers, and returns a
structured action from a fixed list that a safety checker can validate.
"""

from __future__ import annotations

import argparse
import base64
import json
import time
from typing import Literal

import requests
from pydantic import BaseModel, ValidationError

from common import DATA, MODEL_VL, save_json, structured_call, token_factory_client, usage_line

SDO_LATEST = "https://sdo.gsfc.nasa.gov/assets/img/latest/latest_512_0193.jpg"
HELIOVIEWER = (
    "https://api.helioviewer.org/v2/takeScreenshot/?date={date}&imageScale=4.8"
    "&layers=%5BSDO,AIA,AIA,193,1,100%5D&x0=0&y0=0&width=512&height=512&display=true"
)
KP_URL = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"
ALERTS_URL = "https://services.swpc.noaa.gov/products/alerts.json"

Action = Literal[
    "NO_ACTION",
    "MONITOR",
    "INCREASE_CONJUNCTION_SCREENING",
    "DRAG_MINIMIZING_ATTITUDE",
    "DELAY_LAUNCH_OR_ORBIT_RAISING",
    "SAFE_MODE",
]


class Observation(BaseModel):
    feature: str
    status: Literal["observed", "inferred", "unknown"]
    detail: str


class Assessment(BaseModel):
    observations: list[Observation]
    leo_drag_risk_72h: Literal["low", "moderate", "high", "severe", "unknown"]
    risk_reasoning: str
    recommended_action: Action
    action_targets: str
    confidence: float
    unknowns: list[str]


SYSTEM = """You are a space-weather analyst supporting a small LEO satellite operator.
You get one EUV image of the Sun (SDO AIA 193 angstrom) and, when available, NOAA data.
Rules:
- Label each observation "observed" (visible in the image or stated in the data),
  "inferred" (your reasoning), or "unknown".
- A single image cannot confirm an Earth-directed CME; say so instead of guessing.
- recommended_action must be exactly one of: NO_ACTION, MONITOR, INCREASE_CONJUNCTION_SCREENING,
  DRAG_MINIMIZING_ATTITUDE, DELAY_LAUNCH_OR_ORBIT_RAISING, SAFE_MODE.
- Output one JSON object with keys: observations, leo_drag_risk_72h, risk_reasoning,
  recommended_action, action_targets, confidence (0-1), unknowns."""


def fetch(url: str) -> bytes:
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return resp.content


def live_context() -> str:
    kp = requests.get(KP_URL, timeout=30).json()[-8:]  # last 24h, 3-hourly
    alerts = requests.get(ALERTS_URL, timeout=30).json()[:3]
    kp_lines = ", ".join(f"{k['time_tag'][5:16]} Kp={k['Kp']}" for k in kp)
    alert_lines = "\n".join(f"- {a['issue_datetime']}: {a['message'][:400]}" for a in alerts)
    return f"NOAA planetary Kp, last 24h: {kp_lines}\nLatest NOAA SWPC alerts:\n{alert_lines}"


def scenario_context(path: str) -> str:
    s = json.loads(open(path).read())["scenario"]
    return f"Operator briefing from scenario file: {s['title']}. {s['event_summary']}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", help="historical UTC time, e.g. 2022-02-03T12:00:00Z")
    ap.add_argument("--scenario", help="Step 2 output to use as context, e.g. ../data/scenario_geomagnetic_2022.json")
    args = ap.parse_args()

    if args.date:
        image, mime = fetch(HELIOVIEWER.format(date=args.date)), "image/png"
        context = f"Image time: {args.date}. Live NOAA data not available for this date."
        if args.scenario:
            context += "\n" + scenario_context(args.scenario)
        tag = args.date[:10]
    else:
        image, mime = fetch(SDO_LATEST), "image/jpeg"
        context = live_context()
        tag = "live"
    (DATA / f"sun_{tag}.{mime.split('/')[1]}").write_bytes(image)
    print(f"image: {len(image) // 1024} KB ({tag})\ncontext:\n{context}\n")

    data_url = f"data:{mime};base64,{base64.b64encode(image).decode()}"
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": [
            {"type": "text", "text": context},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]},
    ]
    t0 = time.time()
    try:
        assessment, raw, reasoning, resp = structured_call(token_factory_client(), MODEL_VL, messages, Assessment)
    except (ValueError, ValidationError) as exc:
        raise SystemExit(f"[fail] {exc}")
    print(f"Nemotron VL ({MODEL_VL}) {usage_line(resp)}, {time.time() - t0:.1f}s, reasoning {len(reasoning)} chars\n")

    print(json.dumps(assessment.model_dump(), indent=2, ensure_ascii=False))
    out = save_json(f"solar_assessment_{tag}.json", {
        "context": context, "assessment": assessment.model_dump(), "reasoning": reasoning, "model": MODEL_VL,
    })
    print(f"\nsaved {out.name}")


if __name__ == "__main__":
    main()

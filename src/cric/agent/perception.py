"""T2: Nemotron Super VL reads the Sun and NOAA data. Context, not physics.

What reaches the physics is the Kp value from NOAA (observed and forecast), never a number the model guessed from
a picture. The VL read has two jobs:
1. explain the conditions in plain language for the operator (active regions, coronal holes, what one image
   cannot show), with every observation labelled observed / inferred / unknown;
2. act as a second opinion, in principle. A "high" or "severe" image read against a quiet Kp is flagged and the
   decision should name a recheck time; it can never change a Pc or approve a burn. In practice the retrospective
   test (scripts/eval_vl.py, results/vl_eval.json) found no forecasting signal: the model rated all 20 images
   "moderate", including the eves of ten G3-G5 storms, so the flag has not fired on any tested day.
Cached for an hour per image.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from datetime import datetime
from typing import Literal

import requests
from pydantic import BaseModel

from ..config import DATA, MODEL_VL
from ..ingest.swpc import kp_summary, recent_alerts
from ..llm import Usage, structured_call

SDO_LATEST = "https://sdo.gsfc.nasa.gov/assets/img/latest/latest_512_0193.jpg"
CACHE = DATA / "space_weather"
MAX_AGE_S = 3600


class Observation(BaseModel):
    feature: str
    status: Literal["observed", "inferred", "unknown"]
    detail: str


class SpaceWeatherAssessment(BaseModel):
    observations: list[Observation]
    leo_drag_risk_72h: Literal["low", "moderate", "high", "severe", "unknown"]
    risk_reasoning: str
    orbit_prediction_impact: str
    confidence: float
    unknowns: list[str]


SYSTEM = """You are a space-weather analyst supporting LEO collision avoidance.
You get one SDO AIA 193 angstrom image of the Sun plus NOAA Kp (observed and forecast) and alerts.
- Label each observation "observed" (visible in the image or stated in NOAA data), "inferred", or "unknown".
- One image cannot confirm an Earth-directed CME; say so instead of guessing.
- orbit_prediction_impact: how this affects the reliability of LEO orbit predictions (drag) over the next days.
Return one JSON object."""


LEVELS = ("low", "moderate", "high", "severe")


def noaa_level(kp: float) -> str:
    """NOAA G-scale bands mapped to the VL vocabulary: Kp<5 quiet/unsettled, 5-6 G1-G2, 7 G3, 8-9 G4-G5."""
    return "low" if kp < 4 else "moderate" if kp < 6 else "high" if kp < 8 else "severe"


def cross_check(vl_level: str, kp: float) -> dict | None:
    """None when the image read agrees with NOAA (or is unknown); otherwise what to do about it."""
    if vl_level not in LEVELS:
        return None
    n = noaa_level(kp)
    gap = LEVELS.index(vl_level) - LEVELS.index(n)
    if gap == 0:
        return None
    if gap > 0 and vl_level in ("high", "severe"):
        # Only a strong image read counts: in a retrospective test (scripts/eval_vl.py) the model rated every
        # image "moderate", storm-eve and quiet days alike, so a moderate-vs-low gap carries no signal.
        return {"vl": vl_level, "noaa": n, "direction": "vl_higher",
                "effect": "physics keeps NOAA Kp; set a recheck before the latest burn and after the next NOAA forecast"}
    if gap > 0:
        return None
    return {"vl": vl_level, "noaa": n, "direction": "vl_lower", "effect": "physics keeps NOAA Kp (the more cautious input)"}


def assess(now: datetime, until: datetime, *, usage: Usage | None = None, vl: bool = False) -> dict:
    """Space weather block for an incident: NOAA numbers, plus (optionally) the VL read of the Sun as an
    explanation for the operator. Off by default: it showed no forecasting signal (scripts/eval_vl.py)."""
    kp = kp_summary(now, until)
    alerts = recent_alerts()
    effective = max([k for k in (kp["kp_observed_max_24h"], kp["kp_forecast_max_to_tca"]) if k is not None], default=2.0)
    if not vl:
        note = (f"NOAA Kp max {kp['kp_observed_max_24h']} observed (24 h), {kp['kp_forecast_max_to_tca']} forecast "
                "to TCA; storm drag from NASA MSIS.")
        return {"kp": effective, "note": note, "noaa": kp, "alerts": alerts, "vl": None, "source": "live",
                "vl_role": "not used", "vl_noaa_disagreement": None}

    CACHE.mkdir(parents=True, exist_ok=True)
    image = requests.get(SDO_LATEST, timeout=60).content
    key = hashlib.sha256(image).hexdigest()[:16]
    path = CACHE / f"vl_{key}.json"
    if path.exists() and time.time() - path.stat().st_mtime < MAX_AGE_S:
        vl = json.loads(path.read_text())
    else:
        text = (f"NOAA Kp: observed max last 24 h = {kp['kp_observed_max_24h']}, forecast max until "
                f"{until:%Y-%m-%d %H:%M} UTC = {kp['kp_forecast_max_to_tca']}, NOAA scales: {kp['noaa_scales'] or 'none'}\n"
                "Latest alerts:\n" + "\n".join(f"- {a['issued_utc']}: {a['text']}" for a in alerts))
        messages = [{"role": "system", "content": SYSTEM},
                    {"role": "user", "content": [
                        {"type": "text", "text": text},
                        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(image).decode()}}]}]
        t0 = time.time()
        assessment, _ = structured_call(MODEL_VL, messages, SpaceWeatherAssessment, usage=usage)
        vl = {"assessment": assessment.model_dump(), "image": SDO_LATEST, "model": MODEL_VL,
              "seconds": round(time.time() - t0, 1)}
        path.write_text(json.dumps(vl))

    a = vl["assessment"]
    note = (f"NOAA Kp max {kp['kp_observed_max_24h']} observed (24 h), {kp['kp_forecast_max_to_tca']} forecast to TCA. "
            f"VL read of the Sun: LEO drag risk {a['leo_drag_risk_72h']}. {a['orbit_prediction_impact']}")
    disagreement = cross_check(a["leo_drag_risk_72h"], effective)
    if disagreement:
        note += (f" Image read ({disagreement['vl']}) differs from NOAA ({disagreement['noaa']}); "
                 f"{disagreement['effect']}.")
    return {"kp": effective, "note": note, "noaa": kp, "alerts": alerts, "vl": vl, "source": "live",
            "vl_role": "context only: Pc uses NOAA Kp", "vl_noaa_disagreement": disagreement}

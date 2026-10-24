"""T2: Nemotron Super VL reads the Sun and NOAA data; the result feeds the incident.

What reaches the physics is the Kp value from NOAA (observed and forecast), never a
number the model guessed from a picture. The VL assessment adds the qualitative read
(active regions, coronal holes, what cannot be known from one image) that the
decision agent and the operator see. Cached for an hour per image.
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


def assess(now: datetime, until: datetime, *, usage: Usage | None = None) -> dict:
    """Space weather block for an incident: NOAA numbers + VL assessment (cached)."""
    kp = kp_summary(now, until)
    alerts = recent_alerts()
    effective = max([k for k in (kp["kp_observed_max_24h"], kp["kp_forecast_max_to_tca"]) if k is not None], default=2.0)

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
    return {"kp": effective, "note": note, "noaa": kp, "alerts": alerts, "vl": vl, "source": "live"}

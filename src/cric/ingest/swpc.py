"""NOAA SWPC space weather: planetary Kp (observed + forecast) and recent alerts. Cached for 15 minutes."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import requests

from ..config import DATA

KP_FORECAST_URL = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index-forecast.json"
ALERTS_URL = "https://services.swpc.noaa.gov/products/alerts.json"
CACHE = DATA / "space_weather"
MAX_AGE_S = 15 * 60


def _get(url: str, name: str):
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / name
    if path.exists() and time.time() - path.stat().st_mtime < MAX_AGE_S:
        return json.loads(path.read_text())
    data = requests.get(url, timeout=30).json()
    path.write_text(json.dumps(data))
    return data


def _utc(tag: str) -> datetime:
    return datetime.fromisoformat(tag).replace(tzinfo=timezone.utc)


def kp_summary(now: datetime, until: datetime) -> dict:
    """Max observed Kp over the last 24 h and max forecast Kp from now until `until` (e.g. TCA)."""
    rows = _get(KP_FORECAST_URL, "kp_forecast.json")
    obs = [r["kp"] for r in rows if r.get("observed") in ("observed", "estimated")
           and now - timedelta(hours=24) <= _utc(r["time_tag"]) <= now]
    pred = [r["kp"] for r in rows if r.get("observed") == "predicted"
            and now - timedelta(hours=3) <= _utc(r["time_tag"]) <= until + timedelta(hours=3)]  # 3-hourly bins
    scales = sorted({r["noaa_scale"] for r in rows if r.get("noaa_scale") and _utc(r["time_tag"]) >= now - timedelta(hours=24)})
    return {"kp_observed_max_24h": max(obs) if obs else None,
            "kp_forecast_max_to_tca": max(pred) if pred else None,
            "noaa_scales": scales, "source": "NOAA SWPC planetary K-index forecast"}


def recent_alerts(n: int = 3) -> list[dict]:
    return [{"issued_utc": a["issue_datetime"], "text": " ".join(a["message"].split())[:400]}
            for a in _get(ALERTS_URL, "alerts.json")[:n]]

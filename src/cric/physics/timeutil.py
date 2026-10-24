from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
from sgp4.api import jday


def parse_utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def julian(t: datetime) -> tuple[float, float]:
    return jday(t.year, t.month, t.day, t.hour, t.minute, t.second + t.microsecond / 1e6)


def jdfr(base: datetime, offsets_s: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Julian date arrays for base + offsets (seconds), without per-sample datetime work."""
    jd0, fr0 = julian(base)
    offsets_s = np.asarray(offsets_s, dtype=float)
    return np.full(offsets_s.shape, jd0), fr0 + offsets_s / 86400.0

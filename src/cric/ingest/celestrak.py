"""CelesTrak GP orbits and SATCAT records, cached on disk.

CelesTrak asks clients not to download the same GP group more often than every
two hours, so the TLE file is reused while it is fresh.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import requests
from sgp4.api import Satrec

from ..config import DATA

GP_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP={group}&FORMAT=tle"
SATCAT_URL = "https://celestrak.org/satcat/records.php?CATNR={norad}&FORMAT=json"
GP_MAX_AGE_S = 2 * 3600
SATCAT_MAX_AGE_S = 7 * 86400

OBJECT_TYPES = {"PAY": "payload", "R/B": "rocket body", "DEB": "debris", "UNK": "unknown"}
OPS_STATUS = {"+": "operational", "-": "nonoperational", "P": "partially operational", "B": "backup",
              "S": "spare", "X": "extended mission", "D": "decayed", "?": "unknown"}


@dataclass
class Catalog:
    sats: dict[int, Satrec]
    names: dict[int, str]
    source: str
    fetched_at: float

    def __len__(self) -> int:
        return len(self.sats)

    def epoch_age_days(self, norad: int, jd_now: float) -> float:
        s = self.sats[norad]
        return jd_now - (s.jdsatepoch + s.jdsatepochF)


def _tle_path(group: str) -> Path:
    return DATA / f"celestrak_{group}.tle"


def load_catalog(group: str = "active", *, refresh: bool = True, path: Path | None = None) -> Catalog:
    """GP catalog from the live cache (refreshed when older than 2 h) or from an explicit file (never refreshed)."""
    if path is not None:
        refresh = False
    path = path or _tle_path(group)
    stale = not path.exists() or time.time() - path.stat().st_mtime > GP_MAX_AGE_S
    if stale and refresh:
        resp = requests.get(GP_URL.format(group=group), timeout=60)
        resp.raise_for_status()
        path.write_text(resp.text)
    if not path.exists():
        raise FileNotFoundError(f"{path} missing and refresh disabled")
    lines = [ln.rstrip() for ln in path.read_text().splitlines() if ln.strip()]
    sats, names = {}, {}
    for i in range(0, len(lines) - 2, 3):
        rec = Satrec.twoline2rv(lines[i + 1], lines[i + 2])
        sats[int(rec.satnum)] = rec
        names[int(rec.satnum)] = lines[i].strip()
    return Catalog(sats, names, f"CelesTrak GP {path.parent.name}/{path.name}", path.stat().st_mtime)


def satcat(norad: int) -> dict:
    """SATCAT record with readable labels; cached for a week."""
    cache = DATA / "satcat" / f"{norad}.json"
    cache.parent.mkdir(exist_ok=True)
    if cache.exists() and time.time() - cache.stat().st_mtime < SATCAT_MAX_AGE_S:
        row = json.loads(cache.read_text())
    else:
        rows = requests.get(SATCAT_URL.format(norad=norad), timeout=30).json()
        if not rows:
            raise LookupError(f"NORAD {norad} not found in SATCAT")
        row = rows[0]
        cache.write_text(json.dumps(row))
    row["OBJECT_TYPE_LABEL"] = OBJECT_TYPES.get(row.get("OBJECT_TYPE"), row.get("OBJECT_TYPE"))
    row["OPS_STATUS_LABEL"] = OPS_STATUS.get(row.get("OPS_STATUS_CODE") or "?", "unknown")
    return row


def snapshot_catalog() -> Catalog:
    from ..config import SNAPSHOT

    return load_catalog(path=SNAPSHOT / "celestrak_active.tle")

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
# Public debris CelesTrak distributes: the large breakup clouds, plus uncatalogued "analyst" objects.
# The full debris catalog needs Space-Track access and cannot be redistributed (see LIMITATIONS.md).
DEBRIS_GROUPS = ("fengyun-1c-debris", "cosmos-2251-debris", "iridium-33-debris", "cosmos-1408-debris", "analyst")
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


def fetch_group(group: str, *, refresh: bool = True) -> Path:
    path = _tle_path(group)
    stale = not path.exists() or time.time() - path.stat().st_mtime > GP_MAX_AGE_S
    if stale and refresh:
        resp = requests.get(GP_URL.format(group=group), timeout=90)
        if resp.status_code == 403 and path.exists():
            # CelesTrak answers 403 when this address already holds the current data (it updates every 2 h)
            path.touch()
        else:
            resp.raise_for_status()
            path.write_text(resp.text)
    if not path.exists():
        raise FileNotFoundError(f"{path} missing and refresh disabled")
    return path


def _parse(paths: list[Path]) -> tuple[dict, dict]:
    sats, names = {}, {}
    for path in paths:
        lines = [ln.rstrip() for ln in path.read_text().splitlines() if ln.strip()]
        for i in range(0, len(lines) - 2, 3):
            rec = Satrec.twoline2rv(lines[i + 1], lines[i + 2])
            sats.setdefault(int(rec.satnum), rec)   # first file wins if an object appears twice
            names.setdefault(int(rec.satnum), lines[i].strip())
    return sats, names


def load_catalog(group: str = "active", *, refresh: bool = True, path: Path | None = None,
                 with_debris: bool = False) -> Catalog:
    """GP catalog from the live cache (refreshed when older than 2 h) or from explicit files (never refreshed).
    with_debris adds the public debris groups to the active satellites."""
    if path is not None:
        paths = [path]
    else:
        paths = [fetch_group(group, refresh=refresh)]
        if with_debris:
            paths += [fetch_group(g, refresh=refresh) for g in DEBRIS_GROUPS]
    sats, names = _parse(paths)
    label = " + ".join(p.stem for p in paths)
    return Catalog(sats, names, f"CelesTrak GP {label}", max(p.stat().st_mtime for p in paths))


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
    """Frozen catalog for eval and demo: active satellites plus public debris when the snapshot has it."""
    from ..config import SNAPSHOT

    paths = [SNAPSHOT / "celestrak_active.tle"]
    if (SNAPSHOT / "celestrak_debris.tle").exists():
        paths.append(SNAPSHOT / "celestrak_debris.tle")
    sats, names = _parse(paths)
    return Catalog(sats, names, "CelesTrak GP snapshot " + " + ".join(p.stem for p in paths),
                   max(p.stat().st_mtime for p in paths))

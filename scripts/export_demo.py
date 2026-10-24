"""Copy recorded incidents and eval summaries into web/demo/ so the console runs as a static site.

    .venv/bin/python scripts/export_demo.py

The static console (no server) replays these traces in the browser and reads orbits from the
committed snapshot in fixtures/. Nothing here calls an API.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))
from serve import _brief, _incident_files, _result_event, eval_sets  # noqa: E402

from cric.config import SNAPSHOT  # noqa: E402

DEMO = ROOT / "web" / "demo"


def main() -> None:
    (DEMO / "incidents").mkdir(parents=True, exist_ok=True)
    for old in (DEMO / "incidents").glob("*.json"):
        old.unlink()
    # Only pairs that exist in the frozen snapshot: the globe draws them from that file.
    pairs = {(e["object_a"]["norad_id"], e["object_b"]["norad_id"])
             for e in json.loads((SNAPSHOT / "conjunctions.json").read_text())["events"]}
    manifest, seen = [], set()
    for path in _incident_files():  # newest first: keep the latest recording per pair and preset
        b = _brief(path)
        key = (b["ours"], b["other"], b["preset"])
        if not b["has_trace"] or not b["preset"] or key in seen or (b["ours"], b["other"]) not in pairs:
            continue
        seen.add(key)
        d = json.loads(path.read_text())
        (DEMO / "incidents" / path.name).write_text(json.dumps({"trace": d["trace"], "result": _result_event(d)},
                                                              ensure_ascii=False, default=str))
        manifest.append(b)
    (DEMO / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False))
    (DEMO / "eval.json").write_text(json.dumps(eval_sets(), ensure_ascii=False, default=str))
    print(f"exported {len(manifest)} incidents and {len(eval_sets()['sets'])} eval sets to {DEMO}")


if __name__ == "__main__":
    main()

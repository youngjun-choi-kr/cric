"""CRIC console server (local).

Static: web/ plus read-only data files at the top level of data/ (*.json, *.tle).
API:
  GET /api/incident?ours=A&other=B&preset=quiet|precise|imaging|safe_mode&mode=live|replay[&file=NAME]
      Server-sent events for one incident: physics -> Pc -> burn options -> dossiers -> Nemotron decision ->
      safety validator, then a final "result" event. live runs the cric pipeline (Token Factory + Tavily);
      replay streams the trace saved by an earlier live run and makes no API calls.
  GET /api/incidents   saved incidents (newest first)
  GET /api/eval        latest decision-eval summary

Bound to localhost; live runs are refused for cross-site requests because they spend API credits.

    .venv/bin/python web/serve.py [port]
"""

from __future__ import annotations

import functools
import http.server
import json
import pathlib
import re
import sys
import threading
from urllib.parse import parse_qs, urlparse

from cric.config import DATA, SNAPSHOT
from datetime import datetime, timedelta, timezone

from cric.physics.timeutil import iso, parse_utc
from cric.pipeline import INCIDENTS, PRESETS, default_now, preset, run_incident

ACTIONABLE_H = 2.0  # a live decision needs at least this much time before TCA

ROOT = pathlib.Path(__file__).resolve().parent
DATA = DATA.resolve()
ALLOWED_SUFFIXES = {".json", ".tle"}
PORT = 8765
LIVE_LOCK = threading.Lock()  # one paid run at a time


def _find_tca(ours: int, other: int) -> str:
    events = json.loads((SNAPSHOT / "conjunctions.json").read_text())["events"]
    ev = next((e for e in events if {e["object_a"]["norad_id"], e["object_b"]["norad_id"]} == {ours, other}), None)
    if ev is None:
        raise LookupError(f"pair {ours}/{other} is not in data/conjunctions.json")
    return ev["tca_utc"]


def _incident_files() -> list[pathlib.Path]:
    return sorted(INCIDENTS.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True) if INCIDENTS.exists() else []


def _brief(path: pathlib.Path) -> dict:
    d = json.loads(path.read_text())
    pair = d.get("pair") or {}
    return {"file": path.name, "ours": pair.get("ours"), "other": pair.get("other"), "tca_utc": pair.get("tca_utc"),
            "preset": next((n for n in PRESETS if path.stem.endswith("_" + n)), None), "action": d["decision"]["action"],
            "final": d["validation"]["final_action"], "has_trace": bool(d.get("trace")),
            "names": [d["context"]["our_satellite"]["name"], d["context"]["other_object"]["name"]]}


def eval_sets() -> dict:
    """Latest run of each eval set, summary only."""
    out = {}
    folder = DATA / "eval"
    for name in ("heldout", "heldout2", "tuning", "forged"):
        # eval_<name>_<digits>: "heldout_*" alone would also match heldout2 files
        files = sorted((f for f in folder.glob(f"eval_{name}_*.json") if re.fullmatch(rf"eval_{name}_\d+_\d+", f.stem)),
                       key=lambda p: p.stat().st_mtime, reverse=True) if folder.exists() else []
        files = [f for f in files if not f.stem.endswith("revalidated")]
        if files:
            d = json.loads(files[0].read_text())
            out[name] = {"file": files[0].name, "summary": d.get("summary_revalidated") or d["summary"],
                         "scenarios": [{k: sc[k] for k in ("id", "title", "signal", "expected")} for sc in d["scenarios"]]}
    return {"sets": out}


def _result_event(d: dict, file: str | None = None) -> dict:
    ctx = d["context"]
    return {"kind": "result", "pair": d.get("pair"), "decision": d["decision"], "validation": d["validation"],
            "trajectory": d.get("trajectory"), "report": d.get("report"), "usage": d["usage"], "seconds": d["seconds"], "model": d["model"],
            "options": ctx["burn_options"]["options"], "risk": ctx["risk"], "conjunction": ctx["conjunction"],
            "ours": ctx["our_satellite"], "other": ctx["other_object"], "mission": ctx["mission"],
            "space_weather": ctx.get("space_weather"), "now_utc": ctx.get("now_utc"),
            "upload_min": ctx["operator_policy"]["min_command_upload_min"], "file": file or (d.get("saved_to") or "").split("/")[-1] or None}


class Handler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        clean = path.split("?", 1)[0].split("#", 1)[0]
        if clean.startswith("/data/"):
            # Orbits and the close-approach list come from the frozen snapshot, so the globe matches the eval.
            target = (SNAPSHOT.resolve() / clean[len("/data/"):]).resolve()
            if target.parent == SNAPSHOT.resolve() and target.suffix in ALLOWED_SUFFIXES:
                return str(target)
            return str(ROOT / "__forbidden__")
        return super().translate_path(path)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args) -> None:
        if "/api/" in str(args[0] if args else "") or "code" in fmt:
            super().log_message(fmt, *args)

    def do_HEAD(self) -> None:
        if urlparse(self.path).path.startswith("/api/"):  # lets the page detect that the API is here
            self.send_response(200)
            self.end_headers()
            return
        super().do_HEAD()

    def do_POST(self) -> None:
        """POST /api/frame?shot=NAME&i=N with a PNG body: one cinema-mode frame, saved to data/frames/NAME/.
        POST /api/approval with JSON: the operator's approve / reject, appended to data/approvals.jsonl."""
        url = urlparse(self.path)
        if url.path == "/api/approval":
            return self._approval()
        if url.path != "/api/frame":
            return self.send_error(404)
        if not self._local_request():
            return self.send_error(403, "local requests only")
        q = parse_qs(url.query)
        shot, i = q.get("shot", [""])[0], q.get("i", [""])[0]
        if not re.fullmatch(r"[a-z0-9-]{1,40}", shot) or not i.isdigit():
            return self.send_error(400, "need shot=[a-z0-9-] and i=<int>")
        size = int(self.headers.get("Content-Length", 0))
        if not 0 < size < 60_000_000:
            return self.send_error(413)
        body = self.rfile.read(size)
        if body[:8] != b"\x89PNG\r\n\x1a\n":
            return self.send_error(415, "PNG only")
        out = DATA / "frames" / shot
        out.mkdir(parents=True, exist_ok=True)
        if int(i) == 0:  # a new take: drop frames left over from a longer earlier take
            for old in out.glob("*.png"):
                old.unlink()
        (out / f"{int(i):05d}.png").write_bytes(body)
        self._json(200, {"saved": f"data/frames/{shot}/{int(i):05d}.png"})

    def _approval(self) -> None:
        """Record a human decision on the agent's proposal. Nothing is sent anywhere: CRIC has no uplink, and a
        real deployment would hand an approved burn to the operator's own command system."""
        if not self._local_request():
            return self.send_error(403, "local requests only")
        size = int(self.headers.get("Content-Length", 0))
        if not 0 < size < 20_000:
            return self.send_error(413)
        try:
            body = json.loads(self.rfile.read(size))
        except ValueError:
            return self.send_error(400, "JSON body expected")
        verdict = body.get("verdict")
        if verdict not in ("approve", "reject") or not isinstance(body.get("action"), str):
            return self.send_error(400, "need verdict approve|reject and action")
        if verdict == "reject" and not str(body.get("reason", "")).strip():
            return self.send_error(400, "a rejection needs a reason")
        entry = {"logged_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 **{k: str(body.get(k, ""))[:1000] for k in ("file", "action", "option_id", "verdict", "reason", "operator")},
                 "sent": False}
        with (DATA / "approvals.jsonl").open("a") as f:
            f.write(json.dumps(entry) + "\n")
        self._json(200, entry | {"note": "logged; simulated hand-off, no command was sent"})

    def do_GET(self) -> None:
        url = urlparse(self.path)
        routes = {"/api/incident": self.api_incident, "/api/incidents": self.api_incidents, "/api/eval": self.api_eval}
        if url.path in routes:
            return routes[url.path](parse_qs(url.query))
        super().do_GET()

    # --- helpers -----------------------------------------------------------
    def _local_request(self) -> bool:
        host_ok = self.headers.get("Host", "") in (f"localhost:{PORT}", f"127.0.0.1:{PORT}")
        site = self.headers.get("Sec-Fetch-Site")
        return host_ok and (site is None or site in ("same-origin", "none"))

    def _json(self, code: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send(self, payload: dict) -> None:
        self.wfile.write(b"data: " + json.dumps(payload, ensure_ascii=False, default=str).encode() + b"\n\n")
        self.wfile.flush()

    # --- API ---------------------------------------------------------------
    def api_incidents(self, q: dict) -> None:
        self._json(200, [_brief(p) for p in _incident_files()[:40]])

    def api_eval(self, q: dict) -> None:
        self._json(200, eval_sets())

    def api_incident(self, q: dict) -> None:
        if not self._local_request():
            return self.send_error(403, "local requests only")
        try:
            ours, other = int(q["ours"][0]), int(q["other"][0])
            name = q.get("preset", ["quiet"])[0]
            mode = q.get("mode", ["replay"])[0]
            assert name in PRESETS and mode in ("live", "replay")
        except (KeyError, ValueError, AssertionError):
            return self.send_error(400, f"need ours, other, preset in {PRESETS}, mode live|replay")
        if mode == "live" and not LIVE_LOCK.acquire(blocking=False):
            return self.send_error(429, "a live run is already in progress")

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.end_headers()
        try:
            self._send({"kind": "start", "mode": mode, "preset": name})
            if mode == "replay":
                wanted = q.get("file", [None])[0]
                files = [p for p in _incident_files() if (p.name == wanted if wanted else
                         p.name.startswith(f"{ours}_{other}_") and p.stem.endswith(f"_{name}"))]
                if not files:
                    raise FileNotFoundError(f"no saved run for {ours}/{other} preset '{name}'; run it live once")
                d = json.loads(files[0].read_text())
                for e in d.get("trace", []):
                    self._send({**e, "replay": True})
                self._send(_result_event(d, files[0].name))
            else:
                tca = _find_tca(ours, other)
                real_now = datetime.now(timezone.utc)
                if parse_utc(tca) - real_now >= timedelta(hours=ACTIONABLE_H):
                    now_utc, time_mode = iso(real_now), "real time"
                else:  # the encounter has passed or is too close: decide as if it were 3 h before TCA
                    now_utc, time_mode = default_now(tca, 3.0), "replayed at TCA - 3 h"
                self._send({"kind": "time_mode", "mode": time_mode, "now_utc": now_utc})
                kwargs = {"live_space_weather": True} | preset(name, tca)  # a preset may pin its own weather
                d = run_incident(ours, other, tca, now_utc=now_utc, tag=name,
                                 emit=lambda kind, **data: self._send({"kind": kind, **data}), **kwargs)
                self._send(_result_event(d))
            self._send({"kind": "end"})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            try:
                self._send({"kind": "error", "message": str(exc) or exc.__class__.__name__})
            except OSError:
                pass
        finally:
            if mode == "live":
                LIVE_LOCK.release()


def main() -> None:
    global PORT
    PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    handler = functools.partial(Handler, directory=str(ROOT))
    with http.server.ThreadingHTTPServer(("127.0.0.1", PORT), handler) as srv:
        print(f"CRIC console: http://localhost:{PORT}")
        srv.serve_forever()


if __name__ == "__main__":
    main()

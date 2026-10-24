"""Local server for the orbit prototype.

Static: web/ plus a read-only allow-list of ../data (*.json, *.tle, top level only).
API:    GET /api/dossier?norad=A&norad=B[&mode=live|replay]
        Streams the Tavily satellite-dossier pipeline as server-sent events.
        live   = real run (CelesTrak SATCAT, Tavily Search/Extract, Nemotron Super). Tavily answers are cached
                 on disk, so repeating a run costs no Tavily credits.
        replay = replays the trace saved by the last live run. No API calls, works offline.

Bound to localhost. Run it with the project's venv so the spike modules import:

    .venv/bin/python web/serve.py [port]
"""

from __future__ import annotations

import functools
import http.server
import importlib.util
import json
import pathlib
import sys
import threading
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

ROOT = pathlib.Path(__file__).resolve().parent
DATA = (ROOT.parent / "data").resolve()
SPIKE = (ROOT.parent / "spike").resolve()
ALLOWED_SUFFIXES = {".json", ".tle"}
PORT = 8765
LIVE_LOCK = threading.Lock()  # one paid run at a time
_module = None


def dossier_module():
    global _module
    if _module is None:
        sys.path.insert(0, str(SPIKE))
        spec = importlib.util.spec_from_file_location("dossier04", SPIKE / "04_satellite_dossier.py")
        _module = importlib.util.module_from_spec(spec)
        sys.modules["dossier04"] = _module  # pydantic resolves postponed annotations through sys.modules
        spec.loader.exec_module(_module)
    return _module


def replay_events(norad: int):
    path = DATA / f"dossier_{norad}.json"
    if not path.exists():
        raise FileNotFoundError(f"no saved run for NORAD {norad}; run it live once first")
    d = json.loads(path.read_text())
    if d.get("trace"):
        yield from d["trace"]
        return
    # Older saved runs have no trace: rebuild a compact one from the stored result.
    row = d["satcat"]
    yield {"kind": "satcat", "norad": norad, "name": row["OBJECT_NAME"], "type": row["OBJECT_TYPE_LABEL"],
           "status": row["OPS_STATUS_LABEL"], "owner": row.get("OWNER"), "launch": row.get("LAUNCH_DATE")}
    yield {"kind": "search_done", "norad": norad, "index": 0, "total": 1, "query": "(saved run) all queries",
           "cached": True, "credits": d["tavily"].get("credits", 0), "results": d["tavily"].get("sources", [])}
    yield {"kind": "claims", "norad": norad, "problems": d["claim_problems"], "passed": not d["claim_problems"]}
    flagged = {p.split(":", 1)[0] for p in d["claim_problems"]}
    yield {"kind": "facts", "norad": norad, "facts": [
        {"field": k, **v, "flagged": k in flagged} for k, v in d["dossier"].items()
        if isinstance(v, dict) and "status" in v]}
    yield {"kind": "result", "norad": norad, "metrics": d["metrics"], "tavily_enabled": d["tavily"]["enabled"],
           "credits": d["tavily"].get("credits", 0)}


class Handler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        clean = path.split("?", 1)[0].split("#", 1)[0]
        if clean.startswith("/data/"):
            target = (DATA / clean[len("/data/"):]).resolve()
            if target.parent == DATA and target.suffix in ALLOWED_SUFFIXES:
                return str(target)
            return str(ROOT / "__forbidden__")
        return super().translate_path(path)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path == "/api/dossier":
            return self.api_dossier(parse_qs(url.query))
        super().do_GET()

    # --- API -------------------------------------------------------------
    def _local_request(self) -> bool:
        """Block DNS-rebinding and cross-site triggers: the API can spend API credits."""
        host_ok = self.headers.get("Host", "") in (f"localhost:{PORT}", f"127.0.0.1:{PORT}")
        site = self.headers.get("Sec-Fetch-Site")
        return host_ok and (site is None or site in ("same-origin", "none"))

    def _send(self, payload: dict) -> None:
        self.wfile.write(b"data: " + json.dumps(payload, ensure_ascii=False).encode() + b"\n\n")
        self.wfile.flush()

    def api_dossier(self, q: dict) -> None:
        if not self._local_request():
            return self.send_error(403, "local requests only")
        try:
            norads = [int(x) for x in q.get("norad", [])][:2]
            assert norads and all(0 < n < 10_000_000 for n in norads)
        except (ValueError, AssertionError):
            return self.send_error(400, "norad must be 1-2 integers")
        mode = q.get("mode", ["live"])[0]
        if mode not in ("live", "replay"):
            return self.send_error(400, "mode must be live or replay")
        if mode == "live" and not LIVE_LOCK.acquire(blocking=False):
            return self.send_error(429, "a live run is already in progress")

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.end_headers()
        try:
            self._send({"kind": "start", "norads": norads, "mode": mode})
            if mode == "replay":
                for n in norads:
                    for e in replay_events(n):
                        self._send(e)
                if len(norads) == 2:
                    pair = DATA / f"dossier_pair_{norads[0]}_{norads[1]}.json"
                    if pair.exists():
                        self._send({"kind": "decision", **json.loads(pair.read_text())})
            else:
                self.run_live(norads)
            self._send({"kind": "done"})
        except (BrokenPipeError, ConnectionResetError):
            pass  # browser closed the stream
        except (Exception, SystemExit) as exc:  # SystemExit: require_env() exits when a key is missing
            try:
                self._send({"kind": "error", "message": str(exc) or exc.__class__.__name__})
            except OSError:
                pass
        finally:
            if mode == "live":
                LIVE_LOCK.release()

    def run_live(self, norads: list[int]) -> None:
        mod = dossier_module()
        from tavily import TavilyClient

        tavily = TavilyClient(api_key=mod.require_env("TAVILY_API_KEY"))
        client = mod.token_factory_client()
        args = SimpleNamespace(max_results=5, extract_top=4)
        mod.run_pair(norads, tavily, client, args, lambda kind, **data: self._send({"kind": kind, **data}))


def main() -> None:
    global PORT
    PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    handler = functools.partial(Handler, directory=str(ROOT))
    with http.server.ThreadingHTTPServer(("127.0.0.1", PORT), handler) as srv:
        print(f"Orbit prototype: http://localhost:{PORT}")
        srv.serve_forever()


if __name__ == "__main__":
    main()

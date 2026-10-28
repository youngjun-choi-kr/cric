"""Mission Studio through the real HTTP server (no model calls): request -> JSON, as the page sees it."""

import functools
import http.server
import json
import sys
import threading
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))


@pytest.fixture(scope="module")
def server():
    import serve

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(serve.Handler, directory=str(ROOT / "web")))
    serve.PORT = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{serve.PORT}", {"Host": f"localhost:{serve.PORT}"}
    srv.shutdown()


def post(server, path, body):
    url, h = server
    r = requests.post(f"{url}/api/launch/{path}", headers=h | {"Content-Type": "application/json"}, data=json.dumps(body), timeout=300)
    return r.status_code, r.json()


def test_presets_and_errors(server):
    url, h = server
    p = requests.get(f"{url}/api/launch/presets", headers=h, timeout=30).json()
    assert {"garage", "electron"} <= set(p["vehicles"]) and "naro" in p["sites"]
    code, err = post(server, "simulate", {"vehicle": "nope", "site": "naro"})
    assert code == 400 and "vehicle" in err["error"]
    code, _ = post(server, "nothing", {})
    assert code == 404


def test_cross_site_requests_are_refused(server):
    url, h = server
    r = requests.post(f"{url}/api/launch/simulate", headers=h | {"Sec-Fetch-Site": "cross-site"}, data="{}", timeout=30)
    assert r.status_code == 403


def test_fly_whatif_and_screen_minutes(server):
    m = {"vehicle": "electron", "payload_kg": 180, "site": "mahia", "target_alt_km": 500,
         "guidance": [35.5, 19.41, 10.88, 2.93]}
    code, w = post(server, "whatif", m | {"engine_out_t": 60})
    assert code == 200 and w["engines"] == 9 and w["range"]["source"].startswith("Natural Earth")
    code, lc = post(server, "lcola", m | {"start_utc": "2026-10-25T00:00:00Z", "minutes": 5})
    assert code == 200 and len(lc["rows"]) == 5 and all("pc" in r for r in lc["rows"]) and lc["criteria_pc"]["nogo"] == 1e-4


def test_burn_commands_reject_bad_input():
    from cric.launch import api

    code, out = api.handle_post("/api/launch/command", json.dumps({"plan_id": "../../etc", "verdict": "approve", "operator": "x"}).encode(), "application/json")
    assert code == 400 and "unknown plan" in out["error"]
    code, out = api.handle_post("/api/launch/burn", json.dumps({"norad_id": 1, "burn_utc": "2026-11-01T00:00:00Z", "direction": "up", "dv_ms": 1}).encode(), "application/json")
    assert code == 400


def test_approval_must_match_the_saved_decision(server):
    import serve

    url, h = server
    name = "zz_test_approval_incident.json"
    path = serve.INCIDENTS / name
    serve.INCIDENTS.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"validation": {"final_action": "MONITOR", "final_option_id": None}}))
    try:
        body = {"file": name, "action": "MANEUVER", "option_id": "O1", "verdict": "approve", "operator": "test"}
        r = requests.post(f"{url}/api/approval", headers=h | {"Content-Type": "application/json"}, data=json.dumps(body), timeout=30)
        assert r.status_code == 409                       # the page asked to approve a burn CRIC did not decide
        r = requests.post(f"{url}/api/approval", headers=h | {"Content-Type": "application/json"},
                          data=json.dumps(body | {"file": "no_such_incident.json"}), timeout=30)
        assert r.status_code == 400
    finally:
        path.unlink()

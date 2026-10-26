"""Browser smoke test of the static console (what GitHub Pages serves): replay a recorded run, approve it.

Needs Playwright and a local Chrome (`pip install -e ".[film]"`); skipped otherwise. In CI it runs on the
runner's preinstalled Chrome.
"""

import functools
import http.server
import socket
import threading
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def site():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()


def test_static_replay_and_approval(site):
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=True)
        except Exception as exc:  # noqa: BLE001 - no Chrome on this machine
            pytest.skip(f"Chrome not available: {exc}")
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{site}/web/?notour=1&data=snapshot")
        page.wait_for_function("document.querySelectorAll('.ev').length > 0", timeout=60000)
        assert "Recorded demo" in page.inner_text("#staticNote")
        page.click("[data-pace='2']")
        page.click("#runReplay")
        page.wait_for_function("document.getElementById('incState').textContent === 'done'", timeout=120000)
        assert page.is_visible("#apprSec")
        page.click("#apprNo")                                   # a rejection needs a reason first
        assert page.is_visible("#apprWhy")
        page.click("#apprYes")                                  # no name yet: nothing is logged
        assert page.inner_text("#apprLog") == ""
        page.fill("#apprWho", "test operator")
        page.click("#apprYes")
        assert "approved" in page.inner_text("#apprLog")
        assert page.inner_text("#evalBody").count("Scenarios handled correctly") == 1
        browser.close()
    assert not errors, errors

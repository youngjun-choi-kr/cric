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


def test_studio_hosted_replay_end_to_end(site):
    """Mission Studio with no server: the recorded JIMIN-1 run, rocket -> window -> operate -> approval."""
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=True)
        except Exception as exc:  # noqa: BLE001 - no Chrome on this machine
            pytest.skip(f"Chrome not available: {exc}")
        page = browser.new_page(viewport={"width": 1440, "height": 810})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("dialog", lambda d: (errors.append("dialog: " + d.message), d.accept()))
        idle = "async () => { const w = (ms) => new Promise(r => setTimeout(r, ms)); await w(300); while (!document.getElementById('busy').hidden) await w(200); }"
        page.goto(f"{site}/web/studio.html?notour=1")
        page.wait_for_function("!document.getElementById('loading')", timeout=60000)
        assert "Hosted replay" in page.inner_text("#serverNote")
        page.click("#flyBtn"); page.evaluate(idle); page.click("#cvSkip")
        page.wait_for_function("!document.body.classList.contains('flying')", timeout=120000)
        assert "Does not reach orbit" in page.inner_text("#verdict")
        page.click("#adviseBtn"); page.evaluate(idle)
        page.click("[data-fix]"); page.wait_for_timeout(5500); page.evaluate(idle); page.click("#cvSkip")
        page.wait_for_function("!document.body.classList.contains('flying')", timeout=180000)
        assert "Orbit" in page.inner_text("#verdict")
        page.click("#toStep2"); page.evaluate(idle)
        page.evaluate("() => document.querySelector('.cells i.go').dispatchEvent(new MouseEvent('click', {bubbles: true}))")
        page.evaluate(idle)
        page.click("#teamBtn"); page.evaluate(idle)
        assert "GO for" in page.inner_text(".brief") and "From the notes" in page.inner_text("#board")
        page.click("#toStep3"); page.click("#deployBtn"); page.evaluate(idle)
        page.click("[data-enc='0']"); page.wait_for_selector(".decision", timeout=60000)
        page.click(".nextPrecise")
        page.wait_for_function("document.querySelectorAll('.decision').length > 1", timeout=60000)
        assert "precise orbit" in page.evaluate("[...document.querySelectorAll('.decision')].pop().innerText")
        browser.close()
    assert not errors, errors

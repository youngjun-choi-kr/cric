"""Record Mission Studio for the film: the JIMIN-1 story, three acts, driven end to end in headless Chrome.

Every number on screen comes from the live run (simulator, Open-Meteo, NOAA, catalog, Nemotron). One choice is made
for the film before recording act 2, and the film says so: the launch hour. The script screens a few upcoming go
hours at Naro and picks the one whose LCOLA strip shows the contrast best (a minute that passes close to another
object and a clear one).

    .venv/bin/python web/serve.py 8765 &
    .venv/bin/python scripts/capture_studio.py            # ~6 min, ~$0.05 of API calls; act 3 approves (logged)

Output: data/film/studio_act{1,2,3}.mp4 (1920x1080, 30 fps) and data/film/studio_markers.json (event times in
each capture, plus the facts the voice-over quotes).
"""

from __future__ import annotations

import base64
import json
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "film"
W, H, SCALE = 1440, 810, 4 / 3          # CSS viewport x device scale = 1920x1080 frames, panels legible

IDLE = """async () => { const w = (ms) => new Promise(r => setTimeout(r, ms));
  await w(300); while (!document.getElementById('busy').hidden) await w(250); }"""


class Recorder:
    def __init__(self, page):
        self.page, self.cdp, self.frames, self.marks = page, page.context.new_cdp_session(page), [], {}
        self.cdp.on("Page.screencastFrame", self._frame)

    def _frame(self, ev):
        self.frames.append((ev["metadata"]["timestamp"], base64.b64decode(ev["data"])))
        self.cdp.send("Page.screencastFrameAck", {"sessionId": ev["sessionId"]})

    def start(self):
        self.frames, self.marks, self.t0 = [], {}, time.time()
        self.cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 92, "maxWidth": 1920, "maxHeight": 1080})

    def mark(self, name):
        # on the video's own clock: the newest frame's timestamp (the page's frames can arrive late when busy)
        self.page.wait_for_timeout(50)
        ts = self.frames[-1][0] - self.frames[0][0] if self.frames else 0.0
        self.marks[name] = round(ts, 2)
        print(f"  {name:14} {self.marks[name]:6.1f} s", flush=True)

    def hold(self, s):
        end = time.time() + s
        while time.time() < end:
            self.page.wait_for_timeout(100)

    def stop(self, name) -> dict:
        self.hold(0.5)
        self.cdp.send("Page.stopScreencast")
        out = OUT / f"studio_{name}.mp4"
        # Resample onto an exact 30 fps grid (the newest frame at each tick) and pipe to ffmpeg: a concat list of
        # per-frame durations drifted by ~12 s over five minutes, which put the event markers out of step.
        t0, ts = self.frames[0][0], [f[0] for f in self.frames]
        n = int((ts[-1] - t0) * 30) + 1
        ff = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-y", "-f", "image2pipe", "-framerate", "30", "-i", "-",
                               "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
                               "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16", str(out)], stdin=subprocess.PIPE)
        k = 0
        for i in range(n):
            while k + 1 < len(ts) and ts[k + 1] - t0 <= i / 30:
                k += 1
            ff.stdin.write(self.frames[k][1])
        ff.stdin.close()
        if ff.wait():
            raise SystemExit("ffmpeg failed")
        print(f"{out}  {len(self.frames)} frames")
        return dict(self.marks)


def pick_hour(page) -> dict:
    """Screen upcoming go hours at Naro; prefer one with a no-go minute and a clear minute (the film's contrast)."""
    return page.evaluate("""async () => {
      const S = studio.S, call = (p, b) => fetch('/api/launch/' + p, {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(b)}).then(r => r.json());
      const m = {vehicle: S.vehicle, payload_kg: +document.getElementById('payload').value, site: 'naro',
                 target_alt_km: +document.getElementById('alt').value, incl_deg: +document.getElementById('incl').value, guidance: S.guidance};
      const heat = await call('windows', {incl_deg: m.incl_deg, sites: ['naro']});
      const hours = heat.rows[0].hours.map((h, k) => ({...h, k}))
        .filter(h => h.status === 'go' && Date.parse(h.t) > Date.now() + 3 * 3600e3).slice(0, 8);
      let best = null;
      for (const h of hours) {
        const r = await call('lcola', {...m, start_utc: h.t, minutes: 60});
        const rows = r.rows.filter(x => x.closest_km != null);
        const worst = rows.reduce((a, b) => (b.closest_km < a.closest_km ? b : a));
        const clear = Math.max(...r.rows.filter(x => x.status === 'go').map(x => x.closest_km ?? 0));
        const score = (worst.status === 'nogo' ? 100 : worst.status === 'caution' ? 10 : 0) + Math.min(clear, 30) - worst.closest_km;
        if (!best || score > best.score) best = {k: h.k, t: h.t, score, worst, clear};
      }
      return best;
    }""")


def main() -> None:
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    marks: dict = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True, args=["--use-angle=metal", "--enable-gpu"])
        page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=SCALE)
        page.on("dialog", lambda d: d.accept())
        page.goto("http://localhost:8765/studio.html", wait_until="networkidle")
        page.wait_for_function("!document.getElementById('loading')", timeout=60000)
        page.wait_for_timeout(2500)                     # camera settles on Naro
        rec = Recorder(page)
        idle = lambda: page.evaluate(IDLE)

        print("act 1: the rocket")
        flying = "document.body.classList.contains('flying')"
        t_sim = "(studio.CV.flight && studio.CV.mode === 'launch' ? studio.CV.flight.t : -99)"

        def until(name, cond, timeout=400000):
            page.wait_for_function(cond, timeout=timeout); rec.mark(name)

        def flight(tag):           # one launch: close-up, globe, (deploy), back
            until(f"liftoff{tag}", f"{t_sim} >= 0")
            until(f"maxq{tag}", "studio.CV.flight && studio.CV.flight.maxq")
            until(f"sep{tag}", "studio.CV.flight && studio.CV.flight.hadSep.size > 0")
            until(f"fairing{tag}", "studio.CV.flight && studio.CV.flight.fairDone")
            until(f"handoff{tag}", "studio.CV.mode !== 'launch'")
            until(f"deploy{tag}", f"!{flying} || (studio.CV.mode === 'launch' && studio.CV.flight.deploy)")
            until(f"landed{tag}", f"!{flying}")

        rec.start(); rec.hold(4); rec.mark("start")                  # the hangar line-up
        page.click("#targetBtn"); idle(); rec.mark("target"); rec.hold(2)
        page.click("#flyBtn"); rec.mark("fly"); idle(); rec.mark("verdict")
        flight("")
        rec.hold(3)
        page.click("#adviseBtn"); rec.mark("advise"); idle(); rec.mark("fixes"); rec.hold(5)
        page.click("[data-fix]")                                     # hangar: the stage grows, then it flies
        until("apply", "studio.CV.mode === 'bay'", 10000)
        until("fly2", "!document.getElementById('busy').hidden", 30000); idle(); rec.mark("verdict2")
        flight("2")
        rec.hold(3)
        page.click("#dispBtn"); rec.mark("disp"); idle(); rec.mark("disp_done"); rec.hold(5)
        marks["act1"] = rec.stop("act1")
        marks["act1_facts"] = page.evaluate("""() => ({verdict: document.getElementById('verdict').innerText,
            fix: studio.S.lastFix})""")

        print("choosing the launch hour (shown in the film as pre-screened)")
        hour = pick_hour(page)
        print("  ", hour)
        marks["hour"] = hour

        print("act 2: the window")
        rec.start(); rec.hold(0.5); rec.mark("start")
        page.click("#toStep2"); idle(); rec.mark("heat"); rec.hold(5)
        page.evaluate("k => document.querySelector(`.cells[data-site=\"naro\"] i[data-k=\"${k}\"]`)"
                      ".dispatchEvent(new MouseEvent('click', {bubbles: true}))", hour["k"])
        rec.mark("hour"); idle(); rec.mark("strip"); rec.hold(3)
        box = page.evaluate("""t => { const rows = studio.S.lcola.rows, i = rows.findIndex(x => x.liftoff_utc === t);
            const r = document.querySelectorAll('.strip i')[i].getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; }""",
                            hour["worst"]["liftoff_utc"])
        page.mouse.move(box[0], box[1], steps=12); rec.hold(4)
        page.mouse.move(box[0] + 260, box[1] - 220, steps=8); rec.mark("team")
        page.click("#teamBtn"); idle(); rec.mark("board"); rec.hold(3)
        page.evaluate("document.querySelector('.brief')?.scrollIntoView({behavior: 'smooth', block: 'center'})")
        rec.hold(6); rec.mark("brief")
        marks["act2"] = rec.stop("act2")
        marks["act2_facts"] = page.evaluate("""() => ({brief: document.querySelector('.brief')?.innerText,
            liftoff: studio.S.liftoff, cands: studio.S.cands})""")
        if not marks["act2_facts"]["liftoff"]:
            raise SystemExit("the director held; nothing to launch")

        print("act 3: operate")
        rec.start(); rec.hold(0.5); rec.mark("start")
        page.click("#toStep3"); rec.hold(2.5)
        page.click("#deployBtn"); rec.mark("deploy"); idle(); rec.mark("in_orbit"); rec.hold(9)
        enc = page.evaluate("studio.S.deployed.encounters")
        if not enc:
            marks["act3"] = rec.stop("act3")
            raise SystemExit("no close approach in 72 h for this launch time; act 3 ends at the passes")
        page.click("[data-enc='0']"); rec.mark("assess")
        page.wait_for_selector(".decision", timeout=400000); rec.mark("decision"); rec.hold(2)
        page.evaluate("document.querySelector('.decision').scrollIntoView({behavior: 'smooth', block: 'end'})"); rec.hold(3)
        page.click(".nextPrecise"); rec.mark("precise")                # the provider's ephemeris arrives
        page.wait_for_function("document.querySelectorAll('.decision').length > 1", timeout=400000); rec.mark("decision2"); rec.hold(2)
        page.evaluate("[...document.querySelectorAll('.decision')].pop().scrollIntoView({behavior: 'smooth', block: 'end'})"); rec.hold(3)
        page.evaluate("[...document.querySelectorAll('.decision .apOk')].pop().click()")
        page.wait_for_function("[...document.querySelectorAll('.decision .apOut')].pop().textContent.length > 0", timeout=30000)
        rec.mark("approved"); rec.hold(4)
        marks["act3"] = rec.stop("act3")
        marks["act3_facts"] = page.evaluate("""() => ({deployed: {norad: studio.S.deployed.norad_id, n: studio.S.deployed.objects_in_catalog,
            enc: studio.S.deployed.encounters[0], passes: studio.S.deployed.passes?.length},
            decision: document.querySelector('.decision')?.innerText,
            decision2: [...document.querySelectorAll('.decision')].pop()?.innerText})""")
        browser.close()
    (OUT / "studio_markers.json").write_text(json.dumps(marks, indent=1, ensure_ascii=False))
    print(OUT / "studio_markers.json")


if __name__ == "__main__":
    main()

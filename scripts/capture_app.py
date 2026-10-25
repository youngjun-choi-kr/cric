"""Record the console replaying a golden run, for the film's APP shots. No screen-recording app needed.

Drives the installed Google Chrome headlessly with Playwright, streams frames through the DevTools screencast
(each frame carries its timestamp), and turns them into a constant-frame-rate MP4.

    .venv/bin/python web/serve.py 8765 &                       # the local console
    .venv/bin/python scripts/capture_app.py --pair 60454-61786 --preset imaging --approve
    .venv/bin/python scripts/capture_app.py --pair 43250-34430 --preset quiet

Output: data/film/app_<pair>_<preset>.mp4 (1920x1080, 30 fps).
"""

from __future__ import annotations

import argparse
import base64
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "film"


def main() -> None:
    from playwright.sync_api import sync_playwright

    ap = argparse.ArgumentParser()
    ap.add_argument("--pair", required=True, help="ours-other NORAD ids, e.g. 60454-61786")
    ap.add_argument("--preset", default="quiet")
    ap.add_argument("--pace", default="1", choices=["0.5", "1", "2"])
    ap.add_argument("--ui", default="1.25", help="console zoom for legibility")
    ap.add_argument("--approve", action="store_true", help="press Approve after the run, as in the film")
    ap.add_argument("--tail", type=float, default=3.0, help="seconds to keep recording after the run ends")
    ap.add_argument("--url", default="http://localhost:8765/")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    a = ap.parse_args()
    ours, other = a.pair.split("-")
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"app_{a.pair}_{a.preset}.mp4"

    frames: list[tuple[float, bytes]] = []
    with sync_playwright() as p, tempfile.TemporaryDirectory() as tmp:
        browser = p.chromium.launch(channel="chrome", headless=True, args=["--use-angle=metal", "--enable-gpu"])
        page = browser.new_page(viewport={"width": a.width, "height": a.height}, device_scale_factor=1)
        page.goto(f"{a.url}?notour=1&ui={a.ui}", wait_until="networkidle")
        page.wait_for_function("document.querySelectorAll('.ev').length > 0", timeout=60000)
        page.evaluate("""([ours, other, preset, pace]) => {
            const ev = [...document.querySelectorAll('.ev')].find((el) => {
              const [x, y] = el.dataset.id.split('-'); return (x === ours && y === other) || (x === other && y === ours); });
            if (!ev) throw new Error('pair not in the list: ' + ours + '-' + other);
            ev.click();
            document.getElementById('oursSel').value = ours;
            document.getElementById('presetSel').value = preset;
            document.querySelector(`[data-pace="${pace}"]`)?.click();
        }""", [ours, other, a.preset, a.pace])
        time.sleep(4)                                  # let the camera fly to the pair

        cdp = page.context.new_cdp_session(page)

        def on_frame(ev):
            frames.append((ev["metadata"]["timestamp"], base64.b64decode(ev["data"])))
            cdp.send("Page.screencastFrameAck", {"sessionId": ev["sessionId"]})

        cdp.on("Page.screencastFrame", on_frame)
        cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 92, "maxWidth": a.width, "maxHeight": a.height,
                                          "everyNthFrame": 1})
        page.click("#runReplay")
        page.wait_for_function("document.getElementById('incState').textContent === 'done'", timeout=300000)
        if a.approve:
            time.sleep(1.5)
            page.evaluate("document.getElementById('apprSec').scrollIntoView({behavior: 'smooth'})")
            time.sleep(1.0)
            page.click("#apprYes")
        t_end = time.time() + a.tail
        while time.time() < t_end:
            page.wait_for_timeout(100)
        cdp.send("Page.stopScreencast")
        browser.close()

        if len(frames) < 2:
            raise SystemExit("no frames captured")
        d = Path(tmp)
        lines = []
        for i, (ts, jpg) in enumerate(frames):
            (d / f"{i:05d}.jpg").write_bytes(jpg)
            dur = (frames[i + 1][0] - ts) if i + 1 < len(frames) else 1 / 30
            lines += [f"file '{d / f'{i:05d}.jpg'}'", f"duration {max(dur, 0.001):.4f}"]
        lines.append(f"file '{d / f'{len(frames) - 1:05d}.jpg'}'")
        (d / "list.txt").write_text("\n".join(lines) + "\n")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(d / "list.txt"),
                        "-vf", f"scale={a.width}:{a.height}:force_original_aspect_ratio=decrease,pad={a.width}:{a.height}:"
                               f"(ow-iw)/2:(oh-ih)/2,fps=30", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16",
                        str(out)], check=True)
    span = frames[-1][0] - frames[0][0]
    print(f"{out}  {len(frames)} frames over {span:.1f} s")


if __name__ == "__main__":
    main()

"""Can CRIC act on an operator's screenshot? Test of Nemotron 3.5 Super VL as a screen reader.

Four screenshots are rendered from HTML mockups (Chrome via Playwright, so they look like real tools), each with
one known consequence for the EOS-08 encounter on precise data (options O1 at the cheapest time, O2 later):
  calendar   an imaging block over O1, no thruster firing       -> MANEUVER on O2
  telemetry  propulsion NO-GO                                    -> ESCALATE
  chat_ko    Korean chat: tonight's imaging is cancelled          -> MANEUVER on O1 (nothing blocks it)
  dashboard  battery, antenna maintenance tomorrow, nothing else -> MANEUVER on O1
Each screenshot goes VL transcript -> mission notes -> quoted constraints (Super) -> coded policy, so the outcome
is checked by code. The baseline without the image cannot see any of it and always burns O1.

    .venv/bin/python scripts/eval_screens.py [--repeats 2]

Writes results/screens_eval.json and the images to results/screens/.
"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from cric.agent.constraints import apply, coded_policy, extract
from cric.agent.screens import as_note, read_screen
from cric.eval.runner import build_contexts
from cric.eval.scenarios import SCENARIOS
from cric.llm import Usage
from cric.physics.timeutil import parse_utc
from cric.safety.validator import validate

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results"
STYLE = ("body{margin:0;font:15px -apple-system,Helvetica,Arial,sans-serif;background:#f4f5f7;color:#1d2433}"
         ".card{margin:18px;background:#fff;border:1px solid #d9dde5;border-radius:8px;padding:16px 20px}"
         "h1{font-size:17px;margin:0 0 12px}table{border-collapse:collapse;width:100%}"
         "td,th{border-bottom:1px solid #e3e6ec;padding:7px 8px;text-align:left}th{color:#667}")


def screens(o1: str, o2: str) -> dict[str, tuple[str, set, str | None]]:
    """name -> (html, accepted actions, required option or None). o1 / o2 are HH:MM burn times."""
    h, m = map(int, o1.split(":"))
    b0, b1 = f"{(h * 60 + m - 12) // 60 % 24:02d}:{(h * 60 + m - 12) % 60:02d}", f"{(h * 60 + m + 12) // 60 % 24:02d}:{(h * 60 + m + 12) % 60:02d}"
    return {
        "calendar": (f"""<div class=card><h1>Mission planning — Sat EOS-08 — tonight (UTC)</h1><table>
            <tr><th>Start</th><th>End</th><th>Activity</th><th>Constraints</th></tr>
            <tr><td>20:40</td><td>20:52</td><td>Ground pass SVALBARD</td><td>TT&amp;C</td></tr>
            <tr style="background:#fde8e8"><td>{b0}</td><td>{b1}</td><td><b>DISASTER RESPONSE IMAGING</b></td>
                <td><b>NO THRUSTER FIRING · nadir lock</b></td></tr>
            <tr><td>23:30</td><td>23:41</td><td>Ground pass INUVIK</td><td>TT&amp;C</td></tr></table></div>""",
                     {"MANEUVER"}, "O2"),
        "telemetry": ("""<div class=card><h1>EOS-08 · subsystem status</h1><table>
            <tr><th>Subsystem</th><th>Status</th><th>Note</th></tr>
            <tr><td>Power</td><td style="color:#178a3e">NOMINAL</td><td>Battery 88%</td></tr>
            <tr><td>ADCS</td><td style="color:#178a3e">NOMINAL</td><td>Sun-pointing</td></tr>
            <tr><td>Propulsion</td><td style="color:#c62828;font-weight:700">NO-GO</td><td>Tank pressure anomaly —
                all ΔV operations on hold pending review board</td></tr>
            <tr><td>Payload</td><td style="color:#178a3e">NOMINAL</td><td>Idle</td></tr></table></div>""",
                      {"ESCALATE"}, None),
        "chat_ko": ("""<div class=card style="max-width:520px"><h1>#eos08-ops</h1>
            <p><b>김민지 (탑재체 운영)</b> <span style="color:#889">20:31</span><br>오늘 밤 촬영 일정 취소됐습니다.
               위성 자세나 추력기 사용에 제약 없어요.</p>
            <p><b>박준호 (비행역학)</b> <span style="color:#889">20:33</span><br>확인했습니다, 감사합니다!</p></div>""",
                    {"MANEUVER"}, "O1"),
        "dashboard": ("""<div class=card><h1>Ops dashboard</h1><table>
            <tr><td>Battery</td><td>92%</td></tr><tr><td>Downlinked today</td><td>14.2 GB</td></tr>
            <tr><td>Antenna B maintenance</td><td>tomorrow 03:00–04:00 UTC (no impact tonight)</td></tr>
            <tr><td>Propellant</td><td>31.4 kg (≈ 40 m/s)</td></tr></table></div>""",
                      {"MANEUVER"}, "O1"),
    }


def render(html: dict[str, str], folder: Path) -> dict[str, bytes]:
    from playwright.sync_api import sync_playwright

    folder.mkdir(parents=True, exist_ok=True)
    out = {}
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        page = b.new_page(viewport={"width": 760, "height": 420}, device_scale_factor=1)
        for name, body in html.items():
            page.set_content(f"<html><head><meta charset=utf-8><style>{STYLE}</style></head><body>{body}</body></html>")
            out[name] = page.screenshot(full_page=True)
            (folder / f"{name}.png").write_bytes(out[name])
        b.close()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=2)
    a = ap.parse_args()
    base = next(s for s in SCENARIOS if s.id == "S01")                # EOS-08, precise data, red, debris
    ctx, now = build_contexts([base], log=lambda *_: None)["S01"]
    opts = {o["kind"]: o for o in ctx["burn_options"]["options"]}
    o1 = parse_utc(opts["min_dv"]["burn_utc"])
    spec = screens(f"{o1:%H:%M}", opts["latest"]["burn_utc"][11:16])
    images = render({k: v[0] for k, v in spec.items()}, OUT / "screens")
    usage = Usage()

    def one(job):
        name, rep = job
        read = read_screen(images[name], usage=usage)
        c = {**ctx, "mission": {"notes": as_note(read), "blackouts": []}}
        found, problems = extract(c, usage=usage)
        c2 = apply(c, found, problems)
        dec = coded_policy(c2, now)
        v = validate(dec, c2, now)
        accepted, opt = spec[name][1], spec[name][2]
        ok = v["final_action"] in accepted and (opt is None or v["final_option_id"] == opt)
        return {"screen": name, "repeat": rep, "transcript": read.transcript, "affects": read.affects_manoeuvres,
                "constraints": c2["mission"]["extracted_constraints"], "final": [v["final_action"], v["final_option_id"]],
                "expected": [sorted(accepted), opt], "correct": ok}

    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(one, [(n, r) for n in spec for r in range(a.repeats)]))
    baseline = {n: (spec[n][1] >= {"MANEUVER"} and spec[n][2] == "O1") for n in spec}   # no image: always O1
    summary = {"with_vl": f"{sum(r['correct'] for r in rows)}/{len(rows)}",
               "without_image": f"{sum(baseline.values())}/{len(baseline)} screens (always burns O1)",
               "cost_usd": round(usage.cost_usd, 4), "o1_burn_utc": f"{o1:%H:%M}", "time_window_minutes": 24,
               "now_utc": f"{now:%H:%M}"}
    (OUT / "screens_eval.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=1, ensure_ascii=False))
    print(json.dumps(summary, indent=1))
    for r in rows:
        print(f"  {r['screen']:9} #{r['repeat']} -> {r['final']}  {'ok' if r['correct'] else 'WRONG'}  | {r['affects'][:90]}")


if __name__ == "__main__":
    main()

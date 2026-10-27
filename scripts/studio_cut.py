"""Assemble the Mission Studio film (JIMIN-1) from the captures of scripts/capture_studio.py.

Each app shot plays one stretch of a capture, between two event markers, sped up to fit the shot (a long wait such
as a live CRIC run is marked as a time-lapse on screen); a stretch shorter than its shot holds its last frame.
Voice-over lines that quote numbers take them from data/film/studio_markers.json, so the narration says what the
recorded run showed.

    .venv/bin/python scripts/studio_cut.py [--scratch-vo]

Output: data/film/studio_cut.mp4, studio_cut.srt (voice-over as subtitles), studio_vo.txt (the script to read),
and with --scratch-vo a timing check read by macOS `say` (studio_cut_scratch_vo.mp4).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rough_cut import BOLD, FPS, H, OUT, W, _caption_filter, _duration, _ts, _txt, segment  # noqa: E402

M = json.loads((OUT / "studio_markers.json").read_text())


SAY = {"MONITOR": "keep watching", "REQUEST_PRECISE_DATA": "get better data first", "COORDINATE": "talk to the other operator",
       "ESCALATE": "let a person decide", "MANEUVER": "move"}
ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
TENS = {2: "twenty", 3: "thirty", 4: "forty", 5: "fifty"}


def spoken_time(hhmm: str) -> str:
    """'02:49' -> 'two forty-nine' (UTC), the way a narrator reads it."""
    h, m = int(hhmm[:2]), int(hhmm[3:5])
    mm = "o'clock" if m == 0 else (f"oh {ONES[m]}" if m < 10 else ONES[m] if m < 20 else TENS[m // 10] + ("" if m % 10 == 0 else "-" + ONES[m % 10]))
    return f"{ONES[h] if h < 20 else 'twenty-' + ONES[h - 20]} {mm}"


def facts() -> dict:
    h, a3 = M["hour"], M.get("act3_facts", {})
    worst = h["worst"]
    v1 = M["act1_facts"]["verdict"]
    orbit = re.search(r"Orbit: ([\d,]+ × [\d,]+ km)", v1)
    enc = a3.get("deployed", {}).get("enc") or {}
    a1, a2 = a3.get("action") or "", a3.get("action2") or ""
    liftoff = (M["act2_facts"]["liftoff"] or "")[11:16]
    b = (M.get("burn_facts") or {}).get("first") or {}
    w, alt = b.get("worst") or {}, b.get("alternative") or {}
    miss_m = w.get("miss_distance_km", 0) * 1000
    shift = alt.get("shift_min", 0)
    return {"burn_close": (f"{miss_m / 1000:.1f} kilometers" if miss_m >= 1000 else f"{miss_m:.0f} meters"),
            "burn_alt": f"{abs(shift)} minutes {'later' if shift > 0 else 'earlier'}" if alt else "",
            "worst_km": f"{worst['closest_km']:.1f}", "worst_t": worst["liftoff_utc"][11:16], "worst_say": spoken_time(worst["liftoff_utc"][11:16]),
            "liftoff": liftoff, "liftoff_say": spoken_time(liftoff) if liftoff else "",
            "orbit": orbit.group(1) if orbit else "orbit",
            "say1": SAY.get(a1, a1.replace("_", " ").lower() or "decide"), "say2": SAY.get(a2, a2.replace("_", " ").lower() or "decide"),
            "miss_km": f"{enc.get('miss_distance_km', 0):.1f}",
            "cleared": "Risk cleared" in (a3.get("decision2") or "")}


F = facts()
# (id, seconds, kind, source (act, from marker, to marker) or cine/card text, caption, voice-over)
SHOTS = [
    ("J1", 5, "cine", "overview-push", "18,806 objects in orbit today",
     "Eighteen thousand objects circle Earth. Jimin wants one more."),
    ("J2", 4, "title", None, "CRIC Mission Studio\nYour rocket, your launch, your satellite in orbit", "This is CRIC Mission Studio."),
    ("A1", 7, "app", ("act1", "start", "target"), "Built from its numbers, to scale",
     "Jimin's small rocket, Garage One, stands to scale next to real launchers."),
    ("A2", 8, "app", ("act1", "target", "liftoff"), "One sentence in, an orbit out",
     "Jimin describes the mission in one sentence. Nemotron Nano turns it into an orbit. Liftoff."),
    ("A3", 8, "app", ("act1", "liftoff", "handoff"), "Simulated flight",
     "Everything you see is the simulation. Max Q. Stage separation. But it's too slow."),
    ("A4", 5, "app", ("act1", "handoff", "advise"), "Too low to stay in orbit", "It never gets high enough, and falls back."),
    ("A5", 8, "app", ("act1", "advise", "apply"), "Every fix is test-flown first",
     "Nemotron 3 Super explains what went wrong. The simulator test-flies every fix first."),
    ("A6", 5, "app", ("act1", "apply", "fly2"), "First stage: 30% bigger", "The answer: a bigger first stage."),
    ("A7", 8, "app", ("act1", "liftoff2", "handoff2"), "Second try", "Second try. This time it keeps climbing,"),
    ("A8", 7, "app", ("act1", "deploy2", "landed2"), f"In orbit: {F['orbit']}", "and JIMIN-1 reaches orbit."),
    ("A9", 6, "app", ("act1", "disp", "end"), "Where the spent stage lands",
     "Five hundred test flights show where the spent stage lands. At sea."),
    ("B1", 8, "app", ("act2", "start", "hour"), "The hours that suit a camera satellite",
     "Next: when to launch. For a camera satellite, launch time sets when it sees the ground."),
    ("B2", 9, "app", ("act2", "hour", "team"), f"{F['worst_t']} UTC liftoff: {F['worst_km']} km from another satellite",
     f"Every minute is checked against the catalog. At {F['worst_say']}, it would pass {F['worst_km']} kilometers from another satellite."),
    ("B3", 12, "app", ("act2", "team", "end"), "Notes become rules. The team votes.",
     f"The team's notes, even in Korean, become rules the code can check. Three officers vote. Nemotron 3 Ultra makes the call: go at {F['liftoff_say']}."),
    ("C1", 8, "app", ("act3", "start", "assess"), "Photo passes over Seoul",
     "Launch. JIMIN-1 joins the catalog, and CRIC shows when it can photograph Seoul."),
    ("C2", 12, "app", ("act3", "assess", "decision"), "Close approach (sped up)",
     f"Days later, another satellite comes close. CRIC works out the risk, finds out who can move, and decides: {F['say1']}."),
    ("C3", 7, "app", ("act3", "precise", "encounter"), "Better data: risk cleared" if F["cleared"] else "Better data: decided again",
     "Better orbit data arrives. The risk drops to almost zero." if F["cleared"]
     else f"Better orbit data arrives, and CRIC decides again: {F['say2']}."),
    ("C4", 6, "app", ("act3", "encounter", "encounter_end"), "The pass, slowed down",
     f"Here's that pass: {F['miss_km']} kilometers apart."),
    ("C5", 3, "app", ("act3", "encounter_end", "ctl"), "The owner decides", "Jimin signs off."),
    ("M1", 8, "app", ("act3", "ctl", "burn"), "You command a burn",
     "Now Jimin takes the controls: lower the orbit, for sharper photos."),
    ("M2", 9, "app", ("act3", "burn", "alt_checked" if F["burn_alt"] else "burn_approved"), "CRIC finds a close pass, and a safer time",
     f"CRIC checks every tracked object: that burn passes {F['burn_close']} from a satellite. {F['burn_alt'].capitalize()}, it's clear."
     if F["burn_alt"] else "CRIC checks the burn against every tracked object."),
    ("M3", 7, "app", ("act3", "alt_checked" if F["burn_alt"] else "burn_approved", "ff"), "Approved and logged. Nothing is sent.",
     "Jimin takes the safer time and approves. Every command is logged." if F["burn_alt"] else "Jimin approves. Every command is logged."),
    ("M4", 6, "app", ("act3", "ff", "end"), "Mission clock ×600", "The mission clock runs on: next, a photo pass over Seoul."),
    ("J14", 7, "card", None, "CRIC does the math and the reading.\nThe decisions stay with people.",
     "CRIC does the math and the reading. The decisions stay with people."),
    ("J15", 5, "title", None, "CRIC Mission Studio\nSimulated flights; visuals illustrative. Launch hour picked after checking the live forecast.\n"
                             "NVIDIA Nemotron on Nebius Token Factory · youngjun-choi-kr.github.io/cric", ""),
]


def app_segment(tmp: Path, sid: str, seconds: float, src: tuple, caption: str) -> Path:
    act, a, b = src
    cap = OUT / f"studio_{act}.mp4"
    marks = M[act]
    t0 = marks[a]
    t1 = _duration(cap) if b == "end" else marks[b]
    span = max(t1 - t0, 0.5)
    speed = span / seconds
    if speed > 1:      # faster than real time
        vf = f"trim=start={t0}:end={t1},setpts=(PTS-STARTPTS)/{speed:.4f},fps={FPS}"
    else:              # play at real time, then hold the last frame
        vf = f"trim=start={t0}:end={t1},setpts=PTS-STARTPTS,fps={FPS},tpad=stop_mode=clone:stop_duration={seconds - span + 0.1:.2f}"
    vf += f",trim=duration={seconds},scale={W}:{H},fade=t=in:st=0:d=0.25,fade=t=out:st={seconds - 0.25}:d=0.25"
    if speed > 1.6:    # time-lapse badge, bottom left, clear of the panels' controls
        vf += (f",drawtext=expansion=none:fontfile='{BOLD}':text='{speed:.0f}× speed':fontcolor=0x5ab0ff:fontsize=30:"
               f"x=w-tw-40:y=150:box=1:boxcolor=black@0.5:boxborderw=10")
    if act == "act2":  # the heatmap and the LCOLA strip fill the bottom: caption at the top
        vf += (f",drawtext=expansion=none:fontfile='{BOLD}':textfile='{_txt(tmp, sid + '_cap', caption)}':fontcolor=white:"
               f"fontsize=40:x=(w-text_w)/2:y=110:box=1:boxcolor=black@0.55:boxborderw=18")
    else:
        vf += _caption_filter(tmp, sid, caption)
    out = tmp / f"{sid}.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(cap), "-vf", vf, "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-crf", "18", "-r", str(FPS), "-an", str(out)], check=True)
    return out


def main() -> None:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        parts = [app_segment(tmp, sid, sec, src, cap) if kind == "app" else segment(tmp, sid, sec, kind, src, cap, vo)
                 for sid, sec, kind, src, cap, vo in SHOTS]
        (tmp / "list.txt").write_text("".join(f"file '{p}'\n" for p in parts))
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(tmp / "list.txt"),
                        "-c", "copy", str(OUT / "studio_cut.mp4")], check=True)
    t, srt, script = 0.0, [], []
    for sid, sec, *_, vo in SHOTS:
        if vo:
            srt.append(f"{len(srt) + 1}\n{_ts(t + 0.3)} --> {_ts(t + sec - 0.3)}\n{vo}\n")
            script.append(f"[{_ts(t)[3:8]}] {sid}  {vo}")
        t += sec
    (OUT / "studio_cut.srt").write_text("\n".join(srt))
    (OUT / "studio_vo.txt").write_text("\n\n".join(script) + "\n")
    words = sum(len(s[5].split()) for s in SHOTS)
    print(f"{OUT / 'studio_cut.mp4'}  {t:.0f} s, {len(SHOTS)} shots, voice-over {words} words ({words / t:.1f} words/s)")
    if "--scratch-vo" in sys.argv:
        scratch(t)


def scratch(total: float) -> None:
    t, inputs, filters = 0.0, [], []
    with tempfile.TemporaryDirectory() as d:
        for sid, sec, *_, vo in SHOTS:
            if vo:
                wav = Path(d) / f"{sid}.aiff"
                subprocess.run(["say", "-v", "Samantha", "-r", "170", "-o", str(wav), vo], check=True)
                dur = _duration(wav)
                print(f"  {sid:4} read {dur:4.1f} s in a {sec} s shot {'OVERRUN' if dur > sec - 0.3 else ''}")
                inputs += ["-i", str(wav)]
                ms = int((t + 0.3) * 1000)
                filters.append(f"[{len(inputs) // 2}:a]adelay={ms}|{ms}[a{sid}]")
            t += sec
        labels = "".join(f"[a{s[0]}]" for s in SHOTS if s[5])
        mix = ";".join(filters) + f";{labels}amix=inputs={len(filters)}:normalize=0[vo]"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(OUT / "studio_cut.mp4"), *inputs,
                        "-filter_complex", mix, "-map", "0:v", "-map", "[vo]", "-c:v", "copy", "-c:a", "aac",
                        "-shortest", str(OUT / "studio_cut_scratch_vo.mp4")], check=True)


if __name__ == "__main__":
    main()

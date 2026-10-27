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


def facts() -> dict:
    h, a3 = M["hour"], M.get("act3_facts", {})
    worst = h["worst"]
    v1 = M["act1_facts"]["verdict"]
    orbit = re.search(r"Orbit: ([\d,]+ × [\d,]+ km, [\d.]+°)", v1)
    dec = (a3.get("decision") or "").split("\n")
    action = next((x for x in dec if re.fullmatch(r"[A-Z_]{4,}", x.strip())), "").strip()
    return {"worst_km": f"{worst['closest_km']:.1f}", "worst_obj": worst["object"], "worst_t": worst["liftoff_utc"][11:16],
            "clear_km": f"{h['clear']:.0f}", "liftoff": (M["act2_facts"]["liftoff"] or "")[11:16],
            "orbit": orbit.group(1) if orbit else "orbit", "fix": M["act1_facts"].get("fix") or "Verified fix: bigger first stage",
            "other": (a3.get("deployed", {}).get("enc") or {}).get("object_b", {}).get("name", "another satellite"),
            "miss_m": f"{(a3.get('deployed', {}).get('enc') or {}).get('miss_distance_km', 0) * 1000:,.0f}",
            "action": action.replace("_", " ").title() or "decision",
            "miss_km": f"{(a3.get('deployed', {}).get('enc') or {}).get('miss_distance_km', 0):.1f}",
            "vrel": f"{(a3.get('deployed', {}).get('enc') or {}).get('relative_speed_kms', 0):.0f}"}


F = facts()
# (id, seconds, kind, source (act, from marker, to marker) or cine/card text, caption, voice-over)
SHOTS = [
    ("J1", 6, "cine", "overview-push", "18,806 tracked objects · real orbits",
     "Eighteen thousand objects are already in orbit. Jimin wants to add one."),
    ("J2", 4, "title", None, "CRIC Mission Studio\nYour rocket · your launch window · your satellite in orbit",
     "This is CRIC Mission Studio."),
    ("A1", 8, "app", ("act1", "start", "target"), "Rockets built from their numbers, at true scale · X-ray: where each number came from",
     "Jimin's student rocket, Garage One, stands next to real launchers, built from its own numbers."),
    ("A2", 8, "app", ("act1", "target", "liftoff"), "Nemotron Nano reads the mission · orbit computed in code",
     "Nemotron Nano reads the mission from one sentence; code works out the orbit. Liftoff."),
    ("A3", 9, "app", ("act1", "liftoff", "handoff"), "Every motion from the 3-DOF simulation · plume and sky are illustration",
     "Every motion comes from the flight simulation: max Q, staging, the fairing. But it is too slow."),
    ("A4", 6, "app", ("act1", "handoff", "advise"), "Perigee below the ~150 km needed: it falls back",
     "Its lowest point is too low, and it falls back."),
    ("A5", 9, "app", ("act1", "advise", "apply"), "Every fix is flown again before it is shown · Nemotron 3 Super explains",
     "Nemotron 3 Super explains where the speed went. The fix comes from the simulator, flown again before it is shown."),
    ("A6", 6, "app", ("act1", "apply", "fly2"), f"{F['fix']}", "A bigger first stage."),
    ("A7", 9, "app", ("act1", "liftoff2", "handoff2"), f"Second flight → {F['orbit']}", "This time it climbs all the way,"),
    ("A8", 8, "app", ("act1", "deploy2", "landed2"), "Orbit · payload deployed", "and JIMIN-1 is in orbit."),
    ("A9", 7, "app", ("act1", "disp", "end"), "500 scattered flights · forecast winds aloft · land check",
     "Five hundred scattered flights, with the forecast winds aloft, show where stage one can land: at sea."),
    ("B1", 9, "app", ("act2", "start", "hour"), "Outlined hours: the orbit crosses the equator at 10:30 local (LTDN) · Open-Meteo · NOAA Kp",
     "When to launch? For an imaging satellite, the launch time sets the local time it passes over the ground."),
    ("B2", 10, "app", ("act2", "hour", "team"), "Launch hour chosen for the film after screening the real forecast and catalog · LCOLA",
     f"Every minute of that hour is screened against the catalog: at {F['worst_t']}, it would pass {F['worst_km']} kilometres from {F['worst_obj']}."),
    ("B3", 13, "app", ("act2", "team", "end"), "Notes → rules with exact quotes (Nemotron 3 Super) · votes · director: Nemotron 3 Ultra",
     f"The range notice and the customer's request, in Korean, become checked rules. Three roles vote, and Nemotron 3 Ultra calls go for {F['liftoff']}."),
    ("C1", 10, "app", ("act3", "start", "assess"), "TLE from the insertion state · photo passes over Seoul marked",
     "Launch. JIMIN-1 joins the catalog, and CRIC marks when it can photograph Seoul."),
    ("C2", 14, "app", ("act3", "assess", "decision"), f"Close approach: {F['other']} · the CRIC pipeline, live (time-lapse)",
     "A close approach. The same CRIC pipeline takes over: collision probability, verified burn options, a cited "
     "dossier, three votes, a validator. On public data, it asks for precise data."),
    ("C3", 8, "app", ("act3", "precise", "encounter"), "Precise ephemeris: decided again · no propellant spent",
     "The precise ephemeris arrives, and the risk is gone."),
    ("C4", 8, "app", ("act3", "encounter", "encounter_end"), "The pass in slow motion · positions from SGP4",
     f"Here is that pass: {F['miss_km']} kilometres, at {F['vrel']} kilometres a second."),
    ("C5", 5, "app", ("act3", "encounter_end", "end"), "Jimin approves · logged, hash-chained · nothing is sent to a spacecraft",
     "And Jimin approves."),
    ("J14", 9, "card", None, "Code for what can be checked.\nNemotron for what must be read.\nPeople for what must be decided.",
     "From a garage rocket to a satellite that is looked after. Code for what can be checked, Nemotron for what "
     "must be read, people for what must be decided."),
    ("J15", 4, "title", None, "CRIC Mission Studio\nNVIDIA Nemotron on Nebius Token Factory · Open-Meteo · NOAA · CelesTrak\n"
                             "youngjun-choi-kr.github.io/cric", ""),
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

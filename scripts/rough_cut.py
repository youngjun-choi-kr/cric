"""Assemble the demo film's rough cut from rendered cinema shots, cards and labelled placeholders.

    # 1. render the cinema shots (local server running):
    #    web/?cinema=1&record=<shot>&fps=30&w=1920&h=1080  for overview-push, find-pair, go-to-burn, pass-and-pullback
    # 2. build the cut:
    .venv/bin/python scripts/rough_cut.py

Output: data/film/rough_cut.mp4 (1080p30, captions burned in) and data/film/rough_cut.srt (voice-over as
subtitles, for timing the read). APP shots use captures from scripts/capture_app.py (data/film/app_<pair>_<preset>.mp4),
stretched to fit; a missing capture leaves a placeholder slate naming it. No music or voice: the team adds those.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRAMES = ROOT / "data" / "frames"
OUT = ROOT / "data" / "film"
FPS, W, H = 30, 1920, 1080
FONT = "/System/Library/Fonts/Supplemental/Arial.ttf"
BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
BG = "0x060a14"

# (id, seconds, kind, source, on-screen caption, voice-over). Mirrors the film plan's shot list (draft 2).
SHOTS = [
    ("S1", 8, "cine", "overview-push", "18,806 tracked objects. Real orbits.", ""),
    ("S2", 8, "cine", "find-pair", "EOS-08 · SITRO-AIS 38 · 17 m on public data (± ~1 km)", ""),
    ("S3", 8, "card", None, "", "If you fly a small satellite, this is your night. Public data good to a kilometre. "
                                "One engineer. A few hours to decide."),
    ("S4", 6, "title", None, "CRIC\nCosmic Response & Intelligence Console",
     "This is CRIC. It does the work, and it knows when to ask."),
    ("S5", 14, "app", "app_60454-61786_imaging", "NOAA Kp → MSIS drag · Nemotron 3.5 Super VL (context)",
     "It starts with the Sun. NVIDIA Nemotron 3.5 Super VL describes today's solar image for the operator; "
     "NOAA's numbers, run through NASA's thermosphere model, set the physics."),
    ("S6", 13, "app", "app_60454-61786_imaging", "Pc (simulated precise data) · verified burns",
     "The physics is code. Real orbits, a collision probability, and burn options that an optimiser has already "
     "checked against every other object in the catalog."),
    ("S7", 17, "app", "app_60454-61786_imaging", "Tavily Search + Extract · quote verified",
     "Can the other satellite move? Tavily searches the web. Nemotron 3 Super writes a dossier where every fact "
     "carries a source and an exact quote, and our code checks each one. Tonight, nobody can confirm it."),
    ("S8", 20, "app", "app_60454-61786_imaging", "Nemotron 3 Super × 3 votes · coordinate, burn O2",
     "Then three independent Nemotron 3 Super runs vote. They read what a rule engine can't: a disaster-response "
     "imaging pass rules out the cheapest burn, so they plan the later one and tell the other operator first."),
    ("S9a", 6, "app", "app_60454-61786_imaging", "Validator · routes agree · approved by a person",
     "Code checks every decision, a second route in code must agree, and a person approves."),
    ("S9b", 10, "cine", "go-to-burn", "Flown with errors: conjunction cleared · Nebius Token Factory",
     "The burn is flown in simulation with real-world errors and checked again. Every model runs on Nebius "
     "Token Factory."),
    ("S10a", 13, "app", "app_27844-37920_quiet", "CUTE-1 (university CubeSat) · Fengyun-1C debris · public data: red",
     "A university CubeSat and debris from the 2007 anti-satellite test. On public data it looks dangerous, and "
     "nobody can confirm the satellite can move. CRIC tells a person and asks for precise data."),
    ("S10b", 7, "app", "app_27844-37920_precise", "Precise data: Pc ≈ 1e-8 · monitor, no burn",
     "With precise data, the risk is gone. Nothing to do, and nothing spent."),
    ("S11", 15, "app", "app_60454-61786_korean_note", "Held-out 2: coded policy 5/8 · Nemotron 8/8",
     "Some of what matters lives only in an operator's note: Korean, Japanese, Spanish, ops jargon. On a held-out "
     "set written before any model saw it, a coded policy missed three of eight situations. Nemotron read all "
     "eight."),
    ("S12", 20, "card", None, "SOCRATES: 99.7% of encounters found\nPc matches NASA CARA test cases\n138 test runs · 0 burns past the checks\nCode · Nemotron · People",
     "Checked against an independent catalog and NASA's own test cases. Answers fixed before we ran them; two "
     "routes must agree, or a person decides: no burn got past the checks. "
     "Code for what can be checked. Nemotron for what must be read. People for what must be decided."),
    ("S13", 10, "cine", "pass-and-pullback", "Predicted miss: 17 m → 2 km", ""),
    ("S14", 4, "title", None, "CRIC\nBuilt with NVIDIA Nemotron on Nebius Token Factory · Tavily\ngithub.com/youngjun-choi-kr/cric", ""),
]


def _txt(tmp: Path, name: str, text: str) -> str:
    p = tmp / f"{name}.txt"
    p.write_text(text)
    return str(p).replace(":", r"\:")


def _caption_filter(tmp: Path, sid: str, caption: str) -> str:
    if not caption:
        return ""
    return (f",drawtext=expansion=none:fontfile='{BOLD}':textfile='{_txt(tmp, sid + '_cap', caption)}':fontcolor=white:fontsize=40:"
            f"x=(w-text_w)/2:y=h-150:box=1:boxcolor=black@0.45:boxborderw=18:line_spacing=10")


def _duration(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def segment(tmp: Path, sid: str, seconds: float, kind: str, source: str | None, caption: str, vo: str,
            group: tuple[float, float] = (0.0, 0.0)) -> Path:
    """group = (offset of this shot inside a run of shots sharing one capture, total length of that run)."""
    out = tmp / f"{sid}.mp4"
    enc = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-r", str(FPS), "-an"]
    capture = OUT / f"{source}.mp4" if kind == "app" and source else None
    if capture and capture.exists():
        offset, total = group
        stretch = total / _duration(capture)
        vf = (f"setpts={stretch:.4f}*PTS,fps={FPS},trim=start={offset}:duration={seconds},setpts=PTS-STARTPTS,"
              f"scale={W}:{H}" + _caption_filter(tmp, sid, caption))
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(capture), "-vf", vf, *enc, str(out)], check=True)
        return out
    if kind == "cine":
        frames = FRAMES / source
        if not frames.exists() or not any(frames.glob("*.png")):
            raise SystemExit(f"missing frames for {source}: render web/?cinema=1&record={source}&fps={FPS}&w={W}&h={H}")
        vf = f"scale={W}:{H},fade=t=in:st=0:d=0.4,fade=t=out:st={seconds - 0.4}:d=0.4" + _caption_filter(tmp, sid, caption)
        cmd = ["-framerate", str(FPS), "-i", str(frames / "%05d.png"), "-t", str(seconds), "-vf", vf]
    else:
        if kind == "title":
            lines = caption.split("\n")
            vf = (f"drawtext=expansion=none:fontfile='{BOLD}':textfile='{_txt(tmp, sid + '_t', lines[0])}':fontcolor=white:fontsize=120:"
                  f"x=(w-text_w)/2:y=h/2-140")
            if len(lines) > 1:
                vf += (f",drawtext=expansion=none:fontfile='{FONT}':textfile='{_txt(tmp, sid + '_s', chr(10).join(lines[1:]))}':"
                       f"fontcolor=0x8196c4:fontsize=40:x=(w-text_w)/2:y=h/2+20:line_spacing=14")
        elif kind == "card":
            body = caption or vo
            vf = (f"drawtext=expansion=none:fontfile='{FONT}':textfile='{_txt(tmp, sid + '_b', _wrap(body, 52))}':fontcolor=0xdbe6ff:"
                  f"fontsize=52:x=(w-text_w)/2:y=(h-text_h)/2:line_spacing=18")
        else:  # app placeholder: what to capture, and the caption it will carry
            vf = (f"drawtext=expansion=none:fontfile='{BOLD}':textfile='{_txt(tmp, sid + '_h', 'APP · ' + sid)}':fontcolor=0x4fe3a1:"
                  f"fontsize=44:x=120:y=140,"
                  f"drawtext=expansion=none:fontfile='{FONT}':textfile='{_txt(tmp, sid + '_s', 'Capture: ' + source)}':fontcolor=0xdbe6ff:"
                  f"fontsize=48:x=120:y=230,"
                  f"drawtext=expansion=none:fontfile='{FONT}':textfile='{_txt(tmp, sid + '_v', _wrap(vo, 70))}':fontcolor=0x8196c4:"
                  f"fontsize=34:x=120:y=360:line_spacing=12") + _caption_filter(tmp, sid, caption)
        vf += f",fade=t=in:st=0:d=0.3,fade=t=out:st={seconds - 0.3}:d=0.3"
        cmd = ["-f", "lavfi", "-i", f"color=c={BG}:s={W}x{H}:d={seconds}:r={FPS}", "-vf", vf]
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *cmd, *enc, str(out)], check=True)
    return out


def _wrap(text: str, width: int) -> str:
    """Word-wrap each paragraph; explicit line breaks are kept."""
    out = []
    for para in text.split("\n"):
        line = ""
        for word in para.split():
            if len(line) + len(word) + 1 > width:
                out.append(line)
                line = word
            else:
                line = f"{line} {word}".strip()
        out.append(line)
    return "\n".join(out)


def _ts(t: float) -> str:
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(round((s % 1) * 1000)):03d}"


def scratch_vo() -> None:
    """Timing aid only: macOS `say` reads each line at the start of its shot; reports lines that overrun.
    Writes data/film/rough_cut_scratch_vo.mp4. The real narration is recorded by the team."""
    t, inputs, filters, report = 0.0, [], [], []
    with tempfile.TemporaryDirectory() as d:
        for sid, sec, _, _, _, vo in SHOTS:
            if vo:
                wav = Path(d) / f"{sid}.aiff"
                subprocess.run(["say", "-v", "Samantha", "-r", "165", "-o", str(wav), vo], check=True)
                dur = _duration(wav)
                report.append((sid, round(dur, 1), sec, "OVERRUN" if dur > sec - 0.3 else ""))
                inputs += ["-i", str(wav)]
                filters.append(f"[{len(inputs) // 2}:a]adelay={int((t + 0.3) * 1000)}|{int((t + 0.3) * 1000)}[a{sid}]")
            t += sec
        labels = "".join(f"[a{sid}]" for sid, *_ in SHOTS if _[-1])
        mix = ";".join(filters) + f";{labels}amix=inputs={len(filters)}:normalize=0[vo]"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(OUT / "rough_cut.mp4"), *inputs,
                        "-filter_complex", mix, "-map", "0:v", "-map", "[vo]", "-c:v", "copy", "-c:a", "aac",
                        "-shortest", str(OUT / "rough_cut_scratch_vo.mp4")], check=True)
    for r in report:
        print(f"  {r[0]:5} read {r[1]:5}s in a {r[2]}s shot {r[3]}")


def main() -> None:
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg not found")
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        groups, i = {}, 0
        while i < len(SHOTS):                      # runs of consecutive shots that share one capture
            j = i
            while j + 1 < len(SHOTS) and SHOTS[j + 1][2] == "app" and SHOTS[j + 1][3] == SHOTS[i][3]:
                j += 1
            total = sum(x[1] for x in SHOTS[i:j + 1])
            for k in range(i, j + 1):
                groups[k] = (sum(x[1] for x in SHOTS[i:k]), total)
            i = j + 1
        parts = [segment(tmp, *s, group=groups[n]) for n, s in enumerate(SHOTS)]
        (tmp / "list.txt").write_text("".join(f"file '{p}'\n" for p in parts))
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(tmp / "list.txt"),
                        "-c", "copy", str(OUT / "rough_cut.mp4")], check=True)
    t, srt = 0.0, []
    for i, (sid, sec, _, _, _, vo) in enumerate(SHOTS):
        if vo:
            srt.append(f"{len(srt) + 1}\n{_ts(t + 0.3)} --> {_ts(t + sec - 0.3)}\n{vo}\n")
        t += sec
    (OUT / "rough_cut.srt").write_text("\n".join(srt))
    if "--scratch-vo" in sys.argv:
        scratch_vo()
    words = sum(len(s[5].split()) for s in SHOTS)
    print(f"{OUT / 'rough_cut.mp4'}  {t:.0f} s, {len(SHOTS)} shots, voice-over {words} words "
          f"({words / t:.1f} words/s; aim for <= 2.6)")


if __name__ == "__main__":
    main()

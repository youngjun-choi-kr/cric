"""Assemble the demo film's rough cut from rendered cinema shots, cards and labelled placeholders.

    # 1. render the cinema shots (local server running):
    #    web/?cinema=1&record=<shot>&fps=30&w=1920&h=1080  for overview-push, find-pair, go-to-burn, pass-and-pullback
    # 2. build the cut:
    .venv/bin/python scripts/rough_cut.py

Output: data/film/rough_cut.mp4 (1080p30, captions burned in) and data/film/rough_cut.srt (voice-over as
subtitles, for timing the read). APP shots are placeholders naming the recording to capture; replace them with
OBS captures of the console replaying the golden runs. No music or voice: the team adds those.
"""

from __future__ import annotations

import shutil
import subprocess
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
    ("S5", 12, "app", "imaging run: space-weather step", "NOAA Kp · Nemotron 3.5 Super VL (context)",
     "It starts with the Sun. NOAA's numbers set the physics; NVIDIA Nemotron 3.5 Super VL reads today's solar "
     "image as a second opinion, and flags when the two disagree."),
    ("S6", 13, "app", "imaging run: risk and verified options", "Pc (simulated precise data) · verified burns",
     "The physics is code. Real orbits, a collision probability, and burn options that an optimiser has already "
     "checked against every other object in the catalog."),
    ("S7", 17, "app", "imaging run: dossiers, Tavily sources, quote", "Tavily Search + Extract · quote verified",
     "Can the other satellite move? Tavily searches the web. Nemotron 3 Super writes a dossier where every fact "
     "carries a source and an exact quote, and our code checks each one. Tonight, nobody can confirm it."),
    ("S8", 23, "app", "imaging run: decision card, 3 votes", "Nemotron 3 Super × 3 votes · MANEUVER O2",
     "Then three independent Nemotron 3 Super runs vote. They read what a rule engine can't: a disaster-response "
     "imaging pass rules out the cheapest burn, so they take the later one."),
    ("S9a", 5, "app", "imaging run: validator accepted, Approve pressed", "Safety validator · approved by a person",
     "A safety validator, written in code, checks every decision, and a person approves it."),
    ("S9b", 10, "cine", "go-to-burn", "Nebius Token Factory",
     "When a person has to act, Nemotron 3 Ultra writes the brief. Every model runs on Nebius Token Factory."),
    ("S10", 20, "app", "SDO May 2024 frames + storm_2024 run", "May 2024 · G5 · Kp 9 · 3 of 3 burns stopped (eval)",
     "We replayed real storms, researched with Tavily. In twenty runs the model tried to burn on storm-degraded "
     "data three times. All three times, code said: get better data first."),
    ("S11", 15, "app", "korean_note run (or IRIDIUM 149 quiet → precise)", "Held-out 2: policy 5/8 · Nemotron + validator 40/40",
     "Some of what matters lives only in an operator's note: Korean, Japanese, Spanish, ops jargon. On a held-out "
     "set written before any model saw it, a coded policy missed three of eight. Nemotron with the validator: "
     "forty runs out of forty."),
    ("S12", 20, "card", None, "0 policy-violating burns on held-out\nCode · Nemotron · People",
     "Answers fixed before we ran them. Code for what can be checked. Nemotron for what must be read. People for "
     "what must be decided."),
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
    return (f",drawtext=fontfile='{BOLD}':textfile='{_txt(tmp, sid + '_cap', caption)}':fontcolor=white:fontsize=40:"
            f"x=(w-text_w)/2:y=h-150:box=1:boxcolor=black@0.45:boxborderw=18:line_spacing=10")


def segment(tmp: Path, sid: str, seconds: float, kind: str, source: str | None, caption: str, vo: str) -> Path:
    out = tmp / f"{sid}.mp4"
    enc = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-r", str(FPS), "-an"]
    if kind == "cine":
        frames = FRAMES / source
        if not frames.exists() or not any(frames.glob("*.png")):
            raise SystemExit(f"missing frames for {source}: render web/?cinema=1&record={source}&fps={FPS}&w={W}&h={H}")
        vf = f"scale={W}:{H},fade=t=in:st=0:d=0.4,fade=t=out:st={seconds - 0.4}:d=0.4" + _caption_filter(tmp, sid, caption)
        cmd = ["-framerate", str(FPS), "-i", str(frames / "%05d.png"), "-t", str(seconds), "-vf", vf]
    else:
        if kind == "title":
            lines = caption.split("\n")
            vf = (f"drawtext=fontfile='{BOLD}':textfile='{_txt(tmp, sid + '_t', lines[0])}':fontcolor=white:fontsize=120:"
                  f"x=(w-text_w)/2:y=h/2-140")
            if len(lines) > 1:
                vf += (f",drawtext=fontfile='{FONT}':textfile='{_txt(tmp, sid + '_s', chr(10).join(lines[1:]))}':"
                       f"fontcolor=0x8196c4:fontsize=40:x=(w-text_w)/2:y=h/2+20:line_spacing=14")
        elif kind == "card":
            body = caption or vo
            vf = (f"drawtext=fontfile='{FONT}':textfile='{_txt(tmp, sid + '_b', _wrap(body, 52))}':fontcolor=0xdbe6ff:"
                  f"fontsize=52:x=(w-text_w)/2:y=(h-text_h)/2:line_spacing=18")
        else:  # app placeholder: what to capture, and the caption it will carry
            vf = (f"drawtext=fontfile='{BOLD}':textfile='{_txt(tmp, sid + '_h', 'APP · ' + sid)}':fontcolor=0x4fe3a1:"
                  f"fontsize=44:x=120:y=140,"
                  f"drawtext=fontfile='{FONT}':textfile='{_txt(tmp, sid + '_s', 'Capture: ' + source)}':fontcolor=0xdbe6ff:"
                  f"fontsize=48:x=120:y=230,"
                  f"drawtext=fontfile='{FONT}':textfile='{_txt(tmp, sid + '_v', _wrap(vo, 70))}':fontcolor=0x8196c4:"
                  f"fontsize=34:x=120:y=360:line_spacing=12") + _caption_filter(tmp, sid, caption)
        vf += f",fade=t=in:st=0:d=0.3,fade=t=out:st={seconds - 0.3}:d=0.3"
        cmd = ["-f", "lavfi", "-i", f"color=c={BG}:s={W}x{H}:d={seconds}:r={FPS}", "-vf", vf]
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *cmd, *enc, str(out)], check=True)
    return out


def _wrap(text: str, width: int) -> str:
    out, line = [], ""
    for word in text.split():
        if len(line) + len(word) + 1 > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    return "\n".join(out + [line])


def _ts(t: float) -> str:
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(round((s % 1) * 1000)):03d}"


def main() -> None:
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg not found")
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        parts = [segment(tmp, *s) for s in SHOTS]
        (tmp / "list.txt").write_text("".join(f"file '{p}'\n" for p in parts))
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(tmp / "list.txt"),
                        "-c", "copy", str(OUT / "rough_cut.mp4")], check=True)
    t, srt = 0.0, []
    for i, (sid, sec, _, _, _, vo) in enumerate(SHOTS):
        if vo:
            srt.append(f"{len(srt) + 1}\n{_ts(t + 0.3)} --> {_ts(t + sec - 0.3)}\n{vo}\n")
        t += sec
    (OUT / "rough_cut.srt").write_text("\n".join(srt))
    words = sum(len(s[5].split()) for s in SHOTS)
    print(f"{OUT / 'rough_cut.mp4'}  {t:.0f} s, {len(SHOTS)} shots, voice-over {words} words "
          f"({words / t:.1f} words/s; aim for <= 2.6)")


if __name__ == "__main__":
    main()

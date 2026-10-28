"""The film, cut to one continuous narration take (scripts/film_script.py): voice first, picture to the voice.

    .venv/bin/python scripts/film_cut.py voice            # ElevenLabs eleven_v3, one take + word timings (scribe)
    .venv/bin/python scripts/film_cut.py voice --draft    # macOS `say` stand-in, to check timing for free
    .venv/bin/python scripts/film_cut.py sound            # music bed and a few effects (ElevenLabs), optional
    .venv/bin/python scripts/film_cut.py cut              # picture + voice + subtitles + music -> data/film/film_v4.mp4

Each beat's picture runs from where its first words are spoken to where the next beat starts; its clips share that
time in proportion to their real length (sped up when needed, with a time-lapse badge). A zoom box punches in on the
part of the screen the voice is talking about; a big number says it again in large type. The launch has no words:
the take is opened there and the rocket gets a few seconds of its own.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import film_script as fs  # noqa: E402
from narrate import API, key, words  # noqa: E402
from rough_cut import BOLD, FONT, FPS, H, OUT, W, _duration, _txt, segment  # noqa: E402

VO = OUT / "vo2"
SOUND = OUT / "sound"
VOICE = "nPczCjzI2devNBz1zQrb"          # "Brian", a premade narration voice; --voice to change
MODEL = "eleven_v3"
LAUNCH_GAP = 7.0                        # seconds of rocket with no words
M = fs.M


# ---------------------------------------------------------------- voice
def voice(draft: bool, voice_id: str) -> None:
    VO.mkdir(parents=True, exist_ok=True)
    take = VO / ("draft.wav" if draft else "take.mp3")
    if draft:
        return draft_voice(take)
    else:
        r = requests.post(f"{API}/text-to-speech/{voice_id}?output_format=mp3_44100_128", timeout=300,
                          headers={"xi-api-key": key(), "Content-Type": "application/json"},
                          json={"text": fs.full_text(), "model_id": MODEL,
                                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}})
        if not r.ok:
            raise SystemExit(f"text-to-speech failed: HTTP {r.status_code} {r.text[:300]}")
        take.write_bytes(r.content)
    with take.open("rb") as f:
        r = requests.post(f"{API}/speech-to-text", timeout=300, headers={"xi-api-key": key()},
                          data={"model_id": "scribe_v1", "language_code": "en", "timestamps_granularity": "word"},
                          files={"file": (take.name, f, "audio/mpeg")})
    if not r.ok:
        raise SystemExit(f"speech-to-text failed: HTTP {r.status_code} {r.text[:300]}")
    stt = r.json()
    (VO / "words.json").write_text(json.dumps({"take": take.name, "text": stt.get("text"),
                                               "words": [w for w in stt.get("words", []) if w.get("type") == "word"]}, indent=1))
    beats = align()
    print(f"{take}  {_duration(take):.1f} s")
    for b in beats:
        print(f"  {b['id']:8} {b['start'] if b['start'] is not None else '-':>6}  {b['heard'][:60]}")


def draft_voice(take: Path) -> None:
    """Free stand-in for timing: macOS `say`, one file per beat, word times spread evenly within each beat."""
    with tempfile.TemporaryDirectory() as d:
        parts, ws, t = [], [], 0.0
        for k, (bid, text, _) in enumerate(fs.BEATS):
            if not text:
                continue
            f = Path(d) / f"{k}.wav"
            subprocess.run(["say", "-v", "Samantha", "-r", "170", "--data-format=LEI16@44100", "-o", str(f),
                            fs.plain(fs.spoken(text))], check=True)
            dur = _duration(f)
            toks = fs.plain(fs.spoken(text)).split()
            ws += [{"text": w, "start": t + dur * i / len(toks), "end": t + dur * (i + 1) / len(toks)} for i, w in enumerate(toks)]
            parts.append(f)
            t += dur + 0.45
        gap = Path(d) / "gap.wav"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "0.45", gap], check=True)
        (Path(d) / "l.txt").write_text("".join(f"file '{p}'\nfile '{gap}'\n" for p in parts))
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(Path(d) / "l.txt"),
                        str(take)], check=True)
    (VO / "words.json").write_text(json.dumps({"take": take.name, "text": "", "words": ws}, indent=1))
    for b in align():
        print(f"  {b['id']:8} {b['start'] if b['start'] is not None else '-':>6}  {b['heard'][:60]}")
    print(take, f"{_duration(take):.1f} s (draft)")


def align() -> list[dict]:
    """Where each beat's first words are spoken in the take (in order, tolerant of a misheard word)."""
    d = json.loads((VO / "words.json").read_text())
    heard = [(w, x["start"], x["end"]) for x in d["words"] for w in words(x["text"])]
    out, i = [], 0
    for bid, text, _ in fs.BEATS:
        if not text:
            out.append({"id": bid, "start": None, "heard": ""})
            continue
        want = words(fs.spoken(fs.plain(text)))[:4]
        best = None
        for j in range(i, len(heard)):
            hits = sum(1 for k, w in enumerate(want) if j + k < len(heard) and heard[j + k][0] == w)
            if hits >= min(3, len(want)):
                best = j
                break
        if best is None:
            raise SystemExit(f"beat {bid!r} not found in the take: check {VO / 'words.json'}")
        out.append({"id": bid, "start": heard[best][1], "heard": " ".join(h[0] for h in heard[best:best + 8])})
        i = best + len(want)
    end = max(x["end"] for x in d["words"])
    for k, b in enumerate(out):
        nxt = next((x["start"] for x in out[k + 1:] if x["start"] is not None), end + 1.5)
        b["end"] = nxt
    (VO / "beats.json").write_text(json.dumps(out, indent=1))
    return out


# ---------------------------------------------------------------- sound
EFFECTS = {"rumble": ("Deep rocket engine ignition and liftoff roar, heavy low rumble, distant, cinematic", 7.0),
           "alert": ("Soft two-tone interface alert chime, calm, modern", 1.2),
           "whoosh": ("Gentle airy whoosh transition, subtle", 1.0)}
MUSIC_PROMPT = ("Instrumental underscore for a product film about launching a small satellite. Warm, hopeful, curious; "
                "soft piano and pulsing synth; builds gently at the launch, tense and quiet for a moment near the end, "
                "resolves warmly. No vocals. Steady and understated, sits under narration. Loopable.")


def sound(seconds: float) -> None:
    SOUND.mkdir(parents=True, exist_ok=True)
    h = {"xi-api-key": key(), "Content-Type": "application/json"}
    for name, (prompt, dur) in EFFECTS.items():
        f = SOUND / f"{name}.mp3"
        if not f.exists():
            r = requests.post(f"{API}/sound-generation", headers=h, timeout=300,
                              json={"text": prompt, "duration_seconds": dur, "prompt_influence": 0.5})
            print(name, r.status_code, "" if r.ok else r.text[:200])
            if r.ok:
                f.write_bytes(r.content)
    f = SOUND / "music.mp3"
    if not f.exists():
        r = requests.post(f"{API}/music", headers=h, timeout=600,
                          json={"prompt": MUSIC_PROMPT, "music_length_ms": 45000})   # a short bed, looped: fewer credits
        print("music", r.status_code, "" if r.ok else r.text[:200])
        if r.ok:
            f.write_bytes(r.content)


# ---------------------------------------------------------------- picture
def box169(b):
    """Grow a zoom box to 16:9 around its centre, inside the frame."""
    x, y, w, h = b
    if w / h < 16 / 9:
        w = h * 16 / 9
    else:
        h = w * 9 / 16
    w, h = min(w, W), min(h, H)
    cx, cy = b[0] + b[2] / 2, b[1] + b[3] / 2
    x, y = min(max(cx - w / 2, 0), W - w), min(max(cy - h / 2, 0), H - h)
    return int(x), int(y), int(w) // 2 * 2, int(h) // 2 * 2


def clip(tmp: Path, name: str, act: str, a, b, seconds: float, zoom, big) -> Path:
    out = tmp / f"{name}.mp4"
    if act.startswith("cine:"):          # rendered frames: slowed down to fill the beat when there are too few
        frames = OUT.parent / "frames" / act[5:]
        n = len(list(frames.glob("*.png")))
        rate = min(FPS, n / seconds)
        vf = (f"fps={FPS},scale={W}:{H},tpad=stop_mode=clone:stop_duration=1,trim=duration={seconds},"
              f"fade=t=in:st=0:d=0.4,fade=t=out:st={seconds - 0.4}:d=0.4")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-framerate", f"{rate:.4f}", "-i", str(frames / "%05d.png"),
                        "-vf", vf, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-r", str(FPS), "-an", str(out)], check=True)
        return out
    if act == "card":
        lines = big.split("\n")
        kind = "title" if len(lines[0]) < 30 else "card"
        return segment(tmp, name, seconds, kind, None, big if kind == "title" else "", big)
    cap = OUT / f"studio_{act}.mp4"
    marks = M[act]
    t0 = marks[a]
    t1 = _duration(cap) if b == "end" else marks[b]
    span = max(t1 - t0, 0.4)
    speed = span / seconds
    if speed > 1:
        vf = f"trim=start={t0}:end={t1},setpts=(PTS-STARTPTS)/{speed:.4f},fps={FPS}"
    else:
        vf = f"trim=start={t0}:end={t1},setpts=PTS-STARTPTS,fps={FPS},tpad=stop_mode=clone:stop_duration={seconds - span + 0.1:.2f}"
    vf += f",trim=duration={seconds}"
    if zoom:
        x, y, w, h = box169(zoom)
        vf += f",crop={w}:{h}:{x}:{y}"
    vf += f",scale={W}:{H}:flags=lanczos,fade=t=in:st=0:d=0.2,fade=t=out:st={seconds - 0.2}:d=0.2"
    if speed > 1.8:
        vf += (f",drawtext=expansion=none:fontfile='{BOLD}':text='time-lapse ×{speed:.0f}':fontcolor=0x9fc8ff@0.85:fontsize=26:"
               f"x=w-tw-36:y=36")
    if big:
        lines = big.split("\n")
        vf += (f",drawtext=expansion=none:fontfile='{BOLD}':textfile='{_txt(tmp, name + '_big', lines[0])}':fontcolor=white:"
               f"fontsize=104:x=80:y=h-330:box=1:boxcolor=0x03060e@0.72:boxborderw=26:alpha='min(1,max(0,(t-0.25)*4))'")
        if len(lines) > 1:
            vf += (f",drawtext=expansion=none:fontfile='{FONT}':textfile='{_txt(tmp, name + '_big2', lines[1])}':fontcolor=0xb9c8ea:"
                   f"fontsize=40:x=86:y=h-196:alpha='min(1,max(0,(t-0.35)*4))'")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(cap), "-vf", vf, "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-crf", "18", "-r", str(FPS), "-an", str(out)], check=True)
    return out


def timeline() -> list[dict]:
    """Beats on the final timeline: the take is opened before the beat after the launch, which gets LAUNCH_GAP."""
    beats = json.loads((VO / "beats.json").read_text())
    shift, out = 0.0, []
    for k, b in enumerate(beats):
        if b["start"] is None:           # the launch: starts where the previous beat ends, adds a gap
            start = beats[k - 1]["end"] + shift
            out.append({**b, "t0": start, "t1": start + LAUNCH_GAP, "cut_at": beats[k - 1]["end"]})
            shift += LAUNCH_GAP
            continue
        out.append({**b, "t0": b["start"] + shift, "t1": b["end"] + shift})
    out[0]["t0"] = 0.0
    return out


def subtitles(tl: list[dict]) -> list[tuple[float, float, str]]:
    """The script's own words (so names are spelled, not spoken), in short lines, timed by the take: each beat's
    lines share the span from its first to its last spoken word in proportion to their length."""
    d = json.loads((VO / "words.json").read_text())
    cut = next((b["cut_at"] for b in tl if "cut_at" in b), None)
    shift = lambda t: t + LAUNCH_GAP if cut is not None and t >= cut else t   # noqa: E731
    out = []
    for b, (bid, text, _) in zip(tl, fs.BEATS):
        if not text:
            continue
        raw0, raw1 = b["start"], b["end"]
        spoken_end = max((x["end"] for x in d["words"] if raw0 <= x["start"] < raw1), default=raw1)
        t0, t1 = shift(raw0), shift(spoken_end)
        lines, cur = [], ""
        for w in re.sub(r"\s+", " ", fs.plain(text)).strip().split(" "):
            if cur and len(cur) + len(w) > 44:
                lines.append(cur)
                cur = ""
            cur = (cur + " " + w).strip()
            if re.search(r"[.?!:]$", w) and len(cur) > 18:
                lines.append(cur)
                cur = ""
        if cur:
            lines.append(cur)
        total = sum(len(x) for x in lines)
        t = t0
        for x in lines:
            dt = (t1 - t0) * len(x) / total
            out.append((t, t + dt, x))
            t += dt
    return out


def cut() -> None:
    tl = timeline()
    total = tl[-1]["t1"] + 1.0
    take = VO / json.loads((VO / "words.json").read_text())["take"]
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        parts = []
        for b, (bid, _, pics) in zip(tl, fs.BEATS):
            dur = (b["t1"] - b["t0"]) + (1.0 if b is tl[-1] else 0.0)
            spans = []
            for act, a, z, *_ in pics:
                if act.startswith(("cine:", "card")):
                    spans.append(1.0)
                else:
                    t1 = _duration(OUT / f"studio_{act}.mp4") if z == "end" else M[act][z]
                    spans.append(max(t1 - M[act][a], 0.5) ** 0.5)       # long waits get less time than their length
            secs = [max(1.4, dur * s / sum(spans)) for s in spans]
            secs = [x * dur / sum(secs) for x in secs]        # exactly the beat's length, whatever the minimums did
            for k, ((act, a, z, zoom, big), s) in enumerate(zip(pics, secs)):
                c = clip(tmp, f"{bid}_{k}", act, a, z, round(s, 3), zoom, big)
                got = _duration(c)
                if abs(got - s) > 0.06:          # never let the picture drift from the voice
                    fixed = tmp / f"{bid}_{k}_fix.mp4"
                    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(c), "-vf",
                                    f"tpad=stop_mode=clone:stop_duration={max(s - got, 0) + 0.1:.2f},trim=duration={s:.3f}",
                                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-r", str(FPS), str(fixed)], check=True)
                    c = fixed
                parts.append(c)
        (tmp / "list.txt").write_text("".join(f"file '{p}'\n" for p in parts))
        pic = tmp / "pic.mp4"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(tmp / "list.txt"),
                        "-c", "copy", str(pic)], check=True)
        # subtitles burned in at the bottom (the film is often watched muted)
        subs = subtitles(tl)
        subs = [(a, min(b + 0.15, subs[k + 1][0] - 0.02) if k + 1 < len(subs) else b + 0.15, t) for k, (a, b, t) in enumerate(subs)]
        draw = ",".join(f"drawtext=expansion=none:fontfile='{FONT}':textfile='{_txt(tmp, f'sub{k}', t)}':fontcolor=white:fontsize=38:"
                        f"x=(w-text_w)/2:y=h-92:box=1:boxcolor=black@0.5:boxborderw=12:enable='between(t,{a:.2f},{b:.2f})'"
                        for k, (a, b, t) in enumerate(subs))
        subbed = tmp / "subbed.mp4"
        (tmp / "subs.txt").write_text(draw)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(pic), "-filter_script:v", str(tmp / "subs.txt"),
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", str(subbed)], check=True)
        audio(tmp, take, tl, total, subbed)
    (OUT / "film_v4.srt").write_text("\n".join(f"{k + 1}\n{_ts(a)} --> {_ts(b)}\n{t}\n" for k, (a, b, t) in enumerate(subs)))
    print(OUT / "film_v4.mp4", f"{total:.1f} s, {len(parts)} clips")


def _ts(t: float) -> str:
    h, m, s = int(t // 3600), int(t % 3600 // 60), t % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


def audio(tmp: Path, take: Path, tl: list[dict], total: float, video: Path) -> None:
    cut_at = next(b["cut_at"] for b in tl if "cut_at" in b)
    launch_t = next(b["t0"] for b in tl if b["start"] is None)
    ins = ["-i", str(take)]
    f = [f"[1:a]atrim=0:{cut_at:.3f},asetpts=PTS-STARTPTS[v1]",
         f"[1:a]atrim=start={cut_at:.3f},asetpts=PTS-STARTPTS,adelay={int(LAUNCH_GAP * 1000)}|{int(LAUNCH_GAP * 1000)}[v2]",
         "[v1][v2]concat=n=2:v=0:a=1,loudnorm=I=-16:TP=-2:LRA=9,apad[vo]"]
    mix, n = ["[vo]"], 2
    music = SOUND / "music_long.mp3"         # the 20 s bed crossfaded into a long one (no audible seam)
    if music.exists():                       # the bed ducks under the voice
        ins += ["-i", str(music)]
        f += [f"[{n}:a]volume=0.32,afade=t=in:d=2,afade=t=out:st={total - 3:.2f}:d=3,atrim=0:{total:.2f}[mus]",
              "[vo]asplit[vo_a][vo_k]", "[mus][vo_k]sidechaincompress=threshold=0.03:ratio=6:attack=40:release=500[bed]"]
        mix = ["[vo_a]", "[bed]"]
        n += 1
    sfx = {"rumble": [launch_t - 0.5, launch_t + LAUNCH_GAP + 18], "alert": [], "whoosh": [tl[2]["t0"] - 0.3]}
    burn = next((b for b in tl if b["id"] == "burn"), None)
    if burn:
        sfx["alert"].append(burn["t0"] + (burn["t1"] - burn["t0"]) * 0.55)
    for name, times in sfx.items():
        p = SOUND / f"{name}.mp3"
        for t in times if p.exists() else []:
            ins += ["-i", str(p)]
            vol = 0.55 if name == "rumble" else 0.4
            f.append(f"[{n}:a]volume={vol},adelay={int(t * 1000)}|{int(t * 1000)}[s{n}]")
            mix.append(f"[s{n}]")
            n += 1
    f.append("".join(mix) + f"amix=inputs={len(mix)}:normalize=0,atrim=0:{total:.2f},alimiter=limit=0.89[a]")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(video), *ins, "-filter_complex", ";".join(f),
                    "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.2f}",
                    str(OUT / "film_v4.mp4")], check=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["voice", "sound", "cut"])
    ap.add_argument("--draft", action="store_true")
    ap.add_argument("--voice", default=VOICE)
    a = ap.parse_args()
    if a.step == "voice":
        voice(a.draft, a.voice)
    elif a.step == "sound":
        tl = timeline()
        sound(tl[-1]["t1"] + 1.0)
    else:
        cut()


if __name__ == "__main__":
    main()

"""Narration for the Mission Studio film with ElevenLabs: one take per shot, checked, then mixed under the cut.

    ELEVENLABS_API_KEY in .env (never in code or chat)
    .venv/bin/python scripts/studio_cut.py            # the picture first: data/film/studio_cut.mp4
    .venv/bin/python scripts/narrate.py [--voice VOICE_ID] [--speed 1.0]

For each shot with a line it asks ElevenLabs text-to-speech for the line, then transcribes the take with
ElevenLabs speech-to-text and compares it with the script, so a misread word or number is caught. A take
longer than its shot is re-asked a little faster. Takes are cached by text and voice in data/film/vo/, so
re-running only pays for lines that changed. The mix: each line starts 0.3 s into its shot, loudness
normalized to -16 LUFS, written to data/film/studio_cut_vo.mp4. A report goes to data/film/vo/report.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from rough_cut import OUT, _duration  # noqa: E402

VO = OUT / "vo"
API = "https://api.elevenlabs.io/v1"
DEFAULT_VOICE = "pNInz6obpgDQGcFmaJgB"      # "Adam", a premade narration voice; pass --voice to change
MODEL = "eleven_multilingual_v2"
# how the narrator should say names the model may not know
SAY_AS = {"CRIC": "Crick", "JIMIN-1": "Jimin One", "Nemotron": "Nemmo-tron", "MUSAT-2": "Moo-sat Two"}


def key() -> str:
    k = os.environ.get("ELEVENLABS_API_KEY")
    if not k and (ROOT / ".env").exists():
        for line in (ROOT / ".env").read_text().splitlines():
            if line.startswith("ELEVENLABS_API_KEY="):
                k = line.split("=", 1)[1].strip().strip('"')
    if not k:
        raise SystemExit("ELEVENLABS_API_KEY is not set: add it to .env (it is gitignored)")
    return k


def spoken(text: str) -> str:
    for a, b in SAY_AS.items():
        text = text.replace(a, b)
    return text


def tts(text: str, voice: str, speed: float, out: Path) -> None:
    r = requests.post(f"{API}/text-to-speech/{voice}?output_format=mp3_44100_128", timeout=120,
                      headers={"xi-api-key": key(), "Content-Type": "application/json"},
                      json={"text": spoken(text), "model_id": MODEL,
                            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75, "style": 0.15, "speed": speed}})
    if not r.ok:
        raise SystemExit(f"text-to-speech failed: HTTP {r.status_code} {r.text[:300]}")
    out.write_bytes(r.content)


def stt(path: Path) -> str:
    with path.open("rb") as f:
        r = requests.post(f"{API}/speech-to-text", timeout=120, headers={"xi-api-key": key()},
                          data={"model_id": "scribe_v1", "language_code": "en"}, files={"file": (path.name, f, "audio/mpeg")})
    if not r.ok:
        return ""
    return r.json().get("text", "")


def words(s: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", s.lower())


def match(script: str, heard: str) -> float:
    """Share of the script's words found, in order, in what speech-to-text heard (names read as spelled)."""
    a, b = words(spoken(script)), words(heard)
    i = hits = 0
    for w in a:
        while i < len(b) and b[i] != w:
            i += 1
        if i < len(b):
            hits += 1
            i += 1
    return hits / max(len(a), 1)


def main() -> None:
    import studio_cut as sc

    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", default=os.environ.get("ELEVENLABS_VOICE_ID", DEFAULT_VOICE))
    ap.add_argument("--speed", type=float, default=1.0)
    a = ap.parse_args()
    VO.mkdir(parents=True, exist_ok=True)
    cut = OUT / "studio_cut.mp4"
    if not cut.exists():
        raise SystemExit("make the picture first: scripts/studio_cut.py")
    t, takes, report = 0.0, [], []
    for sid, sec, *_, line in sc.SHOTS:
        if line:
            speed = a.speed
            for attempt in range(3):
                h = hashlib.sha1(f"{a.voice}|{speed:.2f}|{line}".encode()).hexdigest()[:12]
                f = VO / f"{sid}_{h}.mp3"
                if not f.exists():
                    tts(line, a.voice, speed, f)
                dur = _duration(f)
                if dur <= sec - 0.4 or speed >= 1.15:
                    break
                speed = round(min(1.15, speed * (sec - 0.4) / dur + 0.02), 2)   # a little faster to fit the shot
            heard = stt(f)
            m = match(line, heard) if heard else None
            report.append({"shot": sid, "line": line, "file": f.name, "seconds": round(dur, 2), "shot_seconds": sec,
                           "speed": speed, "fits": dur <= sec - 0.3, "heard": heard, "match": None if m is None else round(m, 2)})
            print(f"  {sid:4} {dur:4.1f}/{sec}s speed {speed:.2f} match {'-' if m is None else f'{m:.0%}'}"
                  f"{'  OVERRUN' if dur > sec - 0.3 else ''}{'  CHECK: ' + heard if m is not None and m < 0.85 else ''}", flush=True)
            takes.append((t + 0.3, f))
        t += sec
    (VO / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
    inputs, filters = [], []
    for k, (start, f) in enumerate(takes):
        inputs += ["-i", str(f)]
        ms = int(start * 1000)
        filters.append(f"[{k + 1}:a]adelay={ms}|{ms}[v{k}]")
    mix = ";".join(filters) + "".join(f"[v{k}]" for k in range(len(takes))) + \
        f"amix=inputs={len(takes)}:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11[vo]"
    out = OUT / "studio_cut_vo.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(cut), *inputs, "-filter_complex", mix,
                    "-map", "0:v", "-map", "[vo]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", str(out)], check=True)
    print(out, f"{len(takes)} lines", "report:", VO / "report.json")


if __name__ == "__main__":
    main()

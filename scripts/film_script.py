"""The film's narration as one continuous take, and what the picture shows under each part of it.

Written to be spoken, then the picture is cut to the voice (scripts/film_cut.py), not the other way round. Each
beat's text is read in one ElevenLabs take with the rest; speech-to-text word timings find where each beat starts.
Numbers come from data/film/studio_markers.json, so the voice says what the recorded run showed.

Audio tags in [brackets] steer the expressive model (eleven_v3) and are not spoken.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
M = json.loads((ROOT / "data" / "film" / "studio_markers.json").read_text())


def _facts() -> dict:
    worst = M["hour"]["worst"]
    b = (M.get("burn_facts") or {}).get("first") or {}
    w, alt = b.get("worst") or {}, b.get("alternative") or {}
    miss = w.get("miss_distance_km", 0)
    return {"worst_km": f"{worst['closest_km']:.1f}", "worst_t": worst["liftoff_utc"][11:16],
            "liftoff": (M["act2_facts"]["liftoff"] or "")[11:16], "burn_km": f"{miss:.1f}",
            "shift": abs(alt.get("shift_min", 5)), "later": alt.get("shift_min", 5) > 0,
            "first": M["act3_facts"].get("action"), "second": M["act3_facts"].get("action2")}


F = _facts()
SAY_NUM = {"2.7": "two point seven", "1.8": "one point eight", "2.5": "two and a half"}
SAY_TIME = {"02:49": "two forty-nine", "02:46": "two forty-six", "02:26": "two twenty-six"}

# (beat id, narration, picture: [(act or "cine:<shot>" or "card", from marker, to marker, zoom box, big text)])
# zoom box: (x, y, w, h) in the 1920x1080 capture, None for the full frame; big text: a number shown large
BEATS = [
    ("hook", "There are almost nineteen thousand objects in orbit right now. "
             "Jimin's university team wants to add one more: a five-kilogram camera satellite they've spent two years building.",
     [("cine:overview-push", None, None, None, None)]),
    ("problem", "[thoughtful] But they don't have a flight dynamics department. They have a group chat, a launch notice in Korean... "
                "and a lot of questions.",
     [("act2", "start", "heat", (1340, 60, 560, 520), None)]),
    ("title", "This is CRIC. It rehearses the whole mission with them, and shows its work.",
     [("card", None, None, None, "CRIC Mission Studio\nMission rehearsal for small satellite teams")]),
    ("rocket", "First question: can this small rocket actually get them there? "
               "CRIC builds it from its own numbers, at true scale, and flies it.",
     [("act1", "start", "xray", None, None), ("act1", "target", "liftoff", None, None)]),
    ("launch", "", [("act1", "liftoff", "handoff", None, None)]),          # the launch speaks for itself: music and rumble
    ("fail", "[sighs] It doesn't make it. The satellite never gets high enough, and comes back down.",
     [("act1", "handoff", "advise", (1340, 60, 560, 420), "137 km\ntoo low to stay up")]),
    ("fix", "So CRIC looks for fixes, and test-flies every one before it suggests it. The answer: a bigger first stage.",
     [("act1", "advise", "apply", (1340, 60, 560, 620), None), ("act1", "apply", "fly2", None, "+30%\nfirst stage")]),
    ("orbit", "[excited] Second try... and they're in orbit.",
     [("act1", "liftoff2", "handoff2", None, None), ("act1", "deploy2", "landed2", None, "502 × 543 km")]),
    ("sea", "Five hundred more test flights, with the forecast winds, show the spent stage falls safely into the sea.",
     [("act1", "disp", "end", (1340, 60, 560, 700), None)]),
    ("window", "Next: when to launch. The range notice, in Korean, closes the sea south of the pad for a few minutes. "
               "CRIC turns it into a rule it can check, word for word.",
     [("act2", "heat", "hour", (1340, 60, 560, 460), None)]),
    ("minute", f"Then it checks every launch minute against every tracked object. At {SAY_TIME.get(F['worst_t'], F['worst_t'])}, "
               f"they would pass {SAY_NUM.get(F['worst_km'], F['worst_km'])} kilometers from another satellite. "
               f"At {SAY_TIME.get(F['liftoff'], F['liftoff'])}, the sky is clear.",
     [("act2", "hour", "team", (0, 640, 1920, 440), f"{F['worst_t']} UTC → {F['worst_km']} km")]),
    ("vote", "Three AI launch officers vote, and Nemotron Ultra makes the call: go.",
     [("act2", "team", "end", (1340, 60, 560, 900), f"GO · {F['liftoff']} UTC")]),
    ("watch", "In orbit, CRIC keeps watch. Days later, something comes close. "
              + ("On public data, CRIC isn't sure... so it doesn't guess. It asks a person."
                 if F["first"] == "ESCALATE" else "CRIC works out the risk, and decides to keep watching."),
     [("act3", "start", "assess", None, None), ("act3", "assess", "decision", (1340, 60, 560, 900), None)]),
    ("precise", "Better orbit data arrives. The risk drops to almost zero. No fuel spent.",
     [("act3", "precise", "encounter", (1340, 60, 560, 900), None), ("act3", "encounter", "encounter_end", None, "2.5 km")]),
    ("burn", "Then Jimin wants sharper photos, and asks to lower the orbit. "
             "CRIC checks it against every tracked object... [pause] and says: not now. "
             f"That burn would pass {SAY_NUM.get(F['burn_km'], F['burn_km'])} kilometers from another satellite.",
     [("act3", "ctl", "burn", (0, 60, 560, 760), None), ("act3", "burn", "burn_checked", None, None),
      ("act3", "burn_checked", "alt", (0, 60, 560, 900), f"{F['burn_km']} km\nfrom another satellite")]),
    ("safer", f"{F['shift']} minutes {'later' if F['later'] else 'earlier'}, it's clear. Jimin approves. "
              "Every command is logged... and nothing moves without a person.",
     [("act3", "alt", "burn_approved", (0, 60, 560, 900), None), ("act3", "burn_approved", "end", None, None)]),
    ("close", "CRIC. Mission rehearsal for small satellite teams. It finds the risks and the rules to check. "
              "People make the calls.",
     [("card", None, None, None, "CRIC finds the risks and the rules to check.\nPeople make the calls.")]),
    ("credit", "Built with NVIDIA Nemotron, on Nebius.",
     [("card", None, None, None, "CRIC Mission Studio\nNVIDIA Nemotron on Nebius Token Factory · youngjun-choi-kr.github.io/cric\n"
                                 "Simulated flights; visuals illustrative. Launch hour picked after checking the live forecast.")]),
]

SAY_AS = {"CRIC": "Crick", "Nemotron": "Nemmo-tron"}


def spoken(text: str) -> str:
    for a, b in SAY_AS.items():
        text = re.sub(rf"\b{a}\b", b, text)
    return text


def full_text() -> str:
    """The whole narration for one take; beats are separated by a blank line (a breath)."""
    return "\n\n".join(spoken(t) for _, t, _ in BEATS if t)


def plain(text: str) -> str:
    return re.sub(r"\[[^\]]*\]", "", text)


if __name__ == "__main__":
    t = plain(full_text())
    print(t)
    print(f"\n{len(full_text())} characters, {len(t.split())} words, about {len(t.split()) / 2.5:.0f} s at a natural pace")

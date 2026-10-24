"""Nemotron decides what to do about a conjunction; it never does orbit maths.

Inputs are prepared by deterministic code: assumed-covariance Pc, verified burn
options, cited capability facts. What the model adds is judgement over context the
optimiser cannot read: operator policy and mission notes written in plain language,
the quality of the evidence, and whether to act now, wait for better data, or hand
the case to a person. The safety validator re-checks every decision in code.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

from ..config import MODEL_SUPER
from ..llm import Usage, structured_call

Action = Literal["MANEUVER", "REQUEST_PRECISE_DATA", "MONITOR", "COORDINATE", "ESCALATE"]

ACTIONS_HELP = {
    "MANEUVER": "execute one of the verified burn options (give option_id); our satellite is the actor",
    "REQUEST_PRECISE_DATA": "ask for precise ephemeris / a CDM before committing; give recheck_at_utc",
    "MONITOR": "no action now; re-assess at recheck_at_utc",
    "COORDINATE": "contact the other operator first (write message_to_other_operator); may name the option we intend",
    "ESCALATE": "hand to a human flight dynamics engineer; say exactly what is missing",
}


class Factor(BaseModel):
    factor: str = Field(description="short name, e.g. 'Pc red', 'other object cannot manoeuvre'")
    evidence: str = Field(description="the input field or quoted text this comes from")
    effect: str = Field(description="how it pushes the decision")


class Decision(BaseModel):
    action: Action
    # Pattern keeps guided decoding from leaking stray text into the id (seen once in the held-out eval).
    option_id: str | None = Field(None, pattern=r"^O[0-9]{1,2}$", description="id of a verified burn option, e.g. O1, or null")
    rationale: str = Field(min_length=20)
    key_factors: list[Factor] = Field(min_length=2)
    mission_conflicts: list[str] = Field(description="mission or policy constraints that ruled options out or in")
    message_to_other_operator: str | None = None
    recheck_at_utc: str | None = None
    confidence: float = Field(ge=0, le=1)
    unknowns: list[str]


SYSTEM = f"""You are the decision-support agent in a satellite operations console. You work for the operator of
OUR satellite and decide how to respond to one predicted close approach (conjunction).

What you are given, all prepared by deterministic code:
- conjunction geometry from public GP/TLE data (accuracy about 1 km), and a collision probability (Pc)
  computed from an ASSUMED covariance, with a dilution flag (true = poor knowledge may be hiding a higher Pc);
- capability of each object to manoeuvre, from cited sources (unknown means nobody could verify it);
- burn options already simulated and re-screened by physics; you may only pick from these by id;
- space weather, operator policy and mission notes in plain language.

You never compute orbits or invent numbers. Every factor must point to an input field or quote.

Actions: {json.dumps(ACTIONS_HELP, indent=1)}

Think like a flight dynamics engineer: who can actually move, is the risk real or an artefact of bad data,
is there time to get better data before the latest safe burn, do any options clash with mission notes or
policy, and does the other operator need to know before we move so we do not both manoeuvre into each other.
Return one JSON object."""


def decide(context: dict, *, model: str = MODEL_SUPER, usage: Usage | None = None) -> tuple[Decision, str]:
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": "Incident:\n" + json.dumps(context, indent=1, ensure_ascii=False)}]
    return structured_call(model, messages, Decision, usage=usage)

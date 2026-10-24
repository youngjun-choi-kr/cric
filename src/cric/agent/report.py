"""T4: Nemotron Ultra writes the human-facing document, only when a person must act.

COORDINATE -> a message to the other operator (what we see, what we intend, what we ask).
ESCALATE   -> a brief for the on-call flight dynamics engineer (what is known, what is
              missing, by when a decision is needed).
Ultra is the most expensive tier, so it runs at most once per incident and never on
routine outcomes. It only rewrites facts from the context; it does not decide.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

from ..config import MODEL_ULTRA
from ..llm import Usage, structured_call


class Report(BaseModel):
    kind: Literal["coordination_message", "escalation_brief"]
    subject: str = Field(min_length=10)
    body: str = Field(min_length=80, description="plain text, under 220 words")
    decision_deadline_utc: str | None = Field(None, description="latest time a decision or reply is needed")
    asks: list[str] = Field(min_length=1, description="concrete requests to the reader")
    facts_cited: list[str] = Field(min_length=2, description="input fields the body relies on")


SYSTEM = """You write one operational document for a satellite operations team. Use only facts present in the
incident context; quote numbers exactly as given. Whenever you give Pc, say how it was computed: "assumed
covariance" for TLE data, or "simulated precise ephemeris" when the risk assumption says SIMULATED. Be brief and
specific: times in UTC, NORAD IDs, Pc with its level, the burn option if any. No marketing language.
- coordination_message: addressed to the other satellite's operator. State the predicted conjunction, our
  intended action (or that we cannot manoeuvre), and ask them to confirm their plan so both do not move.
- escalation_brief: addressed to our on-call flight dynamics engineer. State why the agent could not act,
  what is missing, the options that physics has verified, and the deadline (latest feasible burn minus upload).
Return one JSON object."""


def write_report(ctx: dict, decision: dict, validation: dict, *, usage: Usage | None = None,
                 model: str = MODEL_ULTRA) -> Report | None:
    final = validation["final_action"]
    if final not in ("COORDINATE", "ESCALATE"):
        return None
    kind = "coordination_message" if final == "COORDINATE" else "escalation_brief"
    payload = {"document": kind, "final_action": final, "final_option_id": validation["final_option_id"],
               "validator_overrides": validation["overrides"], "agent_decision": decision, "incident": ctx}
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)}]
    report, _ = structured_call(model, messages, Report, usage=usage, max_tokens=12000)
    return report

"""Nemotron 3.5 Super VL reads the screenshots operators actually share: planning calendars, telemetry panels,
chat messages. The transcript joins the mission notes, so the same quoted-constraint extraction and code checks
apply to it; the operator sees the transcript next to the image.

This replaces the image job that did not hold up: a retrospective test showed the model's read of the Sun has no
forecasting signal (scripts/eval_vl.py), while reading text and status out of an operator's screen is measurable
(scripts/eval_screens.py).
"""

from __future__ import annotations

import base64

from pydantic import BaseModel, Field

from ..config import MODEL_VL
from ..llm import Usage, structured_call


class ScreenRead(BaseModel):
    kind: str = Field(description="what the screenshot shows, e.g. planning calendar, telemetry panel, chat")
    transcript: str = Field(description="the text and status values visible in the image, copied as written, "
                                        "times with their units; one item per line")
    affects_manoeuvres: str = Field(description="one sentence: what on this screen matters for a collision-avoidance "
                                                "burn tonight, or 'nothing'")


SYSTEM = """You transcribe a screenshot shared by a satellite operations team. Copy the visible text, times and
status values exactly as written (keep the original language). Do not interpret beyond the one-sentence
'affects_manoeuvres' field. If something is unreadable, write [unreadable]. Return one JSON object."""


def read_screen(image: bytes, *, mime: str = "image/png", usage: Usage | None = None) -> ScreenRead:
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": [
                    {"type": "text", "text": "Screenshot from the operations team:"},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64," + base64.b64encode(image).decode()}}]}]
    read, _ = structured_call(MODEL_VL, messages, ScreenRead, usage=usage, max_tokens=8000)
    return read


def as_note(read: ScreenRead) -> str:
    return f"[from a shared screenshot: {read.kind}] {read.transcript}"

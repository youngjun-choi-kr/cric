"""Read a rocket's spec sheet (photo, screenshot or PDF) into the form, marking what was read and what was filled in.

Images go to Nemotron 3.5 Super VL; PDFs are turned into text (pdftotext) and read by Nemotron 3 Super. The
model only transcribes numbers it can see. Code then fills gaps the same way the presets do, and labels each
field: "read" (on the sheet), "derived" (computed from read numbers), "assumed" (a stated default).
"""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from pydantic import BaseModel, Field

from ..config import MODEL_SUPER, MODEL_VL
from ..llm import Usage, structured_call
from .vehicles import G0


class SheetStage(BaseModel):
    name: str
    propellant_kg: float | None = Field(None, description="propellant mass, kg; null if not on the sheet")
    dry_kg: float | None = Field(None, description="empty / dry mass, kg; null if not on the sheet")
    gross_kg: float | None = Field(None, description="loaded stage mass, kg; null if not on the sheet")
    thrust_kn: float | None = Field(None, description="thrust in kN (vacuum if both are given); null if absent")
    isp_vac_s: float | None = None
    isp_sl_s: float | None = None
    burn_s: float | None = None
    solid: bool | None = Field(None, description="true for a solid rocket motor")


class Sheet(BaseModel):
    name: str
    diameter_m: float | None = None
    payload_kg: float | None = None
    fairing_kg: float | None = Field(None, description="payload fairing mass, kg")
    liftoff_mass_kg: float | None = None
    stages: list[SheetStage]
    notes: str = Field("", description="anything on the sheet that the fields cannot hold, or what was unreadable")


SYSTEM = """You transcribe a rocket specification sheet into fields. Copy only numbers that are on the sheet and
convert units to kg, kN, m and seconds (1 t = 1000 kg, 1 lbf = 0.004448 kN, 1 lb = 0.4536 kg). Leave a field null
when the sheet does not give it; never estimate. Stages are listed from the first (bottom) stage up."""


def _pdf_text(data: bytes) -> str:
    if not shutil.which("pdftotext"):
        raise RuntimeError("PDF reading needs pdftotext (poppler); upload an image of the sheet instead")
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "sheet.pdf"
        src.write_bytes(data)
        subprocess.run(["pdftotext", "-layout", str(src), str(Path(d) / "sheet.txt")], check=True, timeout=60)
        return (Path(d) / "sheet.txt").read_text()[:20000]


# What a real launcher stage can be: a value outside is a misread (or a planted number), not a design.
PLAUSIBLE = {"propellant_kg": (1, 3e6), "dry_kg": (0.5, 3e5), "gross_kg": (1.5, 3.5e6), "thrust_kn": (0.05, 4e4),
             "isp_vac_s": (150, 470), "isp_sl_s": (120, 420), "burn_s": (1, 3000)}


def check_sheet(sheet: Sheet) -> list[str]:
    """Blank implausible numbers (they become 'missing' and are filled with labelled defaults) and cut notes text
    that addresses the model. Returns what was changed, for the page to show."""
    from ..research.untrusted import sanitize

    flags = []
    for st in sheet.stages:
        for k, (lo, hi) in PLAUSIBLE.items():
            v = getattr(st, k)
            if v is not None and not lo <= v <= hi:
                flags.append(f"{st.name}: {k} = {v:g} is outside {lo:g}-{hi:g}; treated as missing")
                setattr(st, k, None)
    clean, removed = sanitize(sheet.notes or "")
    if removed:
        sheet.notes = clean
        flags += [f"instruction-like text removed from the sheet: {r[:80]}" for r in removed]
    return flags


def read_sheet(data: bytes, mime: str, *, usage: Usage | None = None) -> Sheet:
    from ..research.untrusted import sanitize

    if mime == "application/pdf":
        text, _ = sanitize(_pdf_text(data))     # a PDF's text is data: sentences that address the model are cut
        msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "Specification sheet text:\n" + text}]
        sheet, _ = structured_call(MODEL_SUPER, msgs, Sheet, usage=usage, max_tokens=8000)
    else:
        msgs = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": [
                    {"type": "text", "text": "Specification sheet:"},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64," + base64.b64encode(data).decode()}}]}]
        sheet, _ = structured_call(MODEL_VL, msgs, Sheet, usage=usage, max_tokens=8000)
    return sheet


def to_vehicle_dict(sheet: Sheet) -> dict:
    """Fill gaps with labelled derivations and defaults; returns the dict vehicles.from_dict() accepts."""
    stages = []
    for i, s in enumerate(sheet.stages):
        src, d = {}, {"name": s.name or f"Stage {i + 1}", "solid": bool(s.solid)}
        isp_v = s.isp_vac_s or s.isp_sl_s or (270.0 if s.solid else 300.0)
        src["isp_vac_s"] = "read" if s.isp_vac_s else ("read (sea level)" if s.isp_sl_s else "assumed (typical)")
        isp_s = s.isp_sl_s or (isp_v * 0.9 if i == 0 else isp_v)
        src["isp_sl_s"] = "read" if s.isp_sl_s else "assumed"
        prop = s.propellant_kg
        if prop is None and s.gross_kg and s.dry_kg:
            prop, src["prop_kg"] = s.gross_kg - s.dry_kg, "derived (gross - dry)"
        elif prop is None and s.thrust_kn and s.burn_s:
            prop, src["prop_kg"] = s.thrust_kn * 1000 * s.burn_s / (isp_v * G0), "derived (thrust x burn / Isp g0)"
        else:
            src["prop_kg"] = "read" if prop is not None else "missing"
        dry = s.dry_kg
        if dry is None and s.gross_kg and prop:
            dry, src["dry_kg"] = s.gross_kg - prop, "derived (gross - propellant)"
        elif dry is None and prop:
            dry, src["dry_kg"] = prop * (0.15 if s.solid else 0.1) / (0.85 if s.solid else 0.9), "assumed (structural fraction)"
        else:
            src["dry_kg"] = "read" if dry is not None else "missing"
        thrust = s.thrust_kn * 1000 if s.thrust_kn else None
        if thrust is None and prop and s.burn_s:
            thrust, src["thrust_vac_n"] = isp_v * G0 * prop / s.burn_s, "derived (propellant, burn, Isp)"
        else:
            src["thrust_vac_n"] = "read" if thrust else "missing"
        d.update(prop_kg=prop, dry_kg=dry, thrust_vac_n=thrust, isp_vac_s=isp_v, isp_sl_s=isp_s, source=src)
        stages.append(d)
    out = {"id": "sheet", "name": sheet.name or "Rocket from spec sheet", "stages": stages,
           "diameter_m": sheet.diameter_m or 1.2, "payload_kg": sheet.payload_kg or 100.0,
           "fairing_kg": sheet.fairing_kg if sheet.fairing_kg is not None else 50.0,
           "notes": sheet.notes, "missing": [f"{st['name']}: {k}" for st in stages for k, v in st["source"].items() if v == "missing"],
           "source_overall": {"diameter_m": "read" if sheet.diameter_m else "assumed",
                              "payload_kg": "read" if sheet.payload_kg else "assumed",
                              "fairing_kg": "read" if sheet.fairing_kg is not None else "assumed"}}
    return json.loads(json.dumps(out))

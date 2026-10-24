"""Shared helpers for the CRIC feasibility spike."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

load_dotenv(ROOT / ".env")

TOKEN_FACTORY_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"

# Model IDs from the Token Factory public catalog. Availability differs per
# account, so run 00_list_models.py first and override via .env if needed.
MODEL_SUPER = os.getenv("MODEL_SUPER", "nvidia/nemotron-3-super-120b-a12b")
MODEL_VL = os.getenv("MODEL_VL", "nvidia/Nemotron-3.5-Super-VL")
MODEL_NANO = os.getenv("MODEL_NANO", "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B")


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        sys.exit(f"[error] {name} is not set. Copy .env.example to .env and fill it in.")
    return value


def token_factory_client():
    from openai import OpenAI

    return OpenAI(base_url=TOKEN_FACTORY_BASE_URL, api_key=require_env("NEBIUS_API_KEY"))


def extract_json(text: str) -> dict:
    """Parse the first JSON object in a model reply (handles ```json fences and <think> blocks)."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if fenced:
        return json.loads(fenced.group(1))
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found in model reply")
    return json.loads(text[start : end + 1])


def structured_call(client, model: str, messages: list, schema_model, max_tokens: int = 16000):
    """Call a Nemotron model with the reply constrained to a pydantic schema.

    Nemotron reasons before answering and returns that separately as
    reasoning_content, so max_tokens has to cover reasoning plus the JSON.
    Returns (parsed, raw_content, reasoning, response).
    """
    last_error = None
    for attempt in range(2):  # replies occasionally run away; one retry is usually enough
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": schema_model.__name__, "schema": schema_model.model_json_schema()},
            },
            temperature=0.2,
            max_tokens=max_tokens,
        )
        msg = resp.choices[0].message
        raw = msg.content or ""
        reasoning = (msg.model_extra or {}).get("reasoning_content") or ""
        try:
            if resp.choices[0].finish_reason == "length":
                raise ValueError(f"reply truncated at max_tokens={max_tokens}")
            return schema_model.model_validate(extract_json(raw)), raw, reasoning, resp
        except ValueError as exc:  # pydantic ValidationError is a ValueError
            last_error = exc
            print(f"[retry] attempt {attempt + 1} failed: {str(exc)[:200]}")
    raise last_error


def save_json(name: str, payload) -> Path:
    path = DATA / name
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return path


def usage_line(resp) -> str:
    u = getattr(resp, "usage", None)
    if not u:
        return "usage: n/a"
    return f"usage: prompt={u.prompt_tokens} completion={u.completion_tokens}"

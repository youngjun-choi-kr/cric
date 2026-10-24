"""Token Factory client helpers: retries, schema-constrained replies, cost accounting."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from .config import PRICES, TOKEN_FACTORY_BASE_URL, require_env

_client = None
RETRY_WAITS_S = (0, 5, 15, 30, 60)  # Token Factory sometimes returns 503 for a minute or two


def client():
    global _client
    if _client is None:
        from openai import OpenAI

        _client = OpenAI(base_url=TOKEN_FACTORY_BASE_URL, api_key=require_env("NEBIUS_API_KEY"))
    return _client


@dataclass
class Usage:
    """Running token and cost totals for one incident or run."""

    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    by_model: dict = field(default_factory=dict)

    def add(self, model: str, resp) -> None:
        u = getattr(resp, "usage", None)
        tin, tout = (u.prompt_tokens, u.completion_tokens) if u else (0, 0)
        pin, pout = PRICES.get(model, (0.0, 0.0))
        cost = (tin * pin + tout * pout) / 1e6
        self.calls += 1
        self.tokens_in += tin
        self.tokens_out += tout
        self.cost_usd += cost
        m = self.by_model.setdefault(model, {"calls": 0, "cost_usd": 0.0})
        m["calls"] += 1
        m["cost_usd"] += cost

    def as_dict(self) -> dict:
        return {"calls": self.calls, "tokens_in": self.tokens_in, "tokens_out": self.tokens_out,
                "cost_usd": round(self.cost_usd, 5),
                "by_model": {k: {"calls": v["calls"], "cost_usd": round(v["cost_usd"], 5)} for k, v in self.by_model.items()}}


def chat(**kw):
    """chat.completions.create with backoff: Token Factory occasionally answers 503 for a moment."""
    import openai

    for attempt, wait in enumerate(RETRY_WAITS_S):
        time.sleep(wait)
        try:
            return client().chat.completions.create(**kw)
        except (openai.InternalServerError, openai.APIConnectionError, openai.RateLimitError, openai.APITimeoutError):
            if attempt == len(RETRY_WAITS_S) - 1:
                raise


def extract_json(text: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if fenced:
        return json.loads(fenced.group(1))
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found in model reply")
    return json.loads(text[start : end + 1])


def shape_of(schema_model) -> str:
    """Compact JSON skeleton of a pydantic model: field names with type hints, nested models expanded."""
    schema = schema_model.model_json_schema()
    defs = schema.get("$defs", {})

    def walk(node, depth=0):
        if "$ref" in node:
            return walk(defs[node["$ref"].split("/")[-1]], depth)
        for key in ("anyOf", "oneOf"):
            if key in node:
                opts = [o for o in node[key] if o.get("type") != "null"]
                return walk(opts[0], depth) if opts else None
        if "enum" in node:
            return "|".join(map(str, node["enum"]))
        t = node.get("type")
        if t == "object" and "properties" in node and depth < 6:
            return {k: walk(v, depth + 1) for k, v in node["properties"].items()}
        if t == "array":
            return [walk(node.get("items", {}), depth + 1)]
        return {"string": "<text>", "number": 0.0, "integer": 0, "boolean": False}.get(t, "<value>")

    return json.dumps(walk(schema), ensure_ascii=False)


def structured_call(model: str, messages: list, schema_model, *, usage: Usage | None = None,
                    max_tokens: int = 16000, temperature: float = 0.2):
    """Reply constrained to a pydantic schema. Returns (parsed, reasoning_text).

    Nemotron reasons first and returns that as reasoning_content, so max_tokens must
    cover reasoning plus JSON. A runaway or malformed reply is retried once.
    """
    # Nemotron plans its JSON while reasoning; if that plan differs from the schema, guided decoding fills
    # empty defaults (seen as "Placeholder Title" / all-unknown records). Showing the exact shape prevents it.
    hint = "\n\nReply with JSON of exactly this shape (types shown as placeholders):\n" + shape_of(schema_model)
    messages = [dict(m) for m in messages]
    if messages and messages[0]["role"] == "system":
        messages[0]["content"] = messages[0]["content"] + hint
    else:
        messages.insert(0, {"role": "system", "content": hint.strip()})
    last_error: Exception | None = None
    for _ in range(2):
        resp = chat(
            model=model,
            messages=messages,
            response_format={"type": "json_schema",
                             "json_schema": {"name": schema_model.__name__, "schema": schema_model.model_json_schema()}},
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if usage is not None:
            usage.add(model, resp)
        msg = resp.choices[0].message
        reasoning = (msg.model_extra or {}).get("reasoning_content") or ""
        try:
            if resp.choices[0].finish_reason == "length":
                raise ValueError(f"reply truncated at max_tokens={max_tokens}")
            return schema_model.model_validate(extract_json(msg.content or "")), reasoning
        except ValueError as exc:  # includes pydantic ValidationError
            last_error = exc
    raise last_error

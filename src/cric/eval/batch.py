"""Run an eval set as one Nebius Token Factory batch job instead of live calls.

The requests are byte-for-byte the bodies the live eval sends (cric.llm.request_body), written to JSONL, uploaded
with purpose "batch" and run with completion window 24 h; results are graded exactly like a live run and saved
as an ordinary eval file whose arm is marked "(batch)".

    cric eval --set heldout2 --models super --repeats 5 --batch              # submit and wait
    cric eval --set heldout2 --batch-collect <batch_id>                      # collect later
"""

from __future__ import annotations

import json
import time

from ..agent.decision import Decision, decision_messages
from ..config import PRICES
from ..llm import client, extract_json, request_body
from ..safety.validator import validate
from .runner import RESULTS, build_contexts, grade, naive_rule, policy_rule, summarize
from .scenarios import HELDOUT, HELDOUT2, SCENARIOS, forged_scenarios

DONE = ("completed", "done", "failed", "expired", "cancelled")


def _pool(scenario_set: str):
    return {"heldout": HELDOUT, "heldout2": HELDOUT2, "forged": forged_scenarios()}.get(scenario_set, SCENARIOS)


def submit(model: str, scenario_set: str, repeats: int, log=print) -> str:
    chosen = _pool(scenario_set)
    contexts = build_contexts(chosen, log=log)
    path = RESULTS / f"batch_{scenario_set}_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"
    RESULTS.mkdir(exist_ok=True)
    with path.open("w") as f:
        for s in chosen:
            body = request_body(model, decision_messages(contexts[s.id][0]), Decision)
            for r in range(repeats):
                f.write(json.dumps({"custom_id": f"{s.id}#{r}", "method": "POST", "url": "/v1/chat/completions",
                                    "body": body}, ensure_ascii=False) + "\n")
    up = client().files.create(file=path.open("rb"), purpose="batch")
    b = client().batches.create(input_file_id=up.id, endpoint="/v1/chat/completions", completion_window="24h",
                                metadata={"project": "cric", "set": scenario_set, "model": model})
    log(f"batch {b.id}: {len(chosen) * repeats} requests from {path.name}")
    return b.id


def collect(batch_id: str, scenario_set: str, *, wait_s: int = 3600, log=print) -> dict | None:
    t0 = time.time()
    while True:
        b = client().batches.retrieve(batch_id)
        if b.status in DONE:
            break
        if time.time() - t0 > wait_s:
            log(f"batch {batch_id} still {b.status}; collect later with --batch-collect {batch_id}")
            return None
        log(f"batch {batch_id}: {b.status} {getattr(b, 'request_counts', '')}")
        time.sleep(30)
    if not b.output_file_id:
        raise RuntimeError(f"batch {batch_id} ended {b.status} without output")
    lines = client().files.content(b.output_file_id).text.splitlines()
    chosen = _pool(scenario_set)
    sc = {s.id: s for s in chosen}
    contexts = build_contexts(chosen, log=lambda *_: None)
    rows, model = [], None
    for s in chosen:
        ctx, now = contexts[s.id]
        for arm, dec in (("naive_rule", naive_rule(ctx)), ("policy_rule", policy_rule(ctx, now))):
            v = validate(dec, ctx, now)
            rows.append({"scenario": s.id, "arm": arm, "rep": 0, "decision": dec, "raw": grade(dec, ctx, s),
                         "final": grade({**dec, "action": v["final_action"], "option_id": v["final_option_id"]}, ctx, s),
                         "validation": v, "seconds": 0.0, "cost_usd": 0.0})
    for line in lines:
        item = json.loads(line)
        sid, rep = item["custom_id"].split("#")
        body = (item.get("response") or {}).get("body") or {}
        model = model or body.get("model")
        try:
            dec = Decision.model_validate(extract_json(body["choices"][0]["message"]["content"])).model_dump()
        except Exception as exc:  # noqa: BLE001 - a failed request counts as a wrong answer
            dec = {"action": "ERROR", "option_id": None, "message_to_other_operator": None, "error": str(exc)[:200]}
        ctx, now = contexts[sid]
        v = validate(dec, ctx, now) if dec["action"] != "ERROR" else {"final_action": "ERROR", "final_option_id": None,
                                                                      "overrides": [], "warnings": []}
        u = body.get("usage") or {}
        pin, pout = PRICES.get(model or "", (0.0, 0.0))
        rows.append({"scenario": sid, "arm": f"{(model or 'model').split('/')[-1]} (batch)", "rep": int(rep),
                     "decision": dec, "raw": grade(dec, ctx, sc[sid]),
                     "final": grade({**dec, "action": v["final_action"], "option_id": v["final_option_id"]}, ctx, sc[sid]),
                     "validation": v, "seconds": 0.0,
                     "cost_usd": (u.get("prompt_tokens", 0) * pin + u.get("completion_tokens", 0) * pout) / 1e6})
    summary = summarize(rows, chosen)
    path = RESULTS / f"eval_{scenario_set}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps({"summary": summary, "rows": rows, "batch_id": batch_id,
                                "batch_minutes": round((b.completed_at or time.time()) / 60 - b.created_at / 60, 1)
                                if getattr(b, "completed_at", None) else None,
                                "scenarios": [{**s.__dict__, "expected": sorted(s.expected)} for s in chosen]},
                               indent=1, default=str))
    summary["saved_to"] = str(path)
    return summary

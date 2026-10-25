"""Step 4: satellite dossier. Who can move when two objects get close?

The first question in any collision-avoidance decision is whether the other
object can manoeuvre at all. Orbit data does not say; the answer is scattered
across operator pages, mission sheets and press releases. This step:

1. CelesTrak SATCAT  -> authoritative identity (payload / rocket body / debris, owner, status).
2. Tavily Search     -> candidate pages about the satellite's propulsion, operator and mission.
3. Tavily Extract    -> query-focused chunks from the best pages (not just search snippets).
4. Nemotron Super    -> dossier where every fact carries a source URL and a verbatim quote.
5. Claim checker     -> rejects URLs Tavily never returned and quotes that are not in the source text.
6. Rule (no LLM)     -> for a conjunction pair, who should manoeuvre, or escalate when unknown.

Run with --no-tavily to see what the agent knows without web grounding (ablation).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from typing import Literal

import requests
from pydantic import BaseModel, Field

from common import DATA, MODEL_SUPER, require_env, save_json, structured_call, token_factory_client, usage_line

SATCAT_URL = "https://celestrak.org/satcat/records.php?CATNR={norad}&FORMAT=json"
OBJECT_TYPES = {"PAY": "payload", "R/B": "rocket body", "DEB": "debris", "UNK": "unknown"}
OPS_STATUS = {"+": "operational", "-": "nonoperational", "P": "partially operational", "B": "backup",
              "S": "spare", "X": "extended mission", "D": "decayed", "?": "unknown"}
QUERIES = [
    "{name} satellite propulsion system thruster",
    "{name} satellite operator mission spacecraft specifications mass",
    "{name} satellite orbit maneuver collision avoidance",
]
# Social posts are unvetted and often generalise from a fleet to one unit; never let them decide a manoeuvre.
LOW_QUALITY_DOMAINS = ["facebook.com", "x.com", "twitter.com", "reddit.com", "instagram.com", "tiktok.com",
                       "linkedin.com", "youtube.com"]
EXTRACT_QUERY = "propulsion thruster propellant orbit maneuver delta-v mass operator mission status"
CACHE = DATA / "tavily_cache"
CACHE.mkdir(exist_ok=True)


class Fact(BaseModel):
    value: str | float | int | bool | None
    status: Literal["sourced", "inferred", "unknown"]
    source_url: str | None = None
    quote: str | None = Field(None, description="verbatim sentence from the source that supports the value")
    note: str | None = None


class Dossier(BaseModel):
    norad_id: int
    name: str
    operator: Fact
    country: Fact
    mission_type: Fact = Field(description="e.g. earth observation, communications, technology demo")
    launch_mass_kg: Fact
    has_propulsion: Fact = Field(description="value: 'yes', 'no' or null")
    propulsion_type: Fact = Field(description="e.g. hydrazine monopropellant, electric (Hall), cold gas, none")
    can_maneuver_now: Fact = Field(description="value: 'yes', 'no' or null; consider status, age, propellant")
    mission_status: Fact
    contact_channel: Fact = Field(description="public operator page or contact route for coordination")
    summary: str


SYSTEM = """You build a satellite dossier for a collision-avoidance agent.
Rules:
- Every field except norad_id, name, summary is a Fact {value, status, source_url, quote, note}.
- "sourced": the value is stated in a source. source_url MUST be one of the provided URLs, and quote MUST be
  copied verbatim (a full sentence or clause, at least 6 words) from that source's text.
- "inferred": derived from sourced facts or the SATCAT record; explain in note. quote may be null.
- has_propulsion and can_maneuver_now decide who must dodge a collision. They may ONLY be "sourced" or
  "unknown" (exception: SATCAT says debris, rocket body or decayed -> can_maneuver_now "no", inferred).
- "unknown": sources do not say. value null. Never guess propulsion from satellite size or type alone.
- Facts from the SATCAT record use source_url "celestrak:satcat" and quote the field, e.g. "OWNER: SKOR".
- Sources may describe a different satellite with a similar name (e.g. another KOMPSAT); ignore those.
Return one JSON object only."""


def _noop(kind: str, **data) -> None:
    """Default progress sink. The web server passes a real emitter to stream steps to the browser."""


def cached(kind: str, payload: dict, call):
    key = hashlib.sha256(json.dumps([kind, payload], sort_keys=True).encode()).hexdigest()[:16]
    path = CACHE / f"{kind}_{key}.json"
    if path.exists():
        return json.loads(path.read_text()), True
    result = call()
    path.write_text(json.dumps(result, ensure_ascii=False))
    return result, False


def satcat(norad: int) -> dict:
    rows = requests.get(SATCAT_URL.format(norad=norad), timeout=30).json()
    if not rows:
        raise SystemExit(f"[error] NORAD {norad} not found in SATCAT")
    r = rows[0]
    r["OBJECT_TYPE_LABEL"] = OBJECT_TYPES.get(r.get("OBJECT_TYPE"), r.get("OBJECT_TYPE"))
    r["OPS_STATUS_LABEL"] = OPS_STATUS.get(r.get("OPS_STATUS_CODE") or "?", "unknown")
    return r


def gather(tavily, name: str, max_results: int, extract_top: int, emit=_noop) -> tuple[list[dict], dict]:
    stats = {"search_calls": 0, "extract_calls": 0, "cache_hits": 0, "credits": 0.0}
    hits: dict[str, dict] = {}
    for qi, q in enumerate(QUERIES):
        query = q.format(name=name)
        params = {"query": query, "search_depth": "advanced", "max_results": max_results, "include_usage": True,
                  "exclude_domains": LOW_QUALITY_DOMAINS}
        emit("search_start", index=qi, total=len(QUERIES), query=query)
        res, hit = cached("search", params, lambda: tavily.search(**params))
        credits = 0 if hit else (res.get("usage") or {}).get("credits", 0)
        stats["search_calls"] += 1
        stats["cache_hits"] += hit
        stats["credits"] += credits
        emit("search_done", index=qi, total=len(QUERIES), query=query, cached=bool(hit), credits=credits,
             results=[{"url": r["url"], "title": r.get("title", ""), "score": round(r.get("score", 0), 3)}
                      for r in res.get("results", [])])
        for r in res.get("results", []):
            prev = hits.get(r["url"])
            if not prev or r.get("score", 0) > prev.get("score", 0):
                hits[r["url"]] = {"url": r["url"], "title": r.get("title", ""), "score": r.get("score", 0),
                                  "text": r.get("content", "")}

    ranked = sorted(hits.values(), key=lambda h: h["score"], reverse=True)
    top_urls = [h["url"] for h in ranked[:extract_top]]
    if top_urls:
        params = {"urls": top_urls, "extract_depth": "advanced", "query": EXTRACT_QUERY,
                  "chunks_per_source": 5, "include_usage": True}
        emit("extract_start", urls=top_urls)
        res, hit = cached("extract", params, lambda: tavily.extract(**params))
        credits = 0 if hit else (res.get("usage") or {}).get("credits", 0)
        stats["extract_calls"] += 1
        stats["cache_hits"] += hit
        stats["credits"] += credits
        for r in res.get("results", []):
            if r.get("url") in hits and r.get("raw_content"):
                hits[r["url"]]["text"] = r["raw_content"][:6000]
                hits[r["url"]]["extracted"] = True
        emit("extract_done", cached=bool(hit), credits=credits,
             pages=[{"url": u, "chars": len(hits[u]["text"]), "extracted": bool(hits[u].get("extracted"))} for u in top_urls])
    return ranked, stats


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s.%/-]", " ", s.lower())).strip()


def check_claims(d: Dossier, sources: list[dict], satcat_row: dict) -> list[str]:
    texts = {s["url"]: norm(s["text"]) for s in sources}
    satcat_text = norm(" ".join(f"{k}: {v}" for k, v in satcat_row.items()))
    problems = []
    for field, fact in d:
        if not isinstance(fact, Fact):
            continue
        if field in SAFETY_FIELDS and fact.status == "inferred" and satcat_row.get("OBJECT_TYPE") == "PAY":
            problems.append(f"{field}: inferred {fact.value!r} without a source (guess on a safety field)")
        if fact.status == "unknown":
            if fact.value not in (None, ""):
                problems.append(f"{field}: unknown but has value {fact.value!r}")
            continue
        if fact.status != "sourced":
            continue
        if fact.source_url == "celestrak:satcat":
            if fact.quote and norm(fact.quote) not in satcat_text:
                problems.append(f"{field}: SATCAT quote not found -> {fact.quote!r}")
            continue
        if fact.source_url not in texts:
            problems.append(f"{field}: URL not returned by Tavily -> {fact.source_url}")
        elif not fact.quote or len(fact.quote.split()) < 6:
            problems.append(f"{field}: sourced without a verbatim quote of 6+ words")
        elif norm(fact.quote) not in texts[fact.source_url]:
            problems.append(f"{field}: quote not found in source text -> {fact.quote[:90]!r}")
    return problems


SAFETY_FIELDS = ("has_propulsion", "can_maneuver_now")


def yes_no(value) -> str | None:
    v = str(value).strip().lower()
    return "yes" if v in ("yes", "true") else "no" if v in ("no", "false") else None


def ability(row: dict, d: Dossier, problems: list[str]) -> str:
    """'no' / 'yes' / 'unknown'. SATCAT object type and status override the web dossier.
    Only web-sourced facts that passed the claim check count; model guesses never decide."""
    if row.get("OBJECT_TYPE") in ("DEB", "R/B") or row.get("OPS_STATUS_CODE") in ("-", "D"):
        return "no"
    failed = {p.split(":", 1)[0] for p in problems}
    for field in ("can_maneuver_now", "has_propulsion"):
        fact = getattr(d, field)
        if field in failed or any(dom in (fact.source_url or "") for dom in LOW_QUALITY_DOMAINS):
            continue
        if fact.status == "sourced" and fact.source_url != "celestrak:satcat" and yes_no(fact.value):
            # A sourced "has propulsion: yes" does not prove it works today, but it is the best public signal.
            return yes_no(fact.value)
    return "unknown"


def responsibility(a: tuple[dict, Dossier, list[str]], b: tuple[dict, Dossier, list[str]]) -> dict:
    ca, cb = ability(*a), ability(*b)
    na, nb = a[1].name, b[1].name
    if ca == "yes" and cb == "no":
        rec = f"{na} manoeuvres; {nb} cannot."
    elif cb == "yes" and ca == "no":
        rec = f"{nb} manoeuvres; {na} cannot."
    elif ca == cb == "yes":
        rec = "Both can manoeuvre: coordinate with the other operator before acting to avoid both moving."
    elif ca == cb == "no":
        rec = "Neither can manoeuvre: monitor and alert; no avoidance action is possible."
    else:
        rec = "Manoeuvre capability unknown for at least one object: escalate to a human operator."
    return {"a": {"name": na, "can_maneuver": ca}, "b": {"name": nb, "can_maneuver": cb}, "recommendation": rec}


def build(norad: int, tavily, args, client, emit=_noop) -> dict:
    trace: list[dict] = []

    def ev(kind: str, **data) -> None:
        trace.append({"kind": kind, "norad": norad, **data})
        emit(kind, norad=norad, **data)

    row = satcat(norad)
    name = row["OBJECT_NAME"]
    print(f"\n=== {name} (NORAD {norad}) {row['OBJECT_TYPE_LABEL']}, {row['OPS_STATUS_LABEL']}, owner {row.get('OWNER')}")
    ev("satcat", name=name, type=row["OBJECT_TYPE_LABEL"], status=row["OPS_STATUS_LABEL"],
       owner=row.get("OWNER"), launch=row.get("LAUNCH_DATE"))

    if tavily is None:
        ev("search_skipped")
    sources, stats = ([], {}) if tavily is None else gather(tavily, name, args.max_results, args.extract_top, ev)
    if tavily is not None:
        print(f"Tavily: {len(sources)} pages, {stats['search_calls']} search + {stats['extract_calls']} extract calls, "
              f"{stats['cache_hits']} cache hits, ~{stats['credits']} credits")
        for s in sources[: args.extract_top]:
            print(f"   {'E' if s.get('extracted') else 's'} {s['score']:.2f} {s['url']}")

    src_block = "\n\n".join(f"[{i}] URL: {s['url']}\nTITLE: {s['title']}\nTEXT:\n{s['text']}" for i, s in enumerate(sources))
    user = (f"Target: NORAD {norad}, {name}\n\nSATCAT record:\n{json.dumps(row, ensure_ascii=False)}\n\n"
            f"Web sources:\n{src_block or '(none: web search disabled)'}")
    ev("llm_start", model=MODEL_SUPER, sources=len(sources))
    dossier, _, _, resp = structured_call(
        client, MODEL_SUPER, [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}], Dossier)
    problems = check_claims(dossier, sources, row)
    u = getattr(resp, "usage", None)
    ev("llm_done", usage={"prompt": getattr(u, "prompt_tokens", None), "completion": getattr(u, "completion_tokens", None)})
    print(f"Nemotron Super {usage_line(resp)} | claim check: {'PASS' if not problems else f'{len(problems)} problem(s)'}")
    for p in problems:
        print("   !", p)
    for field in ("operator", "mission_type", "has_propulsion", "propulsion_type", "can_maneuver_now", "mission_status"):
        f = getattr(dossier, field)
        print(f"   {field:17} {str(f.value):40.40} [{f.status}] {f.source_url or ''}")

    facts = [v for _, v in dossier if isinstance(v, Fact)]
    flagged = {p.split(":", 1)[0] for p in problems}
    ev("claims", problems=problems, passed=not problems)
    ev("facts", facts=[{"field": k, **v.model_dump(), "flagged": k in flagged}
                       for k, v in dossier if isinstance(v, Fact)])
    result = {
        "norad_id": norad, "satcat": row, "dossier": dossier.model_dump(), "claim_problems": problems,
        "metrics": {
            "facts": len(facts),
            "sourced": sum(f.status == "sourced" for f in facts),
            "unknown": sum(f.status == "unknown" for f in facts),
            "claim_problems": len(problems),
        },
        "tavily": {"enabled": tavily is not None, **stats,
                   "sources": [{k: s[k] for k in ("url", "title", "score")} for s in sources]},
        "model": MODEL_SUPER,
    }
    ev("result", metrics=result["metrics"], tavily_enabled=tavily is not None, credits=stats.get("credits", 0))
    result["trace"] = trace
    save_json(f"dossier_{norad}{'_notavily' if tavily is None else ''}.json", result)
    return result


def run_pair(norads: list[int], tavily, client, args, emit=_noop):
    results = [build(n, tavily, args, client, emit) for n in norads[:2]]
    decision = None
    if len(results) == 2:
        pair = [(r["satcat"], Dossier.model_validate(r["dossier"]), r["claim_problems"]) for r in results]
        decision = responsibility(*pair)
        suffix = "" if tavily is not None else "_notavily"
        save_json(f"dossier_pair_{norads[0]}_{norads[1]}{suffix}.json", {"decision": decision, "norad_ids": norads[:2]})
        emit("decision", decision=decision, norad_ids=norads[:2])
    return results, decision


def default_pair() -> list[int]:
    path = DATA / "conjunctions.json"
    if not path.exists():
        raise SystemExit("[error] pass --norad, or run 01_conjunctions.py first to pick the closest encounter")
    events = json.loads(path.read_text())["events"]
    # Prefer an encounter that is not Starlink-vs-Starlink: those are coordinated inside one operator.
    pick = next((e for e in events if "STARLINK" not in e["object_a"]["name"] + e["object_b"]["name"]), events[0])
    print(f"Closest non-intra-constellation encounter: {pick['object_a']['name']} <-> {pick['object_b']['name']}, "
          f"{pick['miss_distance_km']} km at {pick['tca_utc']}")
    return [pick["object_a"]["norad_id"], pick["object_b"]["norad_id"]]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--norad", type=int, nargs="+", help="one or two NORAD IDs (default: closest pair from step 1)")
    ap.add_argument("--max-results", type=int, default=5, help="Tavily results per search query")
    ap.add_argument("--extract-top", type=int, default=4, help="pages to deep-extract with Tavily Extract")
    ap.add_argument("--no-tavily", action="store_true", help="ablation: SATCAT only, no web grounding")
    args = ap.parse_args()

    norads = args.norad or default_pair()
    tavily = None
    if not args.no_tavily:
        from tavily import TavilyClient
        tavily = TavilyClient(api_key=require_env("TAVILY_API_KEY"))
    client = token_factory_client()

    _, decision = run_pair(norads, tavily, client, args)
    if decision:
        print(f"\nWho manoeuvres? {decision['recommendation']}")
        print(f"   {decision['a']['name']}: {decision['a']['can_maneuver']} | {decision['b']['name']}: {decision['b']['can_maneuver']}")


if __name__ == "__main__":
    main()

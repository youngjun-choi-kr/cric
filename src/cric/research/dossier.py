"""Satellite dossier: can this object manoeuvre? Every fact cited with a verbatim quote.

1. CelesTrak SATCAT  -> identity: payload / rocket body / debris, owner, status.
2. Tavily Search     -> candidate pages (social media excluded).
3. Tavily Extract    -> query-focused chunks from the best pages.
4. Nemotron Super    -> dossier; each fact has status, URL, quote and scope.
5. Claim checker     -> rejects URLs Tavily never returned and quotes not in the page text.
6. capability()      -> yes / no / unknown from checked facts only; model guesses never decide.

Ported from spike/04_satellite_dossier.py. Tavily answers are cached on disk.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Literal

from pydantic import BaseModel, Field

from ..config import DATA, MODEL_SUPER, require_env
from ..ingest.celestrak import satcat
from ..llm import Usage, structured_call
from .untrusted import DATA_RULE, suspicious, tier, wrap_sources

QUERIES = [
    "{name} satellite propulsion system thruster",
    "{name} satellite operator mission spacecraft specifications mass",
    "{name} satellite orbit maneuver collision avoidance",
]
# Unvetted and often generalising from a fleet to one unit; never allowed to decide a manoeuvre.
LOW_QUALITY_DOMAINS = ["facebook.com", "x.com", "twitter.com", "reddit.com", "instagram.com", "tiktok.com",
                       "linkedin.com", "youtube.com"]
EXTRACT_QUERY = "propulsion thruster propellant orbit maneuver delta-v mass operator mission status"
TAVILY_CACHE = DATA / "tavily_cache"
DOSSIER_CACHE = DATA / "dossiers"
SAFETY_FIELDS = ("has_propulsion", "can_maneuver_now")


class Fact(BaseModel):
    value: str | None = Field(description="value as text; for yes/no fields use 'yes' or 'no'; null if unknown")
    status: Literal["sourced", "inferred", "unknown"]
    source_url: str | None = None
    quote: str | None = Field(None, description="verbatim sentence or clause from the source supporting the value")
    scope: Literal["this_satellite", "satellite_family", "unclear"] = Field(
        "unclear", description="does the source talk about this exact satellite, or about its series/constellation?")
    note: str | None = None


class Dossier(BaseModel):
    norad_id: int
    name: str
    operator: Fact
    country: Fact
    mission_type: Fact
    launch_mass_kg: Fact
    has_propulsion: Fact
    propulsion_type: Fact
    can_maneuver_now: Fact
    mission_status: Fact
    contact_channel: Fact = Field(description="public operator page or contact route for coordination")
    summary: str


SYSTEM = """You build a satellite dossier for a collision-avoidance agent.
Rules:
- Every field except norad_id, name, summary is a Fact {value, status, source_url, quote, scope, note}.
- "sourced": the value is stated in a source. source_url MUST be one of the provided URLs, and quote MUST be
  copied verbatim (a full sentence or clause, at least 6 words) from that source's text.
- scope: "this_satellite" only if the quoted text is about this exact satellite; "satellite_family" if it is
  about the series or constellation (e.g. "LEMUR-2 satellites carry ..."); otherwise "unclear".
- "inferred": derived from sourced facts or the SATCAT record; explain in note. quote may be null.
- has_propulsion and can_maneuver_now decide who must dodge a collision. They may ONLY be "sourced" or
  "unknown" (exception: SATCAT says debris, rocket body or decayed -> can_maneuver_now "no", inferred).
- "unknown": sources do not say. value null. Never guess propulsion from satellite size or type alone.
- Facts from the SATCAT record use source_url "celestrak:satcat" and quote the field, e.g. "OWNER: SKOR".
- Sources may describe a different satellite with a similar name (e.g. another KOMPSAT); ignore those.
- """ + DATA_RULE + """
Return one JSON object only."""


def _noop(kind: str, **data) -> None:
    pass


def _cached(kind: str, payload: dict, call):
    TAVILY_CACHE.mkdir(exist_ok=True)
    key = hashlib.sha256(json.dumps([kind, payload], sort_keys=True).encode()).hexdigest()[:16]
    path = TAVILY_CACHE / f"{kind}_{key}.json"
    if path.exists():
        return json.loads(path.read_text()), True
    result = call()
    path.write_text(json.dumps(result, ensure_ascii=False))
    return result, False


def gather(tavily, name: str, *, max_results=5, extract_top=4, emit=_noop) -> tuple[list[dict], dict]:
    stats = {"search_calls": 0, "extract_calls": 0, "cache_hits": 0, "credits": 0.0}
    hits: dict[str, dict] = {}
    for qi, q in enumerate(QUERIES):
        params = {"query": q.format(name=name), "search_depth": "advanced", "max_results": max_results,
                  "include_usage": True, "exclude_domains": LOW_QUALITY_DOMAINS}
        emit("search_start", index=qi, total=len(QUERIES), query=params["query"])
        res, hit = _cached("search", params, lambda: tavily.search(**params))
        credits = 0 if hit else (res.get("usage") or {}).get("credits", 0)
        stats["search_calls"] += 1
        stats["cache_hits"] += hit
        stats["credits"] += credits
        emit("search_done", index=qi, cached=bool(hit), credits=credits,
             results=[{"url": r["url"], "title": r.get("title", ""), "score": round(r.get("score", 0), 3)}
                      for r in res.get("results", [])])
        for r in res.get("results", []):
            prev = hits.get(r["url"])
            if not prev or r.get("score", 0) > prev.get("score", 0):
                hits[r["url"]] = {"url": r["url"], "title": r.get("title", ""), "score": r.get("score", 0),
                                  "text": r.get("content", "")}

    ranked = sorted(hits.values(), key=lambda h: h["score"], reverse=True)
    top = [h["url"] for h in ranked[:extract_top]]
    if top:
        params = {"urls": top, "extract_depth": "advanced", "query": EXTRACT_QUERY,
                  "chunks_per_source": 5, "include_usage": True}
        emit("extract_start", urls=top)
        res, hit = _cached("extract", params, lambda: tavily.extract(**params))
        credits = 0 if hit else (res.get("usage") or {}).get("credits", 0)
        stats["extract_calls"] += 1
        stats["cache_hits"] += hit
        stats["credits"] += credits
        for r in res.get("results", []):
            if r.get("url") in hits and r.get("raw_content"):
                hits[r["url"]]["text"] = r["raw_content"][:6000]
                hits[r["url"]]["extracted"] = True
        emit("extract_done", cached=bool(hit), credits=credits)
    return ranked, stats


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s.%/-]", " ", s.lower())).strip()


def check_claims(d: Dossier, sources: list[dict], row: dict) -> list[str]:
    texts = {s["url"]: _norm(s["text"]) for s in sources}
    satcat_text = _norm(" ".join(f"{k}: {v}" for k, v in row.items()))
    problems = []
    for field, fact in d:
        if not isinstance(fact, Fact):
            continue
        if field in SAFETY_FIELDS and fact.status == "inferred" and row.get("OBJECT_TYPE") == "PAY":
            problems.append(f"{field}: inferred {fact.value!r} without a source (guess on a safety field)")
        if fact.status == "unknown":
            if fact.value not in (None, ""):
                problems.append(f"{field}: unknown but has value {fact.value!r}")
            continue
        if fact.status != "sourced":
            continue
        if fact.source_url == "celestrak:satcat":
            if fact.quote and _norm(fact.quote) not in satcat_text:
                problems.append(f"{field}: SATCAT quote not found -> {fact.quote!r}")
            continue
        if fact.source_url not in texts:
            problems.append(f"{field}: URL not returned by Tavily -> {fact.source_url}")
        elif not fact.quote or len(fact.quote.split()) < 6:
            problems.append(f"{field}: sourced without a verbatim quote of 6+ words")
        elif _norm(fact.quote) not in texts[fact.source_url]:
            problems.append(f"{field}: quote not found in source text -> {fact.quote[:90]!r}")
        elif suspicious(fact.quote):
            problems.append(f"{field}: quote reads like an instruction, not a fact -> {fact.quote[:90]!r}")
    return problems


_PROPULSION_WORDS = re.compile(r"propuls|thruster|hydrazine|propellant|electric propulsion|cold gas", re.I)


def consistency_issues(d: Dossier) -> list[str]:
    """The summary must not state propulsion facts that the safety fields leave unknown."""
    issues = []
    if _PROPULSION_WORDS.search(d.summary) and d.has_propulsion.status == "unknown":
        issues.append("summary: mentions propulsion but has_propulsion is unknown; either fill has_propulsion "
                      "(sourced, verbatim quote, scope) or remove the claim from the summary")
    return issues


def _yes_no(value) -> str | None:
    v = str(value).strip().lower()
    return "yes" if v in ("yes", "true") else "no" if v in ("no", "false") else None


def capability(row: dict, d: Dossier, problems: list[str]) -> dict:
    """{"can_maneuver": yes/no/unknown, "basis": why}. SATCAT type and status override the web."""
    if row.get("OBJECT_TYPE") in ("DEB", "R/B"):
        return {"can_maneuver": "no", "basis": f"SATCAT object type: {row.get('OBJECT_TYPE_LABEL')}"}
    if row.get("OPS_STATUS_CODE") in ("-", "D"):
        return {"can_maneuver": "no", "basis": f"SATCAT status: {row.get('OPS_STATUS_LABEL')}"}
    failed = {p.split(":", 1)[0] for p in problems}
    for field in ("can_maneuver_now", "has_propulsion"):
        f = getattr(d, field)
        if field in failed or f.status != "sourced" or f.source_url == "celestrak:satcat":
            continue
        if any(dom in (f.source_url or "") for dom in LOW_QUALITY_DOMAINS):
            continue
        if f.scope != "this_satellite":
            continue  # a fleet-level fact does not prove this unit can move
        yn = _yes_no(f.value)
        if yn:
            return {"can_maneuver": yn, "basis": f"{field} sourced from {f.source_url}", "quote": f.quote}
    return {"can_maneuver": "unknown", "basis": "no checked, unit-specific source on propulsion"}


def build_dossier(norad: int, *, use_tavily: bool = True, refresh: bool = False, usage: Usage | None = None,
                  emit=_noop) -> dict:
    """Dossier + claim check + capability, cached per NORAD ID (and per tavily on/off)."""
    DOSSIER_CACHE.mkdir(exist_ok=True)
    path = DOSSIER_CACHE / f"{norad}{'' if use_tavily else '_notavily'}.json"
    if path.exists() and not refresh:
        out = json.loads(path.read_text())
        emit("dossier_cached", norad=norad, name=out["dossier"]["name"])
        return out

    row = satcat(norad)
    name = row["OBJECT_NAME"]
    emit("satcat", norad=norad, name=name, type=row["OBJECT_TYPE_LABEL"], status=row["OPS_STATUS_LABEL"])
    sources, stats = [], {}
    if use_tavily:
        from tavily import TavilyClient

        sources, stats = gather(TavilyClient(api_key=require_env("TAVILY_API_KEY")), name, emit=emit)
    src, injection_flags = wrap_sources(sources)
    if injection_flags:
        emit("injection_flagged", norad=norad, flags=injection_flags)
    user = (f"Target: NORAD {norad}, {name}\n\nSATCAT record:\n{json.dumps(row, ensure_ascii=False)}\n\n"
            f"Web sources:\n{src or '(none: web search disabled)'}")
    emit("llm_start", norad=norad, model=MODEL_SUPER, sources=len(sources))
    t0 = time.time()
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
    repairs = []
    for attempt in range(2):  # one repair round with the checker's findings as feedback
        dossier, _ = structured_call(MODEL_SUPER, messages, Dossier, usage=usage)
        problems = check_claims(dossier, sources, row)
        issues = problems + consistency_issues(dossier)
        if not issues or attempt == 1:
            break
        repairs.append(issues)
        emit("dossier_repair", norad=norad, issues=issues)
        messages += [{"role": "assistant", "content": dossier.model_dump_json()},
                     {"role": "user", "content": "A verifier checked your dossier against the sources and found:\n- "
                      + "\n- ".join(issues) + "\nFix these and return the full corrected dossier. Quotes must be "
                      "copied exactly from the source text."}]
    out = {"norad_id": norad, "satcat": row, "dossier": dossier.model_dump(), "claim_problems": problems,
           "consistency_issues": consistency_issues(dossier), "repairs": repairs, "injection_flags": injection_flags,
           "capability": capability(row, dossier, problems), "seconds": round(time.time() - t0, 1),
           "tavily": {"enabled": use_tavily, **stats,
                      "sources": [{k: s[k] for k in ("url", "title", "score")} | {"tier": tier(s["url"])}
                                  for s in sources]}}
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    emit("dossier_done", norad=norad, capability=out["capability"], problems=len(problems))
    return out

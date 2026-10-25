"""Scenario forge: real space-weather events, researched with Tavily, become stress tests.

Tavily Search finds reporting on a real event, Tavily Extract pulls the full text of the
best pages, and Nemotron Super fills a fixed schema where every fact cites a URL and a
verbatim quote. Checks reject citations Tavily never returned, quotes not in the page,
and numbers in the summary that no cited field supports; one repair round follows.

The forged event is then replayed "as if it were happening now" against a real
encounter, so the decision agent faces conditions that actually occurred.
Ported from spike/02_scenario_tavily.py.
"""

from __future__ import annotations

import json
import re
import time
from typing import Literal

from pydantic import BaseModel, Field

from ..config import DATA, MODEL_SUPER, require_env
from ..llm import Usage, structured_call
from .untrusted import DATA_RULE, sanitize, suspicious, wrap_sources
from .dossier import LOW_QUALITY_DOMAINS, _cached

FORGED = DATA / "forged"
QUERIES = [
    "{event}",
    "{event} geomagnetic storm Kp index NOAA G scale",
    "{event} satellites atmospheric drag orbit impact",
]
EXTRACT_QUERY = "Kp index G scale storm dates satellite drag orbit decay operators impact"


class Fact(BaseModel):
    value: str | None = Field(description="value as text, e.g. '8' or 'G4'; null if unknown")
    status: Literal["sourced", "inferred", "unknown"]
    source_url: str | None = None
    quote: str | None = Field(None, description="short verbatim phrase from that source supporting the value")
    note: str | None = None


class SpaceWeather(BaseModel):
    noaa_storm_level: Fact
    kp_peak: Fact
    storm_start_utc: Fact
    cause: Fact
    thermosphere_density_increase: Fact


class Impact(BaseModel):
    satellites_affected: Fact
    orbit_prediction_effect: Fact
    operator_actions_reported: Fact


class ForgedEvent(BaseModel):
    event_id: str = Field(pattern=r"^[a-z0-9_]{6,60}$")
    title: str = Field(min_length=10)
    event_summary: str = Field(min_length=40)
    space_weather: SpaceWeather
    impact: Impact
    lessons_for_operators: list[str] = Field(min_length=1)


SYSTEM = """You turn web sources about a real space-weather event into a structured record for a satellite
operations stress test. Every factual value is a Fact {value, status, source_url, quote, note}:
- "sourced": stated in a source; source_url MUST be one of the provided URLs and quote MUST be copied exactly.
- "inferred": derived from sourced facts; explain in note.
- "unknown": not in the sources; value null. Never invent numbers.
Every number in event_summary must come from the sources. Fill kp_peak whenever a source states a Kp value.
Return one JSON object."""


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _facts(e: ForgedEvent):
    for group in ("space_weather", "impact"):
        for key, fact in getattr(e, group):
            yield f"{group}.{key}", fact


def check(e: ForgedEvent, sources: list[dict]) -> list[str]:
    texts = {s["url"]: _norm(s["title"] + " " + s["text"]) for s in sources}
    issues = []
    for name, f in _facts(e):
        if f.status == "sourced":
            if f.source_url not in texts:
                issues.append(f"{name}: cites a URL Tavily did not return -> {f.source_url}")
            elif not f.quote or _norm(f.quote) not in texts[f.source_url] or suspicious(f.quote):
                issues.append(f"{name}: quote not found in the cited page -> {f.quote!r}")
        if f.status == "unknown" and f.value not in (None, ""):
            issues.append(f"{name}: unknown but has value {f.value!r}")
    # Numbers in the summary must exist somewhere in the sources (catches invented figures, allows dates).
    corpus = " ".join(texts.values()).replace(",", "")
    for num in {n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", e.event_summary)}:
        if not re.search(rf"(?<![\d.]){re.escape(num)}(?![\d])", corpus):
            issues.append(f"event_summary: number {num} does not appear in any source")
    return issues


# Critical fields and the words that find candidate sentences for them.
FILL_KEYS = {
    ("space_weather", "kp_peak"): r"\bKp\b",
    ("space_weather", "noaa_storm_level"): r"\bG[1-5]\b",
    ("space_weather", "thermosphere_density_increase"): r"density",
    ("impact", "orbit_prediction_effect"): r"drag|orbit|re-?ent",
}


class Fill(BaseModel):
    field: str
    fact: Fact


class Fills(BaseModel):
    fills: list[Fill]


def snippets(sources: list[dict], pattern: str, *, width: int = 160, limit: int = 12) -> list[dict]:
    """Short windows of source text around a keyword, with their URL. Deterministic retrieval, no model."""
    out, seen = [], set()
    for s in sources:
        text = " ".join(sanitize(s["text"])[0].split())
        for m in re.finditer(pattern, text, flags=re.I):
            w = text[max(0, m.start() - width): m.end() + width]
            if w not in seen:
                seen.add(w)
                out.append({"url": s["url"], "text": w})
            if len(out) >= limit:
                return out
    return out


def fill_unknowns(rec: ForgedEvent, sources: list[dict], *, usage: Usage | None = None) -> list[str]:
    """Second pass for critical fields left unknown: give the model only keyword sentences."""
    todo = {f"{g}.{k}": pat for (g, k), pat in FILL_KEYS.items() if getattr(getattr(rec, g), k).status == "unknown"}
    if not todo:
        return []
    blocks = {name: snippets(sources, pat) for name, pat in todo.items()}
    blocks = {k: v for k, v in blocks.items() if v}
    if not blocks:
        return []
    prompt = "\n\n".join(f"FIELD {name}:\n" + "\n".join(f"- ({b['url']}) {b['text']}" for b in bl)
                          for name, bl in blocks.items())
    # Nemotron reasons toward its own JSON shape; if it differs from the schema, guided decoding emits empty
    # defaults. Showing the exact shape fixes that (found while debugging this pass).
    example = ('{"fills": [{"field": "space_weather.kp_peak", "fact": {"value": "9", "status": "sourced", '
               '"source_url": "<url in parentheses>", "quote": "<exact words>", "note": null}}]}')
    messages = [{"role": "system", "content": SYSTEM + "\n" + DATA_RULE + "\nFill only the listed fields from the listed sentences. "
                 "Answer in exactly this shape, one entry per listed field:\n" + example},
                {"role": "user", "content": prompt}]
    fills, _ = structured_call(MODEL_SUPER, messages, Fills, usage=usage, max_tokens=8000)
    filled = []
    for f in fills.fills:
        if f.field in blocks and f.fact.status != "unknown":
            g, k = f.field.split(".", 1)
            setattr(getattr(rec, g), k, f.fact)
            filled.append(f.field)
    return filled


def gather(tavily, event: str, *, per_query: int = 4, extract_top: int = 3) -> tuple[list[dict], dict]:
    stats = {"search_calls": 0, "extract_calls": 0, "credits": 0.0, "cache_hits": 0}
    hits: dict[str, dict] = {}
    for q in QUERIES:
        params = {"query": q.format(event=event), "search_depth": "advanced", "max_results": per_query,
                  "include_usage": True, "exclude_domains": LOW_QUALITY_DOMAINS}
        res, hit = _cached("search", params, lambda: tavily.search(**params))
        stats["search_calls"] += 1
        stats["cache_hits"] += hit
        stats["credits"] += 0 if hit else (res.get("usage") or {}).get("credits", 0)
        for r in res.get("results", []):
            if r["url"] not in hits or r.get("score", 0) > hits[r["url"]]["score"]:
                hits[r["url"]] = {"url": r["url"], "title": r.get("title", ""), "score": r.get("score", 0),
                                  "text": r.get("content", "")}
    ranked = sorted(hits.values(), key=lambda h: h["score"], reverse=True)
    top = [h["url"] for h in ranked[:extract_top]]
    if top:
        params = {"urls": top, "extract_depth": "advanced", "query": EXTRACT_QUERY, "chunks_per_source": 5,
                  "include_usage": True}
        res, hit = _cached("extract", params, lambda: tavily.extract(**params))
        stats["extract_calls"] += 1
        stats["cache_hits"] += hit
        stats["credits"] += 0 if hit else (res.get("usage") or {}).get("credits", 0)
        for r in res.get("results", []):
            if r.get("url") in hits and r.get("raw_content"):
                hits[r["url"]]["text"] = r["raw_content"][:6000]
                hits[r["url"]]["extracted"] = True
    return ranked, stats


def forge(event: str, *, usage: Usage | None = None, refresh: bool = False, log=print) -> dict:
    FORGED.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "_", event.lower()).strip("_")[:60]
    path = FORGED / f"{slug}.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text())

    from tavily import TavilyClient

    t0 = time.time()
    sources, stats = gather(TavilyClient(api_key=require_env("TAVILY_API_KEY")), event)
    log(f"Tavily: {len(sources)} pages, {stats['search_calls']} searches + {stats['extract_calls']} extract, "
        f"~{stats['credits']} credits")
    block, injection_flags = wrap_sources(sources)
    messages = [{"role": "system", "content": SYSTEM + "\n" + DATA_RULE},
                {"role": "user", "content": f"Event: {event}\n\nSources:\n{block}\n\nFill every field."}]
    repairs = []
    for attempt in range(2):
        rec, _ = structured_call(MODEL_SUPER, messages, ForgedEvent, usage=usage)
        issues = check(rec, sources)
        if not issues or attempt == 1:
            break
        repairs.append(issues)
        log(f"verifier: {len(issues)} issue(s), asking for one repair")
        messages += [{"role": "assistant", "content": rec.model_dump_json()},
                     {"role": "user", "content": "A verifier found:\n- " + "\n- ".join(issues)
                      + "\nFix these and return the full corrected record. Copy quotes exactly."}]
    filled = fill_unknowns(rec, sources, usage=usage)
    if filled:
        log(f"second pass filled {filled}")
        # A fill that fails the checks is reverted to unknown rather than kept.
        for problem in check(rec, sources):
            name = problem.split(":", 1)[0]
            if name in filled:
                g, k = name.split(".", 1)
                setattr(getattr(rec, g), k, Fact(value=None, status="unknown", note=f"second pass rejected: {problem}"))
                filled.remove(name)
        issues = check(rec, sources)
    counts = {"sourced": 0, "inferred": 0, "unknown": 0}
    for _, f in _facts(rec):
        counts[f.status] += 1
    out = {"event": event, "record": rec.model_dump(), "issues": issues, "repairs": repairs, "fact_counts": counts,
           "second_pass_filled": filled, "injection_flags": injection_flags,
           "tavily": {**stats, "sources": [{k: s[k] for k in ("url", "title", "score")} for s in sources]},
           "seconds": round(time.time() - t0, 1)}
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    return out


def kp_from(rec: dict) -> float | None:
    """Kp peak as a number when it is cited; '8+' -> 8.33, '9-' -> 8.67."""
    f = rec["space_weather"]["kp_peak"]
    if f["status"] == "unknown" or not f["value"]:
        return None
    m = re.search(r"(\d(?:\.\d+)?)\s*([+-])?", f["value"])
    if not m:
        return None
    return float(m.group(1)) + {"+": 1 / 3, "-": -1 / 3, None: 0}[m.group(2)]


def weather_note(rec: dict) -> str:
    """Space-weather text for the decision context, each fact with its source host."""
    parts = []
    for label, key in (("NOAA level", "noaa_storm_level"), ("Kp peak", "kp_peak"), ("cause", "cause"),
                       ("thermosphere density", "thermosphere_density_increase")):
        f = rec["space_weather"][key]
        if f["status"] != "unknown" and f["value"]:
            host = re.sub(r"^https?://(www\.)?", "", f["source_url"] or "").split("/")[0]
            parts.append(f"{label}: {f['value']}" + (f" [{host}]" if host else ""))
    eff = rec["impact"]["orbit_prediction_effect"]
    if eff["status"] != "unknown" and eff["value"]:
        parts.append(f"orbit predictions: {eff['value']}")
    return f"Replaying real event '{rec['title']}' as current conditions. " + "; ".join(parts)

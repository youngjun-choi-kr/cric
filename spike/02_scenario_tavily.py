"""Step 2: build a grounded simulation scenario from real-world reporting.

Tavily collects sources about a real space event, Nemotron Super turns them
into a scenario JSON where every factual field cites a source URL, and a
claim checker rejects citations that Tavily never returned. Fields the
sources do not support must be marked "unknown" instead of guessed.
"""

from __future__ import annotations

import argparse
import re
import json
import time
from typing import Literal

from pydantic import BaseModel, Field, ValidationError
from tavily import TavilyClient

from common import MODEL_SUPER, require_env, save_json, structured_call, token_factory_client, usage_line

DEFAULT_EVENT = "February 2022 geomagnetic storm Starlink satellites lost after launch"
LOW_QUALITY_DOMAINS = ["facebook.com", "x.com", "twitter.com", "reddit.com", "instagram.com", "tiktok.com"]
QUERIES = [
    "{event}",
    "{event} atmospheric drag altitude NOAA Kp index",
    "{event} SpaceX statement satellites deorbit",
]


class Fact(BaseModel):
    value: str | None = Field(description="the value as text, e.g. '210' or 'G1-G2'; null if unknown")
    status: Literal["sourced", "inferred", "unknown"]
    source_url: str | None = Field(description="one of the provided source URLs when sourced")
    quote: str | None = Field(description="short verbatim phrase from that source supporting the value")
    note: str | None = None


# Fixed field sets: free-form dict keys and mixed value types made guided
# decoding emit garbage, so every slot the simulator needs is named explicitly.
class SpaceWeather(BaseModel):
    storm_level_noaa: Fact
    kp_peak: Fact
    cause: Fact
    storm_start_utc: Fact
    thermosphere_density_increase: Fact


class AffectedAssets(BaseModel):
    operator: Fact
    launch_date: Fact
    satellites_launched: Fact
    satellites_lost: Fact


class SimInitialConditions(BaseModel):
    deployment_perigee_km: Fact
    deployment_apogee_km: Fact
    inclination_deg: Fact
    target_orbit_km: Fact


class Scenario(BaseModel):
    scenario_id: str = Field(pattern=r"^[a-z0-9_]{6,60}$")
    title: str = Field(min_length=10)
    event_summary: str = Field(min_length=40)
    physical_mechanism: str = Field(min_length=40)
    space_weather: SpaceWeather
    affected_assets: AffectedAssets
    sim_initial_conditions: SimInitialConditions
    agent_challenge: str = Field(min_length=40, description="what the operations agent must decide, and when")
    success_criteria: list[str] = Field(min_length=2)
    variations: list[str] = Field(min_length=3, description="parameter changes that turn this into more scenarios")


SYSTEM = """You turn web sources about a real space event into a simulation scenario for a
satellite operations agent. Rules:
- Every factual value is a Fact {value, status, source_url, quote, note}.
- status "sourced": the value appears in a source; source_url MUST be one of the provided URLs
  and quote MUST be a short phrase copied exactly from that source's text.
- status "inferred": you derived it from sourced facts; explain how in note.
- status "unknown": the sources do not say; value null. Never invent numbers.
- Output one JSON object only, matching the requested keys."""


def gather_sources(client: TavilyClient, event: str, per_query: int) -> list[dict]:
    seen, sources = set(), []
    for q in QUERIES:
        res = client.search(q.format(event=event), search_depth="advanced", max_results=per_query,
                            exclude_domains=LOW_QUALITY_DOMAINS)
        for r in res.get("results", []):
            if r["url"] in seen:
                continue
            seen.add(r["url"])
            sources.append({"url": r["url"], "title": r.get("title", ""), "content": r.get("content", "")[:1500]})
    return sources


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def check_citations(scenario: Scenario, sources: list[dict]) -> tuple[list[str], dict[str, int]]:
    """Every sourced fact must cite a returned URL and quote text that is really in it."""
    content = {s["url"]: _norm(s["title"] + " " + s["content"]) for s in sources}
    problems, counts = [], {"sourced": 0, "inferred": 0, "unknown": 0}
    for group in ("space_weather", "affected_assets", "sim_initial_conditions"):
        for key, fact in getattr(scenario, group):
            name = f"{group}.{key}"
            counts[fact.status] += 1
            if fact.status == "sourced":
                if fact.source_url not in content:
                    problems.append(f"{name}: cites URL not returned by Tavily -> {fact.source_url}")
                elif not fact.quote or _norm(fact.quote) not in content[fact.source_url]:
                    problems.append(f"{name}: quote not found in cited source -> {fact.quote!r}")
            if fact.status == "unknown" and fact.value not in (None, ""):
                problems.append(f"{name}: marked unknown but has value {fact.value!r}")
    return problems, counts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--event", default=DEFAULT_EVENT)
    ap.add_argument("--per-query", type=int, default=4)
    args = ap.parse_args()

    tavily = TavilyClient(api_key=require_env("TAVILY_API_KEY"))
    sources = gather_sources(tavily, args.event, args.per_query)
    print(f"Tavily: {len(sources)} unique sources")
    for s in sources:
        print("  -", s["url"])

    source_block = "\n\n".join(f"[{i}] {s['url']}\n{s['title']}\n{s['content']}" for i, s in enumerate(sources))
    user = (
        f"Event: {args.event}\n\nSources:\n{source_block}\n\n"
        "Fill every field of the scenario schema."
    )

    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
    t0 = time.time()
    try:
        scenario, raw, reasoning, resp = structured_call(token_factory_client(), MODEL_SUPER, messages, Scenario)
    except (ValueError, ValidationError) as exc:
        raise SystemExit(f"[fail] {exc}")
    print(f"\nNemotron Super ({MODEL_SUPER}) {usage_line(resp)}, {time.time() - t0:.1f}s, reasoning {len(reasoning)} chars")

    problems, counts = check_citations(scenario, sources)
    print(f"\nFacts: {counts}")
    print(f"Citation check: {'PASS' if not problems else f'{len(problems)} problem(s)'}")
    for p in problems:
        print("  !", p)

    print("\n" + json.dumps(scenario.model_dump(), indent=2, ensure_ascii=False)[:3000])
    out = save_json("scenario_geomagnetic_2022.json", {
        "scenario": scenario.model_dump(),
        "citation_problems": problems,
        "fact_counts": counts,
        "reasoning": reasoning,
        "tavily_sources": sources,
        "model": MODEL_SUPER,
    })
    print(f"\nsaved {out.name}")


if __name__ == "__main__":
    main()

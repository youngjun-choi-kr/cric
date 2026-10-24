# CRIC feasibility spike

CRIC (Cosmic Response & Intelligence Console, working title): AI mission control for satellite conjunctions and space weather.

Three checks before committing to the satellite-operations idea.

| Step | Script | Needs key | Checks |
|---|---|---|---|
| 0 | `00_list_models.py` | Nebius | Token Factory key works; which Nemotron model IDs this account can call |
| 1 | `01_conjunctions.py` | none | Real CelesTrak orbits + SGP4 screening find close approaches fast enough |
| 2 | `02_scenario_tavily.py` | Nebius, Tavily | Tavily + Nemotron Super build a scenario where every fact cites a real source |
| 3 | `03_solar_vl.py` | Nebius | Nemotron Super VL reads a solar image and returns a structured, checkable action |
| 4 | `04_satellite_dossier.py` | Nebius, Tavily | Tavily Search + Extract find whether each object in a conjunction can manoeuvre; every fact cites a verbatim quote; a rule decides who moves |
| 5 | `05_avoidance.py` | none | Smallest burn that makes a conjunction safe, re-screened against the catalog; the physical consequence of the agent's decision |
| 6 | `06_agent.py` | Nebius | Nemotron Super plans the burn with tools (simulate / rescreen / submit); the harness re-verifies it and compares against a fixed rule, random search and exhaustive search |

## Setup

```bash
cd /Users/yj-choi/Desktop/nebius
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # then fill in NEBIUS_API_KEY and TAVILY_API_KEY
```

## Run

```bash
cd spike
../.venv/bin/python 00_list_models.py
../.venv/bin/python 01_conjunctions.py --hours 1
../.venv/bin/python 02_scenario_tavily.py
../.venv/bin/python 03_solar_vl.py                                    # live Sun + NOAA data
../.venv/bin/python 03_solar_vl.py --date 2022-01-29T23:30:00Z \
    --scenario ../data/scenario_geomagnetic_2022.json                 # around the flare behind the Feb 2022 storm
../.venv/bin/python 04_satellite_dossier.py                          # closest non-Starlink pair from step 1
../.venv/bin/python 04_satellite_dossier.py --no-tavily              # ablation: SATCAT only
../.venv/bin/python 04_satellite_dossier.py --norad 40536 42845      # explicit pair
../.venv/bin/python 05_avoidance.py --norad 40536 42845 --who 40536   # avoidance plan for KOMPSAT-3A
../.venv/bin/python 06_agent.py --norad 40536 42845 --who 40536       # Nemotron plans the burn, graded against baselines
```

Outputs go to `data/` (git-ignored).

## Pass criteria

- Step 0: the Super and VL model IDs resolve (otherwise set `MODEL_*` in `.env`).
- Step 1: a 1-hour window over all active satellites finishes in under a minute.
- Step 2: the scenario validates against the schema and the citation check passes, or fails only on fields that are clearly marked.
- Step 3: the reply validates, uses one of the allowed actions, and separates observed from inferred.
- Step 4: with Tavily, `has_propulsion` / `can_maneuver_now` become `sourced` (quote verified) for at least one object where the `--no-tavily` run said `unknown`, and the claim check passes.
- Step 5: nominal miss matches step 1; a plan reaches the safe miss distance with 0 new close approaches in the re-screen; the linear-model check against a two-body integration is within a few percent.
- Step 6: the agent's plan is accepted by the independent verifier (target miss reached, no new close approach) and the comparison table shows its simulate-call count and delta-v next to the baselines.

## Known limits

- GP/TLE data is accurate to about a kilometre; screening only, no covariance or collision probability.
- Most close approaches in the `active` group are Starlink-to-Starlink crossings that the operator coordinates; real risk ranking needs operator data we do not have.
- One EUV image cannot confirm an Earth-directed CME; Step 3 tests reasoning discipline, not forecasting skill.
- Step 5 treats burns as impulsive and uses a linear (Clohessy-Wiltshire) model on top of SGP4; the planner picks the *smallest* burn that just reaches the safe distance, so add margin (`--safe-km`) for real use.

## Web prototype

```bash
/Users/yj-choi/Desktop/nebius/.venv/bin/python /Users/yj-choi/Desktop/nebius/web/serve.py 8765   # http://localhost:8765
```

Globe + conjunction list, live Tavily research panel (`/api/dossier`), and avoidance manoeuvre planner (`/api/avoidance`).
- Step 6, prompt v1 (no search procedure) failed: the agent bisected the burn size at the minimum lead time, never varied the lead, and ran out of turns. Prompt v2 adds a coarse-scan-then-refine procedure (no answer values). The v1 run is kept in `data/agent_40536_42845_40536_prompt-v1.json`.
- Step 6 numbers come from a handful of runs of a stochastic model, not a statistical benchmark.

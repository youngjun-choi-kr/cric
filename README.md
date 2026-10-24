# CRIC — Cosmic Response & Intelligence Console

AI mission control for small satellite operators. CRIC watches real orbits and space weather, works out who can manoeuvre when two objects get close, plans a response, checks it in physics, and hands it to a human for approval.

Built for the [Nebius x NVIDIA Global AI Hackathon](https://nebiusglobalaihackathon.devpost.com/) (Physical AI track).

> Status: early development. The `spike/` folder holds the feasibility checks; the full architecture is in [ARCHITECTURE.md](ARCHITECTURE.md).

## How it uses Nebius and NVIDIA

| Piece | Model / service | Role |
|---|---|---|
| Triage | NVIDIA Nemotron 3 Nano on Nebius Token Factory | classify every incident cheaply |
| Space-weather perception | NVIDIA Nemotron 3.5 Super VL | read Sun images + NOAA data, drag risk with observed / inferred / unknown |
| Planning and dossiers | NVIDIA Nemotron 3 Super | tool-using planner; satellite capability facts with citations |
| Coordination reports | NVIDIA Nemotron 3 Ultra | rare, high-severity incidents only |
| Web grounding | Tavily Search + Extract | real-event scenarios and satellite facts, every fact cited with a verbatim quote |

Orbit maths never goes through an LLM: screening uses SGP4 on CelesTrak data, and safety rules are enforced in code.

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env                          # add NEBIUS_API_KEY and TAVILY_API_KEY (see SETUP.md)
.venv/bin/cric screen --hours 6               # real close approaches from CelesTrak
.venv/bin/cric incident --ours 40536 --other 42845 --mission-notes "No burns 09:05-09:30 UTC"
.venv/bin/python -m pytest -q                 # offline tests: physics and safety rules
```

An incident runs: SGP4 geometry -> Pc from an assumed covariance -> verified burn options from the optimiser
-> cited dossiers for both objects (Tavily + Nemotron, with a verifier repair loop) -> Nemotron decision
(MANEUVER / REQUEST_PRECISE_DATA / MONITOR / COORDINATE / ESCALATE) -> safety validator in code.

The numeric search is done by the optimiser, not the model: Nemotron chooses among options that physics has
already checked, using context the optimiser cannot read (policy and mission notes in plain language, evidence
quality, time left for better data). `spike/` keeps the original feasibility scripts.

## Data sources

CelesTrak GP and SATCAT, NOAA SWPC, NASA SDO via Helioviewer, and web pages found through Tavily. Orbit data is screening-grade (about 1 km); CRIC is a research prototype, not an operational collision-avoidance service.

## License

MIT, see [LICENSE](LICENSE).

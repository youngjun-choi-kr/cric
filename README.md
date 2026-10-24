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
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # add NEBIUS_API_KEY and TAVILY_API_KEY (see SETUP.md)
cd spike
../.venv/bin/python 00_list_models.py       # check Token Factory access
../.venv/bin/python 01_conjunctions.py --hours 1
```

See [spike/README.md](spike/README.md) for all feasibility steps.

## Data sources

CelesTrak GP and SATCAT, NOAA SWPC, NASA SDO via Helioviewer, and web pages found through Tavily. Orbit data is screening-grade (about 1 km); CRIC is a research prototype, not an operational collision-avoidance service.

## License

MIT, see [LICENSE](LICENSE).

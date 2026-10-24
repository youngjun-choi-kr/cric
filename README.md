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

## Results

Decision accuracy on scenarios built on real CelesTrak encounters (frozen snapshot in `fixtures/`). Each model
answer is graded raw and after the safety validator. "Policy-violating burns" are manoeuvres the scenario does not
allow under the operator policy, counted before -> after the validator.

| Set | Coded policy (fields only) | Nemotron 3 Super | Super + validator | Super policy-violating burns | Text-only cases: policy / Super |
|---|---|---|---|---|---|
| **Held-out** (9 scenarios x 5 runs, answers committed before running) | **0.89** | 0.82 | 0.82 | 0 -> 0 | 0.75 / **0.90** |
| Tuning (13 x 3, used to design the validator) | 0.77 | **0.92** | 0.92 | 0 -> 0 | 0.25 / **1.00** |
| Real storms via Tavily (4 x 3: 1989, 2003, 2022, 2024) | 1.00 | 0.58 | **1.00** | 5 -> **0** | - |

What this shows:
- Where the deciding fact is only in plain-language notes (including notes in Korean), Super reads it and a coded
  policy cannot.
- When Super is wrong on held-out cases it errs toward caution (asks for data, escalates), never a burn.
- On real storm replays Super burned 5 times on storm-degraded TLE data while there was time to get precise data;
  the validator stopped all 5. Rules that can be checked on structured fields belong in code, judgement over text
  belongs to the model.
- Nemotron 3 Nano was slower (median 32 s vs 12 s) and less accurate (0.69-0.72) for this task, so Super does the
  deciding and Ultra only writes the human-facing message when a person must act.

Reproduce: `cric eval --set heldout --models super,nano --repeats 5` (needs keys; about $0.15).
Limits and label choices: [LIMITATIONS.md](LIMITATIONS.md). Our feedback on the tools: [FEEDBACK.md](FEEDBACK.md).

## Console

```bash
.venv/bin/python web/serve.py 8765        # http://localhost:8765
```

Globe of every tracked object, the close-approach list, and the incident pipeline step by step: geometry, Pc,
verified burn options, cited dossiers, the Nemotron decision with its evidence, and the safety validator.
**Replay** streams a recorded run with no API calls (works when credits run out); **Run live** calls
Token Factory and Tavily. Four situations are recorded for EOS-08 / SITRO-AIS 38 (a real 17 m close approach
in the snapshot; in the demo we play the operator of EOS-08, see `fixtures/fleet.json`): quiet day (TLE only),
precise ephemeris, imaging pass blocking the cheapest burn, and safe mode.

Without the server the page runs as a static site (GitHub Pages or `python -m http.server` at the repo root,
then open `/web/`): it replays the recordings in `web/demo/` (refresh with `scripts/export_demo.py`).

## Data sources

CelesTrak GP and SATCAT, NOAA SWPC, NASA SDO via Helioviewer, and web pages found through Tavily. Orbit data is screening-grade (about 1 km); CRIC is a research prototype, not an operational collision-avoidance service.

## License

MIT, see [LICENSE](LICENSE).

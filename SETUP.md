# Run CRIC locally

The hosted pages replay recorded runs. To fly your own rocket, read your own notes or get a live decision, run it
on your machine. Python 3.11 or newer; tested on macOS with Python 3.14.

## 1. Install (about 20 s)

```bash
git clone https://github.com/youngjun-choi-kr/cric && cd cric
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q        # offline: no keys, no network for the core tests
```

The browser tests (`tests/test_web.py`) need Playwright and an installed Chrome:
`.venv/bin/pip install -e ".[film]"`; without them they are skipped.

## 2. Run it

```bash
.venv/bin/python web/serve.py 8765
```

- Mission Studio: http://localhost:8765/studio.html
- Operations console: http://localhost:8765/

The server only answers requests from this machine.

**Without any key** these work: flying any rocket (3-DOF ascent, guidance search, fixes, engine-out what-if,
500-flight dispersion with forecast winds), launch-window weather and collision screening (Open-Meteo, NOAA,
CelesTrak are public), putting the satellite in orbit with a 72 h screening, mission control burns, lifetime,
the photo planner, and every replay. Anything that needs a model answers with "NEBIUS_API_KEY is not set": reading a
sentence or a spec sheet, the launch-team votes, the notes reader, and CRIC's decisions on close approaches.

## 3. Keys (for the live model parts)

```bash
cp .env.example .env
```

Open `.env` and paste each key after `=` (no quotes). `.env` is in `.gitignore`.

| Key | What uses it | Where to get it |
|---|---|---|
| `NEBIUS_API_KEY` | every Nemotron call (Nano, Super, Ultra, Super VL) on Nebius Token Factory | https://tokenfactory.nebius.com, **Get API key** |
| `TAVILY_API_KEY` | web evidence for the other satellite's dossier | https://app.tavily.com (free tier: 1,000 credits a month) |
| `ELEVENLABS_API_KEY` | only `scripts/narrate.py`, the film's narration | https://elevenlabs.io, Developers, API Keys (Text to Speech and Speech to Text) |

Check the model connection:

```bash
cd archive/spike && ../../.venv/bin/python 00_list_models.py     # the configured models should all say [OK]
```

A live decision costs about $0.02 in model tokens and takes under a minute:

```bash
.venv/bin/cric incident --ours 60454 --other 61786      # EOS-08 x SITRO-AIS 38, decided 3 h before closest approach
```

## 4. Reproduce the results

```bash
.venv/bin/cric eval --set heldout3 --models super+hybrid --repeats 3     # one row of the README's comparison
.venv/bin/python scripts/live_new_input.py                               # the live run on a new note
.venv/bin/python scripts/validate_socrates.py                            # screening vs CelesTrak SOCRATES
```

Every published run is in `results/` (see `results/README.md`). The team's Korean key guide is in
[SETUP.ko.md](SETUP.ko.md).

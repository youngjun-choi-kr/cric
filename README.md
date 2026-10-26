# CRIC — Cosmic Response & Intelligence Console

AI mission control for small satellite operators. CRIC watches real orbits and space weather, works out who can manoeuvre when two objects get close, plans a response, checks it in physics, and hands it to a human for approval.

Built for the [Nebius x NVIDIA Global AI Hackathon](https://nebiusglobalaihackathon.devpost.com/) (Physical AI track).

**Live demo (no install, replays recorded runs): https://youngjun-choi-kr.github.io/cric/web/**
Pick a situation in the left panel and press *Replay recorded run*. Architecture: [ARCHITECTURE.md](ARCHITECTURE.md).

![How CRIC works: sense, read, two routes must agree, a person, act and sense again](docs/how-it-works.svg)

**In one minute**
- **Sense:** real orbits (CelesTrak + SGP4) find close approaches; code computes Pc, burn options checked against
  every other object, and storm drag from NASA's MSIS model.
- **Read:** Nemotron 3 Super turns the team's notes (any language) into constraints with exact quotes;
  Nemotron 3.5 Super VL does the same for screenshots. Code checks every quote.
- **Decide:** two independent routes must agree before anything spends propellant: Super's three votes after the
  hard rules, and the operator policy in code. If they disagree, a person decides, with a brief from Nemotron 3 Ultra.
- **Act and check:** a person approves; the burn is flown in simulation with real-world errors and the encounter
  is checked again. CRIC never commands a spacecraft.
- **Measured:** held-out sets fixed before running (the last one run once on the final pipeline), screening
  checked against CelesTrak SOCRATES, Pc against NASA CARA's test values. See Results.

**New: Mission Studio** (`/studio.html` on the local server): bring your own rocket, find out whether it reaches
orbit and where its stages fall, pick a launch window with weather and launch collision avoidance, then launch the
satellite into the same catalog CRIC operates. See [Mission Studio](#mission-studio).

## How it uses Nebius and NVIDIA

| Piece | Model / service | Role |
|---|---|---|
| Screen reading | NVIDIA Nemotron 3.5 Super VL on Nebius Token Factory | reads screenshots the team shares (planning calendars, telemetry panels, chats) into text that becomes enforceable constraints; 8/8 on our screenshot test. Its read of the Sun showed no forecasting signal, so it no longer runs by default |
| Decisions | NVIDIA Nemotron 3 Super | choose among physics-verified burn options, ask for data, coordinate or escalate |
| Satellite dossiers, real-event records | NVIDIA Nemotron 3 Super + Tavily | capability facts and storm records, every fact cited with a verbatim quote |
| Human-facing documents | NVIDIA Nemotron 3 Ultra | coordination message or escalation brief, only when a person must act |
| (evaluated, not routed) | NVIDIA Nemotron 3 Nano | slower and less accurate than Super for these decisions; see Results |
| Mission Studio | Nano · Super · Ultra · 3.5 Super VL | Nano reads the owner's plain-language target orbit; VL (images) or Super (PDF text) transcribes a rocket spec sheet; Super explains a failed flight and casts the weather, range and orbital-safety votes; Ultra is the mission director who writes the launch brief |
| Web grounding | Tavily Search + Extract | satellite facts and real space-weather events for the eval |

Orbit maths never goes through an LLM: screening uses SGP4 on CelesTrak data, and safety rules are enforced in code.

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env                          # add NEBIUS_API_KEY and TAVILY_API_KEY (see SETUP.md)
.venv/bin/cric screen --hours 6               # real close approaches from CelesTrak
.venv/bin/cric incident --ours 60454 --other 61786   # EOS-08 x SITRO-AIS 38, decided at TCA - 3 h
.venv/bin/python -m pytest -q                 # offline tests: physics and safety rules
```

An incident runs: SGP4 geometry -> Pc from an assumed covariance -> verified burn options from the optimiser
-> cited dossiers for both objects (Tavily + Nemotron, with a verifier repair loop) -> Nemotron decision
(MANEUVER / REQUEST_PRECISE_DATA / MONITOR / COORDINATE / ESCALATE), three independent votes, a split goes to a
person -> safety validator in code -> Ultra brief when a person must act -> approve / reject in the console.

**Unattended:** `cric watch` screens a fresh catalog every 30 minutes, runs the pipeline for any encounter with
our fleet, and notifies a person; it runs as a Nebius Serverless AI Job ([deploy/nebius](deploy/nebius/README.md)).

The numeric search is done by the optimiser, not the model: Nemotron chooses among options that physics has
already checked, using context the optimiser cannot read (policy and mission notes in plain language, evidence
quality, time left for better data). `archive/spike/` keeps the original day-1 feasibility scripts.

## Results

Scenarios are built on real CelesTrak encounters (frozen snapshot: 15,912 active satellites + 2,894 public debris
objects). Every arm ran each scenario 3 times; a scenario counts as passed when most runs are right after the
safety validator. Intervals are 95% Wilson **over scenarios, not runs**: these sets are small, and overlapping
intervals mean a difference is indicative, not proven. "Burns" are policy-violating burns that got past the
validator, summed over runs. Every answer is in [`results/`](results/README.md).

| Arm | **Held-out 3 (12)**, run once | Held-out 2 (8) | Held-out (9) | Tuning (13) | Storms (4) | Burns past the validator | $ per decision |
|---|---|---|---|---|---|---|---|
| Coded policy, structured fields only | 11/12 | 5/8 | 8/9 | 10/13 | 4/4 | 8 | 0 |
| Nemotron Super, one call | 9/12 | 8/8 | 7/9 | 11/13 | 4/4 | 2 | 0.0025 |
| Super, 3 votes | - | 8/8 | 8/9 | 11/13 | 4/4 | 2 | 0.0076 |
| Nano reads the notes, code decides | - | 7/8 | 8/9 | 12/13 | 4/4 | 1 | 0.0003 |
| Super reads the notes, code decides | **12/12** | 8/8 | 8/9 | **13/13** | 4/4 | 2 | 0.0005 |
| **CRIC pipeline**: votes and code must agree | 11/12 | 8/8 | 7/9 | 12/13 | 4/4 | **0 (138 runs)** | 0.0084 |

**Held-out 3** was written after the final design, on six new real encounters (353-819 km, 6-15 km/s, also
listed by SOCRATES), committed to GitHub before any model ran on it, and run exactly once on the final pipeline.
The earlier sets shaped the design (the agreement rule was set on them), so they are no longer clean tests.

What this shows, and what we chose:
- **Reading free text is where the model earns its place.** Super reading the notes into quoted constraints, with
  code deciding, was the most accurate arm on every set (12/12 on held-out 3) at a tenth of a cent a decision.
- **But a reading error becomes an unchecked action**: that arm burned after a ground-contact cut-off in 2 of
  27 held-out runs, because the validator trusts the fields it was given.
- **The CRIC pipeline requires two independent routes to agree** before anything spends propellant: Super's three
  votes after the hard rules, and the operator policy in code. It is the only arm with **zero policy-violating
  burns in all 138 runs**. Its misses are cautious: asking for precise data where monitoring was enough, or
  escalating when the routes split on a burn (unneeded escalations: 3% on held-out 3).
- The coded policy alone did well on held-out 3 (11/12) but burned once with a French note saying propulsion was
  unavailable; it cannot read.
- Intervals over scenarios are wide (held-out 3: 0.65-1.00 for 11/12); the burn counts are the clearest signal.

**Screening checked against an independent source.** CelesTrak SOCRATES ran at 2026-10-24 15:00, five minutes
before our snapshot. Of its 1,885 crossing pairs under 2 km in the same 24 h (both objects in our catalog), CRIC
found 1,879 (99.7%); all 6 misses involve "analyst" objects whose elements differ between data pulls. Times of
closest approach agree within 1 ms and miss distances within 1.1 m at the 95th percentile
(`scripts/validate_socrates.py`, `results/socrates.json`).

**Collision probability checked against NASA.** `cric cdm` reads a CCSDS Conjunction Data Message and computes Pc
by integrating the combined covariance over the hard-body disk. It reproduces NASA CARA's published unit-test
values (Omitron case 01: 2.706e-5 within 0.1%; the CDM of case 08: 2.27e-20 within 1%; `tests/test_cdm.py`). The
same exact integral now replaces the small-object shortcut CRIC used before, which overestimated Pc about 2x when
the covariance is as tight as precise ephemeris (no scenario's risk level changed). Against SOCRATES's maximum Pc
the ranking agrees only moderately (Spearman 0.39 over the 300 closest pairs): both are screening numbers from
different covariance assumptions.

**Does the Sun image add anything?** We tested Nemotron 3.5 Super VL on archived SDO images: 10 calm days before
G3-G5 storms and 10 calm days with no storm (2023-2026). Given the image alone, it rated drag risk "moderate"
for all 20. No forecasting signal, so CRIC no longer reads the Sun by default; the physics uses NOAA Kp through NASA's MSIS
thermosphere model (`scripts/eval_vl.py`). VL's job is now reading screenshots the team shares: on four rendered
screens (a planning calendar, a telemetry panel, a Korean chat, a dashboard) the transcript-to-constraints path
reached the right decision 8/8 times, where ignoring the image is right on 2 of 4 (`scripts/eval_screens.py`;
a small test we designed).

**Who writes the human-facing documents.** Every ESCALATE / COORDINATE case gets a brief from Super and from
Ultra (14 cases x 2 runs each). A code checker, not a model, verifies that every number comes from the context
and that the NORAD IDs, Pc with its basis, option and deadline are present. With the original prompt Ultra passed
86% and Super 7%; a reviewer pointed out that Super's misses were omissions a checklist could fix, so we added one
to the shared prompt and reran both: Ultra 100%, Super 82% (Super still dropped a NORAD ID or the Pc basis in 5
of 28). Neither invented a number. Ultra was faster (5.6 s vs 9.1 s median) at $0.0067 vs $0.0022 per brief;
briefs are written only when a person must act, so Ultra writes them (`python -m cric.eval.reports`).

**Web text is data, and we tested it.** Tavily results are tiered by source, instruction-like sentences (English
plus common phrasings in six other languages) are cut and logged before any model sees them, sources sit in tags a
page cannot close, quotes that read like instructions are rejected, and only agency, operator or reference pages
may decide whether another satellite can manoeuvre. `scripts/eval_injection.py` plants six injection styles in a
page about SITRO-AIS 38 (true answer: propulsion unknown) on an unknown site and on a trusted domain: with the
defences 0 of 24 attacks changed the verdict, without them 15 of 24 did. Our first run let a Korean injection
through 1 of 12 times; the multilingual patterns were added after that, so that case is not held-out
(`results/injection_eval_v1_before_fix.json`). A plain false fact on a trusted domain is still accepted:
misinformation is a different problem.

Limits and label choices: [LIMITATIONS.md](LIMITATIONS.md). Our feedback on the tools: [FEEDBACK.md](FEEDBACK.md).

## Console

```bash
.venv/bin/python web/serve.py 8765        # http://localhost:8765
```

Globe of every tracked object, the close-approach list, and the incident pipeline step by step: geometry, Pc,
verified burn options, cited dossiers, the Nemotron decision with its evidence, and the safety validator.
**Replay** streams a recorded run with no API calls (works when credits run out); **Run live** calls
Token Factory and Tavily. Recorded runs:
- **EOS-08 x SITRO-AIS 38** (EOS-08 is a ~175 kg Earth-observation satellite; 17 m on public data, which is
  accurate to about a kilometre): quiet day (ask for precise data), precise ephemeris and an imaging pass over
  the cheapest burn (coordinate, then the later burn; flown with errors and cleared), safe mode (escalate), the
  May 2024 storm replayed as tonight's weather (storm drag ~40 km: ask for data), a mission note in Korean, and a
  planning screenshot shared in chat (read by Nemotron VL).
- **CUTE-1 x FENGYUN 1C DEB** (a university CubeSat and debris from the 2007 anti-satellite test, 348 m on public
  data): red on screening data and nobody can confirm the CubeSat can move, so CRIC hands it to a person and
  asks for precise data; with precise data Pc is about 1e-8 and the answer is to monitor.
- Two more pairs (yellow and green Pc) show the non-burn paths.

In the demo we play the operator of EOS-08 (a fictional operator scenario; the console tags it on screen, and nothing here speaks for its real operator): its propulsion state comes from
`fixtures/fleet.json`, standing in for telemetry, not from the web. Every run ends with an approve / reject
step for a person; nothing is ever sent to a spacecraft.

Without the server the page runs as a static site (GitHub Pages or `python -m http.server` at the repo root,
then open `/web/`): it replays the recordings in `web/demo/` (refresh with `scripts/export_demo.py`).

## Mission Studio

```bash
.venv/bin/python web/serve.py 8765        # http://localhost:8765/studio.html
```

Three steps for someone who builds a small rocket or owns a cubesat:

1. **Your rocket.** Pick a published launcher (Nuri, Electron, SSLV, Firefly Alpha, or the fictional Garage One),
   drop a spec sheet photo or PDF (Nemotron reads it; every field is tagged *read*, *derived*, *assumed* or
   *missing*), or edit a form with sliders. Describe the orbit in a sentence ("my 5 kg cubesat, 500 km
   sun-synchronous, I live in Seoul"); Nemotron Nano turns it into a target and code computes the inclination.
   **Fly it**: a 3-DOF ascent (staging, drag, Earth rotation, guidance found by differential evolution) says
   whether it reaches orbit, where every stage comes down, and where the delta-v went. If it falls short, the
   studio searches for fixes and **re-flies each one** (the smallest bigger stage or lighter payload that reaches
   the orbit with a 50 m/s reserve); Super explains why. A Monte Carlo (thrust, Isp, mass, pitch, azimuth
   scatter) draws the impact areas.
2. **When and where.** A seven-day hourly go / caution / no-go map for every launch site that can reach the orbit
   over its sea corridor (Open-Meteo winds, rain, lightning potential, upper winds; NOAA Kp). Pick an hour and
   the studio screens every liftoff minute against the catalog for the first orbit after insertion (LCOLA): one
   minute can pass a kilometre or two from another satellite while a few minutes later is clear. Three launch-team roles vote
   on the candidate minutes and Nemotron 3 Ultra, as mission director, picks one; code refuses any pick that a
   check or a vote vetoed.
3. **Operate.** The insertion state becomes a TLE (mean elements fitted so SGP4 reproduces it), the satellite
   joins the catalog under a studio NORAD ID (99001+), and CRIC shows its passes over home and screens it for 72 h.
   Any close approach opens the same incident pipeline as the console (dossier from the owner's registration, not
   the web), ending in the owner's approve / reject.

How close the ascent model is to reality, measured by the payload it can place in each real launcher's published
orbit: Nuri −12%, Electron −8%, SSLV −2%, Alpha −20% (conservative throughout; `scripts/validate_vehicles.py`).
The dispersion also runs as a batch job: `cric launch-dispersion --vehicle electron --site mahia --runs 5000`
(2,000 runs in 12 s on an 8-core laptop), the shape of a Nebius Serverless Job. Limits: [LIMITATIONS.md](LIMITATIONS.md#mission-studio).

## Cinema mode (for the demo film)

The console's globe engine doubles as the film's camera. Shots are scripted, so a take renders the same frames
every time.

```bash
.venv/bin/python web/serve.py 8765
# preview:  http://localhost:8765/?cinema=1        keys 1-4 shot, Space pause, R restart, L letterbox, C caption, U console
# render:   http://localhost:8765/?cinema=1&record=find-pair&fps=60&w=3840&h=2160   -> data/frames/find-pair/
ffmpeg -framerate 60 -i data/frames/find-pair/%05d.png -c:v libx264 -pix_fmt yuv420p -crf 12 find-pair.mp4
```

Shots: `overview-push`, `find-pair`, `go-to-burn` (chase camera on the post-burn path, exaggerated x200),
`pass-and-pullback`. For screen captures of the console: `?ui=1.5` scales the panels, the incident panel has replay
pace (0.5x / 1x / 2x) and pause, and each step briefly highlights the element it fills. Two extra recorded
situations exist for the film: the May 2024 G5 storm replayed as tonight's weather, and a mission note in Korean.

## Data sources

CelesTrak GP and SATCAT, NOAA SWPC, NASA SDO via Helioviewer, Open-Meteo forecasts, published launcher figures
(Wikipedia and makers' pages, linked per preset), and web pages found through Tavily. Orbit data is screening-grade (about 1 km); CRIC is a research prototype, not an operational collision-avoidance service.

## License

MIT, see [LICENSE](LICENSE).

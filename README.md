# CRIC — Cosmic Response & Intelligence Console

AI mission control for small satellite operators. CRIC watches real orbits and space weather, works out who can manoeuvre when two objects get close, plans a response, checks it in physics, and hands it to a human for approval.

Built for the [Nebius x NVIDIA Global AI Hackathon](https://nebiusglobalaihackathon.devpost.com/) (Physical AI track).

**Live demo (no install, replays recorded runs): https://youngjun-choi-kr.github.io/cric/web/**
Pick a situation in the left panel and press *Replay recorded run*. Architecture: [ARCHITECTURE.md](ARCHITECTURE.md).

## How it uses Nebius and NVIDIA

| Piece | Model / service | Role |
|---|---|---|
| Space-weather perception | NVIDIA Nemotron 3.5 Super VL on Nebius Token Factory | read the latest SDO image with NOAA Kp and alerts in plain language for the operator; observed / inferred / unknown. It never sets Pc (NOAA Kp does). A retrospective test found no forecasting signal in the image read (see Results) |
| Decisions | NVIDIA Nemotron 3 Super | choose among physics-verified burn options, ask for data, coordinate or escalate |
| Satellite dossiers, real-event records | NVIDIA Nemotron 3 Super + Tavily | capability facts and storm records, every fact cited with a verbatim quote |
| Human-facing documents | NVIDIA Nemotron 3 Ultra | coordination message or escalation brief, only when a person must act |
| (evaluated, not routed) | NVIDIA Nemotron 3 Nano | slower and less accurate than Super for these decisions; see Results |
| Web grounding | Tavily Search + Extract | satellite facts and real space-weather events for the eval |

Orbit maths never goes through an LLM: screening uses SGP4 on CelesTrak data, and safety rules are enforced in code.

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env                          # add NEBIUS_API_KEY and TAVILY_API_KEY (see SETUP.md)
.venv/bin/cric screen --hours 6               # real close approaches from CelesTrak
.venv/bin/cric incident --ours 43250 --other 34430   # IRIDIUM 149 x COSMOS 2251 DEB, decided at TCA - 3 h
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

| Arm | Held-out 2 (8) | Held-out (9) | Tuning (13) | Storms (4) | Burns past the validator | $ per decision |
|---|---|---|---|---|---|---|
| Coded policy, structured fields only | 5/8 | 8/9 | 10/13 | 4/4 | 7 | 0 |
| Nemotron Super, one call | 8/8 | 7/9 | 11/13 | 4/4 | 1 | 0.0025 |
| Super, 3 votes | 8/8 | 8/9 | 11/13 | 4/4 | 2 | 0.0076 |
| Nano reads the notes, code decides | 7/8 | 8/9 | 12/13 | 4/4 | 1 | 0.0003 |
| Super reads the notes, code decides | 8/8 | 8/9 | **13/13** | 4/4 | 2 | 0.0006 |
| **CRIC pipeline**: votes and code must agree | 7/8 | 6/9 | 11/13 | 4/4 | **0** | 0.0084 |

What this shows, and what we chose:
- **Reading free text is where the model earns its place.** Notes in Korean, Japanese, Spanish and ops jargon
  decide held-out 2; the coded policy, which cannot read them, passes 5 of 8. Letting Super only *read* the
  notes into quoted constraints and having code decide was the most accurate arm at a tenth of the cost.
- **But a reading error becomes an unchecked action.** In 2 of 27 held-out runs that arm extracted a contact
  cut-off as the wrong kind of constraint and burned after the cut-off; the validator trusts the extracted
  fields, so nothing caught it.
- **The CRIC pipeline therefore requires two independent routes to agree**: the model's 3-vote decision after
  the hard rules, and the operator policy in code over the same fields. It is the only arm with **zero
  policy-violating burns in all 102 runs**. Every miss is an escalation to a person (for example when the
  routes pick different fallback burns), never a wrong action. We accept more escalations for that.
- **Voting buys consistency, not accuracy**: same scenarios passed, stability up (tuning 0.54 -> 0.69,
  held-out 0.78 -> 0.89).
- Statistically, none of these differences is proven on sets this size; the burn counts are the clearest signal.

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
for all 20. No forecasting signal, so the image read only explains conditions to the operator; the physics uses
NOAA Kp through NASA's MSIS thermosphere model (`scripts/eval_vl.py`).

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
- **IRIDIUM 149 x COSMOS 2251 DEB** (debris from the 2009 Iridium-Cosmos collision; 548 m on public TLE data):
  the TLE Pc is red and two of three Super votes wanted to burn; the validator turned that into a request for
  precise data, and with precise ephemeris the Pc drops to about 1e-31 (monitor). Public data finds candidates;
  it does not decide burns.
- **EOS-08 x SITRO-AIS 38** (17 m on TLE data, which is accurate to about a kilometre): quiet day, precise
  ephemeris, an imaging pass over the cheapest burn, safe mode, the May 2024 storm replayed as tonight's weather,
  and a mission note in Korean.
- Two more pairs (yellow and green Pc) show the non-burn paths.

In the demo we play the operator of EOS-08 and IRIDIUM 149 (a fictional operator scenario; the console tags it on screen, and nothing here speaks for their real operators): their propulsion state comes from
`fixtures/fleet.json`, standing in for telemetry, not from the web. Every run ends with an approve / reject
step for a person; nothing is ever sent to a spacecraft.

Without the server the page runs as a static site (GitHub Pages or `python -m http.server` at the repo root,
then open `/web/`): it replays the recordings in `web/demo/` (refresh with `scripts/export_demo.py`).

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

CelesTrak GP and SATCAT, NOAA SWPC, NASA SDO via Helioviewer, and web pages found through Tavily. Orbit data is screening-grade (about 1 km); CRIC is a research prototype, not an operational collision-avoidance service.

## License

MIT, see [LICENSE](LICENSE).

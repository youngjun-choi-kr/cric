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

Decision accuracy on scenarios built on real CelesTrak encounters (frozen snapshot in `fixtures/`: 15,912 active
satellites + 2,894 public debris objects). Each answer is graded raw and after the safety validator.
"Policy-violating burns" are manoeuvres the operator policy does not allow, counted before -> after the validator.
Nemotron 3 Super, 5 runs per scenario; "3 votes" is the majority of 3 independent runs (every 3-run combination of
those 5), which is how the pipeline decides.

| Set | Coded policy (fields only) | Super | Super + validator (95% CI) | 3 votes + validator | Policy-violating burns |
|---|---|---|---|---|---|
| **Held-out 2** (8 x 5, written after the design froze, before any model ran) | 0.62 | 0.90 | **1.00** (0.91-1.00) | **1.00** | 0 -> 0 |
| **Held-out** (9 x 5, answers committed before running) | **0.89** | 0.82 | 0.82 (0.69-0.91) | 0.86 | 0 -> 0 |
| Tuning (13 x 5, used to design the validator) | 0.77 | 0.86 | 0.91 | **0.95** | 2 -> 1 |
| Real storms via Tavily (4 x 5: 1989, 2003, 2022, 2024) | 1.00 | 0.85 | **1.00** | **1.00** | 3 -> 0 |

What this shows:
- Where the deciding fact is only in plain-language notes (Korean, Japanese, Spanish, ops jargon, chat messages),
  Super reads it and a coded policy cannot: on held-out 2 the policy fails 3 of 8, Super + validator fails none.
- The validator catches what can be checked on fields: on storm replays Super burned 3 times on storm-degraded TLE
  data with time left to get precise data; the validator stopped all 3.
- What the validator cannot catch: a constraint that exists only in text. In 1 of 5 runs of S11 Super burned
  0.148 m/s although a note required ops-lead approval above 0.1 m/s. The other 4 runs escalated, so no 3-vote
  majority burns; this is why the pipeline votes.
- On the first held-out set the coded policy is ahead (0.89 vs 0.82-0.86). Super's misses there are cautious
  (escalate or ask for data instead of monitor), never a burn.
- Nemotron 3 Nano was slower (median 32 s vs 12 s) and less accurate (0.69 on held-out, in the run before the debris snapshot), so Super decides.

Reproduce: `cric eval --set heldout2 --models super --repeats 5` (needs keys; about $0.10 per set);
`cric eval --regrade <file>` re-applies the current validator to saved answers without new model calls.

**Who writes the human-facing documents.** Every ESCALATE / COORDINATE case gets a brief from Super and from
Ultra (14 cases x 2 runs each). A code checker, not a model, verifies that every number comes from the context
and that the NORAD IDs, Pc with its basis, option and deadline are present. Ultra passed 86%, Super 7%: Super
never invented a number but routinely left out the IDs and how Pc was computed. Ultra was also faster (5.9 s vs
13.2 s median) at $0.0067 vs $0.0024 per report, so Ultra writes them (`python -m cric.eval.reports`).

**Web text is data.** Tavily results are tiered by source, instruction-like sentences are cut and logged before
any model sees them, sources sit in tags a page cannot close, and the claim checker rejects quotes that read like
instructions (`src/cric/research/untrusted.py`).

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

# Limitations

CRIC is a research prototype for decision support. It is not an operational collision-avoidance service.

## Data
- Orbits come from public CelesTrak GP/TLE data, accurate to roughly a kilometre. Close-approach distances
  from this data are screening-grade.
- No real CDM for our encounters was available. `cric cdm` reads CDMs and was checked on NASA's sample CDMs, but the eval's "precise data" still uses a **simulated** covariance. Pc otherwise uses an **assumed** covariance (TLE age and Kp driven). The
  "precise data" mode in the eval uses a **simulated** CDM-grade covariance (~100 m along-track), not real data.
- Debris: the snapshot includes the public debris groups CelesTrak redistributes (Fengyun-1C, Cosmos 2251,
  Iridium 33, Cosmos 1408 and "analyst" objects: 2,894 objects). The full debris catalog needs Space-Track
  access and cannot be redistributed, so most of the ~30,000 tracked pieces are not screened.
- Public TLE data never decides a burn while there is time to get precise ephemeris (policy text and a validator
  rule). The demo's "precise" runs use a simulated CDM; no real CDM was available to us.
- Most close approaches in the active catalog are Starlink-to-Starlink crossings that SpaceX coordinates
  internally; CRIC does not rank them by operator knowledge it does not have.

## Physics
- Burns are impulsive; no finite-burn duration or execution error.
- A burn's effect is a Clohessy-Wiltshire offset on top of the SGP4 path. Checked against a two-body
  integration (about 1% error for the burns tested), valid for hours to about a day after the burn.
- Re-screening covers catalogued objects whose altitude band overlaps; uncatalogued debris is not modelled.
- Pc is Foster's 2D integral computed exactly over the hard-body disk (20 m combined radius), verified against NASA CARA test values; it is a 2D method, and NASA's case 08 shows 3D effects can make the true Pc far larger for slow, long encounters. Each Pc
  also comes with a sensitivity sweep (assumed sigmas x0.5-x2, combined radius 10-40 m); the console shows the
  range and whether the risk level holds across it.
- Screening bug fixed on 2026-10-25: the coarse pass kept one sample per pair over the whole window, so a later,
  wider pass of the same pair could hide the real encounter (it hid IRIDIUM 149 / COSMOS 2251 DEB at 548 m).
  It now refines every pass. Rescreening the snapshot with the same catalog and start time raised the 24 h list
  from 998 to 2,067 encounters under 2 km; none were lost, and the eval encounters are unchanged.

## Research (dossiers)
- Capability facts come from web pages found by Tavily. A fact counts only if the quote is verbatim in the
  page, the source is not social media, and the page is about this exact satellite (not its series).
  Unknown capability is never guessed; it leads to escalation.
- Nemotron sometimes writes a fact into the summary but leaves the field empty; a verifier sends the
  problems back for one repair round. Remaining problems are kept in the record.

## Decisions
- Nemotron's answers vary between runs at the same input; the eval repeats each case and reports the spread
  as stability. The pipeline takes the majority of 3 independent decisions and escalates a split; the "3 votes"
  arm in the eval makes 3 real, independent calls per run. Demo recordings were not re-recorded to get a preferred answer; the IRIDIUM 149
  quiet run was re-recorded once, after adding the satellite to the demo fleet (the first run escalated
  correctly because its propulsion was unknown).
- Retakes: during development the EOS-08 "imaging" situation was recorded three times on 2026-10-24 with
  different results while the dossier and fleet-registry code was being fixed, and the demo showed the last. The
  demo situations were then re-recorded once each after every pipeline change (2026-10-25, and on 2026-10-26 for
  the final pipeline with constraints from notes, the agreement check, MSIS drag and the execution check); the
  latest single takes replaced all earlier ones and were kept whatever they said. On the final pipeline EOS-08
  "precise" escalates because the two routes chose different fallback burns, and the green pair escalates on a
  three-way vote split.
- Constraints in free text are now read into quoted fields that code enforces, but a reading error becomes a
  wrong field the validator trusts: on held-out H03, "Super reads notes, code decides" burned after a contact
  cut-off in 2 of 3 runs. The pipeline's agreement check (model votes vs coded policy) caught every such case in
  the final runs, at the price of more escalations (held-out 6/9 scenarios, all misses escalations).
- Eval statistics: intervals are over scenarios (8-13 per set), so no difference between arms is statistically
  established; earlier versions of this README computed intervals over repeated runs, which overstated certainty.
- Voting and the agreement check cost about $0.008 per decision (one call: $0.0025; reading notes only: $0.0006).
- The eval's "policy-violating burns" count manoeuvres the scenario does not accept under the operator policy
  (for example burning on storm-degraded TLE data while there is time to get precise data). Some operators would
  burn in those cases; they are policy violations, not necessarily collision risks. Earlier commits called this
  "unsafe".
- Held-out label H02 (yellow Pc, one hour left) expects MONITOR or ESCALATE; the model consistently asks for
  precise data instead, which is harmless. We kept the label fixed before running rather than relaxing it after.
- The eval scenarios set capabilities, notes and data quality by hand on top of real encounters. Expected
  answers follow the operator policy in `cric/pipeline.py` and common conjunction-assessment practice;
  they are our labels, not an external benchmark.
- Hard rules (verified propulsion, verified option, upload window, structured blackouts, coordination)
  are enforced in code by `cric.safety.validator`. Constraints written only in free text are not
  enforced by the validator; that is exactly where the model has to read.

## Data snapshot
- The first eval and demo runs used a 2026-10-24 CelesTrak file that a later refresh overwrote; that file was not
  saved and cannot be recovered (the KOMPSAT-3A / LEMUR-2 encounter moved from 0.106 km to 12.4 km after the
  refresh). Eval and demo now read the committed snapshot in `fixtures/snapshot_20261025`. Encounters were
  re-selected there, note times became relative to the burn options, and H01's burn cap was made relative so its
  intent holds; expected answers did not change. Numbers in README come from runs on this snapshot.

## Space-weather image read
- Retrospective test (`scripts/eval_vl.py`, `results/vl_eval.json`): on 10 calm days before G3-G5 storms and 10
  calm days with no storm, both in 2023-2026, Nemotron 3.5 Super VL rated LEO drag risk "moderate" for all 20
  images when given the SDO image alone. It does not discriminate, so CRIC uses it only to explain conditions;
  the recheck flag fires only on a "high" or "severe" read, which did not occur in the test.

## Reports
- The report comparison (Ultra vs Super, `cric.eval.reports`) checks grounding and completeness by code. It does
  not judge tone or usefulness, and it counts correct arithmetic derived from the context (for example "2.5 min
  left") as unsupported, which is deliberately strict.

## Prompt injection
- Web text is tiered, filtered for instruction-like sentences, and wrapped as data; quotes that read like
  instructions are rejected. Tested on six injection styles (0/24 succeeded with the defences, 15/24 without), but
  the filter is pattern-based: the Korean pattern was added after a Korean injection got through once, and a new
  phrasing in another language could pass. What stops an unsafe action is the validator and the human approval
  step, not the filter.
- Misinformation is not injection: a page on a trusted domain that simply states a false capability is accepted
  (2/2 in the test). Pages on unknown sites cannot decide capability.

## Deployment
- `cric eval --batch` submits an eval set as one Token Factory batch job; batch creation was refused by the
  service ("temporarily unavailable") both times we tried, so that path has not run end to end.
- `cric watch` and the Dockerfile are written for a Nebius Serverless AI Job. The image builds (in CI on every push)
  and the loop ran inside it in dry-run mode; nothing was run on Nebius AI Cloud.
- The approval step logs a decision; it does not send anything. There is no uplink.

## History
- An earlier spike let Nemotron search burn parameters with tools. It found a valid burn but used 45% more
  delta-v than exhaustive search, took about 2 minutes, and its first prompt version failed outright.
  Numeric search was moved to the optimiser; see ARCHITECTURE.md section 4.

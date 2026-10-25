# Limitations

CRIC is a research prototype for decision support. It is not an operational collision-avoidance service.

## Data
- Orbits come from public CelesTrak GP/TLE data, accurate to roughly a kilometre. Close-approach distances
  from this data are screening-grade.
- There is no real covariance or CDM. Pc uses an **assumed** covariance (TLE age and Kp driven). The
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
- The Pc formula is the small-object approximation of Foster's 2D integral with a 20 m combined radius. Each Pc
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
  with 95% Wilson intervals. The pipeline takes the majority of 3 independent decisions and escalates a split.
  "3 votes" accuracy in the README is computed from the 5 repeats already collected (all 3-run combinations),
  not from separate 3-call runs. Demo recordings were not re-recorded to get a preferred answer; the IRIDIUM 149
  quiet run was re-recorded once, after adding the satellite to the demo fleet (the first run escalated
  correctly because its propulsion was unknown).
- Retakes: during development the EOS-08 "imaging" situation was recorded three times on 2026-10-24 with
  different results (ESCALATE, ESCALATE, MANEUVER) while the dossier and fleet-registry code was being fixed, and
  the demo showed the last. On 2026-10-25 every EOS-08 situation was re-recorded once on the final pipeline
  (3 votes, debris snapshot, TLE rule) and those single takes replaced all earlier ones: quiet -> request data,
  precise -> manoeuvre O1, imaging -> manoeuvre O2, safe mode -> escalate, May 2024 storm -> request data,
  Korean note -> coordinate on O2.
- A constraint that exists only in free text cannot be enforced by the validator. On tuning scenario S11 one of
  five runs burned above a 0.1 m/s approval limit written in a mission note; voting removes it (no 3-run
  majority burns), but a single run can still make that mistake.
- Majority voting triples decision cost (about $0.007 per incident instead of $0.0025).
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

## Reports
- The report comparison (Ultra vs Super, `cric.eval.reports`) checks grounding and completeness by code. It does
  not judge tone or usefulness, and it counts correct arithmetic derived from the context (for example "2.5 min
  left") as unsupported, which is deliberately strict.

## Prompt injection
- Web text is tiered, filtered for instruction-like sentences, and wrapped as data; quotes that read like
  instructions are rejected. The filter is pattern-based and can be evaded by a determined page. What stops an
  unsafe action is the validator and the human approval step, not the filter.

## Deployment
- `cric watch` and the Dockerfile are written for a Nebius Serverless AI Job. The loop was run locally in dry-run
  mode; the image was not built (no Docker daemon on the dev machine) and nothing was run on Nebius AI Cloud.
- The approval step logs a decision; it does not send anything. There is no uplink.

## History
- An earlier spike let Nemotron search burn parameters with tools. It found a valid burn but used 45% more
  delta-v than exhaustive search, took about 2 minutes, and its first prompt version failed outright.
  Numeric search was moved to the optimiser; see ARCHITECTURE.md section 4.

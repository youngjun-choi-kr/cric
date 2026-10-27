# Limitations

CRIC is a research prototype for decision support. It is not an operational collision-avoidance service.

## Data
- Orbits come from public CelesTrak GP/TLE data, accurate to roughly a kilometer. Close-approach distances
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
- Re-screening covers cataloged objects whose altitude band overlaps; uncatalogued debris is not modeled.
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
  latest single takes replaced all earlier ones and were kept whatever they said. A last round on 2026-10-26
  followed agreement v2 and the exact Pc; the EOS-08 imaging take was then recorded once more after we found the
  pipeline displayed but did not apply a "notify first" agreement outcome (fixed, with a test). IRIDIUM 149 left
  the demo fleet (a large-constellation satellite contradicted the small-operator story); its recordings are
  archived, and CUTE-1, a university CubeSat with its real, unknown propulsion state, took its place.
- Constraints in free text are now read into quoted fields that code enforces, but a reading error becomes a
  wrong field the validator trusts: on held-out H03, "Super reads notes, code decides" burned after a contact
  cut-off in 2 of 3 runs. The pipeline's agreement check (model votes vs coded policy) caught every such case in
  the final runs, at the price of more escalations (held-out 6/9 scenarios, all misses escalations).
- Eval statistics: intervals are over scenarios (8-13 per set), so no difference between arms is statistically
  established; earlier versions of this README computed intervals over repeated runs, which overstated certainty.
- Voting and the agreement check cost about $0.008 per decision (one call: $0.0025; reading notes only: $0.0006).
- The eval's "policy-violating burns" count maneuvers the scenario does not accept under the operator policy
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

## Speed
- Screening the 24 h window for 18,806 objects takes about 70 s on an 8-core laptop (coarse pass in parallel, and
  only passes whose straight-line prediction comes within 4 km are refined with SGP4; same 2,067 encounters as the
  full refinement). "Real time" in CRIC means a fresh 24 h screen every 30 minutes, not a continuous stream.

## Fixed physical assumptions
- Storm drag assumes F10.7 = 150 sfu and B = 0.02 m^2/kg; the execution check assumes 3% / 1° / 2 s errors (1 sigma,
  our assumption, not a thruster datasheet). `results/sensitivity.md` varies them: the storm term moves by more
  than an order of magnitude but stays far above the TLE error, and the recorded burns stay cleared at 4x the
  default execution errors.

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

## Mission Studio
- The ascent is 3-DOF in one plane: no yaw steering or dog-legs, no winds in the powered flight (winds aloft only
  move falling stages), the US 1976 standard atmosphere, a constant drag coefficient. Guidance is a four-number
  pitch program found by an optimizer, not real closed-loop guidance; the engine-out what-if flies the same plan
  without re-planning. Against published capacities it is within -17% to +4% (README); for a rocket that has never
  flown, treat "reaches orbit" as a first check, not a qualification.
- Wind drift of falling stages uses terminal speed below 12 km and the forecast profile at five pressure levels,
  with 20% speed and 15 deg direction error per run; break-up and lift are ignored.
- LCOLA collision probability uses assumed uncertainties (new satellite 1 / 6 / 1 km radial / along-track /
  normal, catalog 0.4 / 2 / 0.4 km) and a 10 m hard-body radius; real values come from the launch provider.
- Preset numbers mix published figures with derived and assumed ones; each field says which. Garage One is
  fictional.
- Spec-sheet reading copies numbers it can see; gaps are filled with labeled defaults and listed as missing. A
  misread unit would propagate; the form shows every value for the owner to check.
- Dispersion sigmas (thrust 1.5%, Isp 0.5%, dry mass 2%, pitch 0.3 deg, azimuth 0.3 deg) are assumptions, not
  any vehicle's data. Impacts ignore wind drift and break-up.
- LTAN/LTDN comes from the simulated insertion state (osculating elements) and a low-precision Sun position: good
  to a few minutes, not an orbit-design tool; drift of the node over the mission is not modeled.
- Photo opportunities use pass elevation and the Sun at home only (no clouds, sensor geometry or off-nadir limits).
- The land check uses Natural Earth 1:50m outlines: small islands and coastlines are approximate (a few km), and it
  ignores wind drift and debris spread beyond the dispersion runs. A range's hazard areas are larger and official.
- Team notes are read by a model and checked in code (quote present, no instruction-like text, values in range).
  A note that is wrong but well-formed (a closure typed with the wrong hour) is applied as written.
- Launch commit criteria are simplified assumptions for a small launcher (CRITERIA in `cric/launch/windows.py`);
  real ranges use their own, stricter rules (lightning field mills, cloud ceilings, toxic-plume corridors). Site
  corridors are coarse azimuth ranges over the sea, not range safety analysis.
- Weather is a public numerical forecast at the site's coordinates; Kp is forecast only about three days ahead,
  later hours are graded on weather alone.
- LCOLA and the 72 h screening use public TLE data (km-level) and the catalog snapshot the server loaded; the
  thresholds (no-go under 2 km, caution under 10 km) are assumed. A real launch gets LCOLA from a provider with
  covariances. The inserted orbit is the nominal flight, not the dispersed one.
- The launched satellite is synthetic: it exists in `data/studio/fleet.json` and in screening, not in the public
  catalog. Its capability is what the owner states.
- Mission control burns are impulsive (instant change of velocity) and perfect; a real thruster has pointing and
  magnitude errors and burns over minutes. The new TLE is fitted to the post-burn state, so the orbit after a burn
  is as good as SGP4 from that state. The 72 h screen samples every 60 s with straight-line refinement between
  samples (checked to give the same encounters as 20 s sampling on the JIMIN-1 orbit) and drops slow co-orbital
  approaches (under 1 km/s). The safer-time search tries shifts of 5 to 30 minutes, not other sizes or directions.
- Lifetime uses MSIS density at a fixed solar-cycle-average activity (F10.7 150, Ap 15) and a cross-section guessed
  from the mass; the real Sun can halve or double it. The 5-year rule is applied as "comes down within 5 years",
  with no allowance for the mission's own length. Propellant defaults (4% of mass, Isp 220 s) are assumptions.
- Photo bookings and the command log's effects live in the Studio only; nothing is sent to a spacecraft.

## History
- An earlier spike let Nemotron search burn parameters with tools. It found a valid burn but used 45% more
  delta-v than exhaustive search, took about 2 minutes, and its first prompt version failed outright.
  Numeric search was moved to the optimizer; see ARCHITECTURE.md section 4.

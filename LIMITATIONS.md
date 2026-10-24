# Limitations

CRIC is a research prototype for decision support. It is not an operational collision-avoidance service.

## Data
- Orbits come from public CelesTrak GP/TLE data, accurate to roughly a kilometre. Close-approach distances
  from this data are screening-grade.
- There is no real covariance or CDM. Pc uses an **assumed** covariance (TLE age and Kp driven). The
  "precise data" mode in the eval uses a **simulated** CDM-grade covariance (~100 m along-track), not real data.
- Most close approaches in the active catalog are Starlink-to-Starlink crossings that SpaceX coordinates
  internally; CRIC does not rank them by operator knowledge it does not have.

## Physics
- Burns are impulsive; no finite-burn duration or execution error.
- A burn's effect is a Clohessy-Wiltshire offset on top of the SGP4 path. Checked against a two-body
  integration (about 1% error for the burns tested), valid for hours to about a day after the burn.
- Re-screening covers catalogued objects whose altitude band overlaps; uncatalogued debris is not modelled.
- The Pc formula is the small-object approximation of Foster's 2D integral with a 20 m combined radius.

## Research (dossiers)
- Capability facts come from web pages found by Tavily. A fact counts only if the quote is verbatim in the
  page, the source is not social media, and the page is about this exact satellite (not its series).
  Unknown capability is never guessed; it leads to escalation.
- Nemotron sometimes writes a fact into the summary but leaves the field empty; a verifier sends the
  problems back for one repair round. Remaining problems are kept in the record.

## Decisions
- Nemotron's answers vary between runs at the same input; the eval repeats each case and reports the spread.
- The eval scenarios set capabilities, notes and data quality by hand on top of real encounters. Expected
  answers follow the operator policy in `cric/pipeline.py` and common conjunction-assessment practice;
  they are our labels, not an external benchmark.
- Hard rules (verified propulsion, verified option, upload window, structured blackouts, coordination)
  are enforced in code by `cric.safety.validator`. Constraints written only in free text are not
  enforced by the validator; that is exactly where the model has to read.

## History
- An earlier spike let Nemotron search burn parameters with tools. It found a valid burn but used 45% more
  delta-v than exhaustive search, took about 2 minutes, and its first prompt version failed outright.
  Numeric search was moved to the optimiser; see ARCHITECTURE.md section 4.

# CRIC's operator policy, checked against NASA's best practices

CRIC's rules were written by us. To check them against how operators are told to work, we compared each one with
the NASA *Spacecraft Conjunction Assessment and Collision Avoidance Best Practices Handbook*, Volume 1, Rev 2
(NASA-SP-20205011318/REV2-VOL1, 2026; [NTRS](https://ntrs.nasa.gov/citations/20260000453)) and NASA CARA's public
guidance ([CARA risk assessment](https://www.nasa.gov/cara/step-2-close-approach-risk-assessment)). This is a
document check, not an operator interview; we have no operator feedback yet.

| CRIC rule (where it lives) | NASA guidance | Agreement |
|---|---|---|
| Act when Pc >= 1e-4; yellow from 1e-5 (`physics/risk.py`, `DEFAULT_POLICY`) | "Pursue mitigation if the Pc value exceeds 1E-04 (1 in 10,000)." (§6); CARA: values above 1E-07 "merit operational attention" | Same mitigation threshold. Our yellow band (1e-5) is stricter attention than CARA's 1e-7 notice level would suggest for alerting, looser for display. |
| Public TLE data never decides a burn while there is time to get precise data (`safety/validator.py`) | TLE "accuracy is not sufficient to perform the necessary conjunction assessment calculations" (§1); TLE-based solutions are "not sufficiently accurate" (§6) | Agrees. CRIC still screens with TLEs, as candidates only. |
| Notify the other operator before moving if it can manoeuvre or is operational (`validator`, coded policy) | "active coordination for every maneuver is required to prevent ... simultaneous maneuvers" (§4, recurring conjunctions between active spacecraft) | Agrees in spirit; the handbook states it for co-located active spacecraft. |
| Every burn option is re-screened against the catalog (`physics/maneuver.py`) | mitigate "while ensuring that no new conjunctions are produced that exceed the threshold" (§6) | Agrees. The handbook prefers a special screening by the provider; CRIC can only re-screen the public catalog. |
| After a burn, Pc should be <= 3.2e-6 (`physics/execute.py`, added after this review) | "remediate it to a Pc 1.5 orders of magnitude below the threshold" (§6) | Now checked after approval, on the executed burn with errors. Burn options still target a 2 km miss rather than the Pc goal directly. |
| A person approves every burn; disagreement escalates (console, `agent/constraints.agree`) | automated systems must "include the ability to pause or abort any maneuver planned for execution" (§6) | CRIC goes further: it never commands, a person always approves. |

## Where CRIC falls short of the handbook
- The handbook expects operators to work with USSPACECOM CDMs (special perturbations, real covariance). CRIC has
  no such access: it screens public data and only *reads* CDMs (`cric cdm`, checked on NASA's samples).
- Burn options are sized to a 2 km miss, not to the post-mitigation Pc goal; the goal is checked afterwards.
- Pc is 2D (Foster). The handbook's appendices discuss 3D Pc for slow encounters; NASA's case 08 shows the 2D
  value can be 15 orders of magnitude too low there. CRIC flags none of this yet.

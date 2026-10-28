# Devpost submission draft

## Project overview
- **Name:** CRIC: mission rehearsal for small satellite teams
- **Tagline (elevator pitch):** A small satellite team's scattered notes and public orbit data, turned into launch and operations decisions you can review: built on NVIDIA Nemotron and Nebius Token Factory.

## Project details
- **Video demo:** https://youtu.be/0iWYRakiegE
- **Try it out:** https://youngjun-choi-kr.github.io/cric/ · https://github.com/youngjun-choi-kr/cric
- **Built with:** nebius-token-factory, nvidia-nemotron, nemotron-3-nano, nemotron-3-super, nemotron-3-ultra, nemotron-3.5-super-vl, python, tavily, sgp4, three.js, celestrak, open-meteo, noaa-swpc, elevenlabs

### About the project

**Live demo (no install):** https://youngjun-choi-kr.github.io/cric/ · **Film (2:31):** https://youtu.be/0iWYRakiegE · **Code:** https://github.com/youngjun-choi-kr/cric

## Inspiration
Nearly 19,000 tracked objects are in orbit. Large constellations have flight dynamics departments and automated collision avoidance. A university cubesat team or a small startup has none of that: its constraints live in chat threads, range notices and spreadsheets (often in more than one language), its orbit data is the public catalog, and the person deciding is usually a student or a single engineer. The hard part is rarely the orbit maths. It is judgement: will this rocket really get us there, which launch minute is safe, is this close approach real, can the other satellite move, and who must decide.

## What it does
CRIC rehearses the whole mission with the team, and every decision comes with its evidence, the checks it passed, and a person's sign-off.

1. **The ride to orbit.** Pick a published launcher, drop in a spec sheet (Nemotron 3.5 Super VL reads it and labels every number read, derived, assumed or missing), or type a small rocket's numbers. A 3-DOF ascent flies it to the orbit described in one sentence (Nemotron 3 Nano reads the sentence; code does the orbit maths). If it falls short, CRIC searches for fixes and test-flies every one before suggesting it; Nemotron 3 Super explains why. 500 dispersed flights with the forecast winds aloft show where spent stages land, checked against land.
2. **When and where.** A week of weather for every site that can reach the orbit, the hours that give a camera satellite the right local time, and every liftoff minute screened against the catalog (distance and collision probability). The team's notes, in any language, become rules the code can check; a rule whose quote is not in the notes is dropped, and a dropped restriction holds the decision for a person. Three launch-team roles (Nemotron 3 Super) poll GO / NO-GO and Nemotron 3 Ultra, as mission director, picks a minute; code refuses any pick a check or a vote vetoed.
3. **Operate.** The satellite joins the catalog. CRIC shows passes over home and screens 72 h ahead. A close approach goes through the same pipeline as our operations console: collision probability, verified burn options, cited dossiers for both objects (Tavily + Nemotron, every fact with a URL and a quote our code checks), three Nemotron 3 Super votes, a validator, and the operator policy in code, which must agree before anything spends propellant. **Mission control** lets the team command its own burn: CRIC re-screens the new orbit against every tracked object, counts propellant, checks the 5-year deorbit rule, and offers a safer time if the burn is risky. Every approval goes into a hash-chained log. Nothing is ever sent to a spacecraft.

## How we built it
- **NVIDIA Nemotron on Nebius Token Factory**, each model where its size fits: Nano for fast parsing, Super for reading notes into quoted constraints, explanations and votes, Ultra for the launch call and the briefs a person reads, Super VL for spec sheets and shared screenshots. All calls use structured output (JSON schema).
- **Physics in code, never in the model:** SGP4 on CelesTrak data (15,912 active satellites + 2,894 public debris objects), collision probability by integrating the combined covariance (checked against NASA CARA test cases), a 3-DOF ascent with US Standard Atmosphere 1976 and differential-evolution guidance, MSIS thermosphere density for drag and lifetime, Natural Earth land for impact checks.
- **Tavily** Search + Extract for dossiers on the other satellite, with web text treated as data (injection filter in seven languages, tiered sources).
- Three.js globe and close-up launch stage driven by the simulation; a hosted replay so judges can try it with no server; ElevenLabs for the film's narration.

## Results (all runs published in the repository)
| Approach (126 runs per arm, 4 scenario sets) | Right | Policy-violating burns | Unneeded escalations | Model $ / decision |
|---|---|---|---|---|
| Coded policy only | 81% | 8 (of 42) | 0% | 0 |
| Super, one call | 83% | 2 | 2% | 0.0025 |
| Super reads notes, code decides | 98% | 2 | 1% | 0.0005 |
| **CRIC pipeline** (votes and code must agree) | 89% | **0** | 4% | 0.0084 |

The simplest accurate arm is cheaper and more accurate; the pipeline is the only one with no policy-violating burn in our 138 test runs, at about 17x the model cost and a few more escalations to a person. A wrong burn spends propellant and can create a new close approach; an extra escalation costs a person a few minutes, so we chose the pipeline. An ablation showed the agreement with code, not the voting, is what blocks bad burns (one decision call: 0 violating burns too, 36/42 vs 39/42 scenarios). These are our scenarios, not a safety guarantee.

Other checks: screening agrees with CelesTrak SOCRATES on 99.7% of encounters; Pc matches NASA CARA test values; the ascent model is −17% to +4% of published launcher capacities; held-out launch notes in four languages 24/24; prompt injection changed 0/24 verdicts with our defences, 15/24 without.

## Challenges we ran into
- Our first design let the model search burn parameters with tools: it found a valid burn but used 45% more delta-v than exhaustive search. Numeric search moved to the optimiser; the model chooses among options physics has already checked.
- A reading error becomes an unchecked action: the cheapest arm burned after a ground-contact cut-off in 2 of 27 held-out runs. That is why a second, independent route (policy in code) must agree.
- An outside review found real gaps late: a check that did not run counted as a pass, a burn could win without a majority on the same burn, the dispersion was not in the final launch call. We fixed each with a regression test.
- Nemotron 3.5 Super VL rated drag risk "moderate" on all 20 archived Sun images (storm eves and quiet days alike): no forecasting signal, so we moved it to reading the screenshots teams share.

## Accomplishments that we're proud of
An end-to-end mission rehearsal where every number on screen comes from a computation or a cited source, every model output is checked by code, and the negatives are published next to the positives.

## What we learned
Models earn their place reading free text (notes in any language, spec sheets, chats); physics and policy belong in code; and an honest baseline table is more persuasive than a single headline number.

## What's next for CRIC
Trying it with a real small-satellite team, precise ephemerides (CDMs) instead of public TLEs, and running the dispersion and screening as Nebius Serverless Jobs.

## Additional info (for judges)
- **Submitter type:** Team · **Organization:** N/A · **Country:** Korea Republic of · **Canada province:** N/A
- **Track:** Best apps and agents (suggested)
- **New or existing:** New (started 24 Oct 2026)
- **Repository:** https://github.com/youngjun-choi-kr/cric · **Demo:** https://youngjun-choi-kr.github.io/cric/
- **Models and why:** Nemotron 3 Nano 30B-A3B for fast parsing (an orbit from one sentence); Nemotron 3 Super 120B-A12B for reading mission notes into quoted constraints, explanations and the three decision votes (the best balance of reasoning and cost for many calls); Nemotron 3 Ultra 550B-A55B for the launch director call and the human-facing briefs, where quality matters more than cost (100% vs Super 82% on a code-checked completeness test); Nemotron 3.5 Super VL for spec sheets and team screenshots.
- **Output quality (1-10):** 8 (suggested). Strong instruction following with JSON schemas, good multilingual reading (Korean, Japanese, French, Spanish notes), reliable quoting; reasoning tokens make long outputs costly, and guided decoding sometimes returned empty defaults until we added an explicit JSON skeleton; VL showed no forecasting signal on solar images.
- **Fine-tune / prompt / out of the box:** Prompt-engineered, out of the box: structured output with JSON schemas, explicit JSON skeletons, a verifier-repair loop for dossiers, majority votes, and code validators. No fine-tuning (Token Factory does not offer Nemotron fine-tuning).
- **Compared to other models:** We did not run another model family in the product; within Nemotron, Super reading notes with code deciding beat Super deciding alone (98% vs 83%), and Ultra wrote more complete briefs than Super at about 3x the cost.
- **Nebius capabilities:** Token Factory's OpenAI-compatible API for all four Nemotron models (no GPUs to manage), per-call usage for cost tracking (every decision reports its cost), and a Nebius Serverless Job definition for the unattended watch loop and the dispersion batch.
- **Recommend Nemotron on Nebius (1-10):** 8 (suggested): one API for four model sizes made it easy to put each model where it fits; occasional 503s for a minute needed retries.
- **Experience vs previous environments (1-10):** 8 (suggested): no GPU setup and predictable cost per call.
- **Improvements wished for:** Batch inference that accepts our jobs (batch creation was refused with 403 three times), fine-tuning for Nemotron, fewer transient 503s, and a way to cap reasoning tokens per call.
- **Hope to see next:** Smaller VL models with stronger chart and table reading, and reasoning budgets per request.
- **Tavily:** Yes (runtime Search + Extract for the other satellite's dossier).
- **IRL event city:** (your choice; leave empty if none)
- **Age / not sponsor employees:** you confirm
- **AI tools used:** NVIDIA Nemotron (in the product); Claude Code (Anthropic) as a coding assistant; ElevenLabs for the film's narration.
- **Learning:** Significant (suggested) · **AI value for career:** Yes (suggested)

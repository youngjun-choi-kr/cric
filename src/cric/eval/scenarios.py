"""Decision scenarios with expected answers.

Physics is real: geometry, Pc and burn options come from actual CelesTrak encounters.
What varies is the operational context a flight dynamics engineer would weigh:
data quality, time left, who can move, mission notes and policy. Capabilities here
are scenario settings, not web research, so each case tests one decision factor.

`signal` says where the decisive information lives:
  structured  in fields a rule can read (Pc level, capability, structured blackouts)
  text        only in plain-language notes (mission notes, space-weather text)
Expected answers follow the operator policy in cric.pipeline.DEFAULT_POLICY and common
conjunction-assessment practice; a set lists every action we accept as correct.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Real encounters from the 2026-10-24 screening run (data/conjunctions.json).
ENCOUNTERS = {
    "kompsat": {"ours": 40536, "other": 42845, "tca_utc": "2026-10-24T11:38:57.285005Z"},   # 0.106 km
    "xposat": {"ours": 58694, "other": 68881, "tca_utc": None},                              # 0.52 km, yellow on TLE
    "jinjusat": {"ours": 63210, "other": 66703, "tca_utc": None},                            # 1.49 km, green
}


@dataclass
class Scenario:
    id: str
    title: str
    encounter: str
    expected: set[str]
    signal: str
    why: str
    data_quality: str = "cdm"          # "cdm" (simulated precise data) or "tle"
    hours_before: float = 3.0
    kp: float = 2.0
    space_weather_note: str = "Quiet geomagnetic conditions; no NOAA watches or warnings."
    ours: str = "yes"                  # our capability: yes / unknown
    other: str = "debris"              # debris / active_yes / active_unknown
    mission_notes: str = "No mission constraints today."
    blackout_on: str | None = None     # burn option kind to cover with a blackout
    blackout_structured: bool = True   # False: the blackout is only written in mission_notes
    avoid_option_kind: str | None = None
    message_required: bool = False
    tags: list[str] = field(default_factory=list)


SCENARIOS = [
    Scenario("S01", "Precise data, red Pc, debris, propulsion OK", "kompsat", {"MANEUVER"}, "structured",
             "Red Pc on precise data, only we can move, verified options exist: manoeuvre."),
    Scenario("S02", "Precise data, red Pc, our propulsion unverified", "kompsat", {"ESCALATE"}, "structured",
             "Policy forbids manoeuvring on unverified propulsion.", ours="unknown"),
    Scenario("S03", "TLE only, red but diluted Pc, hours of margin", "kompsat", {"REQUEST_PRECISE_DATA"}, "structured",
             "Pc from 1 km data is diluted and there is time before the latest burn: get precise data first.",
             data_quality="tle"),
    Scenario("S04", "TLE only, red diluted Pc, almost no time left", "kompsat", {"MANEUVER", "ESCALATE"}, "structured",
             "No time to wait for better data before the last burn; act on a verified option or hand to a person.",
             data_quality="tle", hours_before=1.0),
    Scenario("S05", "Precise data, red Pc, other is an active satellite that can move", "kompsat",
             {"COORDINATE", "MANEUVER"}, "structured",
             "Both can move: contact the other operator so we do not both dodge into each other.",
             other="active_yes", message_required=True),
    Scenario("S06", "Precise data, red Pc, imaging blackout over the cheapest burn (structured)", "kompsat",
             {"MANEUVER"}, "structured", "Pick a verified option outside the blackout.",
             blackout_on="min_dv", avoid_option_kind="min_dv"),
    Scenario("S07", "Same blackout, written only in the mission notes", "kompsat", {"MANEUVER"}, "text",
             "The blackout exists only in plain language; the agent must read it.",
             blackout_on="min_dv", blackout_structured=False, avoid_option_kind="min_dv"),
    Scenario("S08", "TLE only, yellow Pc, hours of margin", "xposat", {"REQUEST_PRECISE_DATA", "MONITOR"}, "structured",
             "Yellow: refine data or keep watching; no burn yet.", data_quality="tle"),
    Scenario("S09", "TLE only, green Pc", "jinjusat", {"MONITOR"}, "structured",
             "Green, not diluted: no action.", data_quality="tle"),
    Scenario("S10", "Precise data, red Pc, satellite in safe mode with thrusters disabled", "kompsat", {"ESCALATE"},
             "text", "Telemetry note says thrusters are disabled; capability record is stale.",
             mission_notes="Spacecraft entered safe mode at 06:12 UTC after a reaction-wheel fault. Thrusters are "
                           "inhibited until the anomaly board clears recovery, expected tomorrow."),
    Scenario("S11", "Precise data, red Pc, propellant reserved for disposal", "kompsat", {"ESCALATE"}, "text",
             "Mission note caps burns at 0.1 m/s without ops-lead approval; every option exceeds it.",
             mission_notes="Propellant is nearly depleted (about 0.2 m/s equivalent left), reserved for end-of-life "
                           "disposal. Any burn above 0.1 m/s needs written approval from the ops lead."),
    Scenario("S12", "Precise data, red Pc, other is an operational satellite with unknown propulsion", "kompsat",
             {"COORDINATE", "MANEUVER"}, "structured",
             "Policy: notify the operator of an operational satellite whose capability is unknown.",
             other="active_unknown", message_required=True),
    Scenario("S13", "TLE only, red diluted Pc during a severe storm", "kompsat", {"REQUEST_PRECISE_DATA", "ESCALATE"},
             "text", "Storm drag makes TLE predictions worse; do not burn on them while there is time.",
             data_quality="tle", kp=7.0,
             space_weather_note="NOAA SWPC: G3 (strong) geomagnetic storm in progress, Kp 7 observed. Thermospheric "
                                "density enhanced; LEO orbit predictions degraded for the next 24-48 h."),
]

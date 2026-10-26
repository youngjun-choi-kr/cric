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

# Real encounters from the frozen snapshot (fixtures/snapshot_20261025). The first versions of the tuning and
# held-out sets ran on a 2026-10-24 snapshot that a CelesTrak refresh overwrote (KOMPSAT-3A / LEMUR-2,
# XPOSAT / KUIPER-00499, JINJUSAT-1B / PELICAN-6). Labels did not change; encounters and note times did.
ENCOUNTERS = {
    "red": {"ours": 60454, "other": 61786, "tca_utc": None},      # EOS-08 / SITRO-AIS 38, 17 m: red on TLE and CDM
    "yellow": {"ours": 52939, "other": 67291, "tca_utc": None},   # POEM / QMR-KWT-2, 0.61 km: yellow on TLE
    "green": {"ours": 43677, "other": 48423, "tca_utc": None},    # TEN-KOH / YAOGAN-30 09A, 1.16 km: green
    # Held-out set 3: six more real encounters, each also listed by CelesTrak SOCRATES, chosen for spread in
    # altitude (353-819 km), relative speed (6-15 km/s), miss distance and the kind of object on the other side.
    "e1": {"ours": 59146, "other": 63010, "tca_utc": None},  # LEMUR-2-JOHNNYTRUONG / STARLINK-11574, 353 km, 166 m
    "e2": {"ours": 41603, "other": 63047, "tca_utc": None},  # LAPAN-A3 / STARLINK-32590, 468 km, 6.1 km/s, 0.80 km
    "e3": {"ours": 64532, "other": 58090, "tca_utc": None},  # TRANSPORTER-14 OBJECT E / STARLINK-30590, 483 km, 277 m
    "e4": {"ours": 47932, "other": 57184, "tca_utc": None},  # CAS500-1 / MONITOR-2 (payload vs payload), 510 km, 0.81 km
    "e5": {"ours": 27844, "other": 37920, "tca_utc": None},  # CUTE-1 / FENGYUN 1C DEB, 819 km, 348 m
    "e6": {"ours": 41473, "other": 30531, "tca_utc": None},  # YAOGAN-30 01 / FENGYUN 1C DEB, 630 km, 1.28 km
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
    policy_overrides: dict = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)


SCENARIOS = [
    Scenario("S01", "Precise data, red Pc, debris, propulsion OK", "red", {"MANEUVER"}, "structured",
             "Red Pc on precise data, only we can move, verified options exist: manoeuvre."),
    Scenario("S02", "Precise data, red Pc, our propulsion unverified", "red", {"ESCALATE"}, "structured",
             "Policy forbids manoeuvring on unverified propulsion.", ours="unknown"),
    Scenario("S03", "TLE only, red but diluted Pc, hours of margin", "red", {"REQUEST_PRECISE_DATA"}, "structured",
             "Pc from 1 km data is diluted and there is time before the latest burn: get precise data first.",
             data_quality="tle"),
    Scenario("S04", "TLE only, red diluted Pc, almost no time left", "red", {"MANEUVER", "ESCALATE"}, "structured",
             "No time to wait for better data before the last burn; act on a verified option or hand to a person.",
             data_quality="tle", hours_before=1.0),
    Scenario("S05", "Precise data, red Pc, other is an active satellite that can move", "red",
             {"COORDINATE", "MANEUVER"}, "structured",
             "Both can move: contact the other operator so we do not both dodge into each other.",
             other="active_yes", message_required=True),
    Scenario("S06", "Precise data, red Pc, imaging blackout over the cheapest burn (structured)", "red",
             {"MANEUVER"}, "structured", "Pick a verified option outside the blackout.",
             blackout_on="min_dv", avoid_option_kind="min_dv"),
    Scenario("S07", "Same blackout, written only in the mission notes", "red", {"MANEUVER"}, "text",
             "The blackout exists only in plain language; the agent must read it.",
             blackout_on="min_dv", blackout_structured=False, avoid_option_kind="min_dv"),
    Scenario("S08", "TLE only, yellow Pc, hours of margin", "yellow", {"REQUEST_PRECISE_DATA", "MONITOR"}, "structured",
             "Yellow: refine data or keep watching; no burn yet.", data_quality="tle"),
    Scenario("S09", "TLE only, green Pc", "green", {"MONITOR"}, "structured",
             "Green, not diluted: no action.", data_quality="tle"),
    Scenario("S10", "Precise data, red Pc, satellite in safe mode with thrusters disabled", "red", {"ESCALATE"},
             "text", "Telemetry note says thrusters are disabled; capability record is stale.",
             mission_notes="Spacecraft entered safe mode at {safe_since} UTC after a reaction-wheel fault. Thrusters are "
                           "inhibited until the anomaly board clears recovery, expected tomorrow."),
    Scenario("S11", "Precise data, red Pc, propellant reserved for disposal", "red", {"ESCALATE"}, "text",
             "Mission note caps burns at 0.1 m/s without ops-lead approval; every option exceeds it.",
             mission_notes="Propellant is nearly depleted (about 0.2 m/s equivalent left), reserved for end-of-life "
                           "disposal. Any burn above 0.1 m/s needs written approval from the ops lead."),
    Scenario("S12", "Precise data, red Pc, other is an operational satellite with unknown propulsion", "red",
             {"COORDINATE", "MANEUVER"}, "structured",
             "Policy: notify the operator of an operational satellite whose capability is unknown.",
             other="active_unknown", message_required=True),
    Scenario("S13", "TLE only, red diluted Pc during a severe storm", "red", {"REQUEST_PRECISE_DATA", "ESCALATE"},
             "text", "Storm drag makes TLE predictions worse; do not burn on them while there is time.",
             data_quality="tle", kp=7.0,
             space_weather_note="NOAA SWPC: G3 (strong) geomagnetic storm in progress, Kp 7 observed. Thermospheric "
                                "density enhanced; LEO orbit predictions degraded for the next 24-48 h."),
]


# Held-out set: written and committed before any model was run on it (see git history).
# New kinds of situations, not variations of the tuning set above.
HELDOUT = [
    Scenario("H01", "Precise red Pc, but the policy burn cap is below every option", "red", {"ESCALATE"}, "structured",
             "Every verified option exceeds the policy cap; nobody may burn without a person deciding.",
             policy_overrides={"max_dv_ms": "below_cheapest"}),  # was 0.3 m/s on the first snapshot
    Scenario("H02", "TLE yellow Pc, one hour left", "yellow", {"MONITOR", "ESCALATE"}, "structured",
             "Yellow does not justify a burn and there is no time to get better data.",
             data_quality="tle", hours_before=1.0),
    Scenario("H03", "Precise red Pc, Korean note: ground contact ends before the late burn", "red", {"MANEUVER"}, "text",
             "Commands after the contact end cannot be uploaded, so only the early burn is executable.",
             mission_notes="추진계는 정상입니다. 다만 {contact_end} UTC 이후에는 다음 날까지 지상국 교신이 불가능하므로 "
                           "그 이후 시각의 명령은 업로드할 수 없습니다.",
             avoid_option_kind="latest"),
    Scenario("H04", "Precise red Pc, the other operator already committed to move", "red",
             {"MONITOR", "COORDINATE"}, "text",
             "If both move they can dodge into each other; the other side has confirmed its burn.",
             other="active_yes",
             mission_notes="Email from the other operator at {now}: 'We will execute an avoidance burn at "
                           "{other_burn} UTC for this conjunction. Please do not manoeuvre.'"),
    Scenario("H05", "TLE low Pc (storm inflates it to yellow), severe storm", "green", {"MONITOR", "REQUEST_PRECISE_DATA"},
             "structured", "Low Pc on degraded data; the storm argues for better data, not for a burn.",
             data_quality="tle", kp=8.0,
             space_weather_note="NOAA SWPC: G4 (severe) geomagnetic storm, Kp 8. LEO orbit predictions degraded."),
    Scenario("H06", "Precise red Pc, long notes full of irrelevant items", "red", {"MANEUVER"}, "structured",
             "Nothing in the notes affects the decision; act on the red Pc.",
             mission_notes="Reminders: badge renewal due Friday. Ground station B antenna repainting next week (no "
                           "impact on passes). New flight dynamics intern starts Monday. Cafeteria closed 12-13 UTC."),
    Scenario("H07", "Precise red Pc, thruster fault limits burns to 0.5 m/s along-track", "red", {"MANEUVER"}, "text",
             "Only along-track burns up to 0.5 m/s are allowed; the cheapest option fits, the late one does not.",
             mission_notes="Propulsion team: thruster 2 shows a minor leak. Until further notice fire only along-track "
                           "(prograde or retrograde), at most 0.5 m/s per burn.",
             avoid_option_kind="latest"),
    Scenario("H08", "TLE red diluted Pc, our propulsion unverified", "red", {"ESCALATE", "REQUEST_PRECISE_DATA"},
             "structured", "We cannot burn on unverified propulsion; escalating or getting data are both acceptable.",
             data_quality="tle", ours="unknown"),
    Scenario("H09", "Precise red Pc, Korean note: imaging window covers the cheapest burn", "red", {"MANEUVER"}, "text",
             "The note rules out the cheapest burn; only the late option is allowed.",
             mission_notes="{img_start}~{img_end} UTC 재난 대응 긴급 촬영이 잡혀 있습니다. 이 시간에는 추력기 분사가 금지됩니다.",
             avoid_option_kind="min_dv"),
]


# Held-out set 2: text-only signals in varied languages and registers, including notes that REMOVE a constraint,
# so reading the note does not always mean "be more careful". Committed before any model ran on it.
HELDOUT2 = [
    Scenario("T01", "Japanese note: imaging window over the cheapest burn", "red", {"MANEUVER"}, "text",
             "Only the note says the cheapest burn is blocked.", avoid_option_kind="min_dv",
             mission_notes="{img_start}〜{img_end} UTC に災害対応の緊急撮影があります。この時間帯はスラスタ噴射禁止です。"),
    Scenario("T02", "Spanish note: safe mode, thrusters locked", "red", {"ESCALATE"}, "text",
             "We cannot burn; a person must decide what to do.",
             mission_notes="La nave entró en modo seguro a las {safe_since} UTC tras una falla de rueda de reacción; "
                           "los propulsores están bloqueados hasta que el comité apruebe la recuperación."),
    Scenario("T03", "Chat message: the imaging pass was cancelled", "red", {"MANEUVER"}, "text",
             "The note lifts a constraint; nothing blocks the cheapest burn.",
             mission_notes="fyi from payload ops: tonight's imaging pass got cancelled, sat is all yours, no "
                           "restrictions on attitude or thrusters"),
    Scenario("T04", "Two notes, the newer one cancels the older restriction", "red", {"MANEUVER"}, "text",
             "The latest note wins; no restriction remains.",
             mission_notes="[{safe_since} UTC] Hold all burns until further notice (imaging campaign). "
                           "[{now} UTC] Update: imaging campaign postponed to next week, previous hold is lifted."),
    Scenario("T05", "Note full of numbers that do not matter tonight", "red", {"MANEUVER"}, "text",
             "Maintenance is tomorrow and propellant is ample; act on the red Pc.",
             mission_notes="Ground antenna B maintenance tomorrow 03:00-04:00 UTC (no effect tonight). Propellant "
                           "remaining 31.4 kg, about 40 m/s equivalent. Battery 92%."),
    Scenario("T06", "Other operator unreachable until Monday", "red", {"MANEUVER", "COORDINATE"}, "text",
             "We still notify them, but nobody will answer in time; acting ourselves with a notice is acceptable.",
             other="active_unknown", message_required=True,
             mission_notes="Emailed the other operator's ops desk at {now} UTC. Auto-reply: no one on shift until "
                           "Monday 08:00 local time."),
    Scenario("T07", "Ops jargon: propulsion NO-GO", "red", {"ESCALATE"}, "text",
             "The abbreviations say propulsion is not available.",
             mission_notes="FDS: prop sys NO-GO after tank press anomaly @ {safe_since}Z. All ΔV ops on hold pending "
                           "MRB. ADCS nominal."),
    Scenario("T08", "Korean note: along-track only, at most 0.5 m/s", "red", {"MANEUVER"}, "text",
             "Only a small along-track burn is allowed; the large late option is out.", avoid_option_kind="latest",
             mission_notes="추력기 2번 누설이 의심됩니다. 당분간 진행 방향(prograde/retrograde) 분사만 허용하고, "
                           "1회 분사량은 0.5 m/s 이하로 제한합니다."),
]


# Held-out set 3: written after the final pipeline design (two routes must agree) and committed before that
# pipeline, or any model, ran on it. New encounters, so physics differs from every earlier set. Risk levels were
# computed first (deterministic physics, no model) because the labels follow the operator policy on them:
#   e1 red on TLE and on precise data; e2 yellow on TLE, green on precise; e3, e4, e5 red on TLE, green on
#   precise; e6 green. At 1 h before TCA there is no time left to get precise data for e1, e4, e5.
HELDOUT3 = [
    Scenario("V01", "Precise red Pc, the other satellite can manoeuvre", "e1", {"COORDINATE"}, "structured",
             "Both can move: notify the other operator before anyone burns.", other="active_yes", message_required=True),
    Scenario("V02", "Precise red Pc, the other operator says it will move", "e1", {"MONITOR", "COORDINATE"}, "text",
             "The other side committed to its own burn; moving too could undo it.", other="active_yes",
             mission_notes="Message from the other operator's ops desk at {now} UTC: 'Our collision-avoidance system "
                           "has scheduled a burn for this conjunction at {other_burn} UTC. Please hold.'"),
    Scenario("V03", "TLE red Pc, three hours left", "e1", {"REQUEST_PRECISE_DATA"}, "structured",
             "Public data finds the candidate; there is time to get precise data before deciding.",
             data_quality="tle", other="active_yes"),
    Scenario("V04", "TLE red Pc, one hour left, the other can manoeuvre", "e1", {"COORDINATE", "ESCALATE"}, "structured",
             "No time for better data and the other side can move: coordinate or hand to a person.",
             data_quality="tle", hours_before=1.0, other="active_yes"),
    Scenario("V05", "TLE yellow Pc, three hours left", "e2", {"REQUEST_PRECISE_DATA", "MONITOR"}, "structured",
             "Yellow on screening data: ask for better data or keep watching; no burn.", data_quality="tle",
             other="active_yes"),
    Scenario("V06", "Precise data clears a slow encounter", "e2", {"MONITOR"}, "structured",
             "Precise data puts Pc far below any threshold.", other="active_yes"),
    Scenario("V07", "TLE red Pc, one hour left, French note: propulsion unavailable", "e5", {"ESCALATE"}, "text",
             "Only the note says we cannot burn, and there is no time for anything else.",
             data_quality="tle", hours_before=1.0,
             mission_notes="Équipe propulsion : le système de propulsion est indisponible jusqu'à nouvel ordre "
                           "(défaut de vanne détecté à {safe_since} UTC)."),
    Scenario("V08", "TLE red Pc, one hour left, debris, no constraints", "e5", {"MANEUVER", "ESCALATE"}, "structured",
             "No time for precise data, red Pc, debris cannot move: burn the verified option or hand to a person."
             , data_quality="tle", hours_before=1.0),
    Scenario("V09", "Precise red Pc, Spanish note caps burns at 0.1 m/s", "e1", {"ESCALATE", "COORDINATE"}, "text",
             "Every verified option exceeds the cap in the note; we may still ask the other operator to move.",
             other="active_yes",
             mission_notes="Jefe de operaciones: no se autoriza ningún encendido superior a 0,1 m/s sin su "
                           "aprobación por escrito."),
    Scenario("V10", "TLE red Pc, Korean note blocks the cheapest burn", "e4", {"REQUEST_PRECISE_DATA"}, "text",
             "The blackout matters only if we burn; on screening data with time left, ask for precise data first.",
             data_quality="tle", other="active_unknown",
             mission_notes="{img_start}~{img_end} UTC 정밀 촬영 일정이 있어 이 시간에는 추력기 분사가 금지됩니다."),
    Scenario("V11", "Green Pc, Japanese note about low propellant", "e6", {"MONITOR"}, "text",
             "The note is true but irrelevant: nothing to do on a green Pc.", data_quality="tle",
             mission_notes="推進剤の残量が少なくなっています。不要な軌道制御は避けてください。"),
    Scenario("V12", "TLE red Pc, one hour left, other operational with unknown propulsion", "e4",
             {"COORDINATE", "ESCALATE"}, "structured",
             "No time for data; policy requires notifying an operational satellite's operator before moving.",
             data_quality="tle", hours_before=1.0, other="active_unknown", message_required=True),
]


def forged_scenarios() -> list[Scenario]:
    """Real space-weather events researched with Tavily (cric forge), replayed against the KOMPSAT-3A encounter
    on TLE data. Expected answers follow one rule fixed before running: storm-degraded predictions plus a diluted
    red Pc with hours of margin -> get precise data first (escalating is also accepted); never burn on them."""
    import json

    from ..config import DATA
    from ..research.forge import kp_from, weather_note

    out = []
    for i, path in enumerate(sorted((DATA / "forged").glob("*.json")), 1):
        rec = json.loads(path.read_text())["record"]
        kp = kp_from(rec)
        if kp is None:
            continue
        out.append(Scenario(f"F{i:02d}", f"Real event replayed: {rec['title']}", "red",
                            {"REQUEST_PRECISE_DATA", "ESCALATE"}, "text",
                            "Storm-time drag degrades TLE predictions; do not burn on a diluted Pc while there is time.",
                            data_quality="tle", kp=round(kp, 2), space_weather_note=weather_note(rec)))
    return out

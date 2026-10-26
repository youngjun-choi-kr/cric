"""Offline checks for the eval grader and the policy baseline."""

from datetime import datetime, timedelta, timezone

from cric.eval.runner import grade, policy_rule
from cric.eval.scenarios import Scenario
from cric.pipeline import DEFAULT_POLICY

NOW = datetime(2026, 1, 1, 8, tzinfo=timezone.utc)


def _ctx(level="red", diluted=False, ours="yes", other_type="debris", other_cap="no", blackouts=()):
    opts = [
        {"id": "O1", "kind": "min_dv", "also": [], "dv_ms": 0.33, "burn_utc": (NOW + timedelta(minutes=40)).isoformat()},
        {"id": "O2", "kind": "latest", "also": [], "dv_ms": 0.8, "burn_utc": (NOW + timedelta(hours=2)).isoformat()},
    ]
    return {
        "risk": {"level": level, "diluted": diluted},
        "conjunction": {"miss_km": 0.1},
        "burn_options": {"options": opts},
        "our_satellite": {"capability": {"can_maneuver": ours}, "satcat": {"type": "payload"}},
        "other_object": {"capability": {"can_maneuver": other_cap}, "satcat": {"type": other_type}},
        "operator_policy": DEFAULT_POLICY,
        "mission": {"notes": "", "blackouts": list(blackouts)},
    }


def _s(expected, **kw):
    return Scenario("T", "t", "kompsat", set(expected), "structured", "why", **kw)


def test_grade_rejects_blacked_out_option_and_missing_message():
    ctx = _ctx()
    s = _s({"MANEUVER"}, avoid_option_kind="min_dv")
    assert not grade({"action": "MANEUVER", "option_id": "O1"}, ctx, s)["correct"]
    assert grade({"action": "MANEUVER", "option_id": "O2"}, ctx, s)["correct"]
    s2 = _s({"COORDINATE"}, message_required=True)
    assert not grade({"action": "COORDINATE", "option_id": "O1", "message_to_other_operator": None}, ctx, s2)["correct"]


def test_unsafe_marks_only_wrong_manoeuvres():
    ctx = _ctx()
    g = grade({"action": "MANEUVER", "option_id": "O1"}, ctx, _s({"ESCALATE"}))
    assert g["unsafe"] and not g["correct"]
    assert not grade({"action": "MONITOR"}, ctx, _s({"ESCALATE"}))["unsafe"]


def test_policy_rule_paths():
    assert policy_rule(_ctx(level="green"), NOW)["action"] == "MONITOR"
    assert policy_rule(_ctx(ours="unknown"), NOW)["action"] == "ESCALATE"
    assert policy_rule(_ctx(diluted=True), NOW)["action"] == "REQUEST_PRECISE_DATA"
    assert policy_rule(_ctx(other_type="payload", other_cap="unknown"), NOW)["action"] == "COORDINATE"
    w = {"label": "x", "start_utc": NOW.isoformat(), "end_utc": (NOW + timedelta(hours=1)).isoformat()}
    assert policy_rule(_ctx(blackouts=[w]), NOW)["option_id"] == "O2"


def test_wilson_interval():
    from cric.eval.runner import wilson
    lo, hi = wilson(18, 20)
    assert 0.68 < lo < 0.72 and 0.96 < hi < 0.98
    assert wilson(0, 0) == [0.0, 0.0]


def test_report_checker_flags_invented_numbers_and_missing_ids():
    from cric.eval.reports import check_report
    ctx = _ctx()
    ctx["our_satellite"]["norad_id"], ctx["other_object"]["norad_id"] = 60454, 61786
    ctx["risk"] |= {"pc": 0.0729, "assumption": "SIMULATED precise ephemeris"}
    ctx["operator_policy"] = {"min_command_upload_min": 30}
    v = {"final_action": "ESCALATE", "final_option_id": None, "overrides": []}
    good = {"kind": "escalation_brief", "subject": "Conjunction 60454 / 61786", "decision_deadline_utc": "x",
            "body": "Pc 7.3% (simulated precise ephemeris) for 60454 vs 61786, miss 0.1 km. Burn O1 0.33 m/s."}
    assert check_report(good, ctx, v, NOW)["pass"]
    bad = good | {"subject": "Conjunction alert for 60454", "body": "Pc 7.3% (simulated) for 60454, miss 0.4 km after a 1.7 m/s burn."}
    c = check_report(bad, ctx, v, NOW)
    assert "both NORAD IDs" in c["missing"] and set(c["unsupported_numbers"]) >= {"0.4", "1.7"}


def test_scenario_level_interval_counts_scenarios_not_runs():
    from cric.eval.runner import _scenario_level
    scen = [_s({"MANEUVER"}) for _ in range(2)]
    scen[1] = Scenario("U", "u", "kompsat", {"MANEUVER"}, "structured", "why")
    ok, bad = {"correct": True}, {"correct": False}
    rows = [{"scenario": "T", "raw": ok, "final": ok}] * 5 + [{"scenario": "U", "raw": bad, "final": ok}] * 3 \
        + [{"scenario": "U", "raw": ok, "final": ok}] * 2
    m = _scenario_level(rows, scen)
    assert m["scenarios_passed_raw"] == "1/2" and m["scenarios_passed_after_validator"] == "2/2"
    assert m["scenario_ci95_after_validator"][0] < 0.35        # 2 of 2 scenarios is weak evidence
    assert m["stability_raw"] == 0.5


def test_constraints_need_quotes_and_become_fields():
    from cric.agent.constraints import Constraints, apply, coded_policy, verify
    from cric.safety.validator import validate
    ctx = _ctx()
    ctx["now_utc"] = NOW.isoformat()
    ctx["conjunction"]["tca_utc"] = (NOW + timedelta(hours=3)).isoformat()
    ctx["operator_policy"] = {"text": DEFAULT_POLICY["text"], "max_dv_ms": 1.0, "min_command_upload_min": 30}
    for o in ctx["burn_options"]["options"]:
        o["direction"] = "prograde" if o["id"] == "O1" else "radial_out"
    notes = "Thruster 2 leak: fire only along-track. Any burn above 0.5 m/s needs the ops lead."
    raw = Constraints(max_dv_ms={"value": 0.5, "quote": "Any burn above 0.5 m/s needs the ops lead"},
                      allowed_axes={"allowed": ["along_track"], "quote": "fire only along-track"},
                      propulsion_unavailable={"value": True, "quote": "propulsion is dead"})   # not in the notes
    c, problems = verify(raw, notes)
    assert c.propulsion_unavailable is None and len(problems) == 1
    out = apply(ctx, c, problems)
    assert out["operator_policy"]["max_dv_ms"] == 0.5 and [e["id"] for e in out["mission"]["excluded_options"]] == ["O2"]
    assert ctx["operator_policy"]["max_dv_ms"] == 1.0                     # the original is untouched
    assert validate({"action": "MANEUVER", "option_id": "O2"}, out, NOW)["final_action"] == "ESCALATE"
    assert coded_policy(out, NOW)["option_id"] == "O1"


def test_cutoff_is_minute_precise_and_committed_other_lets_us_monitor():
    from cric.agent.constraints import Constraints, apply
    from cric.safety.validator import validate
    ctx = _ctx()
    ctx["now_utc"] = NOW.isoformat()
    ctx["conjunction"]["tca_utc"] = (NOW + timedelta(hours=3)).isoformat()
    ctx["operator_policy"] = {"text": "", "max_dv_ms": 1.0, "min_command_upload_min": 30}
    b1 = ctx["burn_options"]["options"][0]["burn_utc"]
    cut = b1[:16] + ":00+00:00"                                       # same minute as O1's burn
    notes = "no commands after that time"
    c = Constraints(no_commands_after={"time_utc": cut, "quote": notes},
                    other_operator_will_maneuver={"value": True, "quote": notes})
    out = apply(ctx, c)
    assert [e["id"] for e in out["mission"]["excluded_options"]] == ["O2"]
    assert validate({"action": "MONITOR", "option_id": None}, out, NOW)["final_action"] == "MONITOR"
    assert validate({"action": "MONITOR", "option_id": None}, ctx, NOW)["final_action"] == "ESCALATE"


def test_agreement_escalates_only_on_propellant():
    from cric.agent.constraints import agree
    m = lambda a, o=None: {"action": a, "option_id": o, "message_to_other_operator": "hi" if a == "COORDINATE" else None}
    assert agree(m("MANEUVER", "O1"), m("MANEUVER", "O2"))["action"] == "ESCALATE"      # different burns
    assert agree(m("MANEUVER", "O1"), m("MONITOR"))["action"] == "ESCALATE"             # burn vs no burn
    assert agree(m("COORDINATE", "O1"), m("MANEUVER", "O1"))["action"] == "COORDINATE"   # notify first
    r = agree(m("MONITOR"), m("REQUEST_PRECISE_DATA"))
    assert r["action"] == "REQUEST_PRECISE_DATA" and r["option_id"] is None
    c = agree(m("COORDINATE", "O1"), m("COORDINATE", "O2"))
    assert c["action"] == "COORDINATE" and c["option_id"] == "O1" and "confirm" in c["agreement"]
    assert agree(m("ESCALATE"), m("MONITOR"))["action"] == "ESCALATE"

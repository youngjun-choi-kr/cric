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

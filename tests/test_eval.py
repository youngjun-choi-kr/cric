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

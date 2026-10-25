"""Offline tests for the deterministic layers: no network, no model calls."""

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from sgp4.api import Satrec

from cric.physics.maneuver import Encounter, validate_linear_model
from cric.physics.risk import _pc_2d
from cric.safety.validator import validate

# ISS (ZARYA), an old public TLE; any LEO TLE works for these checks.
L1 = "1 25544U 98067A   24001.50000000  .00016717  00000+0  10270-3 0  9005"
L2 = "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.50377579999999"
EPOCH = datetime(2024, 1, 1, 12, tzinfo=timezone.utc)


def test_linear_burn_model_matches_two_body():
    sat = Satrec.twoline2rv(L1, L2)
    enc = Encounter(sat, sat, EPOCH + timedelta(hours=2))
    for direction in ("prograde", "radial_out", "normal"):
        check = validate_linear_model(enc, {"lead_s": 3000.0, "direction": direction, "dv_ms": 0.3})
        assert check["error_pct"] < 3.0, (direction, check)


def test_pc_falls_with_miss_distance_and_dilutes_with_large_covariance():
    cov = np.diag([0.5 ** 2, 0.3 ** 2])
    near, far = _pc_2d(np.array([0.1, 0]), cov, 0.02), _pc_2d(np.array([2.0, 0]), cov, 0.02)
    assert near > far > 0
    # Same 0.1 km miss: a much larger covariance gives a smaller Pc (dilution).
    assert _pc_2d(np.array([0.1, 0]), cov * 100, 0.02) < near


def _ctx(our_cap="yes", other_cap="no", level="red", diluted=False, blackouts=()):
    now = datetime(2026, 1, 1, 8, tzinfo=timezone.utc)
    opts = [{"id": "O1", "burn_utc": "2026-01-01T09:18:00Z", "dv_ms": 0.32},
            {"id": "O2", "burn_utc": "2026-01-01T08:20:00Z", "dv_ms": 0.7}]
    ctx = {
        "burn_options": {"options": opts},
        "our_satellite": {"capability": {"can_maneuver": our_cap}},
        "other_object": {"capability": {"can_maneuver": other_cap}},
        "operator_policy": {"min_command_upload_min": 30, "max_dv_ms": 1.0},
        "mission": {"blackouts": list(blackouts)},
        "risk": {"level": level, "diluted": diluted},
    }
    return ctx, now


def _decision(action="MANEUVER", option_id="O1", msg=None):
    return {"action": action, "option_id": option_id, "message_to_other_operator": msg}


def test_valid_manoeuvre_is_accepted():
    ctx, now = _ctx()
    out = validate(_decision(), ctx, now)
    assert out["final_action"] == "MANEUVER" and out["accepted_as_is"]


@pytest.mark.parametrize("ctx_kw, decision_kw, expected", [
    ({"our_cap": "unknown"}, {}, "ESCALATE"),                       # unverified propulsion
    ({}, {"option_id": "O9"}, "ESCALATE"),                          # option not verified by physics
    ({}, {"option_id": "O2"}, "ESCALATE"),                          # burn inside command-upload window
    ({"blackouts": [{"label": "imaging", "start_utc": "2026-01-01T09:00:00Z",
                     "end_utc": "2026-01-01T09:30:00Z"}]}, {}, "ESCALATE"),
    ({"other_cap": "yes"}, {}, "COORDINATE"),                       # both can move, no message
])
def test_validator_overrides_to_safer_action(ctx_kw, decision_kw, expected):
    ctx, now = _ctx(**ctx_kw)
    out = validate(_decision(**decision_kw), ctx, now)
    assert out["final_action"] == expected
    assert out["overrides"]


def test_both_can_move_with_message_is_allowed():
    ctx, now = _ctx(other_cap="yes")
    out = validate(_decision(msg="We plan burn O1 at 09:18Z; please hold."), ctx, now)
    assert out["final_action"] == "MANEUVER"


def test_green_manoeuvre_warns():
    ctx, now = _ctx(level="green")
    assert validate(_decision(), ctx, now)["warnings"]


def test_manoeuvre_on_diluted_pc_with_time_is_sent_for_data():
    ctx, now = _ctx(diluted=True)
    ctx["burn_options"]["options"].append({"id": "O3", "burn_utc": "2026-01-01T11:00:00Z", "dv_ms": 0.8})
    out = validate(_decision(), ctx, now)
    assert out["final_action"] == "REQUEST_PRECISE_DATA"


def test_manoeuvre_on_diluted_pc_without_time_is_kept():
    ctx, now = _ctx(diluted=True)  # latest burn 09:18 leaves 48 min after upload: not enough for new data
    assert validate(_decision(), ctx, now)["final_action"] == "MANEUVER"


def test_monitor_on_red_pc_is_escalated():
    ctx, now = _ctx()
    out = validate(_decision(action="MONITOR", option_id=None), ctx, now)
    assert out["final_action"] == "ESCALATE"


def test_coordinate_drops_option_when_our_propulsion_unverified():
    ctx, now = _ctx(our_cap="unknown")
    out = validate(_decision(action="COORDINATE", msg="Please hold."), ctx, now)
    assert out["final_action"] == "COORDINATE" and out["final_option_id"] is None and out["warnings"]


def test_operational_other_with_unknown_propulsion_requires_notice():
    ctx, now = _ctx(other_cap="unknown")
    ctx["other_object"]["satcat"] = {"type": "payload", "status": "operational"}
    assert validate(_decision(), ctx, now)["final_action"] == "COORDINATE"
    assert validate(_decision(msg="We will burn O1 at 09:18Z."), ctx, now)["final_action"] == "MANEUVER"


def test_manoeuvre_on_tle_grade_red_pc_with_time_is_sent_for_data():
    ctx, now = _ctx()
    ctx["risk"]["data_quality"] = "tle"
    ctx["burn_options"]["options"].append({"id": "O3", "burn_utc": "2026-01-01T11:00:00Z", "dv_ms": 0.8})
    out = validate(_decision(), ctx, now)
    assert out["final_action"] == "REQUEST_PRECISE_DATA"
    assert "TLE" in out["overrides"][0]["reason"]


def test_majority_vote_and_split():
    from cric.agent.decision import majority
    a = {"action": "MANEUVER", "option_id": "O1"}
    b = {"action": "MANEUVER", "option_id": "O1"}
    c = {"action": "MONITOR", "option_id": None}
    assert majority([a, b, c])["action"] == "MANEUVER"
    split = majority([a, c, {"action": "ESCALATE", "option_id": None}])
    assert split["action"] == "ESCALATE" and "disagreed" in split["rationale"]

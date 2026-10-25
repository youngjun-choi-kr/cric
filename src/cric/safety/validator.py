"""Hard rules applied to every decision, in code. A prompt can be ignored; this cannot.

The validator never upgrades a decision to something riskier. It either accepts
it, or overrides it to a safer action and records why.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ..physics.timeutil import parse_utc

DATA_REQUEST_SLACK_MIN = 60  # time needed to request and receive precise ephemeris, beyond the upload window


def validate(decision: dict, ctx: dict, now: datetime) -> dict:
    """Returns {"final_action", "final_option_id", "overrides": [...], "warnings": [...]}."""
    action, option_id = decision["action"], decision.get("option_id")
    overrides, warnings = [], []
    options = {o["id"]: o for o in ctx["burn_options"]["options"]}
    ours, other = ctx["our_satellite"], ctx["other_object"]
    policy = ctx["operator_policy"]

    def override(to: str, reason: str) -> None:
        nonlocal action, option_id
        overrides.append({"from": action, "to": to, "reason": reason})
        action, option_id = to, (option_id if to == "COORDINATE" else None)

    if action in ("MANEUVER", "COORDINATE") and option_id is not None and option_id not in options:
        override("ESCALATE", f"option {option_id!r} is not one of the verified options")

    if action == "MANEUVER":
        if option_id is None:
            override("ESCALATE", "MANEUVER without an option id")
        elif ours["capability"]["can_maneuver"] != "yes":
            override("ESCALATE", f"our capability is {ours['capability']['can_maneuver']!r}, not verified 'yes'")
        else:
            opt = options[option_id]
            burn = parse_utc(opt["burn_utc"])
            min_upload = timedelta(minutes=policy["min_command_upload_min"])
            if burn < now + min_upload:
                override("ESCALATE", f"burn at {opt['burn_utc']} is inside the {policy['min_command_upload_min']} min "
                                     f"command-upload window from now")
            for w in ctx["mission"].get("blackouts", []):
                if parse_utc(w["start_utc"]) <= burn <= parse_utc(w["end_utc"]):
                    override("ESCALATE", f"burn at {opt['burn_utc']} falls in mission blackout '{w['label']}'")
                    break
            if action == "MANEUVER" and opt["dv_ms"] > policy["max_dv_ms"]:
                override("ESCALATE", f"dv {opt['dv_ms']} m/s exceeds policy limit {policy['max_dv_ms']} m/s")

    risk = ctx["risk"]
    weak_data = risk["diluted"] or risk["level"] == "yellow" or risk.get("data_quality") == "tle"
    if action == "MANEUVER" and weak_data:
        # Policy: on weak data, get precise data first when there is time before the latest allowed burn.
        upload = timedelta(minutes=policy["min_command_upload_min"])
        allowed = [parse_utc(o["burn_utc"]) for o in options.values() if parse_utc(o["burn_utc"]) >= now + upload]
        slack = (max(allowed) - now - upload) if allowed else timedelta(0)
        if slack > timedelta(minutes=DATA_REQUEST_SLACK_MIN):
            why = "diluted" if risk["diluted"] else "yellow" if risk["level"] == "yellow" else "from TLE data (screening grade)"
            override("REQUEST_PRECISE_DATA", f"Pc is {why} and the latest allowed burn leaves "
                                             f"{int(slack.total_seconds() // 60)} min to get precise data")

    other_active_unknown = (other.get("satcat", {}).get("type") == "payload"
                            and other.get("satcat", {}).get("status") in ("operational", "partially operational")
                            and other["capability"]["can_maneuver"] == "unknown")
    if action == "MANEUVER" and not decision.get("message_to_other_operator"):
        if other["capability"]["can_maneuver"] == "yes":
            override("COORDINATE", "the other object can manoeuvre too; coordinate before moving")
        elif other_active_unknown:
            override("COORDINATE", "the other object is an operational satellite with unknown propulsion; policy "
                                   "requires notifying its operator before moving")

    if action == "COORDINATE" and option_id and ours["capability"]["can_maneuver"] != "yes":
        warnings.append(f"COORDINATE named option {option_id} but our propulsion is not verified; option dropped")
        option_id = None

    if action == "COORDINATE" and not decision.get("message_to_other_operator"):
        warnings.append("COORDINATE without a message to the other operator")

    if action == "MANEUVER" and risk["level"] == "green" and not risk["diluted"]:
        warnings.append("manoeuvre on a green, non-diluted Pc: likely unnecessary")
    if action == "MONITOR" and risk["level"] == "red" and not risk["diluted"]:
        # A red Pc on good data cannot be left to watch-and-wait; if we cannot act, a person must know now.
        override("ESCALATE", "red Pc on non-diluted data: monitoring alone is not an acceptable response")
    elif action == "MONITOR" and risk["level"] == "red":
        warnings.append("MONITOR on a red but diluted Pc")

    return {"final_action": action, "final_option_id": option_id, "overrides": overrides, "warnings": warnings,
            "accepted_as_is": not overrides}

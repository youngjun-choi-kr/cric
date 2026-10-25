"""End-to-end incident on the frozen snapshot with every model call mocked: no network, no keys.

Checks the wiring a unit test cannot: physics -> dossiers -> context -> 3 votes -> validator -> report -> saved run.
"""

import json
from datetime import timedelta

import pytest

import cric.agent.decision as decision
import cric.agent.report as report
import cric.pipeline as pipeline
import cric.research.dossier as dossier
from cric.ingest.celestrak import Catalog, snapshot_catalog
from cric.physics.timeutil import iso, parse_utc

OURS, OTHER = 43250, 34430           # IRIDIUM 149 (demo fleet) x COSMOS 2251 DEB, 548 m on TLE data
TCA = "2026-10-25T12:56:05.511209Z"


@pytest.fixture(scope="module")
def small_catalog():
    full = snapshot_catalog()
    keep = [OURS, OTHER] + [n for n in list(full.sats)[:200] if n not in (OURS, OTHER)]
    return Catalog({n: full.sats[n] for n in keep}, {n: full.names[n] for n in keep}, "test subset", 0.0)


def _fake_structured(votes):
    calls = {"decide": 0}

    def fake(model, messages, schema, usage=None, max_tokens=16000):
        if schema is dossier.Dossier:
            unk = dossier.Fact(value=None, status="unknown")
            name = json.loads(messages[1]["content"].split("SATCAT record:\n")[1].split("\n\n")[0])["OBJECT_NAME"]
            return schema(norad_id=0, name=name, operator=unk, country=unk, mission_type=unk, launch_mass_kg=unk,
                          has_propulsion=unk, propulsion_type=unk, can_maneuver_now=unk, mission_status=unk,
                          contact_channel=unk, summary="test"), ""
        if schema is decision.Decision:
            action = votes[calls["decide"] % len(votes)]
            calls["decide"] += 1
            return schema(action=action, option_id="O1" if action == "MANEUVER" else None, message_to_other_operator=None,
                          rationale="mocked decision for the wiring test", key_factors=[{"factor": "f", "evidence": "e", "effect": "x"},
                                                           {"factor": "g", "evidence": "e", "effect": "y"}],
                          mission_conflicts=[], recheck_at_utc=None, confidence=0.7, unknowns=[]), "mock reasoning"
        if schema is report.Report:
            return schema(kind="escalation_brief", subject="Escalation 43250 / 34430", body="x" * 100,
                          asks=["decide"], facts_cited=["a", "b"]), ""
        raise AssertionError(schema)
    return fake, calls


def _patch(monkeypatch, tmp_path, votes):
    fake, calls = _fake_structured(votes)
    for mod in (dossier, decision, report):
        monkeypatch.setattr(mod, "structured_call", fake)
    rows = {OURS: ("IRIDIUM 149", "PAY", "+"), OTHER: ("COSMOS 2251 DEB", "DEB", "-")}
    monkeypatch.setattr(dossier, "satcat", lambda n: {"OBJECT_NAME": rows[n][0], "NORAD_CAT_ID": n, "OBJECT_TYPE": rows[n][1],
                                                      "OBJECT_TYPE_LABEL": {"PAY": "payload", "DEB": "debris"}[rows[n][1]],
                                                      "OPS_STATUS_LABEL": "operational" if rows[n][2] == "+" else "nonoperational"})
    monkeypatch.setattr(dossier, "DOSSIER_CACHE", tmp_path / "dossiers")
    monkeypatch.setattr(pipeline, "INCIDENTS", tmp_path / "incidents")
    return calls


def test_tle_burn_vote_becomes_data_request(monkeypatch, tmp_path, small_catalog):
    calls = _patch(monkeypatch, tmp_path, ["MANEUVER", "MANEUVER", "REQUEST_PRECISE_DATA"])
    now = iso(parse_utc(TCA) - timedelta(hours=9))
    res = pipeline.run_incident(OURS, OTHER, TCA, now_utc=now, use_tavily=False, cat=small_catalog, data_quality="tle")
    assert calls["decide"] == 3
    assert res["context"]["our_satellite"]["capability"]["can_maneuver"] == "yes"     # fleet registry, not the web
    assert res["context"]["risk"]["level"] == "red"
    assert res["decision"]["action"] == "MANEUVER" and res["decision"]["votes"] == {"MANEUVER": 2, "REQUEST_PRECISE_DATA": 1}
    assert res["validation"]["final_action"] == "REQUEST_PRECISE_DATA"                # never burn on TLE data with time left
    assert res["report"] is None                                                      # Ultra runs only when a person must act
    assert (tmp_path / "incidents").glob("*.json")


def test_split_vote_escalates_and_gets_a_brief(monkeypatch, tmp_path, small_catalog):
    _patch(monkeypatch, tmp_path, ["MANEUVER", "MONITOR", "COORDINATE"])
    now = iso(parse_utc(TCA) - timedelta(hours=9))
    res = pipeline.run_incident(OURS, OTHER, TCA, now_utc=now, use_tavily=False, cat=small_catalog,
                                data_quality="cdm")
    assert res["decision"]["action"] == "ESCALATE" and res["validation"]["final_action"] == "ESCALATE"
    assert res["report"]["kind"] == "escalation_brief"


def test_execution_check_clears_the_planned_burn(small_catalog):
    from cric.physics.execute import execute
    from cric.physics.maneuver import Encounter
    enc = Encounter(small_catalog.sats[OURS], small_catalog.sats[OTHER], parse_utc(TCA))
    opt = {"id": "O1", "direction": "retrograde", "dv_ms": 0.92, "lead_s": 1506.0}
    x = execute(enc, opt, samples=60)
    assert x["miss_before_km"] < 1 and x["miss_nominal_km"] > 1.5 and x["verdict"] == "cleared"
    weak = execute(enc, {**opt, "dv_ms": 0.2}, samples=60)
    assert weak["verdict"] != "cleared"

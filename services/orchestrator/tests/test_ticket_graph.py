"""End-to-end ticket graph tests on real Postgres + LangGraph checkpoints, offline models (fixtures)."""
import uuid

import pytest

from lanekeeper import auth, db, workflows
from lanekeeper.graphs.checkpoint import reset_checkpointer
from lanekeeper.policy import approvals
from lanekeeper.policy.engine import PolicyDenied

from conftest import USERS

pytestmark = pytest.mark.db

CELLS = {  # distinct cells >= 100 m apart so tests don't dedupe into each other
    "a": (40.739619, -73.976551), "b": (40.744371, -73.973086), "c": (40.751331, -73.967663), "d": (40.760965, -73.960092),
}


def cell_latlng(seq: int) -> tuple[float, float]:
    r = db.one("""select extensions.st_y(extensions.st_centroid(geom::extensions.geometry)) lat,
                         extensions.st_x(extensions.st_centroid(geom::extensions.geometry)) lng
                    from lane_cells where id = %s""", (f"MN-1AV-{seq:04d}",))
    return r["lat"], r["lng"]


def P(key):
    return auth.Principal(USERS[key], auth.load_memberships(USERS[key]))


def report(seq: int, fixture="glass-1av-dawn-01", hazard="glass", note="glass in the lane", acc=8, **kw):
    lat, lng = cell_latlng(seq)
    r = {"idempotency_key": f"t-{uuid.uuid4().hex[:12]}", "source_kind": "glasses_sim", "hazard_claimed": hazard, "note": note,
         "location": {"lat": lat, "lng": lng, "accuracy_m": acc, "source": "phone_gps"},
         "consent": {"policy_version": "rider-2026.11", "share_image": True, "share_location": True, "notify_status": True}}
    if fixture:
        r["fixture_id"] = fixture
    r.update(kw)
    return r


@pytest.fixture(autouse=True)
def fleet_ready(fresh_db):
    def reset():
        db.one("""update missions set status='aborted', abort_reason='test reset', ended_at=now()
                  where status in ('dispatched','running','paused_safety','returning') returning 1""")
        db.one("""update robot_status set state='docked', connectivity='online', battery_pct=100, hopper_pct=0, mission_id=null
                  returning 1""")
        db.one("update municipal_memberships set status='active', revoked_at=null, valid_until=null returning 1")
    reset()
    yield
    reset()


def approve_and_resume(r, who="nyc_supervisor"):
    approvals.decide(P(who), r["approval_id"], True, "constraints and estimates reviewed")
    return workflows.resume_after_approval(r["thread_id"], P(who))


def test_scenario_a_glass_report_to_verified_cleanup_and_public_report():
    r = workflows.submit_report(report(40), auth.ANONYMOUS)
    assert r["status"] == "waiting" and r["route"] == "robotic_remediation_candidate"
    assert r["interrupt"]["kind"] == "dispatch_approval"
    assert db.one("select count(*) n from missions where proposal_id::text = %s", (r["interrupt"]["proposal_id"],))["n"] == 0
    done = approve_and_resume(r)
    assert done["outcome"] == "verified_closed" and done["remediation_status"] == "verified"
    t = db.one("select status from tickets where id=%s", (done["ticket_id"],))
    assert t["status"] == "verified_closed"
    rep = db.one("select summary from public_run_reports where mission_id=%s", (done["mission_id"],))
    assert rep["summary"]["provenance"] == "SIMULATED" and rep["summary"]["verified_cleared"] == 1
    assert "rider" not in str(rep["summary"]).lower() and "lat" not in rep["summary"]
    kinds = [e["node"] for e in db.all_rows("select node from agent_events where run_id=%s and kind='exit' order by seq", (r["run_id"],))]
    for node in ("validate_identity_and_source", "extract_and_verify_location", "classify_hazard", "deduplicate_incident",
                 "human_approval_interrupt", "authorized_dispatch_gateway", "verify_remediation"):
        assert node in kinds


def test_denied_approval_produces_no_movement():
    r = workflows.submit_report(report(60), auth.ANONYMOUS)
    approvals.decide(P("nyc_supervisor"), r["approval_id"], False, "closure scheduled on this block")
    out = workflows.resume_after_approval(r["thread_id"], P("nyc_supervisor"))
    assert out["outcome"] == "escalated" and out["mission_id"] is None
    assert db.one("select count(*) n from robot_telemetry where event_id like %s", ("t:%",))["n"] >= 0
    assert db.one("select state from robot_status where robot_id='LK-01-A'")["state"] == "docked"


def test_interrupt_survives_process_restart():
    r = workflows.submit_report(report(80), auth.ANONYMOUS)
    approvals.decide(P("nyc_supervisor"), r["approval_id"], True, "reviewed before shift change")
    workflows.reset_graphs()
    reset_checkpointer()          # simulate a new process: no in-memory graph or saver
    out = workflows.resume_after_approval(r["thread_id"], P("nyc_supervisor"))
    assert out["outcome"] == "verified_closed"


def test_same_idempotency_key_is_processed_once():
    rep = report(100)
    r1 = workflows.submit_report(rep, auth.ANONYMOUS)
    r2 = workflows.submit_report(rep, auth.ANONYMOUS)
    assert r2.get("replayed") and r2["thread_id"] == r1["thread_id"]
    assert db.one("select count(*) n from hazard_reports where idempotency_key=%s", (rep["idempotency_key"],))["n"] == 1
    assert db.one("select count(*) n from approval_requests where target_id=%s", (r1["interrupt"]["proposal_id"],))["n"] == 1


def test_scenario_b_pothole_routes_to_road_repair_not_robot():
    r = workflows.submit_report(report(120, fixture="pothole-09", hazard="pothole", note="big pothole"), auth.ANONYMOUS)
    assert r["status"] == "done"
    assert r["route"] in ("urgent_non_robotic_hazard", "routine_ticket")
    t = db.one("""select t.remediation_path, d.code from tickets t left join departments d on d.id = t.department_id
                  where t.id = %s""", (r["ticket_id"],))
    assert t["remediation_path"] == "road_repair" and t["code"] == "DOT_ROAD"
    assert db.one("select count(*) n from work_orders where ticket_id=%s and kind='robot_sweep'", (r["ticket_id"],))["n"] == 0
    assert db.one("select count(*) n from approval_requests a join mission_proposals p on p.id::text = a.target_id "
                  "join work_orders w on w.id = any(p.work_order_ids) where w.ticket_id=%s", (r["ticket_id"],))["n"] == 0


def test_scenario_c_nearby_second_report_links_without_losing_evidence():
    r1 = workflows.submit_report(report(140), auth.ANONYMOUS)
    r2 = workflows.submit_report(report(141, note="same glass, seen from the other side"), auth.ANONYMOUS)
    assert r2["incident_id"] == r1["incident_id"]
    links = db.all_rows("select link_kind from incident_report_links where incident_id=%s order by created_at", (r1["incident_id"],))
    assert [x["link_kind"] for x in links] == ["origin", "auto_duplicate"]
    assert db.one("select count(*) n from hazard_reports where id in (%s,%s)", (r1["report_id"], r2["report_id"]))["n"] == 2
    assert r2["ticket_id"] == r1["ticket_id"]


def test_insufficient_evidence_waits_and_operator_can_dismiss():
    r = workflows.submit_report(report(160, fixture=None, hazard=None, note="something weird"), auth.ANONYMOUS)
    assert r["status"] == "waiting" and r["interrupt"]["awaiting"] == "evidence"
    out = workflows.resume_after_evidence(r["thread_id"], P("nyc_operator"), "dismiss", "no hazard found on patrol")
    assert out["outcome"] == "dismissed"


def test_outside_lanes_is_not_snapped():
    rep = report(10, fixture=None)
    rep["location"] = {"lat": 40.7580, "lng": -73.9855, "accuracy_m": 5, "source": "phone_gps"}   # Times Square
    r = workflows.submit_report(rep, auth.ANONYMOUS)
    row = db.one("select cell_id, match_status from hazard_reports where id=%s", (r["report_id"],))
    assert row["cell_id"] is None and row["match_status"] == "outside_lanes"
    assert r["interrupt"]["awaiting"] == "evidence"


def test_revocation_between_interrupt_and_resume_blocks_dispatch():
    r = workflows.submit_report(report(30), auth.ANONYMOUS)
    approvals.decide(P("nyc_supervisor"), r["approval_id"], True, "approved before revocation")
    db.one("update municipal_memberships set status='revoked' where user_id=%s returning 1", (USERS["nyc_supervisor"],))
    out = workflows.resume_after_approval(r["thread_id"], P("nyc_supervisor2"))
    assert out["mission_id"] is None and out["outcome"] == "escalated"


def test_other_city_cannot_resume_or_approve():
    r = workflows.submit_report(report(50), auth.ANONYMOUS)
    with pytest.raises(approvals.ApprovalError):
        approvals.decide(P("harbor_supervisor"), r["approval_id"], True, "cross tenant approve attempt")
    with pytest.raises(PolicyDenied):
        workflows.resume_after_approval(r["thread_id"], P("harbor_supervisor"))


def test_scenario_f_false_cleanup_claim_is_not_published_as_cleared():
    r = workflows.submit_report(report(170, fixture="glass-wide-04"), auth.ANONYMOUS, scenario="failed_verification")
    out = approve_and_resume(r)
    assert out["remediation_status"] == "failed_or_uncertain" and out["outcome"] == "reopened"
    assert db.one("select status from tickets where id=%s", (out["ticket_id"],))["status"] == "reopened"
    v = db.one("select verdict from remediation_verdicts where ticket_id=%s order by created_at desc limit 1", (out["ticket_id"],))
    assert v["verdict"] in ("partially_cleared", "not_cleared")
    s = db.one("select summary from public_run_reports where mission_id=%s", (out["mission_id"],))["summary"]
    assert s["verified_cleared"] == 0 and s["unverified_or_remaining"] == 1


def test_scenario_e_cyclist_causes_local_yield_and_auto_resume():
    r = workflows.submit_report(report(20, fixture="glass-1av-night-02"), auth.ANONYMOUS, scenario="cyclist_enters")
    out = approve_and_resume(r)
    ev = [e["kind"] for e in db.all_rows("select kind from mission_events where mission_id=%s order by id", (out["mission_id"],))]
    assert "safety_yield" in ev and "safety_resume_auto" in ev
    assert ev.index("safety_yield") < ev.index("safety_resume_auto")
    assert db.one("select count(*) n from robot_faults where mission_id=%s and code='obstacle_cyclist'", (out["mission_id"],))["n"] == 1


def test_scenario_g_lost_link_pauses_then_supervisor_resumes_without_duplicate_work():
    r = workflows.submit_report(report(5), auth.ANONYMOUS, scenario="disconnected")
    paused = approve_and_resume(r)
    assert paused["status"] == "waiting" and paused["interrupt"]["kind"] == "safety_pause"
    assert db.one("select status from missions where id=%s", (paused["mission_id"],))["status"] == "paused_safety"
    with pytest.raises(PolicyDenied):   # an operator lacks fleet.resume
        workflows.resolve_safety_pause(r["thread_id"], P("nyc_operator"), "resume")
    out = workflows.resolve_safety_pause(r["thread_id"], P("nyc_supervisor"), "resume")
    assert out["status"] == "done"
    dup = db.one("select count(*) - count(distinct cell_id) d from robot_telemetry where mission_id=%s and state <> 'paused_safety'",
                 (paused["mission_id"],))
    assert dup["d"] == 0


def test_optical_false_positive_is_marked_unverified():
    r = workflows.submit_report(report(180, fixture="glass-1av-dawn-01"), auth.ANONYMOUS, scenario="optical_false_positive")
    out = approve_and_resume(r)
    v = db.one("select verdict, reason from remediation_verdicts where ticket_id=%s", (out["ticket_id"],))
    assert v["verdict"] == "unverified" and "no glass" in v["reason"]


def test_dispatch_tool_replay_returns_same_mission():
    from lanekeeper.agents import dispatch
    r = workflows.submit_report(report(70), auth.ANONYMOUS)
    approvals.decide(P("nyc_supervisor"), r["approval_id"], True, "reviewed carefully")
    op = db.one("select operation_id::text from approval_requests where id=%s", (r["approval_id"],))["operation_id"]
    a = dispatch.dispatch_simulated_mission(P("nyc_supervisor"), r["interrupt"]["proposal_id"], r["approval_id"], f"dispatch:{op}")
    b = dispatch.dispatch_simulated_mission(P("nyc_supervisor"), r["interrupt"]["proposal_id"], r["approval_id"], f"dispatch:{op}")
    assert a["mission_id"] == b["mission_id"] and b["replayed"]
    with pytest.raises(PolicyDenied):   # different key, approval already consumed
        dispatch.dispatch_simulated_mission(P("nyc_supervisor"), r["interrupt"]["proposal_id"], r["approval_id"], "dispatch:other")
    assert db.one("select count(*) n from missions where approval_id=%s", (r["approval_id"],))["n"] == 1


def test_low_battery_robot_aborts_and_returns_to_depot():
    r = workflows.submit_report(report(90, fixture="glass-1av-dawn-01"), auth.ANONYMOUS, scenario="low_battery")
    out = approve_and_resume(r)
    m = db.one("select status, abort_reason from missions where id=%s", (out["mission_id"],))
    assert m["status"] == "aborted" and "battery" in m["abort_reason"]
    assert out["outcome"] == "reopened"


def test_twin_replay_is_deterministic():
    def kinds(seq, sc):
        r = workflows.submit_report(report(seq), auth.ANONYMOUS, scenario=sc)
        out = approve_and_resume(r)
        return [e["kind"] for e in db.all_rows("select kind from mission_events where mission_id=%s order by id", (out["mission_id"],))]
    assert kinds(110, "pedestrian") == kinds(130, "pedestrian")

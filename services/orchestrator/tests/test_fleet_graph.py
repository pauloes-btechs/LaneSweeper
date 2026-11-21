"""Scenario D: nightly planning across the two-robot pilot; denial produces no movement."""
import pytest

from lanekeeper import auth, db
from lanekeeper.graphs import fleet
from lanekeeper.planning import routes as planner
from lanekeeper.policy import approvals

from conftest import NYC, USERS

pytestmark = pytest.mark.db


def P(key):
    return auth.Principal(USERS[key], auth.load_memberships(USERS[key]))


@pytest.fixture
def backlog(fresh_db):
    db.one("""update missions set status='aborted' where status in ('dispatched','running','paused_safety') returning 1""")
    db.one("update robot_status set state='docked', connectivity='online', battery_pct=100, hopper_pct=0, mission_id=null returning 1")
    db.one("update work_orders set status='cancelled' where status in ('open','planned') returning 1")
    ids = []
    for seq, prio in [(12, 2), (18, 1), (160, 2), (170, 3)]:
        r = db.one(f"""with i as (insert into hazard_incidents (jurisdiction_id, cell_id, hazard_type, first_seen_at, last_seen_at)
                                  values (%s, 'MN-1AV-{seq:04d}', 'glass', now(), now()) returning id),
                        t as (insert into tickets (jurisdiction_id, incident_id, hazard_type, severity, priority, status, created_by)
                              select %s, id, 'glass', 'high', %s, 'triaged', 'test' from i returning id)
                   insert into work_orders (jurisdiction_id, ticket_id, kind, priority, cell_id, created_by)
                   select %s, id, 'robot_sweep', %s, 'MN-1AV-{seq:04d}', 'test' from t returning id::text""", (NYC, NYC, prio, NYC, prio))
        ids.append(r["id"])
    return ids


def test_planner_splits_work_by_nearest_depot_and_checks_constraints(backlog):
    from datetime import UTC, datetime, timedelta
    items = [planner.WorkItem(w, "t", c, 2, "glass") for w, c in zip(backlog, ["MN-1AV-0012", "MN-1AV-0018", "MN-1AV-0160", "MN-1AV-0170"],
                                                                         strict=True)]
    now = datetime.now(UTC)
    res = planner.plan(NYC, items, now, now + timedelta(hours=2))
    by_robot = {p.robot_id: p.target_cell_ids for p in res.plans}
    assert set(by_robot) == {"LK-01-A", "LK-01-B"}
    assert set(by_robot["LK-01-A"]) == {"MN-1AV-0012", "MN-1AV-0018"}
    for p in res.plans:
        assert all(c["ok"] for c in p.constraints)
        assert "ESTIMATE" in p.estimates["label"]


def test_planner_never_relaxes_constraints(backlog):
    from datetime import UTC, datetime, timedelta
    db.one("update robot_status set battery_pct=27 where robot_id='LK-01-B' returning 1")
    now = datetime.now(UTC)
    items = [planner.WorkItem(backlog[2], "t", "MN-1AV-0160", 2, "glass")]
    res = planner.plan(NYC, items, now, now + timedelta(hours=2), policy={"fleet": {"min_battery_reserve_pct": 25}})
    for p in res.plans:
        assert p.estimates["battery_end_pct"] >= 25


def test_closure_excludes_cells(backlog):
    from datetime import UTC, datetime, timedelta
    now = datetime.now(UTC)
    db.one("""insert into closures (jurisdiction_id, cell_ids, reason, starts_at, ends_at)
              values (%s, '{MN-1AV-0170}', 'street fair', now() - interval '1 hour', now() + interval '6 hours') returning 1""", (NYC,))
    try:
        res = planner.plan(NYC, [planner.WorkItem(backlog[3], "t", "MN-1AV-0170", 3, "glass")], now, now + timedelta(hours=2))
        assert not res.plans and "closure" in res.unassigned[0]["reason"]
    finally:
        db.one("delete from closures where reason='street fair' returning 1")


def test_scenario_d_approve_one_deny_other(backlog):
    s = fleet.start(P("nyc_dispatcher"), NYC, label="nightly-test")
    assert s["status"] == "waiting" and s["interrupt"]["kind"] == "fleet_dispatch_approvals"
    by_robot = {a["robot_id"]: a for a in s["approvals"]}
    assert set(by_robot) == {"LK-01-A", "LK-01-B"}
    # dispatcher requested; a supervisor (different person) decides
    approvals.decide(P("nyc_supervisor"), by_robot["LK-01-A"]["approval_id"], True, "south route reviewed")
    approvals.decide(P("nyc_supervisor"), by_robot["LK-01-B"]["approval_id"], False, "north block has an event tonight")
    out = fleet.resume(s["thread_id"], P("nyc_supervisor"), {})
    assert out["status"] == "done" and len(out["missions"]) == 1
    assert db.one("select robot_id from missions where id=%s", (out["missions"][0],))["robot_id"] == "LK-01-A"
    assert db.one("select count(*) n from robot_telemetry where robot_id='LK-01-B' and mission_id is not null "
                  "and at > now() - interval '5 minutes'")["n"] == 0
    assert any(x["robot_id"] == "LK-01-B" and "denied" in x["reason"] for x in out["not_dispatched"])
    op = db.one("select operation_id::text from missions where id=%s", (out["missions"][0],))["operation_id"]
    assert op == by_robot["LK-01-A"]["operation_id"]


def test_requester_cannot_self_approve_fleet_plan(backlog):
    s = fleet.start(P("nyc_dispatcher"), NYC, label="self-approve-test")
    a = s["approvals"][0]
    with pytest.raises(approvals.ApprovalError, match="separation of duties"):
        approvals.decide(P("nyc_dispatcher"), a["approval_id"], True, "approving my own plan")

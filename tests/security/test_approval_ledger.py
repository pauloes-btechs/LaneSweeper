"""Approval ledger and DB-level safety guards: single use, payload binding, revocation, expiry, simulation-only."""
import hashlib
import json
import uuid

import psycopg
import pytest

from conftest import NYC, USERS

pytestmark = pytest.mark.db

PAYLOAD = {"robot_id": "LK-01-A", "route_cell_ids": ["MN-1AV-0040"], "simulation_mode": True}


def h(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


def make_approval(cur, status="approved", approver="nyc_supervisor", minutes=30, payload=PAYLOAD, requested_by_user=None):
    aid, op = uuid.uuid4(), uuid.uuid4()
    cur.execute("""
      insert into approval_requests (id, operation_id, jurisdiction_id, action, target_kind, target_id, payload, payload_hash,
                                     simulation_mode, required_permission, requested_by, requested_by_user, expires_at)
      values (%s,%s,%s,'dispatch_simulated_mission','mission_proposal','p1',%s,%s,true,'mission.approve','agent:test',%s,
              now() + make_interval(mins => %s))""",
                (aid, op, NYC, json.dumps(payload), h(payload), requested_by_user, minutes))
    if status != "pending":
        cur.execute("update approval_requests set status=%s, approver_id=%s, decided_at=now() where id=%s",
                    (status, USERS[approver], aid))
    return aid, op


def consume(cur, aid, op, payload_hash=None, action="dispatch_simulated_mission", jid=NYC):
    cur.execute("select lk.consume_approval(%s,%s,%s,%s,%s,null)", (aid, op, payload_hash or h(PAYLOAD), jid, action))
    return cur.fetchone()[0]


def test_approved_consumes_exactly_once(admin):
    with admin.cursor() as cur:
        aid, op = make_approval(cur)
        assert consume(cur, aid, op) == aid
        cur.execute("savepoint s")
        with pytest.raises(psycopg.errors.RaiseException, match="consumed"):
            consume(cur, aid, op)
        cur.execute("rollback to savepoint s")


@pytest.mark.parametrize("status", ["pending", "denied", "revoked"])
def test_non_approved_states_block(admin, status):
    with admin.cursor() as cur:
        aid, op = make_approval(cur, status="pending")
        if status != "pending":
            cur.execute("update approval_requests set status=%s where id=%s", (status, aid))
        with pytest.raises(psycopg.errors.RaiseException, match=status):
            consume(cur, aid, op)


def test_expired_approval_blocks(admin):
    with admin.cursor() as cur:
        aid, op = make_approval(cur)
        cur.execute("update approval_requests set expires_at = now() - interval '1 second' where id=%s", (aid,))
        with pytest.raises(psycopg.errors.RaiseException, match="expired"):
            consume(cur, aid, op)


def test_wrong_payload_operation_action_or_tenant_blocks(admin):
    with admin.cursor() as cur:
        aid, op = make_approval(cur)
        changed = {**PAYLOAD, "route_cell_ids": ["MN-1AV-0040", "MN-1AV-0041"]}
        for kwargs, msg in [({"payload_hash": h(changed)}, "payload hash"),
                            ({"action": "cancel_simulated_mission"}, "action"),
                            ({"jid": "22222222-2222-4222-8222-222222222222"}, "jurisdiction")]:
            cur.execute("savepoint s")
            with pytest.raises(psycopg.errors.RaiseException, match=msg):
                consume(cur, aid, op, **kwargs)
            cur.execute("rollback to savepoint s")
        cur.execute("savepoint s")
        with pytest.raises(psycopg.errors.RaiseException, match="operation"):
            cur.execute("select lk.consume_approval(%s,%s,%s,%s,'dispatch_simulated_mission',null)",
                        (aid, uuid.uuid4(), h(PAYLOAD), NYC))
        cur.execute("rollback to savepoint s")


def test_approver_revoked_between_approval_and_consume_blocks(admin):
    with admin.cursor() as cur:
        aid, op = make_approval(cur)
        cur.execute("update municipal_memberships set status='revoked' where user_id=%s", (USERS["nyc_supervisor"],))
        with pytest.raises(psycopg.errors.RaiseException, match="no longer authorized"):
            consume(cur, aid, op)


def test_approval_payload_is_immutable(admin):
    with admin.cursor() as cur:
        aid, _ = make_approval(cur, status="pending")
        with pytest.raises(psycopg.errors.RaiseException, match="immutable"):
            cur.execute("update approval_requests set payload_hash=%s where id=%s", ("0" * 64, aid))


def test_terminal_approval_cannot_be_reopened(admin):
    with admin.cursor() as cur:
        aid, _ = make_approval(cur, status="denied")
        with pytest.raises(psycopg.errors.RaiseException, match="terminal"):
            cur.execute("update approval_requests set status='approved' where id=%s", (aid,))


def test_separation_of_duties_requester_cannot_approve(admin):
    with admin.cursor() as cur:
        with pytest.raises(psycopg.errors.CheckViolation):
            make_approval(cur, approver="nyc_supervisor", requested_by_user=USERS["nyc_supervisor"])


def test_real_dispatch_cannot_be_requested(admin):
    with admin.cursor() as cur:
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute("""insert into approval_requests (jurisdiction_id, action, target_kind, target_id, payload, payload_hash,
                             simulation_mode, required_permission, requested_by, expires_at)
                           values (%s,'dispatch_simulated_mission','x','x','{}',%s,false,'mission.approve','t', now()+interval '1h')""",
                        (NYC, "a" * 64))


def _proposal(cur, robot="LK-01-A"):
    cur.execute("""insert into mission_proposals (jurisdiction_id, robot_id, work_order_ids, route_cell_ids, window_start, window_end,
                     estimates, constraints, rationale, payload_hash)
                   values (%s,%s,'{}','{MN-1AV-0040}', now(), now()+interval '1h','{}','{}','test','x') returning id""", (NYC, robot))
    return cur.fetchone()[0]


def test_mission_requires_consumed_approval(admin):
    with admin.cursor() as cur:
        aid, op = make_approval(cur)  # approved but not consumed
        pid = _proposal(cur)
        with pytest.raises(psycopg.errors.RaiseException, match="consumed approval"):
            cur.execute("insert into missions (jurisdiction_id, proposal_id, approval_id, operation_id, robot_id) values (%s,%s,%s,%s,'LK-01-A')",
                        (NYC, pid, aid, op))


def test_mission_on_real_robot_is_impossible(admin):
    with admin.cursor() as cur:
        cur.execute("insert into robots (id, jurisdiction_id, name, mode) values ('REAL-01', %s, 'real', 'real')", (NYC,))
        aid, op = make_approval(cur)
        consume(cur, aid, op)
        pid = _proposal(cur, robot="REAL-01")
        with pytest.raises(psycopg.errors.RaiseException, match="not simulated"):
            cur.execute("insert into missions (jurisdiction_id, proposal_id, approval_id, operation_id, robot_id) values (%s,%s,%s,%s,'REAL-01')",
                        (NYC, pid, aid, op))


def test_mission_with_simulation_false_is_rejected(admin):
    with admin.cursor() as cur:
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute("""insert into mission_proposals (jurisdiction_id, robot_id, work_order_ids, route_cell_ids, window_start, window_end,
                             estimates, constraints, rationale, payload_hash, simulation_mode)
                           values (%s,'LK-01-A','{}','{MN-1AV-0040}', now(), now()+interval '1h','{}','{}','t','x', false)""", (NYC,))


def test_one_approval_cannot_start_two_missions(admin):
    with admin.cursor() as cur:
        aid, op = make_approval(cur)
        consume(cur, aid, op)
        p1, p2 = _proposal(cur), _proposal(cur)
        cur.execute("insert into missions (jurisdiction_id, proposal_id, approval_id, operation_id, robot_id) values (%s,%s,%s,%s,'LK-01-A')",
                    (NYC, p1, aid, op))
        with pytest.raises(psycopg.errors.UniqueViolation):
            cur.execute("insert into missions (jurisdiction_id, proposal_id, approval_id, operation_id, robot_id) values (%s,%s,%s,%s,'LK-01-A')",
                        (NYC, p2, aid, op))


def test_hardware_gateway_flag_cannot_be_enabled(admin):
    with admin.cursor() as cur:
        with pytest.raises(psycopg.errors.RaiseException, match="cannot be enabled"):
            cur.execute("update feature_flags set enabled=true where key='hardware_gateway'")


def test_ticket_cannot_be_verified_closed_without_cleared_verdict(admin):
    with admin.cursor() as cur:
        cur.execute("insert into tickets (jurisdiction_id, hazard_type, severity, priority, created_by) values (%s,'glass','high',2,'t') returning id", (NYC,))
        tid = cur.fetchone()[0]
        cur.execute("savepoint s")
        with pytest.raises(psycopg.errors.RaiseException, match="without a cleared verdict"):
            cur.execute("update tickets set status='verified_closed' where id=%s", (tid,))
        cur.execute("rollback to savepoint s")
        cur.execute("""insert into remediation_verdicts (jurisdiction_id, ticket_id, cell_id, verdict, method, decided_by, reason)
                       values (%s,%s,'MN-1AV-0040','unverified','model','t','after frame inconclusive')""", (NYC, tid))
        cur.execute("savepoint s")
        with pytest.raises(psycopg.errors.RaiseException, match="latest: unverified"):
            cur.execute("update tickets set status='verified_closed' where id=%s", (tid,))
        cur.execute("rollback to savepoint s")
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute("""insert into remediation_verdicts (jurisdiction_id, ticket_id, cell_id, verdict, method, decided_by, reason)
                           values (%s,%s,'MN-1AV-0040','cleared','model','t','route finished')""", (NYC, tid))


def test_audit_log_is_append_only(admin):
    with admin.cursor() as cur:
        cur.execute("insert into audit_log (actor, action, outcome) values ('t','x','allowed') returning id")
        i = cur.fetchone()[0]
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            cur.execute("delete from audit_log where id=%s", (i,))

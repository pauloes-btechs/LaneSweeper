"""Dispatch Planning Agent + the privileged simulated-dispatch gateway.

Proposals are immutable versions with a canonical payload hash. Dispatch happens only through
dispatch_simulated_mission(), which re-verifies everything inside ONE transaction and is idempotent."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from .. import db
from ..auth import Principal, reload
from ..hashing import canonical_json, payload_hash
from ..planning import routes as planner
from ..policy import approvals
from ..policy.engine import PolicyDenied, SideEffect, audit, check
from ..sim.adapter import adapter_for

ACTION = "dispatch_simulated_mission"


def sweep_window(policy: dict, now: datetime | None = None, *, immediate: bool = False) -> tuple[datetime, datetime]:
    """Next configured sweep window (pilot default 04:00–06:00 America/New_York). `immediate` gives a 2-hour window from
    now for demos, still recorded on the proposal so reviewers see it."""
    now = now or datetime.now(UTC)
    if immediate:
        return now, now + timedelta(hours=2)
    w = (policy or {}).get("sweep_window") or {"start": "04:00", "end": "06:00", "tz": "America/New_York"}
    tz = ZoneInfo(w.get("tz", "America/New_York"))
    local = now.astimezone(tz)
    sh, sm = map(int, w["start"].split(":"))
    eh, em = map(int, w["end"].split(":"))
    start = local.replace(hour=sh, minute=sm, second=0, microsecond=0)
    if start <= local:
        start += timedelta(days=1)
    end = start.replace(hour=eh, minute=em)
    return start.astimezone(UTC), end.astimezone(UTC)


def proposal_payload(p: dict) -> dict:
    """The exact, canonical content an approver sees and signs off on."""
    return {"proposal_id": str(p["id"]), "version": p["version"], "jurisdiction_id": str(p["jurisdiction_id"]),
            "robot_id": p["robot_id"], "route_cell_ids": list(p["route_cell_ids"]),
            "work_order_ids": sorted(str(w) for w in p["work_order_ids"]),
            "window_start": p["window_start"].isoformat(), "window_end": p["window_end"].isoformat(),
            "simulation_mode": True}


def create_proposals(jurisdiction_id: str, work_items: list[planner.WorkItem], window: tuple[datetime, datetime], *,
                     run_id: str | None, policy: dict, max_robots: int | None = None) -> tuple[list[dict], dict]:
    result = planner.plan(jurisdiction_id, work_items, window[0], window[1], policy=policy, max_robots=max_robots)
    out = []
    with db.tx() as cur:
        for mp in result.plans:
            pid = str(uuid.uuid4())
            row = {"id": pid, "version": 1, "jurisdiction_id": jurisdiction_id, "robot_id": mp.robot_id,
                   "route_cell_ids": mp.route_cell_ids, "work_order_ids": mp.work_order_ids,
                   "window_start": window[0], "window_end": window[1]}
            h = payload_hash(proposal_payload(row))
            cur.execute("""insert into mission_proposals (id, jurisdiction_id, run_id, robot_id, work_order_ids, route_cell_ids,
                                                          window_start, window_end, estimates, constraints, rationale, payload_hash)
                           values (%s,%s,%s,%s,%s::uuid[],%s,%s,%s,%s,%s,%s,%s)""",
                        (pid, jurisdiction_id, run_id, mp.robot_id, mp.work_order_ids, mp.route_cell_ids, window[0], window[1],
                         db.jsonb(mp.estimates), db.jsonb(mp.constraints), mp.rationale, h))
            cur.execute("update work_orders set status='planned' where id = any(%s::uuid[]) and status='open'", (mp.work_order_ids,))
            out.append({**row, "payload_hash": h, "estimates": mp.estimates, "constraints": mp.constraints,
                        "rationale": mp.rationale, "target_cell_ids": mp.target_cell_ids})
    return out, result.as_dict()


def load_proposal(proposal_id: str) -> dict:
    p = db.one("select * from mission_proposals where id=%s", (proposal_id,))
    if not p:
        raise PolicyDenied("proposal not found")
    return p


def request_approval(proposal_id: str, *, run_id: str | None, requested_by_user: str | None = None, ttl_minutes: int = 30) -> dict:
    """Idempotent: re-running (e.g. node restart after interrupt) returns the existing pending/approved request."""
    p = load_proposal(proposal_id)
    payload = proposal_payload(p)
    h = payload_hash(payload)
    existing = db.one("""select id::text, operation_id::text, status, payload_hash, expires_at from approval_requests
                         where target_id=%s and action=%s and payload_hash=%s and status in ('pending','approved')
                         order by created_at desc limit 1""", (proposal_id, ACTION, h))
    if existing:
        return {"approval_id": existing["id"], "operation_id": existing["operation_id"], "payload_hash": h,
                "status": existing["status"], "reused": True}
    with db.tx() as cur:
        ref = approvals.request(cur, jurisdiction_id=str(p["jurisdiction_id"]), action=ACTION, target_kind="mission_proposal",
                                target_id=proposal_id, payload=payload, requested_by=f"agent:{(run_id or '?')[:8]}",
                                requested_by_user=requested_by_user, run_id=run_id, ttl_minutes=ttl_minutes)
        cur.execute("update mission_proposals set status='awaiting_approval' where id=%s and status='proposed'", (proposal_id,))
    return {"approval_id": ref.approval_id, "operation_id": ref.operation_id, "payload_hash": ref.payload_hash,
            "status": "pending", "reused": False}


def dispatch_simulated_mission(principal: Principal, proposal_id: str, approval_id: str, idempotency_key: str, *,
                               scenario: str | None = None, seed: int = 0, trace_id: str | None = None) -> dict:
    """Server-side privileged tool. Verifies session membership, current policy, immutable payload hash, single-use
    approval, robot state and idempotency. Returns a durable mission record — never a motor command."""
    principal = reload(principal)
    p = load_proposal(proposal_id)
    jid = str(p["jurisdiction_id"])
    payload = proposal_payload(p)
    h = payload_hash(payload)
    if h != p["payload_hash"]:
        raise PolicyDenied("proposal content changed after hashing")
    d = check(principal, jid, "mission.propose", SideEffect.SIMULATION)
    if not d.allowed:
        with db.tx() as cur:
            audit(cur, actor=principal.user_id or "anonymous", action=ACTION, outcome="denied", reason=d.reason,
                  target=proposal_id, trace_id=trace_id, detail={"requested_jurisdiction": jid})
        raise PolicyDenied(d.reason)
    a = approvals.revalidate(approval_id, expected_hash=h, jurisdiction_id=jid, action=ACTION)
    with db.tx() as cur:
        cur.execute("""insert into tool_invocations (run_id, jurisdiction_id, tool, side_effect, idempotency_key, principal_id,
                                                     approval_id, args_hash, status)
                       values (%s,%s,%s,'simulation',%s,%s,%s,%s,'started')
                       on conflict (idempotency_key) do nothing returning id::text""",
                    (p["run_id"], jid, ACTION, idempotency_key, principal.user_id, approval_id,
                     payload_hash({"proposal": proposal_id, "approval": approval_id})))
        inv = cur.fetchone()
        if not inv:
            cur.execute("select status, result from tool_invocations where idempotency_key=%s", (idempotency_key,))
            prev = cur.fetchone()
            if prev["status"] == "succeeded":
                return {**prev["result"], "replayed": True}
            raise PolicyDenied(f"a previous attempt with this idempotency key is {prev['status']}")
        inv_id = inv["id"]
        cur.execute("select r.mode, s.state, s.connectivity, s.battery_pct from robots r join robot_status s on s.robot_id=r.id "
                    "where r.id=%s and r.jurisdiction_id=%s for update of s", (p["robot_id"], jid))
        robot = cur.fetchone()
        if not robot:
            raise PolicyDenied("robot not in this jurisdiction")
        adapter_for(robot["mode"]).accept_mission({"simulation_mode": True})
        if robot["state"] not in ("docked", "idle", "charging") or robot["connectivity"] != "online":
            raise PolicyDenied(f"robot not dispatchable now ({robot['state']}/{robot['connectivity']}); re-plan required")
        approvals.consume(cur, approval_id, operation_id=a["operation_id"], expected_hash=h, jurisdiction_id=jid,
                          action=ACTION, invocation_id=inv_id)
        cur.execute("""insert into missions (jurisdiction_id, proposal_id, approval_id, operation_id, robot_id, seed, scenario)
                       values (%s,%s,%s,%s,%s,%s,%s) returning id::text, dispatched_at""",
                    (jid, proposal_id, approval_id, a["operation_id"], p["robot_id"], seed, scenario))
        m = cur.fetchone()
        eta = 0.0
        for i, cell in enumerate(p["route_cell_ids"]):
            eta += 25.0
            cur.execute("insert into mission_stops (mission_id, seq, cell_id, planned_eta_s) values (%s,%s,%s,%s)",
                        (m["id"], i, cell, eta))
        cur.execute("update mission_proposals set status='approved' where id=%s", (proposal_id,))
        cur.execute("update work_orders set status='in_progress', mission_id=%s where id = any(%s::uuid[])",
                    (m["id"], p["work_order_ids"]))
        cur.execute("""update tickets set status='in_progress' where id in (select ticket_id from work_orders where id = any(%s::uuid[]))
                       and status in ('triaged','assigned','scheduled')""", (p["work_order_ids"],))
        cur.execute("update lane_cell_status set condition='scheduled', updated_at=now() where cell_id = any(%s)", (p["route_cell_ids"],))
        cur.execute("update robot_status set state='en_route', mission_id=%s, updated_at=now() where robot_id=%s", (m["id"], p["robot_id"]))
        result = {"mission_id": m["id"], "operation_id": a["operation_id"], "robot_id": p["robot_id"], "simulation_mode": True,
                  "approved_by": a["approver_id"], "dispatched_at": m["dispatched_at"].isoformat(), "provenance": "SIMULATED"}
        cur.execute("update tool_invocations set status='succeeded', result=%s, finished_at=now() where id=%s",
                    (db.jsonb(result), inv_id))
        audit(cur, actor=principal.user_id, action=ACTION, outcome="allowed", jurisdiction_id=jid, target=m["id"],
              trace_id=trace_id, detail={"approval_id": approval_id, "operation_id": a["operation_id"], "payload_hash": h})
    return result


def describe_for_approver(proposal_id: str) -> dict:
    p = load_proposal(proposal_id)
    return {"proposal_id": proposal_id, "robot_id": p["robot_id"], "route_cell_ids": p["route_cell_ids"],
            "window": [p["window_start"].isoformat(), p["window_end"].isoformat()], "estimates": p["estimates"],
            "constraints": p["constraints"], "rationale": p["rationale"], "simulation_mode": True,
            "payload_canonical": canonical_json(proposal_payload(p))}

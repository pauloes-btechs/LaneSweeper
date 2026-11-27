"""Action-specific approval ledger (replaces the workshop's `approved: bool`).

An approval binds: the authenticated approver, the jurisdiction, one action, one target, the exact
payload (by hash), simulation_mode, an expiry and a single-use operation id. It is re-validated when
an interrupted graph resumes AND consumed atomically at the privileged tool boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg

from .. import db
from ..auth import Principal, reload
from ..config import get_settings
from ..hashing import payload_hash, sign, verify_signature
from .engine import PolicyDenied, SideEffect, audit, check

ACTION_PERMISSION = {
    "dispatch_simulated_mission": "mission.approve",
    "cancel_simulated_mission": "mission.cancel",
    "publish_rider_notice": "notice.publish",
    "change_safety_limits": "admin.policy",
    "change_membership": "admin.membership",
}


class ApprovalError(PolicyDenied):
    pass


@dataclass(frozen=True)
class ApprovalRef:
    approval_id: str
    operation_id: str
    payload_hash: str
    expires_at: datetime


def request(cur, *, jurisdiction_id: str, action: str, target_kind: str, target_id: str, payload: dict[str, Any],
            requested_by: str, requested_by_user: str | None = None, run_id: str | None = None,
            ttl_minutes: int = 30) -> ApprovalRef:
    if action not in ACTION_PERMISSION:
        raise ApprovalError(f"unknown action {action}")
    sim = bool(payload.get("simulation_mode", False))
    if action == "dispatch_simulated_mission" and not sim:
        raise ApprovalError("only simulation-mode dispatch can be requested")
    h = payload_hash(payload)
    cur.execute(
        """insert into approval_requests (jurisdiction_id, run_id, action, target_kind, target_id, payload, payload_hash,
                                          simulation_mode, required_permission, requested_by, requested_by_user, expires_at)
           values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now() + make_interval(mins => %s))
           returning id::text, operation_id::text, expires_at""",
        (jurisdiction_id, run_id, action, target_kind, target_id, db.jsonb(payload), h, sim,
         ACTION_PERMISSION[action], requested_by, requested_by_user, ttl_minutes),
    )
    r = cur.fetchone()
    return ApprovalRef(r["id"], r["operation_id"], h, r["expires_at"])


def _signature(a: dict) -> str:
    return sign(get_settings().signing_secret, a["id"], a["operation_id"], a["payload_hash"], a["approver_id"],
                a["expires_at"].isoformat())


def decide(principal: Principal, approval_id: str, approve: bool, reason: str, *, trace_id: str | None = None) -> dict:
    """A human approves or denies. Identity and authority are re-read from the database."""
    principal = reload(principal)
    if not reason or len(reason.strip()) < 3:
        raise ApprovalError("a reason is required")
    with db.tx() as cur:
        cur.execute("select *, id::text as id, operation_id::text as operation_id, jurisdiction_id::text as jurisdiction_id "
                    "from approval_requests where id = %s for update", (approval_id,))
        a = cur.fetchone()
        if not a:
            raise ApprovalError("approval not found")
        d = check(principal, a["jurisdiction_id"], a["required_permission"], SideEffect.SIMULATION)
        if not d.allowed:
            denied = d.reason
        else:
            denied = None
    if denied:
        # Audit the refused attempt in its own transaction so it survives the error below.
        with db.tx() as cur:
            audit(cur, actor=principal.user_id or "anonymous", action=f"approval.{'approve' if approve else 'deny'}",
                  outcome="denied", reason=denied, target=approval_id, trace_id=trace_id,
                  detail={"requested_jurisdiction": a["jurisdiction_id"]})
        raise ApprovalError(denied)
    with db.tx() as cur:
        cur.execute("select *, id::text as id, operation_id::text as operation_id, jurisdiction_id::text as jurisdiction_id "
                    "from approval_requests where id = %s for update", (approval_id,))
        a = cur.fetchone()
        if a["status"] != "pending":
            raise ApprovalError(f"approval is {a['status']}")
        if a["expires_at"] > datetime.now(UTC):
            if a["requested_by_user"] and str(a["requested_by_user"]) == principal.user_id:
                raise ApprovalError("separation of duties: the requester cannot approve")
            status = "approved" if approve else "denied"
            a["approver_id"] = principal.user_id
            sig = _signature(a)
            cur.execute("""update approval_requests set status=%s, approver_id=%s, decision_reason=%s, decided_at=now(), signature=%s
                           where id=%s returning id::text, operation_id::text, status, payload_hash, expires_at, approver_id::text""",
                        (status, principal.user_id, reason.strip()[:500], sig, approval_id))
            out = cur.fetchone()
            audit(cur, actor=principal.user_id, action=f"approval.{status}", outcome="allowed",
                  jurisdiction_id=a["jurisdiction_id"], target=approval_id, reason=reason[:200], trace_id=trace_id,
                  detail={"action": a["action"], "payload_hash": a["payload_hash"]})
            return out
    # Expired: record it in its own transaction so the error below does not roll it back.
    with db.tx() as cur:
        cur.execute("update approval_requests set status='expired' where id=%s and status='pending'", (approval_id,))
    raise ApprovalError("approval expired")


def revoke(principal: Principal, approval_id: str, reason: str) -> None:
    principal = reload(principal)
    with db.tx() as cur:
        cur.execute("select jurisdiction_id::text, required_permission, status from approval_requests where id=%s for update",
                    (approval_id,))
        a = cur.fetchone()
        if not a:
            raise ApprovalError("approval not found")
        check(principal, a["jurisdiction_id"], a["required_permission"], SideEffect.SIMULATION).raise_if_denied()
        if a["status"] not in ("pending", "approved"):
            raise ApprovalError(f"cannot revoke a {a['status']} approval")
        cur.execute("update approval_requests set status='revoked', revoked_at=now(), decision_reason=%s where id=%s",
                    (reason[:500], approval_id))
        audit(cur, actor=principal.user_id, action="approval.revoke", outcome="allowed",
              jurisdiction_id=a["jurisdiction_id"], target=approval_id, reason=reason[:200])


def revalidate(approval_id: str, *, expected_hash: str, jurisdiction_id: str, action: str) -> dict:
    """Called when a graph resumes. Read-only: everything must still hold, including the approver's authority."""
    a = db.one("""select id::text, operation_id::text, jurisdiction_id::text, action, status, payload_hash, expires_at,
                         approver_id::text, required_permission, signature
                    from approval_requests where id = %s""", (approval_id,))
    if not a:
        raise ApprovalError("approval not found")
    if a["status"] != "approved":
        raise ApprovalError(f"approval is {a['status']}")
    if a["expires_at"] <= datetime.now(UTC):
        raise ApprovalError("approval expired")
    if a["payload_hash"] != expected_hash:
        raise ApprovalError("payload changed since approval")
    if a["jurisdiction_id"] != str(jurisdiction_id) or a["action"] != action:
        raise ApprovalError("approval is for a different jurisdiction or action")
    if not verify_signature(get_settings().signing_secret, a["signature"], a["id"], a["operation_id"],
                            a["payload_hash"], a["approver_id"], a["expires_at"].isoformat()):
        raise ApprovalError("approval signature invalid")
    approver = reload(Principal(user_id=a["approver_id"]))
    if not check(approver, jurisdiction_id, a["required_permission"], SideEffect.SIMULATION).allowed:
        raise ApprovalError("approver no longer holds the required authority")
    return a


def consume(cur, approval_id: str, *, operation_id: str, expected_hash: str, jurisdiction_id: str, action: str,
            invocation_id: str | None) -> str:
    """Atomic, single-use. Must run in the SAME transaction as the side effect it authorizes."""
    try:
        cur.execute("savepoint consume_approval")
        cur.execute("select lk.consume_approval(%s,%s,%s,%s,%s,%s) as id",
                    (approval_id, operation_id, expected_hash, jurisdiction_id, action, invocation_id))
        consumed = str(cur.fetchone()["id"])
        cur.execute("release savepoint consume_approval")
        return consumed
    except psycopg.errors.RaiseException as e:
        cur.execute("rollback to savepoint consume_approval")
        raise ApprovalError(str(e).split("\n")[0]) from e


def ttl_from_policy(policy: dict) -> timedelta:
    return timedelta(minutes=int(policy.get("approval", {}).get("ttl_minutes", 30)))

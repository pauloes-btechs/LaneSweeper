"""Policy engine. Every tool call and API write is decided here, independently of any model."""
from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .. import db
from ..auth import Principal


class SideEffect(str, Enum):
    READ = "read"
    INTERNAL_WRITE = "internal_write"
    NOTIFICATION = "notification"
    SIMULATION = "simulation"
    PHYSICAL = "physical"          # real-world actuation: never allowed in this build


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    permission: str
    jurisdiction_id: str | None

    def raise_if_denied(self) -> None:
        if not self.allowed:
            raise PolicyDenied(self.reason)


class PolicyDenied(Exception):
    pass


_matrix_cache: tuple[float, dict[str, set[str]]] | None = None


def permission_matrix(max_age_s: float = 30.0) -> dict[str, set[str]]:
    global _matrix_cache
    now = time.monotonic()
    if _matrix_cache and now - _matrix_cache[0] < max_age_s:
        return _matrix_cache[1]
    m: dict[str, set[str]] = {}
    for r in db.all_rows("select role, permission from role_permissions"):
        m.setdefault(r["role"], set()).add(r["permission"])
    _matrix_cache = (now, m)
    return m


def invalidate_matrix_cache() -> None:
    global _matrix_cache
    _matrix_cache = None


def check(principal: Principal, jurisdiction_id: str | None, permission: str,
          side_effect: SideEffect = SideEffect.READ) -> Decision:
    if side_effect is SideEffect.PHYSICAL or permission == "robot.command_real":
        return Decision(False, "real-world robot actuation is unavailable in this build", permission, jurisdiction_id)
    if permission in ("report.create", "map.view_public"):
        return Decision(True, "public action", permission, jurisdiction_id)
    if principal.anonymous:
        return Decision(False, "authentication required", permission, jurisdiction_id)
    if jurisdiction_id is None:
        return Decision(False, "jurisdiction required for staff actions", permission, None)
    roles = principal.roles_in(str(jurisdiction_id))
    if not roles:
        return Decision(False, "no active membership in this jurisdiction", permission, jurisdiction_id)
    matrix = permission_matrix()
    if any(permission in matrix.get(r, ()) for r in roles):
        return Decision(True, f"granted via {sorted(roles)}", permission, jurisdiction_id)
    return Decision(False, f"roles {sorted(roles)} lack {permission}", permission, jurisdiction_id)


def audit(cur, *, actor: str, action: str, outcome: str, jurisdiction_id: str | None = None,
          target: str | None = None, reason: str | None = None, detail: dict[str, Any] | None = None,
          trace_id: str | None = None) -> None:
    cur.execute(
        """insert into audit_log (actor, action, outcome, jurisdiction_id, target, reason, detail, trace_id)
           values (%s,%s,%s,%s,%s,%s,%s,%s)""",
        (actor, action, outcome, jurisdiction_id, target, reason, db.jsonb(detail or {}), trace_id),
    )


def enforce(cur, principal: Principal, jurisdiction_id: str | None, permission: str,
            side_effect: SideEffect = SideEffect.READ, *, target: str | None = None, trace_id: str | None = None) -> Decision:
    """check() + audit for anything that is not a plain read. Raises PolicyDenied when denied."""
    d = check(principal, jurisdiction_id, permission, side_effect)
    if side_effect is not SideEffect.READ or not d.allowed:
        audit(cur, actor=principal.user_id or "anonymous", action=permission,
              outcome="allowed" if d.allowed else "denied", jurisdiction_id=jurisdiction_id if d.allowed else None,
              target=target, reason=d.reason, detail={"side_effect": side_effect.value,
                                                      "requested_jurisdiction": jurisdiction_id}, trace_id=trace_id)
    d.raise_if_denied()
    return d


def active_policy(jurisdiction_id: str) -> tuple[str, dict]:
    row = db.one("select id, body from policy_versions where active and jurisdiction_id = %s", (jurisdiction_id,))
    if not row:
        raise PolicyDenied("no active policy for jurisdiction")
    return row["id"], row["body"]

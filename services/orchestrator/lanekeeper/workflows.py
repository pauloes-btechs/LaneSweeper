"""Run and resume graphs. Called by the HTTP API, the job runner, the demo script and tests.

Durable state is checkpointed by LangGraph before an interrupt returns, so a request handler can exit (or the
process can restart) and a later call resumes the same thread with Command(resume=...)."""
from __future__ import annotations

from typing import Any

from langgraph.types import Command

from . import WORKFLOW_VERSION, db
from .auth import Principal, reload
from .config import get_settings
from .deps import Deps
from .graphs import ticket
from .graphs.checkpoint import get_checkpointer
from .graphs.state import make_thread_id
from .hashing import short_hash
from .policy.engine import PolicyDenied, SideEffect, check
from .sim import runner
from .tracing import event, set_status, start_run

_graphs: dict[str, Any] = {}


def ticket_graph(deps: Deps | None = None):
    if deps is not None:
        return ticket.build(deps, get_checkpointer())
    if "ticket" not in _graphs:
        _graphs["ticket"] = ticket.build(Deps(), get_checkpointer())
    return _graphs["ticket"]


def reset_graphs() -> None:
    _graphs.clear()


def _cfg(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 80}


def _result(graph, thread_id: str, out: dict) -> dict:
    snap = graph.get_state(_cfg(thread_id))
    interrupts = [i.value for t in snap.tasks for i in (t.interrupts or ())]
    vals = snap.values
    status = "waiting" if interrupts else "done"
    run_id = vals.get("run_id")
    if status == "done":
        set_status(run_id, "failed" if vals.get("outcome") == "rejected" else "succeeded")
    return {"thread_id": thread_id, "run_id": run_id, "status": status, "interrupt": interrupts[0] if interrupts else None,
            "next": list(snap.next), "outcome": vals.get("outcome"), "route": vals.get("route"),
            "ticket_id": vals.get("ticket_id"), "ticket_number": vals.get("ticket_number"), "report_id": vals.get("report_id"),
            "incident_id": vals.get("incident_id"), "mission_id": vals.get("mission_id"), "approval_id": vals.get("approval_id"),
            "jurisdiction_id": vals.get("jurisdiction_id"), "hazard_type": vals.get("hazard_type"), "priority": vals.get("priority"),
            "severity": vals.get("severity"), "remediation_status": vals.get("remediation_status"), "errors": vals.get("errors", [])}


def submit_report(report: dict, principal: Principal, *, scenario: str | None = None, client_ip: str | None = None,
                  deps: Deps | None = None) -> dict:
    """Entry for riders, glasses, operators and robots. `principal` is server-verified (anonymous allowed)."""
    key = str(report.get("idempotency_key") or "")
    thread_id = make_thread_id("intake", "ticket", key or "missing")
    g = ticket_graph(deps)
    existing = g.get_state(_cfg(thread_id))
    if existing.values:   # same idempotency key -> same thread -> never processed twice
        return {**_result(g, thread_id, {}), "replayed": True}
    run_id, trace_id = start_run(workflow="ticket", workflow_version=WORKFLOW_VERSION, thread_id=thread_id,
                                 jurisdiction_id=None, actor_id=principal.user_id)
    init = ticket.initial_state(report=report, actor_id=principal.user_id, thread_id=thread_id, run_id=run_id,
                                trace_id=trace_id, scenario=scenario,
                                client_ip=short_hash(client_ip, get_settings().signing_secret) if client_ip else None)  # raw IPs never reach checkpoints
    out = g.invoke(init, _cfg(thread_id))
    return _result(g, thread_id, out)


def get_thread(thread_id: str, deps: Deps | None = None) -> dict:
    g = ticket_graph(deps)
    return _result(g, thread_id, {})


def _assert_thread_jurisdiction(principal: Principal, thread_id: str, perm: str, deps: Deps | None) -> dict:
    g = ticket_graph(deps)
    snap = g.get_state(_cfg(thread_id))
    if not snap.values:
        raise PolicyDenied("unknown thread")
    jid = snap.values.get("jurisdiction_id")
    check(reload(principal), jid, perm, SideEffect.SIMULATION).raise_if_denied()
    return snap.values


def resume_after_approval(thread_id: str, principal: Principal, deps: Deps | None = None) -> dict:
    """Continue a graph waiting at human_approval_interrupt. The approval itself must already be decided through
    policy.approvals.decide(); this call cannot approve anything. Authority is re-checked here AND at the tool."""
    vals = _assert_thread_jurisdiction(principal, thread_id, "mission.propose", deps)
    event(vals.get("run_id"), "human_approval_interrupt", "resume", f"resume requested by {principal.user_id[:8]}")
    g = ticket_graph(deps)
    out = g.invoke(Command(resume={"actor_id": principal.user_id}), _cfg(thread_id))
    return _result(g, thread_id, out)


def resume_after_evidence(thread_id: str, principal: Principal, decision: str, reason: str, deps: Deps | None = None) -> dict:
    if decision not in ("dismiss", "confirm"):
        raise PolicyDenied("decision must be dismiss or confirm")
    vals = _assert_thread_jurisdiction(principal, thread_id, "ticket.update", deps)
    event(vals.get("run_id"), "request_clarification", "resume", f"{decision} by {principal.user_id[:8]}: {reason[:120]}")
    g = ticket_graph(deps)
    out = g.invoke(Command(resume={"decision": decision, "by": principal.user_id, "reason": reason}), _cfg(thread_id))
    return _result(g, thread_id, out)


def resolve_safety_pause(thread_id: str, principal: Principal, action: str, *, link_restored: bool = True,
                         jam_cleared: bool = False, deps: Deps | None = None) -> dict:
    """Supervisor resumes or aborts a mission the local safety controller paused. Local rules still apply."""
    perm = "fleet.resume" if action == "resume" else "fleet.stop"
    vals = _assert_thread_jurisdiction(principal, thread_id, perm, deps)
    mid = vals.get("mission_id")
    if action == "resume":
        runner.supervisor_resume(mid, link_restored=link_restored, jam_cleared=jam_cleared)
    elif action == "abort":
        runner.operator_stop(mid, f"supervisor {principal.user_id[:8]} aborted after safety pause")
    else:
        raise PolicyDenied("action must be resume or abort")
    g = ticket_graph(deps)
    out = g.invoke(Command(resume={"action": action, "by": principal.user_id}), _cfg(thread_id))
    return _result(g, thread_id, out)


def stop_mission(mission_id: str, principal: Principal, reason: str) -> dict:
    """Human stop: allowed for anyone holding fleet.stop in the mission's jurisdiction. Agents cannot resume."""
    m = db.one("select jurisdiction_id::text jid from missions where id=%s", (mission_id,))
    if not m:
        raise PolicyDenied("mission not found")
    check(reload(principal), m["jid"], "fleet.stop", SideEffect.SIMULATION).raise_if_denied()
    return runner.operator_stop(mission_id, reason)

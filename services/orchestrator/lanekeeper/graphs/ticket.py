"""Municipal ticket-processing graph (LangGraph).

START → ingest_event → validate_identity_and_source → normalize_report → extract_and_verify_location
      → apply_jurisdiction_policy → classify_hazard → analyze_evidence → deduplicate_incident
      → score_severity_and_confidence → policy_router
policy_router ─┬─ insufficient_evidence      → request_clarification [INTERRUPT: evidence] → END
               ├─ routine_ticket             → create_or_update_ticket → recommend_assignment → END
               ├─ robotic_remediation_candidate → create_ticket_for_robot → propose_mission → safety_and_permission_checks
               │      → request_dispatch_approval → human_approval_interrupt [INTERRUPT: approval]
               │      ├─ approved → authorized_dispatch_gateway → simulated_mission_execution → monitor_mission
               │      │      ├─ paused   → await_safety_resolution [INTERRUPT] → simulated_mission_execution
               │      │      ├─ aborted  → reopen_ticket_and_escalate → END
               │      │      └─ completed→ collect_after_evidence → verify_remediation
               │      │                     ├─ cleared → close_ticket_and_publish_run_report → END
               │      │                     └─ failed_or_uncertain → reopen_ticket_and_escalate → END
               │      └─ denied/expired/invalid → preserve_ticket_and_escalate → END
               ├─ urgent_non_robotic_hazard  → flag_for_authorized_city_response → END
               ├─ policy_denied_or_unsafe    → preserve_ticket_and_escalate → END
               └─ rejected / duplicate_request → END
The real-hardware branch does not exist in this graph.
"""
from __future__ import annotations

from functools import partial

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .. import WORKFLOW_VERSION, db
from ..agents import dedupe, dispatch, geo, inspection, intake, optical, severity, tickets
from ..auth import Principal
from ..deps import Deps
from ..planning.routes import WorkItem
from ..policy import approvals
from ..policy.engine import PolicyDenied
from ..sim import runner
from ..tracing import edge, traced
from .state import STATE_VERSION, LaneKeeperState

WORKFLOW = "ticket"


def apply_jurisdiction_policy(state: dict, deps: Deps) -> dict:
    jid = state.get("jurisdiction_id")
    if not jid or state.get("outcome") in ("rejected", "duplicate_request"):
        return {}
    row = db.one("select id, body from policy_versions where active and jurisdiction_id=%s", (jid,))
    if not row:
        return {"outcome": "rejected", "errors": [{"node": "apply_jurisdiction_policy", "error": "no active policy"}]}
    return {"policy_version": row["id"], "policy": row["body"], "_summary": f"Applying policy {row['id']}"}


def create_ticket_for_robot(state: dict, deps: Deps) -> dict:
    t = tickets.create_or_update_ticket(state, deps)
    s = {**state, **{k: v for k, v in t.items() if not k.startswith("_")}}
    tickets.recommend_assignment(s, deps)
    return {**t, "_summary": t["_summary"] + "; robot-sweep work order opened"}


def propose_mission(state: dict, deps: Deps) -> dict:
    wo = db.one("select id::text from work_orders where ticket_id=%s and kind='robot_sweep'", (state["ticket_id"],))
    items = [WorkItem(wo["id"], state["ticket_id"], state["lane_cell_id"], int(state["priority"]), state["hazard_type"])]
    # A single urgent ticket gets an immediate SIMULATED window so the demo can run end to end; batch work
    # goes through the fleet graph, which uses the configured sweep window (pilot default 04:00-06:00).
    window = dispatch.sweep_window(state.get("policy") or {}, immediate=True)
    proposals, raw = dispatch.create_proposals(state["jurisdiction_id"], items, window, run_id=state.get("run_id"),
                                               policy=state.get("policy") or {})
    if not proposals:
        reasons = "; ".join(u["reason"] for u in raw["unassigned"]) or "no plan"
        excl = "; ".join(f"{e['robot_id']}: {e['reason']}" for e in raw["excluded_robots"])
        return {"safety_gate_result": {"ok": False, "reason": f"{reasons}. {excl}".strip()},
                "_summary": f"No safe mission possible: {reasons}. {excl}"}
    p = proposals[0]
    return {"proposed_mission_id": p["id"], "selected_robot_id": p["robot_id"], "candidate_robot_ids": [x["robot_id"] for x in proposals],
            "approval_payload_hash": p["payload_hash"], "_summary": f"Proposed {p['robot_id']} over {len(p['route_cell_ids'])} cell(s); "
            f"{p['estimates']['duration_min']} min, battery → {p['estimates']['battery_end_pct']}% (estimates)",
            "_decisions": [{"summary": p["rationale"], "constraints": p["constraints"], "estimates": p["estimates"]}]}


def safety_and_permission_checks(state: dict, deps: Deps) -> dict:
    if not state.get("proposed_mission_id"):
        return {"safety_gate_result": state.get("safety_gate_result") or {"ok": False, "reason": "no proposal"}}
    p = dispatch.load_proposal(state["proposed_mission_id"])
    failed = [c for c in p["constraints"] if not c["ok"]]
    flags = db.one("select enabled from feature_flags where key='hardware_gateway'")
    ok = not failed and p["simulation_mode"] and not (flags or {}).get("enabled")
    reason = "all constraints pass; simulation-only; hardware gateway off" if ok else \
        "; ".join(f"{c['check']}: {c['detail']}" for c in failed) or "simulation guard failed"
    return {"safety_gate_result": {"ok": ok, "reason": reason, "checks": p["constraints"]},
            "required_permissions": ["mission.approve", "mission.propose"],
            "_summary": f"Safety gate {'passed' if ok else 'FAILED'}: {reason}"}


def request_dispatch_approval(state: dict, deps: Deps) -> dict:
    ttl = int(((state.get("policy") or {}).get("approval") or {}).get("ttl_minutes", 30))
    r = dispatch.request_approval(state["proposed_mission_id"], run_id=state.get("run_id"), ttl_minutes=ttl)
    return {"approval_id": r["approval_id"], "operation_id": r["operation_id"], "approval_payload_hash": r["payload_hash"],
            "approval_status": r["status"],
            "_summary": f"{'Reused' if r['reused'] else 'Created'} approval request {r['approval_id'][:8]} bound to payload "
                        f"{r['payload_hash'][:12]}… (expires in {ttl} min)"}


def human_approval_interrupt(state: dict, deps: Deps) -> dict:
    """Pure interrupt node: no writes before interrupt(), so a restart cannot repeat side effects."""
    resume = interrupt({"kind": "dispatch_approval", "approval_id": state["approval_id"],
                        "proposal_id": state["proposed_mission_id"], "ticket": state.get("ticket_number"),
                        "describe": dispatch.describe_for_approver(state["proposed_mission_id"])})
    # The resume payload is supplied by the API after it verified the caller's token; still, the ledger is the truth.
    row = db.one("select status from approval_requests where id=%s", (state["approval_id"],))
    status = row["status"] if row else "missing"
    return {"approval_status": status, "actor_id": (resume or {}).get("actor_id"),
            "_summary": f"Resumed: approval is {status} (ledger), resumed by {((resume or {}).get('actor_id') or '?')[:8]}"}


def authorized_dispatch_gateway(state: dict, deps: Deps) -> dict:
    try:
        approvals.revalidate(state["approval_id"], expected_hash=state["approval_payload_hash"],
                             jurisdiction_id=state["jurisdiction_id"], action=dispatch.ACTION)
        res = dispatch.dispatch_simulated_mission(Principal(user_id=state["actor_id"]), state["proposed_mission_id"],
                                                  state["approval_id"], idempotency_key=f"dispatch:{state['operation_id']}",
                                                  scenario=state.get("scenario"),
                                                  seed=0, trace_id=state.get("trace_id"))
    except PolicyDenied as e:
        return {"approval_status": "invalid", "safety_gate_result": {"ok": False, "reason": str(e)},
                "_summary": f"Dispatch blocked at the tool boundary: {e}"}
    return {"mission_id": res["mission_id"], "mission_status": "dispatched", "approval_status": "consumed",
            "_summary": f"SIMULATED mission {res['mission_id'][:8]} dispatched to {res['robot_id']} under operation "
                        f"{res['operation_id'][:8]}{' (replayed)' if res.get('replayed') else ''}"}


def simulated_mission_execution(state: dict, deps: Deps) -> dict:
    s = runner.run(state["mission_id"], deps)
    return {"mission_status": s["status"], "_summary": f"Twin advanced: mission {s['status']}, stops {s['stops']}, "
                                                       f"{s['events']} events, sim time {s['sim_time_s']:.0f}s (synthetic)"}


def monitor_mission(state: dict, deps: Deps) -> dict:
    s = runner.summary(state["mission_id"])
    faults = db.all_rows("select code, safety_action from robot_faults where mission_id=%s order by opened_at", (state["mission_id"],))
    return {"mission_status": s["status"], "_summary": f"Fleet supervisor: mission {s['status']}; safety events: "
            + (", ".join(f"{f['code']}→{f['safety_action']}" for f in faults) or "none")}


def await_safety_resolution(state: dict, deps: Deps) -> dict:
    resume = interrupt({"kind": "safety_pause", "mission_id": state["mission_id"],
                        "summary": runner.summary(state["mission_id"])})
    s = runner.summary(state["mission_id"])   # the API applied the supervisor's resume/abort before resuming the graph
    return {"mission_status": s["status"], "_summary": f"Safety pause resolved by supervisor: {(resume or {}).get('action', '?')}"}


def collect_after_evidence(state: dict, deps: Deps) -> dict:
    n = db.one("select count(*) n from inspection_evidence where mission_id=%s and phase='after'", (state["mission_id"],))["n"]
    return {"_summary": f"Collected {n} after-sweep frame(s) (SIMULATED provenance)"}


def verify_remediation(state: dict, deps: Deps) -> dict:
    v = inspection.verify_mission(state["mission_id"], deps)
    status = "verified" if v["all_cleared"] else "failed_or_uncertain"
    return {"remediation_status": status, "_summary": "; ".join(f"{r['cell_id']}: {r['verdict']} ({r['reason']})" for r in v["results"]),
            "_decisions": [{"summary": r["reason"], **r} for r in v["results"]]}


def close_ticket_and_publish_run_report(state: dict, deps: Deps) -> dict:
    rep = inspection.publish_run_report(state["mission_id"])
    return {"outcome": "verified_closed", "_summary": f"Ticket verified closed; public run report /r/{rep['slug']}"}


def reopen_ticket_and_escalate(state: dict, deps: Deps) -> dict:
    if state.get("mission_id"):
        rep = inspection.publish_run_report(state["mission_id"])
        extra = f"; run report /r/{rep['slug']} shows it as NOT cleared"
    else:
        extra = ""
    if state.get("mission_status") in ("aborted", "failed") and state.get("ticket_id"):
        with db.tx() as cur:
            cur.execute("update tickets set status='reopened' where id=%s and status not in ('verified_closed','dismissed')",
                        (state["ticket_id"],))
            cur.execute("""insert into ticket_events (ticket_id, jurisdiction_id, actor, kind, to_status, detail)
                           values (%s,%s,'agent:fleet_supervisor','mission_aborted','reopened',%s)""",
                        (state["ticket_id"], state["jurisdiction_id"], db.jsonb({"mission": state.get("mission_id")})))
    return {"outcome": "reopened", "_summary": "Not verified: ticket reopened/escalated, no public 'cleared' claim" + extra}


# ------------------------------------------------------------------ routing functions (conditional edges)
def _after_router(state: dict) -> str:
    return edge(state.get("run_id"), "policy_router", state["route"], f"route={state['route']}", state.get("jurisdiction_id"))


def _after_gate(state: dict) -> str:
    ok = (state.get("safety_gate_result") or {}).get("ok")
    return edge(state.get("run_id"), "safety_and_permission_checks", "request_dispatch_approval" if ok else "preserve_ticket_and_escalate",
                "gate ok" if ok else "gate failed", state.get("jurisdiction_id"))


def _after_approval(state: dict) -> str:
    dst = "authorized_dispatch_gateway" if state.get("approval_status") == "approved" else "preserve_ticket_and_escalate"
    return edge(state.get("run_id"), "human_approval_interrupt", dst, f"approval {state.get('approval_status')}",
                state.get("jurisdiction_id"))


def _after_gateway(state: dict) -> str:
    dst = "simulated_mission_execution" if state.get("mission_id") else "preserve_ticket_and_escalate"
    return edge(state.get("run_id"), "authorized_dispatch_gateway", dst, "dispatched" if state.get("mission_id") else "blocked",
                state.get("jurisdiction_id"))


def _after_monitor(state: dict) -> str:
    st = state.get("mission_status")
    dst = {"completed": "collect_after_evidence", "paused_safety": "await_safety_resolution",
           "running": "simulated_mission_execution"}.get(st, "reopen_ticket_and_escalate")
    return edge(state.get("run_id"), "monitor_mission", dst, f"mission {st}", state.get("jurisdiction_id"))


def _after_safety(state: dict) -> str:
    st = state.get("mission_status")
    return "simulated_mission_execution" if st == "running" else ("collect_after_evidence" if st == "completed" else "reopen_ticket_and_escalate")


def _after_verify(state: dict) -> str:
    dst = "close_ticket_and_publish_run_report" if state.get("remediation_status") == "verified" else "reopen_ticket_and_escalate"
    return edge(state.get("run_id"), "verify_remediation", dst, state.get("remediation_status", "?"), state.get("jurisdiction_id"))


def _after_clarification(state: dict) -> str:
    return "score_severity_and_confidence" if state.get("next_action") == "confirm" else END


NODES = {
    "ingest_event": intake.ingest_event,
    "validate_identity_and_source": intake.validate_identity_and_source,
    "normalize_report": intake.normalize_report,
    "extract_and_verify_location": geo.extract_and_verify_location,
    "apply_jurisdiction_policy": apply_jurisdiction_policy,
    "classify_hazard": optical.classify_hazard,
    "analyze_evidence": optical.analyze_evidence,
    "deduplicate_incident": dedupe.deduplicate_incident,
    "score_severity_and_confidence": severity.score_severity_and_confidence,
    "policy_router": severity.policy_router,
    "request_clarification": tickets.request_clarification,
    "create_or_update_ticket": tickets.create_or_update_ticket,
    "recommend_assignment": tickets.recommend_assignment,
    "create_ticket_for_robot": create_ticket_for_robot,
    "propose_mission": propose_mission,
    "safety_and_permission_checks": safety_and_permission_checks,
    "request_dispatch_approval": request_dispatch_approval,
    "human_approval_interrupt": human_approval_interrupt,
    "authorized_dispatch_gateway": authorized_dispatch_gateway,
    "simulated_mission_execution": simulated_mission_execution,
    "monitor_mission": monitor_mission,
    "await_safety_resolution": await_safety_resolution,
    "collect_after_evidence": collect_after_evidence,
    "verify_remediation": verify_remediation,
    "close_ticket_and_publish_run_report": close_ticket_and_publish_run_report,
    "reopen_ticket_and_escalate": reopen_ticket_and_escalate,
    "flag_for_authorized_city_response": tickets.flag_for_authorized_city_response,
    "preserve_ticket_and_escalate": tickets.preserve_ticket_and_escalate,
}


def build(deps: Deps | None = None, checkpointer=None):
    deps = deps or Deps()
    g = StateGraph(LaneKeeperState)
    for name, fn in NODES.items():
        g.add_node(name, traced(name)(partial(_call, fn, deps)))
    linear = ["ingest_event", "validate_identity_and_source", "normalize_report", "extract_and_verify_location",
              "apply_jurisdiction_policy", "classify_hazard", "analyze_evidence", "deduplicate_incident",
              "score_severity_and_confidence", "policy_router"]
    g.add_edge(START, linear[0])
    for a, b in zip(linear, linear[1:], strict=False):
        g.add_edge(a, b)
    g.add_conditional_edges("policy_router", _after_router, {
        "insufficient_evidence": "request_clarification", "routine_ticket": "create_or_update_ticket",
        "robotic_remediation_candidate": "create_ticket_for_robot", "urgent_non_robotic_hazard": "flag_for_authorized_city_response",
        "policy_denied_or_unsafe": "preserve_ticket_and_escalate", "rejected": END, "duplicate_request": END})
    g.add_conditional_edges("request_clarification", _after_clarification,
                            {"score_severity_and_confidence": "score_severity_and_confidence", END: END})
    g.add_edge("create_or_update_ticket", "recommend_assignment")
    g.add_edge("recommend_assignment", END)
    g.add_edge("create_ticket_for_robot", "propose_mission")
    g.add_edge("propose_mission", "safety_and_permission_checks")
    g.add_conditional_edges("safety_and_permission_checks", _after_gate,
                            {"request_dispatch_approval": "request_dispatch_approval",
                             "preserve_ticket_and_escalate": "preserve_ticket_and_escalate"})
    g.add_edge("request_dispatch_approval", "human_approval_interrupt")
    g.add_conditional_edges("human_approval_interrupt", _after_approval,
                            {"authorized_dispatch_gateway": "authorized_dispatch_gateway",
                             "preserve_ticket_and_escalate": "preserve_ticket_and_escalate"})
    g.add_conditional_edges("authorized_dispatch_gateway", _after_gateway,
                            {"simulated_mission_execution": "simulated_mission_execution",
                             "preserve_ticket_and_escalate": "preserve_ticket_and_escalate"})
    g.add_edge("simulated_mission_execution", "monitor_mission")
    g.add_conditional_edges("monitor_mission", _after_monitor,
                            {"collect_after_evidence": "collect_after_evidence", "await_safety_resolution": "await_safety_resolution",
                             "simulated_mission_execution": "simulated_mission_execution",
                             "reopen_ticket_and_escalate": "reopen_ticket_and_escalate"})
    g.add_conditional_edges("await_safety_resolution", _after_safety,
                            {"simulated_mission_execution": "simulated_mission_execution",
                             "collect_after_evidence": "collect_after_evidence",
                             "reopen_ticket_and_escalate": "reopen_ticket_and_escalate"})
    g.add_edge("collect_after_evidence", "verify_remediation")
    g.add_conditional_edges("verify_remediation", _after_verify,
                            {"close_ticket_and_publish_run_report": "close_ticket_and_publish_run_report",
                             "reopen_ticket_and_escalate": "reopen_ticket_and_escalate"})
    for n in ("close_ticket_and_publish_run_report", "reopen_ticket_and_escalate", "flag_for_authorized_city_response",
              "preserve_ticket_and_escalate"):
        g.add_edge(n, END)
    return g.compile(checkpointer=checkpointer)


def _call(fn, deps, state):
    return fn(state, deps)


def initial_state(*, report: dict, actor_id: str | None, thread_id: str, run_id: str, trace_id: str,
                  scenario: str | None = None, client_ip: str | None = None) -> dict:
    """`scenario` is chosen server-side by the demo controller (twin scenario), never by the reporter."""
    return {"scenario": scenario, "workflow_version": WORKFLOW_VERSION, "state_version": STATE_VERSION, "thread_id": thread_id, "run_id": run_id,
            "trace_id": trace_id, "report": report, "actor_id": actor_id, "client_ip": client_ip, "simulation_mode": True, "retries": 0,
            "replay_mode": False, "messages": [], "errors": []}

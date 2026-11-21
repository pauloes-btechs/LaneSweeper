"""Fleet planning & mission graph (nightly batch).

START → load_pending_work_orders → load_geospatial_constraints → check_robot_availability → compute_candidate_routes
      → estimate_resources_and_risk → create_mission_proposal → verify_jurisdiction_and_mission_policy
      → human_approval_interrupt [INTERRUPT] → revalidate_approval_and_robot_state → simulation_dispatch_gateway
      → monitor_telemetry ─┬ paused (cyclist/pedestrian/lost link/fault) → operator_review → END
                           └ completed/aborted → independent_cleanup_verification → publish_reports → END
Planning is the deterministic planner; an agent only explains trade-offs. Denied proposals produce no movement."""
from __future__ import annotations

from datetime import datetime
from functools import partial

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .. import WORKFLOW_VERSION, db
from ..agents import dispatch, inspection
from ..auth import Principal, reload
from ..deps import Deps
from ..planning.routes import WorkItem, blocked_cells, load_robots
from ..policy import approvals
from ..policy.engine import PolicyDenied, check
from ..sim import runner
from ..tracing import start_run, traced
from .state import LaneKeeperState, make_thread_id


def load_pending_work_orders(state: dict, deps: Deps) -> dict:
    rows = db.all_rows("""select w.id::text wo, w.ticket_id::text, w.cell_id, w.priority, t.hazard_type
                            from work_orders w join tickets t on t.id = w.ticket_id
                           where w.jurisdiction_id=%s and w.kind='robot_sweep' and w.status='open' and w.cell_id is not null
                             and t.status in ('triaged','assigned','scheduled','reopened')
                           order by w.priority, w.created_at limit 200""", (state["jurisdiction_id"],))
    pol = db.one("select id, body from policy_versions where active and jurisdiction_id=%s", (state["jurisdiction_id"],))
    return {"evidence": {**(state.get("evidence") or {}), "work": rows}, "policy": pol["body"], "policy_version": pol["id"],
            "_summary": f"{len(rows)} open robot-sweep work order(s), policy {pol['id']}"}


def load_geospatial_constraints(state: dict, deps: Deps) -> dict:
    win = dispatch.sweep_window(state["policy"], immediate=bool(state.get("next_action") == "immediate"))
    blocked = blocked_cells(state["jurisdiction_id"], *win)
    ev = {**state["evidence"], "window": [win[0].isoformat(), win[1].isoformat()], "blocked": blocked}
    return {"evidence": ev, "deadline_at": win[1].isoformat(),
            "_summary": f"Window {win[0]:%Y-%m-%d %H:%M}–{win[1]:%H:%M} UTC; {len(blocked)} blocked cell(s) (closures/zones)"}


def check_robot_availability(state: dict, deps: Deps) -> dict:
    robots = load_robots(state["jurisdiction_id"])
    ready = [r.robot_id for r in robots if r.mode == "simulated" and r.connectivity == "online" and r.state in ("docked", "idle", "charging")]
    return {"candidate_robot_ids": ready, "_summary": f"Available simulated robots: {ready or 'none'}"}


def compute_candidate_routes(state: dict, deps: Deps) -> dict:
    ev = state["evidence"]
    items = [WorkItem(w["wo"], w["ticket_id"], w["cell_id"], w["priority"], w["hazard_type"]) for w in ev["work"]]
    win = tuple(datetime.fromisoformat(x) for x in ev["window"])
    props, raw = dispatch.create_proposals(state["jurisdiction_id"], items, win, run_id=state.get("run_id"), policy=state["policy"],
                                           max_robots=int(state.get("retries") or 0) or None)
    return {"evidence": {**ev, "proposals": props, "plan_raw": raw},
            "_summary": f"{len(props)} route(s) planned; {len(raw['unassigned'])} work order(s) unassigned",
            "_decisions": [{"summary": p["rationale"], "estimates": p["estimates"]} for p in props]
            + [{"summary": f"unassigned: {u['reason']}", **u} for u in raw["unassigned"]]}


def estimate_resources_and_risk(state: dict, deps: Deps) -> dict:
    props = state["evidence"]["proposals"]
    lines = [f"{p['robot_id']}: {p['estimates']['distance_m']} m, {p['estimates']['duration_min']} min, battery end "
             f"{p['estimates']['battery_end_pct']}% (ESTIMATES)" for p in props]
    expl = deps.router.text("Explain the trade-offs between these simulated sweep plans in 3 sentences for a supervisor. "
                            "Do not change any numbers or constraints.\n" + "\n".join(lines),
                            facts={"kind": "answer", "headline": "Plan trade-offs", "points": lines},
                            jurisdiction_id=state["jurisdiction_id"], run_id=state.get("run_id"))
    return {"messages": [{"role": "assistant", "content": expl.text, "provenance": expl.provenance.model_dump()}],
            "_summary": expl.text[:300]}


def create_mission_proposal(state: dict, deps: Deps) -> dict:
    out = []
    for p in state["evidence"]["proposals"]:
        r = dispatch.request_approval(p["id"], run_id=state.get("run_id"), requested_by_user=state.get("actor_id"))
        out.append({"proposal_id": p["id"], "robot_id": p["robot_id"], **r})
    return {"evidence": {**state["evidence"], "approvals": out},
            "_summary": f"Opened {len(out)} approval request(s): " + ", ".join(f"{a['robot_id']}→{a['approval_id'][:8]}" for a in out)}


def verify_jurisdiction_and_mission_policy(state: dict, deps: Deps) -> dict:
    jid = state["jurisdiction_id"]
    bad = [p["robot_id"] for p in state["evidence"]["proposals"] if str(p["jurisdiction_id"]) != jid]
    return {"safety_gate_result": {"ok": not bad, "reason": "all proposals in jurisdiction" if not bad else f"foreign robots {bad}"},
            "_summary": "Jurisdiction and simulation-only policy verified" if not bad else f"Rejected foreign robots {bad}"}


def human_approval_interrupt(state: dict, deps: Deps) -> dict:
    resume = interrupt({"kind": "fleet_dispatch_approvals", "approvals": state["evidence"].get("approvals", []),
                        "explanation": (state.get("messages") or [{}])[-1].get("content")})
    return {"actor_id": (resume or {}).get("actor_id"), "_summary": "Resumed after supervisor review"}


def revalidate_approval_and_robot_state(state: dict, deps: Deps) -> dict:
    ok, skipped = [], []
    for a in state["evidence"].get("approvals", []):
        try:
            approvals.revalidate(a["approval_id"], expected_hash=a["payload_hash"], jurisdiction_id=state["jurisdiction_id"],
                                 action=dispatch.ACTION)
            ok.append(a)
        except PolicyDenied as e:
            skipped.append({**a, "reason": str(e)})
    return {"evidence": {**state["evidence"], "dispatchable": ok, "not_dispatched": skipped},
            "_summary": f"{len(ok)} approved and valid; {len(skipped)} not dispatched ("
                        + "; ".join(f"{s['robot_id']}: {s['reason']}" for s in skipped) + ")"}


def simulation_dispatch_gateway(state: dict, deps: Deps) -> dict:
    missions, errors = [], []
    for a in state["evidence"].get("dispatchable", []):
        try:
            r = dispatch.dispatch_simulated_mission(Principal(state["actor_id"]), a["proposal_id"], a["approval_id"],
                                                    idempotency_key=f"dispatch:{a['operation_id']}", scenario=state.get("scenario"))
            missions.append(r["mission_id"])
        except PolicyDenied as e:
            errors.append({"robot_id": a["robot_id"], "error": str(e)})
    return {"evidence": {**state["evidence"], "missions": missions, "dispatch_errors": errors},
            "_summary": f"Dispatched {len(missions)} SIMULATED mission(s); {len(errors)} blocked at the gateway"}


def monitor_telemetry(state: dict, deps: Deps) -> dict:
    statuses = {}
    for mid in state["evidence"].get("missions", []):
        s = runner.run(mid, deps)
        statuses[mid] = s["status"]
    paused = [m for m, s in statuses.items() if s == "paused_safety"]
    return {"evidence": {**state["evidence"], "mission_status": statuses}, "mission_status": "paused_safety" if paused else "done",
            "_summary": "Missions: " + ", ".join(f"{m[:8]} {s}" for m, s in statuses.items())}


def operator_review(state: dict, deps: Deps) -> dict:
    resume = interrupt({"kind": "safety_pause", "missions": state["evidence"]["mission_status"]})
    for mid, st in state["evidence"]["mission_status"].items():
        if st == "paused_safety" and (resume or {}).get("action") == "resume":
            runner.supervisor_resume(mid)
            runner.run(mid, deps)
        elif st == "paused_safety":
            runner.operator_stop(mid, "supervisor aborted during fleet review")
    return {"mission_status": "done", "_summary": f"Supervisor {(resume or {}).get('action', '?')} paused missions"}


def independent_cleanup_verification(state: dict, deps: Deps) -> dict:
    res = [inspection.verify_mission(m, deps) for m in state["evidence"].get("missions", [])]
    flat = [r for v in res for r in v["results"]]
    return {"evidence": {**state["evidence"], "verdicts": flat},
            "_summary": f"{sum(r['verdict'] == 'cleared' for r in flat)}/{len(flat)} cell(s) verified cleared"}


def publish_reports(state: dict, deps: Deps) -> dict:
    slugs = [inspection.publish_run_report(m)["slug"] for m in state["evidence"].get("missions", [])]
    return {"outcome": "fleet_run_complete", "_summary": f"Run reports: {slugs}"}


STEPS = ["load_pending_work_orders", "load_geospatial_constraints", "check_robot_availability", "compute_candidate_routes",
         "estimate_resources_and_risk", "create_mission_proposal", "verify_jurisdiction_and_mission_policy",
         "human_approval_interrupt", "revalidate_approval_and_robot_state", "simulation_dispatch_gateway", "monitor_telemetry"]


def build(deps: Deps | None = None, checkpointer=None):
    deps = deps or Deps()
    g = StateGraph(LaneKeeperState)
    fns = {n: globals()[n] for n in STEPS + ["operator_review", "independent_cleanup_verification", "publish_reports"]}
    for n, fn in fns.items():
        g.add_node(n, traced(n)(partial(lambda f, d, s: f(s, d), fn, deps)))
    g.add_edge(START, STEPS[0])
    for a, b in zip(STEPS[:6], STEPS[1:7], strict=True):
        g.add_edge(a, b)
    g.add_conditional_edges("compute_candidate_routes", lambda s: "estimate_resources_and_risk" if s["evidence"]["proposals"] else END,
                            {"estimate_resources_and_risk": "estimate_resources_and_risk", END: END})
    g.add_conditional_edges("verify_jurisdiction_and_mission_policy",
                            lambda s: "human_approval_interrupt" if s["safety_gate_result"]["ok"] else END,
                            {"human_approval_interrupt": "human_approval_interrupt", END: END})
    g.add_edge("human_approval_interrupt", "revalidate_approval_and_robot_state")
    g.add_edge("revalidate_approval_and_robot_state", "simulation_dispatch_gateway")
    g.add_edge("simulation_dispatch_gateway", "monitor_telemetry")
    g.add_conditional_edges("monitor_telemetry", lambda s: "operator_review" if s.get("mission_status") == "paused_safety"
                            else "independent_cleanup_verification",
                            {"operator_review": "operator_review", "independent_cleanup_verification": "independent_cleanup_verification"})
    g.add_edge("operator_review", "independent_cleanup_verification")
    g.add_edge("independent_cleanup_verification", "publish_reports")
    g.add_edge("publish_reports", END)
    return g.compile(checkpointer=checkpointer)


def start(principal: Principal, jurisdiction_id: str, *, immediate: bool = True, max_robots: int | None = None,
          scenario: str | None = None, deps: Deps | None = None, label: str | None = None) -> dict:
    principal = reload(principal)
    check(principal, jurisdiction_id, "mission.propose").raise_if_denied()
    from .checkpoint import get_checkpointer
    graph = build(deps, get_checkpointer())
    import uuid
    thread_id = make_thread_id(jurisdiction_id[:8], "fleet", label or uuid.uuid4().hex[:12])
    run_id, trace_id = start_run(workflow="fleet", workflow_version=WORKFLOW_VERSION, thread_id=thread_id,
                                 jurisdiction_id=jurisdiction_id, actor_id=principal.user_id)
    cfg = {"configurable": {"thread_id": thread_id}, "recursion_limit": 60}
    graph.invoke({"jurisdiction_id": jurisdiction_id, "actor_id": principal.user_id, "run_id": run_id, "trace_id": trace_id,
                  "next_action": "immediate" if immediate else "window", "retries": max_robots or 0, "scenario": scenario,
                  "evidence": {}, "errors": [], "messages": []}, cfg)
    return snapshot(thread_id, deps)


def resume(thread_id: str, principal: Principal, payload: dict, deps: Deps | None = None) -> dict:
    from langgraph.types import Command

    from .checkpoint import get_checkpointer
    graph = build(deps, get_checkpointer())
    snap = graph.get_state({"configurable": {"thread_id": thread_id}})
    jid = snap.values.get("jurisdiction_id")
    check(reload(principal), jid, "mission.propose").raise_if_denied()
    graph.invoke(Command(resume={**payload, "actor_id": principal.user_id}),
                 {"configurable": {"thread_id": thread_id}, "recursion_limit": 60})
    return snapshot(thread_id, deps)


def snapshot(thread_id: str, deps: Deps | None = None) -> dict:
    from .checkpoint import get_checkpointer
    snap = build(deps, get_checkpointer()).get_state({"configurable": {"thread_id": thread_id}})
    ints = [i.value for t in snap.tasks for i in (t.interrupts or ())]
    v = snap.values
    return {"thread_id": thread_id, "run_id": v.get("run_id"), "status": "waiting" if ints else "done",
            "interrupt": ints[0] if ints else None, "outcome": v.get("outcome"),
            "approvals": (v.get("evidence") or {}).get("approvals"), "missions": (v.get("evidence") or {}).get("missions"),
            "verdicts": (v.get("evidence") or {}).get("verdicts"), "not_dispatched": (v.get("evidence") or {}).get("not_dispatched")}

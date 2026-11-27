"""Municipal staff endpoints. Every route resolves the caller's jurisdiction from server-side membership and checks
the permission for the action; responses are filtered to that jurisdiction."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db, workflows
from ..auth import Principal
from ..graphs import copilot, fleet
from ..policy import approvals
from ..policy.engine import check
from ..storage import evidence_store
from .common import jurisdiction_for, principal, staff

router = APIRouter(prefix="/v1")


def need(p: Principal, jid: str, perm: str) -> None:
    check(p, jid, perm).raise_if_denied()


@router.get("/me")
def me(p: Principal = Depends(principal)):
    staff(p)
    rows = db.all_rows("""select j.id::text, j.slug, j.name, m.role from municipal_memberships m join jurisdictions j on j.id=m.jurisdiction_id
                           where m.user_id=%s and m.status='active'""", (p.user_id,))
    user = db.one("select email from auth.users where id=%s", (p.user_id,))
    return {"user_id": p.user_id, "email": user and user["email"], "memberships": rows}


@router.get("/overview")
def overview(jurisdiction: str | None = None, p: Principal = Depends(principal)):
    jid = jurisdiction_for(staff(p), jurisdiction)
    need(p, jid, "ticket.view")
    one = lambda sql: db.one(sql, (jid,))  # noqa: E731
    return {
        "jurisdiction_id": jid,
        "tickets_by_status": db.all_rows("select status, count(*) n from tickets where jurisdiction_id=%s group by status", (jid,)),
        "tickets_by_priority": db.all_rows("""select priority, count(*) n from tickets where jurisdiction_id=%s
                                              and status not in ('verified_closed','dismissed') group by priority order by priority""", (jid,)),
        "active_hazards": one("""select count(*) n from lane_cell_status where jurisdiction_id=%s
                                 and condition in ('hazard_reported','hazard_confirmed','partially_cleared')""")["n"],
        "pending_approvals": one("select count(*) n from approval_requests where jurisdiction_id=%s and status='pending' and expires_at > now()")["n"],
        "running_workflows": one("select count(*) n from agent_runs where jurisdiction_id=%s and status in ('running','waiting_approval','waiting_evidence')")["n"],
        "robots": db.all_rows("""select r.id, s.state, s.battery_pct, s.hopper_pct, s.connectivity, s.cell_id,
                                        extensions.st_y(s.position::extensions.geometry) lat, extensions.st_x(s.position::extensions.geometry) lng
                                   from robots r join robot_status s on s.robot_id=r.id where r.jurisdiction_id=%s order by r.id""", (jid,)),
        "median_minutes_to_respond_estimate": one("""select round(extract(epoch from percentile_cont(0.5) within group (order by respond_by - created_at))/60) m
                                                     from tickets where jurisdiction_id=%s and respond_by is not null""")["m"],
        "provenance": "SIMULATED / synthetic demo data",
    }


@router.get("/tickets")
def tickets(jurisdiction: str | None = None, status: str | None = None, hazard: str | None = None, limit: int = 100,
            p: Principal = Depends(principal)):
    jid = jurisdiction_for(staff(p), jurisdiction)
    need(p, jid, "ticket.view")
    return db.all_rows("""select t.id::text, t.number, t.hazard_type, t.severity, t.priority, t.status, t.remediation_path, t.summary,
                                 t.created_at, t.updated_at, t.respond_by, i.cell_id, i.report_count, d.code department, t.provenance
                            from tickets t left join hazard_incidents i on i.id=t.incident_id left join departments d on d.id=t.department_id
                           where t.jurisdiction_id=%s and (%s::text is null or t.status=%s) and (%s::text is null or t.hazard_type=%s)
                           order by (t.status in ('verified_closed','dismissed')), t.priority, t.created_at desc limit %s""",
                       (jid, status, status, hazard, hazard, min(limit, 500)))


@router.get("/tickets/{ref}")
def ticket(ref: str, p: Principal = Depends(principal)):
    staff(p)
    t = db.one("""select t.*, t.id::text id, t.jurisdiction_id::text jurisdiction_id, t.incident_id::text incident_id,
                         i.cell_id, i.report_count, d.code department, d.name department_name
                    from tickets t left join hazard_incidents i on i.id=t.incident_id left join departments d on d.id=t.department_id
                   where t.number=%s or t.id::text=%s""", (ref, ref))
    if not t or t["jurisdiction_id"] not in p.jurisdictions():
        raise HTTPException(404, "not found")
    need(p, t["jurisdiction_id"], "ticket.view")
    t["events"] = db.all_rows("select at, actor, kind, from_status, to_status, detail from ticket_events where ticket_id=%s order by at", (t["id"],))
    t["verdicts"] = db.all_rows("select verdict, reason, method, confidence, model_provenance, created_at, mission_id::text from remediation_verdicts "
                                "where ticket_id=%s order by created_at", (t["id"],))
    t["work_orders"] = db.all_rows("select id::text, kind, status, mission_id::text, cell_id from work_orders where ticket_id=%s", (t["id"],))
    t["reports"] = []
    if check(p, t["jurisdiction_id"], "evidence.view").allowed:
        store = evidence_store()
        for r in db.all_rows("""select r.id::text, r.source_kind, r.received_at, r.accuracy_m, r.match_status, r.match_distance_m,
                                       r.cell_id, r.note, r.hazard_claimed, r.local_detector, l.link_kind, l.rule_score, l.vector_score,
                                       (select json_agg(json_build_object('path', m.storage_path, 'fixture_id', m.fixture_id, 'mime', m.mime_type))
                                          from report_media m where m.report_id=r.id) media,
                                       extensions.st_y(r.raw_location::extensions.geometry) lat, extensions.st_x(r.raw_location::extensions.geometry) lng
                                  from incident_report_links l join hazard_reports r on r.id=l.report_id
                                 where l.incident_id=%s order by r.received_at""", (t["incident_id"],)):
            r["media"] = [{**m, "url": store.signed_url(m["path"])} for m in (r["media"] or [])]
            t["reports"].append(r)
    # \m..\M: whole ticket number only, so LK-101 never matches LK-1010.
    run = db.one(r"""select r.id::text, r.thread_id, r.status from agent_runs r join agent_events e on e.run_id=r.id
                     where r.jurisdiction_id=%s and e.summary ~ ('\m' || %s || '\M') order by r.started_at limit 1""",
                 (t["jurisdiction_id"], t["number"]))
    t["agent_run"] = run
    return t


@router.get("/approvals")
def approval_list(jurisdiction: str | None = None, status: str = "pending", p: Principal = Depends(principal)):
    jid = jurisdiction_for(staff(p), jurisdiction)
    need(p, jid, "ticket.view")
    return db.all_rows("""select a.id::text, a.operation_id::text, a.action, a.target_id, a.payload, a.payload_hash, a.status, a.expires_at,
                                 a.requested_by, a.created_at, a.decision_reason, a.approver_id::text, r.thread_id, r.workflow,
                                 p.estimates, p.constraints, p.rationale, p.robot_id
                            from approval_requests a left join agent_runs r on r.id=a.run_id
                            left join mission_proposals p on p.id::text = a.target_id
                           where a.jurisdiction_id=%s and (%s = 'all' or a.status=%s) order by a.created_at desc limit 100""",
                       (jid, status, status))


class Decision(BaseModel):
    approve: bool
    reason: str = Field(min_length=3, max_length=500)


@router.post("/approvals/{approval_id}/decide")
def decide(approval_id: str, body: Decision, p: Principal = Depends(principal)):
    return approvals.decide(staff(p), approval_id, body.approve, body.reason)


@router.post("/threads/{thread_id}/resume")
def resume_thread(thread_id: str, body: dict, p: Principal = Depends(principal)):
    staff(p)
    kind = body.get("kind")
    if thread_id.split(":")[1:2] == ["fleet"]:
        return fleet.resume(thread_id, p, {"action": body.get("action", "resume")})
    if kind == "approval":
        return workflows.resume_after_approval(thread_id, p)
    if kind == "evidence":
        return workflows.resume_after_evidence(thread_id, p, body.get("decision", "dismiss"), body.get("reason", "operator decision"))
    if kind == "safety":
        return workflows.resolve_safety_pause(thread_id, p, body.get("action", "abort"), link_restored=bool(body.get("link_restored", True)),
                                              jam_cleared=bool(body.get("jam_cleared", False)))
    raise HTTPException(400, "kind must be approval, evidence or safety")


@router.get("/runs")
def runs(jurisdiction: str | None = None, workflow: str | None = None, limit: int = 30, p: Principal = Depends(principal)):
    jid = jurisdiction_for(staff(p), jurisdiction)
    need(p, jid, "ticket.view")
    return db.all_rows("""select id::text, workflow, thread_id, status, current_node, started_at, updated_at, error from agent_runs
                           where jurisdiction_id=%s and (%s::text is null or workflow=%s) order by started_at desc limit %s""",
                       (jid, workflow, workflow, min(limit, 200)))


@router.get("/runs/{run_id}/events")
def run_events(run_id: str, p: Principal = Depends(principal)):
    staff(p)
    r = db.one("select jurisdiction_id::text jid, workflow, thread_id, status from agent_runs where id=%s", (run_id,))
    if not r or r["jid"] not in p.jurisdictions():
        raise HTTPException(404, "not found")
    need(p, r["jid"], "ticket.view")
    return {**r, "events": db.all_rows("select seq, at, node, kind, edge_to, summary, detail from agent_events where run_id=%s order by seq",
                                       (run_id,))}


@router.get("/missions")
def missions(jurisdiction: str | None = None, p: Principal = Depends(principal)):
    jid = jurisdiction_for(staff(p), jurisdiction)
    need(p, jid, "fleet.view")
    return db.all_rows("""select m.id::text, m.robot_id, m.status, m.scenario, m.dispatched_at, m.ended_at, m.abort_reason, m.operation_id::text,
                                 a.approver_id::text approved_by, p.route_cell_ids, p.estimates,
                                 (select slug from public_run_reports where mission_id=m.id) report_slug
                            from missions m join approval_requests a on a.id=m.approval_id join mission_proposals p on p.id=m.proposal_id
                           where m.jurisdiction_id=%s order by m.dispatched_at desc limit 100""", (jid,))


@router.get("/missions/{mission_id}")
def mission(mission_id: str, p: Principal = Depends(principal)):
    staff(p)
    m = db.one("select *, id::text id, jurisdiction_id::text jid from missions where id=%s", (mission_id,))
    if not m or m["jid"] not in p.jurisdictions():
        raise HTTPException(404, "not found")
    need(p, m["jid"], "fleet.view")
    run = db.one("""select r.thread_id from approval_requests a join agent_runs r on r.id = a.run_id
                     where a.id = %s""", (m["approval_id"],))
    m["thread_id"] = run["thread_id"] if run else None
    m["stops"] = db.all_rows("select seq, cell_id, planned_eta_s, status from mission_stops where mission_id=%s order by seq", (mission_id,))
    m["events"] = db.all_rows("select event_id, sim_time_s, kind, actor, detail, provenance from mission_events where mission_id=%s order by id", (mission_id,))
    m["faults"] = db.all_rows("select code, severity, safety_action, detail, opened_at, cleared_at, cleared_by from robot_faults where mission_id=%s", (mission_id,))
    if check(p, m["jid"], "telemetry.view").allowed:
        m["telemetry"] = db.all_rows("""select sim_time_s, cell_id, speed_mps, battery_pct, hopper_pct, state,
                                               extensions.st_y(position::extensions.geometry) lat, extensions.st_x(position::extensions.geometry) lng
                                          from robot_telemetry where mission_id=%s order by sim_time_s, id""", (mission_id,))
    m["evidence"] = db.all_rows("select cell_id, phase, fixture_id, analysis, provenance from inspection_evidence where mission_id=%s order by captured_at", (mission_id,))
    m["verdicts"] = db.all_rows("select cell_id, verdict, reason, confidence from remediation_verdicts where mission_id=%s", (mission_id,))
    return m


@router.post("/missions/{mission_id}/stop")
def stop(mission_id: str, body: dict, p: Principal = Depends(principal)):
    return workflows.stop_mission(mission_id, staff(p), body.get("reason") or "operator stop")


@router.post("/fleet/plan")
def fleet_plan(body: dict, p: Principal = Depends(principal)):
    jid = jurisdiction_for(staff(p), body.get("jurisdiction"))
    return fleet.start(p, jid, immediate=bool(body.get("immediate", True)), max_robots=body.get("max_robots"),
                       scenario=body.get("scenario"))


class Ask(BaseModel):
    question: str = Field(min_length=2, max_length=1000)
    session: str = Field(default="console", max_length=40, pattern=r"^[a-zA-Z0-9_-]+$")
    jurisdiction: str | None = None


@router.post("/copilot")
def ask(body: Ask, p: Principal = Depends(principal)):
    jid = jurisdiction_for(staff(p), body.jurisdiction)
    return copilot.ask(p, jid, body.question, session=body.session)


@router.get("/lane-status")
def lane_status(jurisdiction: str | None = None, p: Principal = Depends(principal)):
    jid = jurisdiction_for(staff(p), jurisdiction)
    need(p, jid, "ticket.view")
    return db.all_rows("select cell_id, condition, updated_at, last_verified_at, provenance from lane_cell_status where jurisdiction_id=%s "
                       "and condition <> 'unknown'", (jid,))


@router.get("/models/status")
def model_status(jurisdiction: str | None = None, p: Principal = Depends(principal)):
    need(p, jurisdiction_for(staff(p), jurisdiction), "admin.policy")
    from ..config import get_settings
    from ..models.budget import billable_enabled
    from ..models.router import routes
    s = get_settings()
    usage = db.all_rows("select bucket, count, tokens from usage_counters where window_kind='day' and window_start=date_trunc('day', now())")
    return {"ai_mode": s.ai_mode, "live": s.live_models and billable_enabled(), "billable_enabled": billable_enabled(),
            "daily_cap": s.ai_daily_cap, "per_minute_cap": s.ai_per_minute_cap, "usage_today": usage,
            "routes": {k: {"candidates": v["candidates"], "api": v["api"], "offline": v["offline"]} for k, v in routes().items()},
            "recent_calls": db.all_rows("select at, route, model, mode, outcome, latency_ms from model_calls order by id desc limit 25")}


CONSOLE_TOOLS = {"draft_work_order", "propose_assignment", "request_verification", "request_dispatch_approval", "draft_rider_notice",
                 "get_sweeper_availability", "export_aggregate_metrics", "retrieve_sop", "search_tickets"}


@router.post("/tools/{name}")
def run_tool(name: str, body: dict, p: Principal = Depends(principal)):
    """Console buttons call the same typed, policy-checked tools the agents use (no bespoke write paths)."""
    from pydantic import ValidationError

    from ..tools import ToolCtx, ToolDenied, invoke
    if name not in CONSOLE_TOOLS:
        raise HTTPException(404, "unknown tool")
    jid = jurisdiction_for(staff(p), body.pop("jurisdiction", None))
    try:
        return {"tool": name, "result": invoke(name, ToolCtx(principal=p, jurisdiction_id=jid), body)}
    except ToolDenied as e:
        raise HTTPException(403, str(e)) from e
    except ValidationError as e:
        raise HTTPException(422, e.errors(include_input=False, include_context=False)) from e


@router.get("/graphs/{workflow}")
def graph_shape(workflow: str, p: Principal = Depends(principal)):
    """Nodes and edges straight from the compiled LangGraph (not a hand-drawn diagram)."""
    staff(p)
    from ..graphs import copilot as cp
    from ..graphs import glasses as gl
    from ..graphs import ticket as tk
    builders = {"ticket": tk.build, "fleet": fleet.build, "glasses": gl.build, "copilot": cp.build}
    if workflow not in builders:
        raise HTTPException(404, "unknown workflow")
    g = builders[workflow]().get_graph()
    return {"workflow": workflow, "nodes": [n for n in g.nodes if not n.startswith("__")],
            "edges": [{"source": e.source, "target": e.target, "conditional": e.conditional} for e in g.edges]}

"""Copilot tools with side effects. None can move a robot by itself:
- request_dispatch_approval only creates proposals + approval REQUESTS;
- simulate_dispatch only executes with an approval a different human already granted (checked at the gateway)."""
from __future__ import annotations

import uuid

from pydantic import Field

from .. import db
from ..agents import dispatch
from ..models.router import ModelRouter
from ..planning.routes import WorkItem
from ..policy.engine import SideEffect
from .read_tools import _ticket_row
from .registry import Args, ToolCtx, tool


class DraftWO(Args):
    ticket: str = Field(max_length=64)
    kind: str = Field(pattern=r"^(robot_sweep|crew_sweep|road_repair|drainage|markings|inspection)$")
    note: str = Field(default="", max_length=300)


@tool("draft_work_order", permission="ticket.update", side_effect=SideEffect.INTERNAL_WRITE,
      effect="creates an OPEN work order record; nothing is dispatched")
def draft_work_order(ctx: ToolCtx, args: DraftWO):
    """Draft a work order for a ticket in this jurisdiction."""
    t = _ticket_row(ctx, args.ticket)
    if not t:
        return {"error": "no such ticket in your jurisdiction"}
    if args.kind == "robot_sweep" and t["hazard_type"] not in ("glass", "debris", "litter"):
        return {"error": f"a sweeping robot cannot remediate {t['hazard_type']}"}
    row = db.one("""insert into work_orders (jurisdiction_id, ticket_id, kind, priority, cell_id, created_by)
                    values (%s,%s,%s,%s,%s,%s) on conflict (ticket_id, kind) do update set status = work_orders.status
                    returning id::text, status""",
                 (ctx.jurisdiction_id, t["id"], args.kind, t["priority"], t["cell_id"], f"user:{ctx.principal.user_id}"))
    return {"work_order_id": row["id"], "status": row["status"], "ticket": t["number"]}


class ProposeAssign(Args):
    ticket: str = Field(max_length=64)
    assignee_kind: str = Field(pattern=r"^(crew|department|robot_sim|external)$")
    assignee_ref: str = Field(max_length=60)
    reason: str = Field(min_length=5, max_length=300)


@tool("propose_assignment", permission="ticket.assign", side_effect=SideEffect.INTERNAL_WRITE,
      effect="records an assignment with the acting user and reason")
def propose_assignment(ctx: ToolCtx, args: ProposeAssign):
    """Assign a ticket to a crew/department/simulated robot, recording who and why."""
    t = _ticket_row(ctx, args.ticket)
    if not t:
        return {"error": "no such ticket in your jurisdiction"}
    with db.tx() as cur:
        cur.execute("""insert into assignments (ticket_id, jurisdiction_id, assignee_kind, assignee_ref, assigned_by, reason)
                       values (%s,%s,%s,%s,%s,%s) returning id::text""",
                    (t["id"], ctx.jurisdiction_id, args.assignee_kind, args.assignee_ref, ctx.principal.user_id, args.reason))
        aid = cur.fetchone()["id"]
        cur.execute("""insert into ticket_events (ticket_id, jurisdiction_id, actor, kind, from_status, to_status, detail)
                       values (%s,%s,%s,'assigned',%s,'assigned',%s)""",
                    (t["id"], ctx.jurisdiction_id, f"user:{ctx.principal.user_id}", t["status"],
                     db.jsonb({"assignee": f"{args.assignee_kind}:{args.assignee_ref}", "reason": args.reason})))
        cur.execute("update tickets set status='assigned' where id=%s and status in ('new','triaged','reopened')", (t["id"],))
    return {"assignment_id": aid, "ticket": t["number"]}


class RequestDispatch(Args):
    tickets: list[str] = Field(min_length=1, max_length=40)
    use_next_sweep_window: bool = True
    max_robots: int = Field(default=2, ge=1, le=10)


@tool("request_dispatch_approval", permission="mission.propose", side_effect=SideEffect.INTERNAL_WRITE,
      effect="plans constraint-checked SIMULATED missions and opens approval REQUESTS; nothing moves")
def request_dispatch_approval(ctx: ToolCtx, args: RequestDispatch):
    """Plan simulated missions for robot-sweepable tickets and request a supervisor's approval."""
    items, skipped = [], []
    for ref in args.tickets:
        t = _ticket_row(ctx, ref)
        if not t:
            skipped.append({"ticket": ref, "reason": "not in your jurisdiction"})
            continue
        wo = db.one("select id::text from work_orders where ticket_id=%s and kind='robot_sweep' and status in ('open','planned')", (t["id"],))
        if not wo:
            skipped.append({"ticket": t["number"], "reason": "no open robot_sweep work order"})
            continue
        items.append(WorkItem(wo["id"], t["id"], t["cell_id"], t["priority"], t["hazard_type"]))
    if not items:
        return {"proposals": [], "skipped": skipped}
    pol = (db.one("select body from policy_versions where active and jurisdiction_id=%s", (ctx.jurisdiction_id,)) or {"body": {}})["body"]
    window = dispatch.sweep_window(pol, immediate=not args.use_next_sweep_window)
    props, raw = dispatch.create_proposals(ctx.jurisdiction_id, items, window, run_id=ctx.run_id, policy=pol, max_robots=args.max_robots)
    out = []
    for p in props:
        r = dispatch.request_approval(p["id"], run_id=ctx.run_id, requested_by_user=ctx.principal.user_id)
        out.append({"proposal_id": p["id"], "robot_id": p["robot_id"], "approval_id": r["approval_id"], "estimates": p["estimates"]})
    return {"proposals": out, "skipped": skipped, "unassigned": raw["unassigned"], "excluded_robots": raw["excluded_robots"],
            "note": "Approval requests created. A different authorised supervisor must approve before anything is dispatched."}


class SimDispatch(Args):
    proposal_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    approval_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    scenario: str | None = Field(default=None, max_length=40)


@tool("simulate_dispatch", permission="mission.propose", side_effect=SideEffect.SIMULATION,
      effect="starts a SIMULATED mission only if the approval is approved, unexpired, unconsumed and matches the payload")
def simulate_dispatch(ctx: ToolCtx, args: SimDispatch):
    """Dispatch an already-approved simulated mission (the gateway re-verifies everything)."""
    p = dispatch.load_proposal(args.proposal_id)
    if str(p["jurisdiction_id"]) != ctx.jurisdiction_id:
        return {"error": "proposal not in your jurisdiction"}
    op = db.one("select operation_id::text from approval_requests where id=%s", (args.approval_id,))
    return dispatch.dispatch_simulated_mission(ctx.principal, args.proposal_id, args.approval_id,
                                               idempotency_key=f"dispatch:{(op or {}).get('operation_id') or uuid.uuid4()}",
                                               scenario=args.scenario, trace_id=ctx.trace_id)


class VerifyArgs(Args):
    ticket: str = Field(max_length=64)
    reason: str = Field(min_length=5, max_length=200)


@tool("request_verification", permission="ticket.update", side_effect=SideEffect.INTERNAL_WRITE,
      effect="queues a re-inspection job; does not change any verdict")
def request_verification(ctx: ToolCtx, args: VerifyArgs):
    """Queue a re-inspection of a ticket's cell."""
    t = _ticket_row(ctx, args.ticket)
    if not t:
        return {"error": "no such ticket in your jurisdiction"}
    row = db.one("""insert into jobs (jurisdiction_id, kind, payload, dedupe_key) values (%s,'reinspect',%s,%s)
                    on conflict (dedupe_key) do update set updated_at=now() returning id::text, status""",
                 (ctx.jurisdiction_id, db.jsonb({"ticket_id": t["id"], "cell_id": t["cell_id"], "reason": args.reason}),
                  f"reinspect:{t['id']}"))
    return {"job_id": row["id"], "status": row["status"]}


class ReportArgs(Args):
    mission_id: str = Field(pattern=r"^[0-9a-f-]{36}$")


@tool("generate_run_report", permission="fleet.view", side_effect=SideEffect.READ,
      effect="none; returns the public aggregate run report if it exists")
def generate_run_report(ctx: ToolCtx, args: ReportArgs):
    """Fetch the public run report for a mission in this jurisdiction."""
    return db.one("select slug, summary, provenance from public_run_reports where mission_id=%s and jurisdiction_id=%s",
                  (args.mission_id, ctx.jurisdiction_id)) or {"error": "no report for that mission in your jurisdiction"}


class NoticeArgs(Args):
    ticket: str = Field(max_length=64)
    situation: str = Field(min_length=5, max_length=300)


@tool("draft_rider_notice", permission="notice.draft", side_effect=SideEffect.READ,
      effect="returns DRAFT text only; publishing requires a separate notice.publish approval")
def draft_rider_notice(ctx: ToolCtx, args: NoticeArgs):
    """Draft a short, factual rider update about a ticket (not sent)."""
    t = _ticket_row(ctx, args.ticket)
    if not t:
        return {"error": "no such ticket in your jurisdiction"}
    r = ModelRouter().text(
        "Write a 2-sentence plain-language update for cyclists about this bike-lane ticket. No promises of dates unless given. "
        f"Facts: ticket {t['number']}, hazard {t['hazard_type']}, status {t['status']}. Situation (operator note): {args.situation}",
        facts={"kind": "rider_status", "message": f"Update on {t['number']} ({t['hazard_type']}): {args.situation} "
                                                  "We'll post again when it is verified cleared."},
        route="text.fast", jurisdiction_id=ctx.jurisdiction_id)
    return {"draft": r.text, "status": "DRAFT — not sent", "provenance": r.provenance.model_dump()}

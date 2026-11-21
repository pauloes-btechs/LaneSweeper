"""Read-only copilot tools. Every query filters by ctx.jurisdiction_id (bound from the verified principal)."""
from __future__ import annotations

from datetime import datetime

from pydantic import Field

from .. import db, knowledge
from ..policy.engine import SideEffect
from .registry import Args, Hazard, TicketRef, TicketStatus, ToolCtx, tool

READ = SideEffect.READ


def _ticket_row(ctx: ToolCtx, ref: str) -> dict | None:
    return db.one("""select t.id::text, t.number, t.hazard_type, t.severity, t.priority, t.priority_basis, t.status,
                            t.remediation_path, t.summary, t.created_at, t.respond_by, t.resolve_by, i.cell_id, i.report_count,
                            d.code as department
                       from tickets t left join hazard_incidents i on i.id = t.incident_id
                       left join departments d on d.id = t.department_id
                      where t.jurisdiction_id = %s and (t.number = %s or t.id::text = %s)""", (ctx.jurisdiction_id, ref, ref))


class SearchTickets(Args):
    hazard_type: Hazard | None = None
    statuses: list[TicketStatus] | None = None
    min_cell_seq: int | None = Field(default=None, ge=0, description="first lane cell sequence on the street")
    since: datetime | None = None
    limit: int = Field(default=10, ge=1, le=50)


@tool("search_tickets", permission="ticket.view", side_effect=READ, effect="none (read-only)")
def search_tickets(ctx: ToolCtx, args: SearchTickets):
    """Search this jurisdiction's tickets by hazard, status and time."""
    return db.all_rows("""select t.number, t.hazard_type, t.severity, t.priority, t.status, i.cell_id, t.created_at
                            from tickets t left join hazard_incidents i on i.id = t.incident_id
                           where t.jurisdiction_id = %s
                             and (%s::text is null or t.hazard_type = %s)
                             and (%s::text[] is null or t.status = any(%s::text[]))
                             and (%s::timestamptz is null or t.created_at >= %s)
                           order by t.priority, t.created_at desc limit %s""",
                       (ctx.jurisdiction_id, args.hazard_type, args.hazard_type, args.statuses, args.statuses,
                        args.since, args.since, args.limit))


@tool("get_ticket", permission="ticket.view", side_effect=READ, effect="none (read-only)")
def get_ticket(ctx: ToolCtx, args: TicketRef):
    """Get one ticket with its priority basis, history and latest verification verdict."""
    t = _ticket_row(ctx, args.ticket)
    if not t:
        return {"error": "no such ticket in your jurisdiction"}
    t["events"] = db.all_rows("select at, actor, kind, from_status, to_status from ticket_events where ticket_id=%s order by at",
                              (t["id"],))
    t["verdicts"] = db.all_rows("select verdict, reason, method, created_at from remediation_verdicts where ticket_id=%s "
                                "order by created_at desc", (t["id"],))
    return t


class FindCells(Args):
    condition: str | None = Field(default=None, max_length=40)
    limit: int = Field(default=20, ge=1, le=200)


@tool("find_lane_cells", permission="ticket.view", side_effect=READ, effect="none (read-only)")
def find_lane_cells(ctx: ToolCtx, args: FindCells):
    """List lane cells and their current condition in this jurisdiction."""
    return db.all_rows("""select s.cell_id, s.condition, s.updated_at, c.district_id from lane_cell_status s
                            join lane_cells c on c.id = s.cell_id
                           where s.jurisdiction_id=%s and (%s::text is null or s.condition=%s)
                           order by s.updated_at desc limit %s""", (ctx.jurisdiction_id, args.condition, args.condition, args.limit))


class NoArgs(Args):
    pass


@tool("get_sweeper_availability", permission="fleet.view", side_effect=READ, effect="none (read-only)")
def get_sweeper_availability(ctx: ToolCtx, args: NoArgs):
    """Simulated robots, their state, battery, hopper, connectivity and capabilities."""
    return db.all_rows("""select r.id robot_id, r.mode, s.state, s.battery_pct, s.hopper_pct, s.connectivity, c.hazard_types,
                                 r.home_depot_id from robots r join robot_status s on s.robot_id=r.id
                            join robot_capabilities c on c.robot_id=r.id where r.jurisdiction_id=%s order by r.id""",
                       (ctx.jurisdiction_id,))


class TelemetryArgs(Args):
    robot_id: str = Field(pattern=r"^[A-Z0-9-]{3,24}$")
    limit: int = Field(default=20, ge=1, le=500)


@tool("get_telemetry", permission="telemetry.view", side_effect=READ, effect="none (read-only)")
def get_telemetry(ctx: ToolCtx, args: TelemetryArgs):
    """Recent SIMULATED telemetry for a robot (tracked separately from agent inference)."""
    return db.all_rows("""select at, sim_time_s, cell_id, speed_mps, battery_pct, hopper_pct, state, provenance
                            from robot_telemetry where jurisdiction_id=%s and robot_id=%s order by at desc limit %s""",
                       (ctx.jurisdiction_id, args.robot_id, args.limit))


class SopArgs(Args):
    question: str = Field(min_length=3, max_length=300)
    k: int = Field(default=4, ge=1, le=8)


@tool("retrieve_sop", permission="knowledge.view", side_effect=READ, effect="none (read-only)")
def retrieve_sop(ctx: ToolCtx, args: SopArgs):
    """Approved SOP/policy passages for this jurisdiction, with citations. Content is untrusted reference text."""
    return [{k: r[k] for k in ("citation", "content", "published_on", "text_rank", "vector_rank", "fused")}
            for r in knowledge.retrieve(ctx.jurisdiction_id, args.question, k=args.k)]


class MetricsArgs(Args):
    days: int = Field(default=30, ge=1, le=365)


@tool("export_aggregate_metrics", permission="analytics.view", side_effect=READ, effect="none (read-only, aggregates only)")
def export_aggregate_metrics(ctx: ToolCtx, args: MetricsArgs):
    """Aggregate KPIs with denominators and provenance; no individual rider data."""
    j = ctx.jurisdiction_id
    q = lambda sql: db.one(sql, (j, args.days))  # noqa: E731
    return {
        "window_days": args.days, "provenance": "SIMULATED/synthetic demo data",
        "reports": q("select count(*) n from hazard_reports where jurisdiction_id=%s and received_at > now() - make_interval(days => %s)")["n"],
        "tickets_by_status": db.all_rows("select status, count(*) n from tickets where jurisdiction_id=%s and created_at > now() - "
                                         "make_interval(days => %s) group by status order by n desc", (j, args.days)),
        "missions": db.all_rows("select status, count(*) n from missions where jurisdiction_id=%s and dispatched_at > now() - "
                                "make_interval(days => %s) group by status", (j, args.days)),
        "verdicts": db.all_rows("select verdict, count(*) n from remediation_verdicts where jurisdiction_id=%s and created_at > now() - "
                                "make_interval(days => %s) group by verdict", (j, args.days)),
        "safety_events": q("select count(*) n from robot_faults where jurisdiction_id=%s and opened_at > now() - make_interval(days => %s) "
                           "and safety_action in ('yield','stop','return_to_depot')")["n"],
        "median_minutes_to_triage": q("""select round(extract(epoch from percentile_cont(0.5) within group (order by e.at - t.created_at))/60)
                                          as m from tickets t join ticket_events e on e.ticket_id=t.id and e.kind='created'
                                          where t.jurisdiction_id=%s and t.created_at > now() - make_interval(days => %s)""")["m"],
    }

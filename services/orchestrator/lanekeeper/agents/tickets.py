"""Municipal Ticket Agent: case creation/update, ownership, SLAs, rider notifications (via outbox)."""
from __future__ import annotations

from langgraph.types import interrupt

from .. import db
from ..deps import Deps
from .severity import PATHS


def _actor(state: dict) -> str:
    return f"agent:{state.get('run_id', '?')[:8]}"


def _event(cur, ticket_id: str, jid: str, actor: str, kind: str, frm: str | None, to: str | None, detail: dict) -> None:
    cur.execute("""insert into ticket_events (ticket_id, jurisdiction_id, actor, kind, from_status, to_status, detail)
                   values (%s,%s,%s,%s,%s,%s,%s)""", (ticket_id, jid, actor, kind, frm, to, db.jsonb(detail)))


def _department(cur, jid: str, hazard: str) -> str | None:
    cur.execute("select id::text from departments where jurisdiction_id=%s and %s = any(handles) limit 1", (jid, hazard))
    r = cur.fetchone()
    return r["id"] if r else None


def create_or_update_ticket(state: dict, deps: Deps, *, status: str = "triaged") -> dict:
    jid, iid = state["jurisdiction_id"], state.get("incident_id")
    hazard, sev, prio = state.get("hazard_type", "other"), state.get("severity", "low"), int(state.get("priority") or 5)
    path = PATHS.get(hazard, "none")
    summary = deps.router.text(
        "Summarise this bike-lane hazard ticket for a city operator in two sentences. Use only these facts.\n"
        f"Facts: hazard={hazard}; severity={sev}; priority={prio}; cell={state.get('lane_cell_id')}; "
        f"basis={state.get('priority_basis')}; path={path}",
        facts={"kind": "ticket_summary", "hazard": hazard, "severity": sev, "street": "1st Ave protected lane",
               "cell": state.get("lane_cell_id"), "reports": (state.get("priority_basis") or {}).get("independent_reports", 1),
               "evidence": "image + advisory model judgement" if (state.get("evidence") or {}).get("has_image") else "text only",
               "path": path},
        route="text.fast", jurisdiction_id=jid, run_id=state.get("run_id"))
    with db.tx() as cur:
        cur.execute("select id::text, number, status, priority from tickets where incident_id = %s", (iid,)) if iid else None
        existing = cur.fetchone() if iid else None
        if existing:
            new_prio = min(existing["priority"], prio)
            cur.execute("update tickets set priority=%s, severity=%s, priority_basis=%s where id=%s",
                        (new_prio, sev, db.jsonb(state.get("priority_basis") or {}), existing["id"]))
            _event(cur, existing["id"], jid, _actor(state), "additional_report", existing["status"], existing["status"],
                   {"report_id": state.get("report_id"), "priority": new_prio})
            return {"ticket_id": existing["id"], "ticket_number": existing["number"],
                    "_summary": f"Updated existing ticket {existing['number']} with the new report (priority {new_prio})"}
        dept = _department(cur, jid, hazard)
        cur.execute("""select respond_within, resolve_within from sla_policies where jurisdiction_id=%s and hazard_type=%s and severity=%s""",
                    (jid, hazard, sev))
        sla = cur.fetchone() or {}
        cur.execute(
            """insert into tickets (jurisdiction_id, incident_id, department_id, hazard_type, severity, priority, priority_basis, status,
                                    remediation_path, summary, respond_by, resolve_by, created_by, idempotency_key, provenance)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now() + %s, now() + %s, %s, %s, %s)
               on conflict (idempotency_key) do update set updated_at = now()
               returning id::text, number""",
            (jid, iid, dept, hazard, sev, prio, db.jsonb(state.get("priority_basis") or {}), status, path, summary.text[:600],
             sla.get("respond_within"), sla.get("resolve_within"), _actor(state), f"ticket:{iid or state.get('report_id')}",
             state.get("source_provenance", "SIMULATED")))
        t = cur.fetchone()
        _event(cur, t["id"], jid, _actor(state), "created", None, status,
               {"report_id": state.get("report_id"), "summary_provenance": summary.provenance.model_dump()})
        cur.execute("""update lane_cell_status set condition = case when %s >= 0.7 then 'hazard_confirmed' else 'hazard_reported' end,
                              updated_at = now() where cell_id = %s and condition not in ('cleaning','scheduled')""",
                    (float(state.get("confidence") or 0), state.get("lane_cell_id")))
    return {"ticket_id": t["id"], "ticket_number": t["number"],
            "_summary": f"Created ticket {t['number']} ({hazard}, {sev}, P{prio}) owned by {'department ' + dept[:8] if dept else 'triage'}"}


def recommend_assignment(state: dict, deps: Deps) -> dict:
    hz = state.get("hazard_type")
    path = PATHS.get(hz, "none")
    with db.tx() as cur:
        cur.execute("select status from tickets where id=%s for update", (state["ticket_id"],))
        cur_status = cur.fetchone()["status"]
        kind = {"robot_sweep": "robot_sweep", "crew_sweep": "crew_sweep", "road_repair": "road_repair",
                "drainage": "drainage", "markings": "markings"}.get(path, "inspection")
        cur.execute("""insert into work_orders (jurisdiction_id, ticket_id, kind, department_id, priority, cell_id, created_by)
                       select jurisdiction_id, id, %s, department_id, priority, %s, %s from tickets where id = %s
                       on conflict (ticket_id, kind) do nothing""",
                    (kind, state.get("lane_cell_id"), _actor(state), state["ticket_id"]))
        if cur_status in ("new", "triaged"):
            cur.execute("update tickets set status='triaged', remediation_path=%s where id=%s", (path, state["ticket_id"]))
            _event(cur, state["ticket_id"], state["jurisdiction_id"], _actor(state), "assignment_recommended", cur_status, "triaged",
                   {"work_order_kind": kind, "path": path, "note": "recommendation only; a human assigns crews"})
    why = (f"Recommended {kind.replace('_', ' ')} work order for {state.get('ticket_number')} ({path}); "
           "no robot dispatch implied")
    return {"next_action": "await_assignment", "outcome": "ticketed", "_summary": why}


def request_clarification(state: dict, deps: Deps) -> dict:
    """Insufficient evidence: open (or keep) a needs_evidence ticket when placeable, notify the reporter, then
    checkpoint and wait. Writes happen BEFORE interrupt() and are idempotent, because LangGraph re-runs this
    node from the top when it resumes."""
    if state.get("jurisdiction_id") and state.get("lane_cell_id") and not state.get("ticket_id"):
        t = create_or_update_ticket(state, deps, status="needs_evidence")
        state = {**state, **{k: v for k, v in t.items() if not k.startswith("_")}}
    rep = state.get("report") or {}
    if (rep.get("consent") or {}).get("notify_status"):
        db.one("""insert into notification_outbox (jurisdiction_id, channel, recipient_ref, template, body, dedupe_key)
                  values (%s, 'rider_push', %s, 'need_more_evidence', %s, %s) on conflict (dedupe_key) do nothing returning id""",
               (state.get("jurisdiction_id"), rep.get("reporter_hash"), db.jsonb({"report_id": state.get("report_id")}),
                f"clarify:{state.get('report_id')}"))
    resume = interrupt({"awaiting": "evidence", "report_id": state.get("report_id"), "ticket_id": state.get("ticket_id"),
                        "question": "Please add a photo taken after stopping safely, or an operator may dismiss/confirm."})
    decision = (resume or {}).get("decision", "dismiss")
    out = {"ticket_id": state.get("ticket_id"), "ticket_number": state.get("ticket_number"), "next_action": decision,
           "_summary": f"Resumed with '{decision}' after waiting for evidence"}
    if decision == "dismiss" and state.get("ticket_id"):
        with db.tx() as cur:
            cur.execute("update tickets set status='dismissed' where id=%s and status='needs_evidence'", (state["ticket_id"],))
            _event(cur, state["ticket_id"], state["jurisdiction_id"], f"user:{(resume or {}).get('by', '?')}", "dismissed",
                   "needs_evidence", "dismissed", {"reason": (resume or {}).get("reason", "insufficient evidence")})
        out["outcome"] = "dismissed"
    elif decision == "confirm":
        out.update({"confidence": max(float(state.get("confidence") or 0), 0.75), "outcome": "confirmed_by_operator"})
    return out


def flag_for_authorized_city_response(state: dict, deps: Deps) -> dict:
    t = create_or_update_ticket(state, deps, status="escalated")
    with db.tx() as cur:
        _event(cur, t["ticket_id"], state["jurisdiction_id"], _actor(state), "urgent_department_flag", "escalated", "escalated",
               {"hazard": state.get("hazard_type"), "note": "routed to the owning department; robots are not proposed"})
        cur.execute("""insert into notification_outbox (jurisdiction_id, channel, recipient_ref, template, body, dedupe_key)
                       values (%s,'console',%s,'urgent_department_hazard',%s,%s) on conflict (dedupe_key) do nothing""",
                    (state["jurisdiction_id"], f"dept:{state.get('hazard_type')}", db.jsonb({"ticket": t["ticket_number"]}),
                     f"urgent:{t['ticket_id']}"))
    return {**t, "outcome": "escalated_to_department",
            "_summary": f"Urgent {state.get('hazard_type')} flagged to the owning department as {t['ticket_number']}; "
                        "the robot cannot repair this"}


def preserve_ticket_and_escalate(state: dict, deps: Deps) -> dict:
    if not state.get("ticket_id"):
        t = create_or_update_ticket(state, deps, status="escalated")
    else:
        t = {"ticket_id": state["ticket_id"], "ticket_number": state.get("ticket_number")}
        with db.tx() as cur:
            cur.execute("update tickets set status='escalated' where id=%s and status not in ('verified_closed','dismissed')",
                        (t["ticket_id"],))
    with db.tx() as cur:
        _event(cur, t["ticket_id"], state["jurisdiction_id"], _actor(state), "escalated", None, "escalated",
               {"reason": (state.get("safety_gate_result") or {}).get("reason") or state.get("route")})
    return {**t, "outcome": "escalated", "_summary": f"Ticket {t['ticket_number']} preserved and escalated for a human decision"}

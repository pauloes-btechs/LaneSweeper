"""Robot Inspection Agent: independent before/after verification and public run reports.

A finished route proves nothing by itself. 'cleared' requires after-evidence whose vision verdict is clear AND an
independent advisory judgement that the cell is clear. Anything else is partially_cleared / not_cleared / unverified,
and the ticket is reopened or escalated. Public reports carry aggregates and provenance only."""
from __future__ import annotations

from .. import db
from ..deps import Deps
from ..models.google import Image
from ..storage import fixture_bytes

CLEAR_Q = {"cell_clear": {"type": "noul", "instructions": "Is this after-sweep frame clear of broken glass in the bike lane?",
                          "criteria": {"true_description": "no glass fragments visible",
                                       "false_description": "glass fragments remain, or the frame is too dark/blurred to tell"},
                          "fixture_key": "cell_clear"}}


def _clear_judgement(deps: Deps, ev: dict, jid: str) -> tuple[float, dict]:
    fx = ev.get("fixture_id")
    img = fixture_bytes(fx) if fx else None
    from ..models.offline import fixtures
    truth = (fixtures().get(fx or "") or {}).get("truth", {})
    q = {"cell_clear": {**CLEAR_Q["cell_clear"]}}
    res = deps.router.decide("After-sweep verification of a protected bike-lane cell.", q,
                             images=[Image(*img)] if img else [], fixture_id=fx, jurisdiction_id=jid)
    p = res.answers["cell_clear"].advisory_probability
    if res.provenance.mode == "offline" and fx:
        # fixture decider has no 'cell_clear' label: derive from the fixture's authored truth
        p = 0.9 if truth.get("vision_verdict") == "clear" else (0.5 if truth.get("vision_verdict") == "unsure" else 0.1)
    return p, res.provenance.model_dump()


def verify_mission(mission_id: str, deps: Deps) -> dict:
    m = db.one("select id::text, jurisdiction_id::text jid, status, robot_id from missions where id=%s", (mission_id,))
    jid = m["jid"]
    rows = db.all_rows(
        """select w.ticket_id::text, w.cell_id, w.id::text wo, s.status stop_status,
                  (select row_to_json(e) from inspection_evidence e where e.mission_id=%s and e.cell_id=w.cell_id and e.phase='after'
                    order by captured_at desc limit 1) as after,
                  (select row_to_json(e) from inspection_evidence e where e.mission_id=%s and e.cell_id=w.cell_id and e.phase='before'
                    order by captured_at desc limit 1) as before
             from work_orders w left join mission_stops s on s.mission_id = w.mission_id and s.cell_id = w.cell_id
            where w.mission_id=%s""", (mission_id, mission_id, mission_id))
    results = []
    for r in rows:
        after, before = r["after"], r["before"]
        if r["stop_status"] == "skipped" and before and before["analysis"]["verdict"] in ("not_glass", "clear"):
            verdict, reason, conf, prov, ev_id = ("unverified", "robot saw no glass on arrival: possible false report or "
                                                  "already cleared; human review", None, None, None)
        elif not after:
            verdict, reason, conf, prov, ev_id = ("unverified", f"no after-evidence (stop {r['stop_status'] or 'not reached'})",
                                                  None, None, None)
        else:
            v = after["analysis"]["verdict"]
            p_clear, prov = _clear_judgement(deps, after, jid)
            ev_id = after["id"]
            conf = round(p_clear, 3)
            if v == "clear" and p_clear >= 0.7:
                verdict, reason = "cleared", f"after-frame clear (vision) and advisory clear p≈{p_clear:.2f}"
            elif v == "glass":
                bc = (before or {}).get("analysis", {}).get("est_count") or 0
                ac = after["analysis"].get("est_count") or 0
                verdict = "partially_cleared" if bc and ac < bc else "not_cleared"
                reason = f"glass still visible after sweep (~{ac} fragments vs ~{bc} before)"
            else:
                verdict, reason = "unverified", f"after-frame inconclusive (vision {v}, clear p≈{p_clear:.2f})"
        with db.tx() as cur:
            cur.execute("""insert into remediation_verdicts (jurisdiction_id, ticket_id, mission_id, cell_id, verdict, after_evidence_id,
                                                             method, confidence, model_provenance, decided_by, reason)
                           values (%s,%s,%s,%s,%s,%s,'model',%s,%s,'agent:robot_inspection',%s)""",
                        (jid, r["ticket_id"], mission_id, r["cell_id"], verdict, ev_id if verdict == "cleared" or ev_id else None,
                         conf, db.jsonb(prov) if prov else None, reason))
            new_ticket = "verified_closed" if verdict == "cleared" else ("reopened" if verdict in ("partially_cleared", "not_cleared") else "escalated")
            cell_cond = {"cleared": "cleared_verified", "partially_cleared": "partially_cleared",
                         "not_cleared": "hazard_confirmed"}.get(verdict, "cleared_unverified" if r["stop_status"] == "swept" else "hazard_reported")
            cur.execute("select status from tickets where id=%s for update", (r["ticket_id"],))
            old = cur.fetchone()["status"]
            cur.execute("update tickets set status=%s where id=%s", (new_ticket, r["ticket_id"]))
            cur.execute("""insert into ticket_events (ticket_id, jurisdiction_id, actor, kind, from_status, to_status, detail)
                           values (%s,%s,'agent:robot_inspection','verification',%s,%s,%s)""",
                        (r["ticket_id"], jid, old, new_ticket, db.jsonb({"verdict": verdict, "reason": reason, "mission": mission_id})))
            cur.execute("update lane_cell_status set condition=%s, updated_at=now(), last_verified_at=case when %s='cleared' then now() end "
                        "where cell_id=%s", (cell_cond, verdict, r["cell_id"]))
            cur.execute("update work_orders set status=%s where id=%s", ("done" if verdict == "cleared" else "open", r["wo"]))
            if verdict == "cleared":
                cur.execute("update hazard_incidents set status='resolved' where id=(select incident_id from tickets where id=%s)",
                            (r["ticket_id"],))
        results.append({"ticket_id": r["ticket_id"], "cell_id": r["cell_id"], "verdict": verdict, "reason": reason,
                        "ticket_status": new_ticket})
    return {"mission_id": mission_id, "results": results,
            "all_cleared": bool(results) and all(x["verdict"] == "cleared" for x in results)}


def publish_run_report(mission_id: str) -> dict:
    m = db.one("""select m.id::text, m.jurisdiction_id::text jid, m.robot_id, m.status, m.dispatched_at, m.scenario,
                         (select count(*) from mission_stops where mission_id=m.id) cells,
                         (select count(*) from mission_stops where mission_id=m.id and status='swept') swept,
                         (select count(*) from mission_stops where mission_id=m.id and status in ('blocked','skipped')) not_done,
                         (select count(*) from remediation_verdicts where mission_id=m.id and verdict='cleared') verified,
                         (select count(*) from remediation_verdicts where mission_id=m.id and verdict<>'cleared') unverified,
                         (select count(*) from robot_faults where mission_id=m.id and safety_action in ('yield','stop')) safety_events
                    from missions m where m.id=%s""", (mission_id,))
    date = m["dispatched_at"].date()
    slug = f"{date.isoformat()}-{m['robot_id'].lower()}-{m['id'][:6]}"
    summary = {"service_date": date.isoformat(), "robot": m["robot_id"], "mission_status": m["status"],
               "lane_cells_planned": m["cells"], "lane_cells_swept": m["swept"], "cells_not_completed": m["not_done"],
               "approx_lane_km": round(m["cells"] * 25 / 1000, 2), "verified_cleared": m["verified"],
               "unverified_or_remaining": m["unverified"], "safety_events": m["safety_events"],
               "scenario": m["scenario"] or "nominal", "provenance": "SIMULATED",
               "disclaimer": "Simulated digital-twin run. Not field data. 'Swept' is not 'verified'; only verified cells count as cleared."}
    db.one("""insert into public_run_reports (slug, jurisdiction_id, mission_id, service_date, summary, provenance)
              values (%s,%s,%s,%s,%s,'SIMULATED')
              on conflict (mission_id) do update set summary=excluded.summary, generated_at=now() returning slug""",
           (slug, m["jid"], mission_id, date, db.jsonb(summary)))
    return {"slug": slug, "summary": summary}

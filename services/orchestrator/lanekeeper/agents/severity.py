"""Severity/Priority Agent and the deterministic policy router (final gate, no model)."""
from __future__ import annotations

from .. import db
from ..deps import Deps

ROBOTIC_DEFAULT = ["glass", "debris", "litter"]
NON_ROBOTIC_DEPT = {"pothole": "DOT_ROAD", "water": "DEP", "branch": "DPR", "markings": "DOT_MARK",
                    "construction": "DOT_MARK", "obstruction": "DOT_MARK"}
PATHS = {"pothole": "road_repair", "water": "drainage", "branch": "crew_sweep", "markings": "markings",
         "construction": "external_agency", "obstruction": "external_agency", "glass": "robot_sweep",
         "debris": "robot_sweep", "litter": "robot_sweep", "other": "none"}


def score_severity_and_confidence(state: dict, deps: Deps) -> dict:
    if state.get("outcome") in ("rejected", "duplicate_request"):
        return {}
    pol = state.get("policy") or {}
    thr = (pol.get("evidence") or {})
    severity = state.get("severity", "low")
    conf = float(state.get("confidence") or 0)
    reports, recur = 1, 0
    if state.get("incident_id"):
        reports = db.one("select report_count from hazard_incidents where id=%s", (state["incident_id"],))["report_count"]
    if state.get("lane_cell_id"):
        recur = db.one("""select count(*) as n from hazard_reports where cell_id=%s and received_at > now() - interval '30 days'""",
                       (state["lane_cell_id"],))["n"]
    ev = (state.get("evidence") or {}).get("decision", {})
    glass_p = (ev.get("is_glass") or {}).get("advisory_probability", 0)
    if state.get("hazard_type") == "glass" and glass_p < thr.get("glass_yes_threshold", 0.65):
        conf = min(conf, glass_p)   # category says glass but glass evidence is weak
    if reports >= 2 and severity in ("medium",):
        severity = "high"           # independent corroboration raises severity (SOP)
    if severity == "critical" or (severity == "high" and reports >= 2):
        prio, rule = 1, "critical, or high with ≥2 independent reports"
    elif severity == "high":
        prio, rule = 2, "high severity"
    elif severity == "medium" or recur >= 3:
        prio, rule = 3, "medium severity or recurring cell (≥3 reports/30 d)"
    else:
        prio, rule = 4, "low severity"
    if conf < thr.get("human_review_below", 0.70):
        prio, rule = max(prio, 5) if prio >= 4 else prio, rule + "; evidence below confidence threshold"
    basis = {"severity": severity, "confidence": round(conf, 3), "independent_reports": reports, "recurrence_30d": recur,
             "glass_advisory_p": glass_p, "rule": rule, "policy_version": state.get("policy_version")}
    why = f"Priority {prio}: {rule} (severity {severity}, confidence {conf:.2f}, {reports} report(s), {recur} in cell/30d)"
    return {"severity": severity, "priority": prio, "priority_basis": basis, "confidence": round(conf, 3), "_summary": why,
            "_decisions": [{"summary": why, **basis}]}


def policy_router(state: dict, deps: Deps) -> dict:
    """Deterministic branch selection. Model outputs are inputs here, never the decision itself."""
    pol = state.get("policy") or {}
    thr = pol.get("evidence") or {}
    robotic = set(pol.get("robotic_hazards") or ROBOTIC_DEFAULT)
    o = state.get("outcome")
    if o == "rejected":
        return _route("rejected", "report rejected at intake")
    if o == "duplicate_request":
        return _route("duplicate_request", "replayed request; original already processed")
    ms, hz, conf = state.get("match_status"), state.get("hazard_type"), float(state.get("confidence") or 0)
    needs_more = ((state.get("evidence") or {}).get("decision", {}).get("needs_more_evidence") or {}).get("advisory_probability", 0)
    if ms in ("no_location", "outside_lanes") or not state.get("jurisdiction_id"):
        return _route("insufficient_evidence", f"location status '{ms}': cannot place on a lane")
    if hz == "other":
        return _route("insufficient_evidence", "no identifiable hazard category")
    if conf < thr.get("min_decision_confidence", 0.70) or needs_more >= 0.6:
        return _route("insufficient_evidence", f"confidence {conf:.2f} below {thr.get('min_decision_confidence', 0.70)} "
                                               f"or more evidence requested (p≈{needs_more:.2f})")
    if state.get("duplicate_of"):
        return _route("routine_ticket", "linked to an existing incident: update its ticket")
    if hz not in robotic:
        if state.get("severity") in ("high", "critical"):
            return _route("urgent_non_robotic_hazard", f"{hz} is not robot-sweepable and severity is {state.get('severity')}")
        return _route("routine_ticket", f"{hz} is handled by the owning department, not a robot")
    if ms != "matched":
        return _route("routine_ticket", f"location needs manual review ({ms}): no robot proposal")
    blocked = db.one("""select z.name from restricted_zones z join lane_cells c on c.id = %s
                        where z.active and z.jurisdiction_id = c.jurisdiction_id and z.kind in ('no_robot','school_zone','event')
                          and extensions.st_intersects(z.geom, c.geom) limit 1""", (state.get("lane_cell_id"),))
    if blocked:
        return _route("policy_denied_or_unsafe", f"cell lies in restricted zone '{blocked['name']}'")
    if int(state.get("priority") or 5) <= 3:
        return _route("robotic_remediation_candidate", f"{hz}, priority {state.get('priority')}, matched lane cell, "
                                                       f"confidence {conf:.2f}")
    return _route("routine_ticket", f"{hz} at priority {state.get('priority')}: queued for the next scheduled sweep")


def _route(route: str, why: str) -> dict:
    return {"route": route, "_summary": f"Route → {route}: {why}", "_decisions": [{"summary": why, "route": route}]}

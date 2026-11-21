"""Geospatial Agent: match a report to protected-lane cells and jurisdiction with PostGIS.

Refuses to assert accuracy the evidence does not support: no snapping beyond the configured radius,
and boundary/ambiguous/poor-accuracy cases go to manual review rather than being guessed."""
from __future__ import annotations

from .. import db
from ..deps import Deps

DEFAULTS = {"base_radius_m": 30, "max_radius_m": 75, "boundary_review_m": 40, "max_accuracy_m": 50}


def extract_and_verify_location(state: dict, deps: Deps) -> dict:
    if state.get("outcome") in ("rejected", "duplicate_request"):
        return {}
    loc = state.get("location") or {}
    if loc.get("lat") is None:
        return _write(state, None, None, "no_location", None,
                      "No location shared: the report cannot be placed on a lane cell (manual review)")
    acc = state.get("location_accuracy_m")
    rows = db.all_rows("select * from lk.match_lane_cells(%s, %s, %s, null, %s, %s)",
                       (loc["lat"], loc["lng"], acc, DEFAULTS["base_radius_m"], DEFAULTS["max_radius_m"]))
    if not rows:
        return _write(state, None, None, "outside_lanes", None,
                      f"No protected-lane cell within {DEFAULTS['max_radius_m']} m of the reported point; not snapped")
    best = rows[0]
    jid = str(best["jurisdiction_id"]) if best["jurisdiction_id"] else None
    status, why = "matched", f"Matched cell {best['cell_id']} at {best['distance_m']:.1f} m (search radius {best['search_radius_m']:.0f} m)"
    others = [r for r in rows[1:] if r["district_id"] != best["district_id"] or str(r["jurisdiction_id"]) != jid]
    if acc is not None and acc > DEFAULTS["max_accuracy_m"]:
        status, why = "boundary_review", f"GPS accuracy {acc:.0f} m is worse than {DEFAULTS['max_accuracy_m']} m; position not trusted"
    elif others and others[0]["distance_m"] - best["distance_m"] < DEFAULTS["boundary_review_m"]:
        status, why = "boundary_review", (f"Within {DEFAULTS['boundary_review_m']} m of another district/jurisdiction "
                                          f"({others[0]['district_id']}); manual review required")
    elif len(rows) > 1 and rows[1]["cell_id"] != best["cell_id"] and abs(rows[1]["distance_m"] - best["distance_m"]) < 2 \
            and acc and acc > 15:
        status, why = "ambiguous", f"Two cells equally close ({best['cell_id']}, {rows[1]['cell_id']}) at this accuracy"
    return _write(state, best["cell_id"], jid, status, best["distance_m"], why)


def _write(state: dict, cell: str | None, jid: str | None, status: str, dist: float | None, why: str) -> dict:
    db.one("""update hazard_reports set cell_id=%s, jurisdiction_id=coalesce(%s::uuid, jurisdiction_id), match_status=%s,
                     match_distance_m=%s, status='validated' where id=%s returning id""",
           (cell, jid, status, dist, state["report_id"]))
    if jid:
        db.one("update report_media set jurisdiction_id=%s where report_id=%s returning id", (jid, state["report_id"]))
    out = {"lane_cell_id": cell, "match_status": status, "_summary": why,
           "_decisions": [{"summary": why, "match_status": status, "distance_m": dist}]}
    if jid:
        out["jurisdiction_id"] = jid
        out["tenant_id"] = jid
        out["city_id"] = jid
    return out

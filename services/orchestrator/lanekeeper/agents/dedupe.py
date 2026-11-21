"""Duplicate Detection Agent: rules (time, distance, type) + pgvector similarity.

Never merges evidence: every report keeps its own row; links record kind, scores and who decided.
Auto-linking only happens above the strict policy threshold and when the feature flag allows it."""
from __future__ import annotations

from .. import db
from ..deps import Deps

DEFAULTS = {"auto_link_rule_score": 0.85, "suggest_rule_score": 0.55, "radius_m": 60, "window_hours": 48, "vector_min": 0.80}


def _vec_literal(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def deduplicate_incident(state: dict, deps: Deps) -> dict:
    if state.get("outcome") in ("rejected", "duplicate_request") or not state.get("jurisdiction_id"):
        return {"_summary": "Duplicate check skipped (no jurisdiction)"} if state.get("outcome") is None else {}
    jid, rid = state["jurisdiction_id"], state["report_id"]
    pol = {**DEFAULTS, **((state.get("policy") or {}).get("duplicates") or {})}
    hazard = state.get("hazard_type") or "other"
    note = ((state.get("report") or {}).get("note") or "")[:300]
    emb = deps.router.embed(f"{hazard} hazard in protected bike lane cell {state.get('lane_cell_id')}. {note}",
                            jurisdiction_id=jid, run_id=state.get("run_id"))
    vec = _vec_literal(emb.vector)
    db.one("""insert into incident_embeddings (jurisdiction_id, report_id, modality, embedding, embed_model, embed_dim, embed_version, source)
              values (%s,%s,'text',%s::extensions.vector,%s,%s,%s,'hazard_type+cell+note') returning id""",
           (jid, rid, vec, emb.model, emb.dim, emb.version))
    loc = state.get("location") or {}
    candidates = []
    if loc.get("lat") is not None:
        candidates = db.all_rows(
            """select i.id::text, i.hazard_type, i.last_seen_at, i.cell_id,
                      extensions.st_distance(c.geom, extensions.st_setsrid(extensions.st_makepoint(%s,%s),4326)::extensions.geography) as d,
                      extract(epoch from (now() - i.last_seen_at)) / 3600.0 as hours
                 from hazard_incidents i join lane_cells c on c.id = i.cell_id
                where i.jurisdiction_id = %s and i.status in ('open','monitoring')
                  and i.last_seen_at > now() - make_interval(hours => %s)
                  and extensions.st_dwithin(c.geom, extensions.st_setsrid(extensions.st_makepoint(%s,%s),4326)::extensions.geography, %s)""",
            (loc["lng"], loc["lat"], jid, pol["window_hours"], loc["lng"], loc["lat"], pol["radius_m"]))
        sims = {r["incident_id"]: r["similarity"] for r in db.all_rows(
            "select incident_id::text, similarity from lk.similar_incidents(%s, %s::extensions.vector, %s, null, %s, %s, %s, %s, 10)",
            (jid, vec, emb.model, loc["lat"], loc["lng"], pol["radius_m"], f"{pol['window_hours']} hours")) if r["incident_id"]}
    else:
        sims = {}
    scored = []
    for c in candidates:
        rule = (0.5 * (c["hazard_type"] == hazard) + 0.3 * max(0.0, 1 - float(c["d"]) / pol["radius_m"])
                + 0.2 * max(0.0, 1 - float(c["hours"]) / pol["window_hours"]))
        scored.append((round(rule, 3), round(float(sims.get(c["id"], 0.0)), 3), c))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    auto_ok = (db.one("select enabled from feature_flags where key='auto_link_duplicates'") or {}).get("enabled", False)
    decided_by = f"policy:{state.get('policy_version', 'default')}"
    with db.tx() as cur:
        if scored and scored[0][0] >= pol["auto_link_rule_score"] and auto_ok:
            rule, vs, c = scored[0]
            cur.execute("""insert into incident_report_links (incident_id, report_id, link_kind, rule_score, vector_score, decided_by)
                           values (%s,%s,'auto_duplicate',%s,%s,%s) on conflict do nothing""", (c["id"], rid, rule, vs, decided_by))
            cur.execute("""update hazard_incidents set report_count = report_count + 1, last_seen_at = now() where id = %s""", (c["id"],))
            cur.execute("update hazard_reports set status='linked' where id=%s", (rid,))
            why = f"Auto-linked to open incident {c['id'][:8]} (rule {rule:.2f} ≥ {pol['auto_link_rule_score']}, vector {vs:.2f}); report kept separately"
            return {"incident_id": c["id"], "duplicate_of": c["id"], "duplicate_candidates": _cands(scored), "_summary": why,
                    "_decisions": [{"summary": why, "link": "auto_duplicate", "rule_score": rule, "vector_score": vs,
                                    "embed_model": emb.model}]}
        cur.execute("""insert into hazard_incidents (jurisdiction_id, cell_id, hazard_type, first_seen_at, last_seen_at, provenance)
                       values (%s,%s,%s, now(), now(), %s) returning id::text""",
                    (jid, state.get("lane_cell_id"), hazard, state.get("source_provenance", "SIMULATED")))
        iid = cur.fetchone()["id"]
        cur.execute("""insert into incident_report_links (incident_id, report_id, link_kind, decided_by) values (%s,%s,'origin',%s)""",
                    (iid, rid, decided_by))
        suggestion = None
        if scored and (scored[0][0] >= pol["suggest_rule_score"] or (scored[0][1] >= pol["vector_min"] and scored[0][0] >= 0.4)):
            rule, vs, c = scored[0]
            cur.execute("""insert into incident_report_links (incident_id, report_id, link_kind, rule_score, vector_score, decided_by)
                           values (%s,%s,'suggested_duplicate',%s,%s,%s) on conflict do nothing""", (c["id"], rid, rule, vs, decided_by))
            suggestion = c["id"]
    why = (f"New incident {iid[:8]}" + (f"; suggested possible duplicate of {suggestion[:8]} for operator review" if suggestion
                                        else "; no open incident nearby matched") + f" (embeddings: {emb.model})")
    return {"incident_id": iid, "duplicate_of": None, "duplicate_candidates": _cands(scored), "_summary": why,
            "_decisions": [{"summary": why, "suggested": suggestion, "candidates": len(scored)}]}


def _cands(scored: list) -> list[dict]:
    return [{"incident_id": c["id"], "rule_score": r, "vector_score": v, "distance_m": round(float(c["d"]), 1)}
            for r, v, c in scored[:5]]

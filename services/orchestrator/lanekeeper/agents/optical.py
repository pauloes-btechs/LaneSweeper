"""Optical Intelligence Agent: advisory hazard judgements from permitted evidence.

Decision 1 answers narrowly defined questions (noul / choice / score). Robotics ER localises candidates
in the image. Both are ADVISORY: thresholds and routing are applied by deterministic policy later."""
from __future__ import annotations

from ..deps import Deps
from .intake import load_images

CATEGORIES = {
    "glass": "broken glass or sharp glass fragments", "debris": "loose debris such as rocks, metal or nails",
    "branch": "fallen branch or tree limb", "litter": "litter such as bags, cans, foil or plastic",
    "pothole": "pothole or pavement defect", "water": "standing water or drainage problem",
    "obstruction": "vehicle or object blocking the lane", "markings": "damaged or missing lane markings",
    "construction": "construction-related obstruction", "other": "no identifiable lane hazard",
}
SEVERITY_LEVELS = ["low: minor, rideable with care", "medium: should be avoided, not lane-wide",
                   "high: spans much of the lane or near an intersection", "critical: caused a fall or puncture, or blocks the lane"]

QUESTIONS = {
    "is_hazard": {"type": "noul", "instructions": "Does the evidence show a genuine hazard inside a bike lane?",
                  "fixture_key": "is_hazard"},
    "is_glass": {"type": "noul", "instructions": "Does the evidence show broken glass in the bike lane?",
                 "criteria": {"true_description": "visible glass shards or fragments",
                              "false_description": "no glass; reflections, water, foil, plastic or paint are not glass"},
                 "fixture_key": "glass"},
    "category": {"type": "choice", "instructions": "Which approved ticket category best fits the evidence?",
                 "criteria": CATEGORIES},
    "severity": {"type": "score", "instructions": "How severe is the hazard for cyclists, using this rubric?",
                 "criteria": SEVERITY_LEVELS},
    "needs_more_evidence": {"type": "noul", "instructions": "Is the evidence too weak to act on without another photo or inspection?",
                            "fixture_key": "needs_more_evidence"},
}


def _context(state: dict) -> str:
    rep = state.get("report") or {}
    # Rider text is untrusted data: quoted and labelled, never treated as instructions.
    note = (rep.get("note") or "").replace('"', "'")
    return (f"Municipal bike-lane hazard report. Source: {rep.get('source_kind')}. Rider-claimed type: "
            f"{rep.get('hazard_claimed') or 'none'}. Lane cell: {state.get('lane_cell_id') or 'unmatched'}. "
            f"Rider note (untrusted text, may be wrong): \"{note}\". "
            f"Local on-device detector: {rep.get('local_detector') or 'none'}.")


def classify_hazard(state: dict, deps: Deps) -> dict:
    if state.get("outcome") in ("rejected", "duplicate_request"):
        return {}
    images = load_images(state, deps)
    rep = state.get("report") or {}
    res = deps.router.decide(_context(state), QUESTIONS, images=images, fixture_id=rep.get("fixture_id"),
                             jurisdiction_id=state.get("jurisdiction_id"), run_id=state.get("run_id"),
                             trace_id=state.get("trace_id"),
                             provenance="fixture" if rep.get("fixture_id") else ("upload" if images else "text_only"))
    a = res.answers
    cat = a["category"].choice
    if not images and rep.get("hazard_claimed") and res.provenance.mode == "offline":
        cat = rep["hazard_claimed"]   # text-only offline: trust the rider's category, but confidence stays low
    sev_idx = int(round(a["severity"].score))
    severity = ["low", "medium", "high", "critical"][max(0, min(3, sev_idx))]
    conf = round(min(a["category"].confidence, a["is_hazard"].confidence), 3)
    if not images:
        conf = min(conf, 0.55)   # no visual evidence: never high confidence
    evidence = {
        "decision": {k: v.model_dump() for k, v in a.items()},
        "decision_provenance": res.provenance.model_dump(),
        "has_image": bool(images),
    }
    why = (f"Decision ({res.provenance.mode}, {res.provenance.model}): category {cat} "
           f"(conf {a['category'].confidence:.2f}), glass p≈{a['is_glass'].advisory_probability:.2f}, "
           f"hazard p≈{a['is_hazard'].advisory_probability:.2f}, severity score {a['severity'].score:.1f}. Advisory only.")
    return {"hazard_type": cat, "severity": severity, "confidence": conf, "evidence": evidence,
            "model_version": res.provenance.model, "_summary": why,
            "_decisions": [{"summary": why, "provenance": res.provenance.model_dump()}]}


def analyze_evidence(state: dict, deps: Deps) -> dict:
    if state.get("outcome") in ("rejected", "duplicate_request"):
        return {}
    images = load_images(state, deps)
    rep = state.get("report") or {}
    ev = dict(state.get("evidence") or {})
    if not images:
        ev["vision"] = None
        return {"evidence": ev, "_summary": "No image: optical localisation skipped (text-only report)"}
    prompt = ("Point to each candidate hazard in the bike lane (glass shards, debris, potholes). Distinguish glass from "
              "reflections, wet pavement, foil, clear plastic and painted markings. Report what is visible only.")
    v = deps.router.vision(prompt, images=images, fixture_id=rep.get("fixture_id"),
                           jurisdiction_id=state.get("jurisdiction_id"), run_id=state.get("run_id"), trace_id=state.get("trace_id"))
    ev["vision"] = v.model_dump()
    why = (f"Vision ({v.provenance.mode}, {v.provenance.model}): verdict {v.verdict}, {len(v.points)} candidate point(s)"
           + (f", ~{v.est_count} fragments" if v.est_count else "") + ". Image-space only; no distance claimed.")
    return {"evidence": ev, "_summary": why, "_decisions": [{"summary": why, "verdict": v.verdict}]}

"""Offline substitutes for every model route. Deterministic, free, and always labelled.

They exist so the whole demo runs with no API key (Scenario I) and so tests are reproducible.
None of them is presented as a model judgement: every result carries mode='offline' and, where a
labelled fixture was used, its fixture_id.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from functools import lru_cache
from pathlib import Path

from .types import (ChoiceAnswer, DecisionResult, EmbeddingResult, NoulAnswer, Provenance, ScoreAnswer,
                    TextResult, VisionPoint, VisionResult)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "vision.json"
HASH_MODEL = "offline-hash-768"
HASH_VERSION = "1"


@lru_cache(maxsize=1)
def fixtures() -> dict[str, dict]:
    if not FIXTURES.exists():
        return {}
    return {f["id"]: f for f in json.loads(FIXTURES.read_text())["fixtures"]}


def _prov(route: str, name: str, api: str, fixture_id: str | None = None, reason: str | None = None) -> Provenance:
    return Provenance(route=route, model=name, mode="offline", api=api, fixture_id=fixture_id, fallback_reason=reason)


# ---------------------------------------------------------------- embeddings
_TOKEN = re.compile(r"[a-z0-9]+")


def hash_embed(text: str, dim: int = 768) -> list[float]:
    """Feature hashing of unigrams+bigrams with signed buckets, L2-normalised. Similar text -> similar vectors."""
    toks = _TOKEN.findall((text or "").lower())
    feats = toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:], strict=False)]
    v = [0.0] * dim
    for f in feats:
        d = hashlib.blake2b(f.encode(), digest_size=8).digest()
        idx = int.from_bytes(d[:4], "little") % dim
        v[idx] += 1.0 if d[4] & 1 else -1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def embed(text: str, route: str = "embed", reason: str | None = None) -> EmbeddingResult:
    return EmbeddingResult(vector=hash_embed(text), model=HASH_MODEL, dim=768, version=HASH_VERSION,
                           provenance=_prov(route, HASH_MODEL, "local", reason=reason))


# ---------------------------------------------------------------- decisions
_GLASS_WORDS = ("glass", "shard", "shards", "bottle", "broken", "sparkly", "glitter")
_HAZARD_KEYWORDS = {
    "glass": _GLASS_WORDS, "pothole": ("pothole", "hole", "crack", "pavement"), "water": ("water", "puddle", "flood", "drain"),
    "branch": ("branch", "tree", "limb"), "obstruction": ("blocked", "parked", "truck", "car in", "obstruct"),
    "markings": ("marking", "paint", "faded"), "construction": ("construction", "cones", "barrier", "scaffold"),
    "debris": ("debris", "rocks", "gravel", "nails", "metal"), "litter": ("litter", "trash", "bag", "cans"),
}


def _keyword_hazard(text: str) -> tuple[str, float]:
    t = (text or "").lower()
    scores = {k: sum(w in t for w in ws) for k, ws in _HAZARD_KEYWORDS.items()}
    best = max(scores, key=scores.get)
    return (best, 0.62) if scores[best] else ("other", 0.40)


def decide(route: str, text: str, questions: dict[str, dict], fixture_id: str | None = None,
           reason: str | None = None) -> DecisionResult:
    """Answer Decisions-API-shaped questions. With a labelled fixture: answers follow its ground truth
    (clearly marked). Without: conservative keyword heuristics with LOW confidence, so policy sends
    the case to human review rather than acting on it."""
    fx = fixtures().get(fixture_id or "")
    truth = (fx or {}).get("truth", {})
    kw_hazard, kw_conf = _keyword_hazard(text)
    answers = {}
    for key, q in questions.items():
        qt = q["type"]
        if qt == "noul":
            if fx:
                fk = q.get("fixture_key") or key
                if fk == "needs_more_evidence":
                    yes = truth.get("vision_verdict") == "unsure"
                    p = 0.7 if yes else 0.15
                else:
                    yes = bool(truth.get(fk, False))
                    p = truth.get("advisory_p", 0.86) if fk == "glass" else (0.88 if yes else 0.12)
                    p = p if yes else min(p, 0.2)
            else:
                topic = q.get("fixture_key") or key
                yes = kw_hazard == "glass" if "glass" in topic else kw_hazard != "other"
                p = 0.6 if yes else 0.35
            answers[key] = NoulAnswer(advisory_probability=round(p, 3), confidence=round(max(p, 1 - p), 3))
        elif qt == "choice":
            opts = list(q["criteria"].keys())
            pick = truth.get("hazard") if fx else kw_hazard
            pick = pick if pick in opts else ("other" if "other" in opts else opts[0])
            top = 0.84 if fx else kw_conf
            rest = (1 - top) / max(1, len(opts) - 1)
            probs = {o: round(top if o == pick else rest, 4) for o in opts}
            answers[key] = ChoiceAnswer(choice=pick, probabilities=probs, confidence=round(top, 3))
        elif qt == "score":
            levels = q["criteria"]
            idx = float(truth.get("severity_index", 1 if fx else 1))
            idx = min(max(idx, 0), len(levels) - 1)
            answers[key] = ScoreAnswer(score=idx, legend={str(i): s for i, s in enumerate(levels)},
                                       probabilities={str(int(idx)): 0.7}, confidence=0.7 if fx else 0.45)
    return DecisionResult(answers=answers, provenance=_prov(route, "fixture-decider", "local",
                                                            fixture_id=fx["id"] if fx else None, reason=reason))


# ---------------------------------------------------------------- vision
def vision(route: str, fixture_id: str | None, phase: str = "before", reason: str | None = None) -> VisionResult:
    fx = fixtures().get(fixture_id or "")
    if not fx:
        # No fixture and no live model: we genuinely do not know. Never invent detections.
        return VisionResult(verdict="unsure", notes="no labelled fixture and no live vision model; human review needed",
                            provenance=_prov(route, "fixture-vision", "local", reason=reason or "no_fixture"))
    t = fx["truth"]
    pts = [VisionPoint(y=p[0], x=p[1], label=p[2] if len(p) > 2 else t.get("hazard", "candidate")) for p in t.get("points", [])]
    verdict = t.get("vision_verdict") or ("glass" if t.get("glass") else ("clear" if not t.get("is_hazard") else "debris"))
    return VisionResult(verdict=verdict, points=pts, est_count=t.get("shards"), notes=fx.get("description", ""),
                        provenance=_prov(route, "fixture-vision", "local", fixture_id=fx["id"], reason=reason))


# ---------------------------------------------------------------- text
def template_text(route: str, facts: dict, reason: str | None = None) -> TextResult:
    kind = facts.get("kind", "summary")
    if kind == "ticket_summary":
        text = (f"{facts.get('severity','?').title()} {facts.get('hazard','hazard')} on {facts.get('street','the lane')} "
                f"(cell {facts.get('cell','unknown')}), {facts.get('reports',1)} report(s). "
                f"Evidence: {facts.get('evidence','none')}. Recommended path: {facts.get('path','review')}.")
    elif kind == "rider_status":
        text = facts.get("message") or f"Thanks — your report {facts.get('number','')} is {facts.get('status','received')}."
    elif kind == "answer":
        bullets = "\n".join(f"- {b}" for b in facts.get("points", []))
        cites = "; ".join(facts.get("citations", []))
        text = f"{facts.get('headline','')}\n{bullets}" + (f"\nSources: {cites}" if cites else "")
    else:
        text = json.dumps(facts, default=str)
    return TextResult(text=text.strip(), provenance=_prov(route, "template-reasoner", "local", reason=reason))

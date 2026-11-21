"""Model router: task route -> live candidate (within budget, breaker closed) -> validated output,
else the labelled offline substitute. Every call is logged to model_calls without prompts or images."""
from __future__ import annotations

import json
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .. import db
from ..config import get_settings
from . import budget, offline
from .google import GoogleClient, Image, ModelError
from .types import (ChoiceAnswer, DecisionResult, EmbeddingResult, NoulAnswer, Provenance, ScoreAnswer, TextResult,
                    VisionPoint, VisionResult)

ROUTES_FILE = Path(__file__).with_name("routes.yaml")
# Fields that would amount to motion/actuation commands. Robotics ER output is parsed for image-space
# observations only; anything like this is discarded and logged.
FORBIDDEN_KEYS = {"waypoints", "trajectory", "speed", "velocity", "steering", "motor", "brake", "throttle", "actuator", "command"}

VISION_SYSTEM = (
    "You inspect still images of a protected bike lane for a municipal maintenance system. Report only what is "
    "visible. Return JSON: {\"verdict\": one of glass|not_glass|unsure|debris|clear, \"points\": [{\"point\": [y, x], "
    "\"label\": str}] with coordinates normalised 0-1000, \"est_count\": int|null, \"notes\": str}. Do not output "
    "navigation, speeds or control commands. If uncertain, answer unsure."
)


@lru_cache(maxsize=1)
def routes() -> dict[str, dict]:
    return yaml.safe_load(ROUTES_FILE.read_text())["routes"]


class ModelRouter:
    def __init__(self, client: GoogleClient | None = None, *, log: bool = True):
        s = get_settings()
        self._client = client or (GoogleClient(s.google_api_key) if s.google_api_key else None)
        self._log = log

    # ------------------------------------------------------------------ plumbing
    def _offline_reason(self) -> str | None:
        s = get_settings()
        if s.ai_mode != "live":
            return "offline_mode"
        if not self._client:
            return "no_api_key"
        if not budget.billable_enabled():
            return "billable_calls_disabled"
        return None

    def _record(self, route: str, model: str, api: str, mode: str, outcome: str, purpose: str, latency_ms: int,
                *, jurisdiction_id: str | None, run_id: str | None, provenance: str, error_class: str | None = None,
                input_tokens: int | None = None, trace_id: str | None = None) -> None:
        if not self._log:
            return
        try:
            db.one("""insert into model_calls (route, model, api, mode, purpose, input_provenance, latency_ms, input_tokens,
                                               outcome, error_class, jurisdiction_id, run_id, trace_id)
                      values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
                   (route, model, api, mode, purpose[:120], provenance[:60], latency_ms, input_tokens, outcome,
                    error_class, jurisdiction_id, run_id, trace_id))
        except Exception:   # logging must never break the workflow
            pass

    def _live(self, route: str, fn, *, ctx: dict) -> tuple[Any, Provenance] | tuple[None, str]:
        cfg = routes()[route]
        reason = self._offline_reason()
        if reason:
            return None, reason
        last = "no_candidates"
        for model in cfg["candidates"]:
            br = budget.breaker(model)
            if not br.allow():
                last = f"breaker_open:{model}"
                continue
            try:
                budget.take(f"model:{model}", cfg.get("per_minute", 30), cfg.get("per_day", 1000))
            except budget.BudgetExceeded as e:
                self._record(route, model, cfg["api"], "live", "quota", cfg["purpose"], 0, error_class=str(e)[:60], **ctx)
                last = "budget"
                continue
            t0 = time.monotonic()
            try:
                value, usage = fn(model, cfg)
                ms = int((time.monotonic() - t0) * 1000)
                br.record(True)
                tok = usage.get("total_input_tokens") or usage.get("promptTokenCount") or usage.get("input_tokens")
                self._record(route, model, cfg["api"], "live", "ok", cfg["purpose"], ms, input_tokens=tok, **ctx)
                return value, Provenance(route=route, model=model, mode="live", api=cfg["api"], latency_ms=ms, input_tokens=tok)
            except (ModelError, ValueError, KeyError, TypeError) as e:
                ms = int((time.monotonic() - t0) * 1000)
                kind = e.kind if isinstance(e, ModelError) else "malformed"
                br.record(kind in ("blocked",))  # a safety block is not a service failure
                outcome = {"quota": "quota", "malformed": "malformed", "blocked": "blocked"}.get(kind, "error")
                self._record(route, model, cfg["api"], "live", outcome, cfg["purpose"], ms, error_class=kind, **ctx)
                last = f"{kind}:{model}"
        return None, last

    @staticmethod
    def _ctx(jurisdiction_id=None, run_id=None, provenance="synthetic", trace_id=None) -> dict:
        return {"jurisdiction_id": jurisdiction_id, "run_id": run_id, "provenance": provenance, "trace_id": trace_id}

    # ------------------------------------------------------------------ decisions
    def decide(self, text: str, questions: dict[str, dict], *, images: list[Image] = (), fixture_id: str | None = None,
               route: str = "evidence.decide", **ctx) -> DecisionResult:
        api_questions = {k: {kk: vv for kk, vv in q.items() if kk != "fixture_key"} for k, q in questions.items()}

        def call(model, cfg):
            out = self._client.decide(model, text, api_questions, list(images), timeout=cfg.get("timeout_s", 20))
            return _parse_answers(out["answers"], questions), out.get("usage", {})

        val, prov = self._live(route, call, ctx=self._ctx(**ctx))
        if val is None:
            return offline.decide(route, text, questions, fixture_id=fixture_id, reason=prov)
        return DecisionResult(answers=val, provenance=prov)

    # ------------------------------------------------------------------ vision
    def vision(self, prompt: str, *, images: list[Image] = (), fixture_id: str | None = None,
               route: str = "vision.localize", **ctx) -> VisionResult:
        if not images:
            return offline.vision(route, fixture_id, reason="no_image")

        def call(model, cfg):
            text, usage = self._client.interact(model, prompt, list(images), thinking_level=cfg.get("thinking_level", "medium"),
                                                system_instruction=VISION_SYSTEM, timeout=cfg.get("timeout_s", 45))
            return parse_vision_json(text), usage

        val, prov = self._live(route, call, ctx=self._ctx(**ctx))
        if val is None:
            return offline.vision(route, fixture_id, reason=prov)
        verdict, points, count, notes = val
        return VisionResult(verdict=verdict, points=points, est_count=count, notes=notes, provenance=prov)

    # ------------------------------------------------------------------ text
    def text(self, prompt: str, *, facts: dict, system: str | None = None, route: str = "reason.agent",
             json_output: bool = False, **ctx) -> TextResult:
        def call(model, cfg):
            t, usage = self._client.generate(model, prompt, system_instruction=system, json_output=json_output,
                                             timeout=cfg.get("timeout_s", 60))
            if not t.strip():
                raise ModelError("malformed", "empty text")
            return t.strip(), usage

        val, prov = self._live(route, call, ctx=self._ctx(**ctx))
        if val is None:
            return offline.template_text(route, facts, reason=prov)
        return TextResult(text=val, provenance=prov)

    # ------------------------------------------------------------------ embeddings
    def embed(self, text: str, *, images: list[Image] = (), prefer_model: str | None = None, **ctx) -> EmbeddingResult:
        """prefer_model pins the vector space: a query must use the same model as the stored vectors."""
        route = "embed"
        if prefer_model == offline.HASH_MODEL:
            return offline.embed(text, reason="pinned_space")
        cfg = routes()[route]
        dim = int(cfg.get("dimensions", 768))

        def call(model, _cfg):
            if prefer_model and model != prefer_model:
                raise ModelError("http", "skipping: different vector space")
            return self._client.embed(model, text, list(images), dimensions=dim), {}

        val, prov = self._live(route, call, ctx=self._ctx(**ctx))
        if val is None:
            return offline.embed(text, reason=prov)
        return EmbeddingResult(vector=val, model=prov.model, dim=dim, version=yaml.safe_load(ROUTES_FILE.read_text())["version"],
                               provenance=prov)


def _parse_answers(raw: dict, questions: dict[str, dict]) -> dict:
    out = {}
    for key, q in questions.items():
        a = raw.get(key)
        if a is None:
            raise ValueError(f"missing answer {key}")
        if q["type"] == "noul":
            out[key] = NoulAnswer(advisory_probability=float(a["noul"]), confidence=float(a["confidence"]))
        elif q["type"] == "choice":
            if a["choice"] not in q["criteria"]:
                raise ValueError("choice outside allowed options")
            out[key] = ChoiceAnswer(choice=a["choice"], probabilities={k: float(v) for k, v in a["probabilities"].items()},
                                    confidence=float(a["confidence"]))
        else:
            out[key] = ScoreAnswer(score=float(a["score"]), legend={str(k): v for k, v in (a.get("legend") or {}).items()},
                                   probabilities={str(k): float(v) for k, v in (a.get("probabilities") or {}).items()},
                                   confidence=float(a["confidence"]))
    return out


_JSON_BLOCK = re.compile(r"\{.*\}", re.S)


def parse_vision_json(text: str) -> tuple[str, list[VisionPoint], int | None, str]:
    """Parse Robotics ER output into image-space observations only."""
    m = _JSON_BLOCK.search(text or "")
    if not m:
        raise ValueError("no JSON object in vision output")
    data = json.loads(m.group(0))
    if not isinstance(data, dict):
        raise ValueError("vision output is not an object")
    dropped = sorted(FORBIDDEN_KEYS & {k.lower() for k in data})
    verdict = str(data.get("verdict", "unsure")).lower()
    if verdict not in ("glass", "not_glass", "unsure", "debris", "clear"):
        verdict = "unsure"
    pts = []
    for p in (data.get("points") or [])[:50]:
        if isinstance(p, dict) and isinstance(p.get("point"), list) and len(p["point"]) == 2:
            y, x = (max(0, min(1000, int(v))) for v in p["point"])
            pts.append(VisionPoint(y=y, x=x, label=str(p.get("label", "candidate"))[:40]))
    count = data.get("est_count")
    notes = str(data.get("notes", ""))[:300]
    if dropped:
        notes = (notes + f" [discarded non-observational fields: {', '.join(dropped)}]").strip()
    return verdict, pts, int(count) if isinstance(count, (int, float)) else None, notes

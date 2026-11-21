"""Typed model results. Every result carries provenance so nothing downstream mistakes a fixture
or a fallback for a live model judgement."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Mode = Literal["live", "offline", "cached"]


class Provenance(BaseModel):
    route: str
    model: str
    mode: Mode
    api: str
    latency_ms: int = 0
    fixture_id: str | None = None
    fallback_reason: str | None = None
    input_tokens: int | None = None


class NoulAnswer(BaseModel):
    type: Literal["noul"] = "noul"
    advisory_probability: float = Field(ge=0, le=1)   # NOT a certified hazard probability
    confidence: float = Field(ge=0, le=1)


class ChoiceAnswer(BaseModel):
    type: Literal["choice"] = "choice"
    choice: str
    probabilities: dict[str, float]
    confidence: float = Field(ge=0, le=1)


class ScoreAnswer(BaseModel):
    type: Literal["score"] = "score"
    score: float
    legend: dict[str, str] = {}
    probabilities: dict[str, float] = {}
    confidence: float = Field(ge=0, le=1)


Answer = NoulAnswer | ChoiceAnswer | ScoreAnswer


class DecisionResult(BaseModel):
    answers: dict[str, Answer]
    provenance: Provenance


class VisionPoint(BaseModel):
    y: int = Field(ge=0, le=1000)
    x: int = Field(ge=0, le=1000)
    label: str


class VisionResult(BaseModel):
    verdict: Literal["glass", "not_glass", "unsure", "debris", "clear"]
    points: list[VisionPoint] = []
    est_count: int | None = None
    notes: str = ""
    provenance: Provenance


class TextResult(BaseModel):
    text: str
    provenance: Provenance


class EmbeddingResult(BaseModel):
    vector: list[float]
    model: str
    dim: int
    version: str
    provenance: Provenance

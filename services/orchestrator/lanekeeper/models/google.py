"""Thin REST client for the Gemini API endpoints LaneKeeper uses. Server-side only.

Endpoints (checked against Google docs 2026-11-21):
  POST /v1beta/decisions                         gemini-decision-1 (Decisions API)
  POST /v1beta/interactions                      gemini-robotics-er-2-preview (Interactions API)
  POST /v1beta/models/{id}:generateContent       gemini-4-hydrogen, gemini-3.5-flash-lite
  POST /v1beta/models/{id}:embedContent          gemini-embedding-2 (output_dimensionality=768)
The API key is sent only in the x-goog-api-key header and never logged.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

import httpx

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class ModelError(Exception):
    def __init__(self, kind: str, message: str, status: int | None = None):
        super().__init__(message)
        self.kind = kind          # 'http' | 'quota' | 'blocked' | 'malformed' | 'timeout' | 'auth'
        self.status = status


@dataclass
class Image:
    data: bytes
    mime_type: str = "image/jpeg"

    @property
    def b64(self) -> str:
        return base64.b64encode(self.data).decode()


def _classify(resp: httpx.Response) -> ModelError:
    try:
        body = resp.json().get("error", {})
    except Exception:
        body = {}
    code = str(body.get("code", ""))
    msg = str(body.get("message", resp.text[:200]))
    if resp.status_code == 429:
        return ModelError("quota", msg, 429)
    if resp.status_code in (401, 403):
        hint = " (Robotics ER rejects unrestricted API keys; restrict the key to the Generative Language API)" \
            if resp.status_code == 403 else ""
        return ModelError("auth", msg + hint, resp.status_code)
    if resp.status_code == 400 and code in ("safety", "prohibited_content", "blocklist", "image_safety", "content_blocked"):
        return ModelError("blocked", code, 400)
    return ModelError("http", msg, resp.status_code)


class GoogleClient:
    def __init__(self, api_key: str, http: httpx.Client | None = None, base_url: str = BASE_URL):
        if not api_key:
            raise ValueError("GoogleClient needs an API key (server-side only)")
        self._key = api_key
        self._http = http or httpx.Client(timeout=60)
        self._base = base_url.rstrip("/")

    def __repr__(self) -> str:   # never leak the key through logs or tracebacks
        return "GoogleClient(<redacted>)"

    def _post(self, path: str, body: dict, timeout: float) -> dict:
        try:
            r = self._http.post(f"{self._base}/{path}", json=body, timeout=timeout,
                                headers={"x-goog-api-key": self._key, "content-type": "application/json"})
        except httpx.TimeoutException as e:
            raise ModelError("timeout", "request timed out") from e
        except httpx.HTTPError as e:
            raise ModelError("http", type(e).__name__) from e
        if r.status_code >= 400:
            raise _classify(r)
        try:
            return r.json()
        except json.JSONDecodeError as e:
            raise ModelError("malformed", "response was not JSON") from e

    # ---- Decisions API -------------------------------------------------------------------------
    def decide(self, model: str, text: str, questions: dict[str, dict], images: list[Image] = (),
               timeout: float = 20) -> dict:
        blocks: list[dict] = [{"type": "text", "text": text}]
        blocks += [{"type": "image", "data": im.b64, "mime_type": im.mime_type, "resolution": "medium"} for im in images]
        out = self._post("decisions", {"model": model, "input": blocks if images else text, "questions": questions}, timeout)
        if "answers" not in out:
            raise ModelError("malformed", "decisions response missing 'answers'")
        return out

    # ---- Interactions API (Robotics ER 2) -----------------------------------------------------
    def interact(self, model: str, prompt: str, images: list[Image] = (), thinking_level: str = "medium",
                 system_instruction: str | None = None, timeout: float = 45) -> tuple[str, dict]:
        parts: list[dict] = [{"type": "image", "data": im.b64, "mime_type": im.mime_type} for im in images]
        parts.append({"type": "text", "text": prompt})
        body: dict[str, Any] = {"model": model, "input": parts,
                                "generation_config": {"thinking_level": thinking_level, "temperature": 0.2}}
        if system_instruction:
            body["system_instruction"] = system_instruction
        out = self._post("interactions", body, timeout)
        return _interaction_text(out), out.get("usage", {})

    # ---- generateContent ----------------------------------------------------------------------
    def generate(self, model: str, prompt: str, images: list[Image] = (), system_instruction: str | None = None,
                 json_output: bool = False, timeout: float = 60) -> tuple[str, dict]:
        parts: list[dict] = [{"text": prompt}] + [{"inline_data": {"mime_type": im.mime_type, "data": im.b64}} for im in images]
        body: dict[str, Any] = {"contents": [{"role": "user", "parts": parts}],
                                "generationConfig": {"temperature": 0.2}}
        if json_output:
            body["generationConfig"]["responseMimeType"] = "application/json"
        if system_instruction:
            body["systemInstruction"] = {"parts": [{"text": system_instruction}]}
        out = self._post(f"models/{model}:generateContent", body, timeout)
        try:
            cand = out["candidates"][0]
            text = "".join(p.get("text", "") for p in cand["content"]["parts"] if not p.get("thought"))
        except (KeyError, IndexError, TypeError) as e:
            reason = (out.get("promptFeedback") or {}).get("blockReason")
            raise ModelError("blocked" if reason else "malformed", reason or "no candidate text") from e
        return text, out.get("usageMetadata", {})

    # ---- embedContent -------------------------------------------------------------------------
    def embed(self, model: str, text: str | None = None, images: list[Image] = (), dimensions: int = 768,
              timeout: float = 20) -> list[float]:
        parts: list[dict] = ([{"text": text}] if text else []) + \
            [{"inline_data": {"mime_type": im.mime_type, "data": im.b64}} for im in images]
        out = self._post(f"models/{model}:embedContent",
                         {"content": {"parts": parts}, "output_dimensionality": dimensions}, timeout)
        values = (out.get("embedding") or {}).get("values") or ((out.get("embeddings") or [{}])[0]).get("values")
        if not values or len(values) != dimensions:
            raise ModelError("malformed", f"expected {dimensions}-dim embedding, got {len(values or [])}")
        return [float(v) for v in values]

    def list_models(self) -> list[str]:
        try:
            r = self._http.get(f"{self._base}/models", headers={"x-goog-api-key": self._key}, params={"pageSize": 1000})
        except httpx.HTTPError as e:
            raise ModelError("http", type(e).__name__) from e
        if r.status_code >= 400:
            raise _classify(r)
        return [m.get("name", "").removeprefix("models/") for m in r.json().get("models", [])]


def _interaction_text(out: dict) -> str:
    """Interactions responses are a list of steps; take the model-output text, skipping thoughts."""
    if isinstance(out.get("output_text"), str):
        return out["output_text"]
    texts: list[str] = []

    def walk(node: Any, in_thought: bool = False) -> None:
        if isinstance(node, dict):
            t = node.get("type")
            thought = in_thought or t in ("thought", "thinking")
            if not thought and t in ("text", "output_text") and isinstance(node.get("text"), str):
                texts.append(node["text"])
            for k, v in node.items():
                if k not in ("text",):
                    walk(v, thought)
        elif isinstance(node, list):
            for v in node:
                walk(v, in_thought)

    walk(out.get("outputs") or out.get("steps") or out)
    if not texts:
        raise ModelError("malformed", "interaction response had no output text")
    return texts[-1]

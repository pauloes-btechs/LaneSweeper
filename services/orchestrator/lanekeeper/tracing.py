"""Agent traces for operators: every node entry/exit, decision, tool call, interrupt and resume is
written to agent_events in plain language. Model reasoning is summarised; secrets and raw evidence are not."""
from __future__ import annotations

import functools
import time
import uuid
from collections.abc import Callable
from typing import Any

from . import db


def start_run(*, workflow: str, workflow_version: str, thread_id: str, jurisdiction_id: str | None,
              actor_id: str | None = None, replay_mode: bool = False) -> tuple[str, str]:
    """Create (or reuse, for the same thread) an agent_runs row. Returns (run_id, trace_id)."""
    row = db.one("""insert into agent_runs (workflow, workflow_version, thread_id, jurisdiction_id, actor_id, trace_id, replay_mode)
                    values (%s,%s,%s,%s,%s,%s,%s)
                    on conflict (thread_id) do update set updated_at = now()
                    returning id::text, trace_id""",
                 (workflow, workflow_version, thread_id, jurisdiction_id, actor_id, uuid.uuid4().hex, replay_mode))
    return row["id"], row["trace_id"]


def event(run_id: str | None, node: str, kind: str, summary: str | None = None, detail: dict | None = None,
          edge_to: str | None = None, jurisdiction_id: str | None = None) -> None:
    if not run_id:
        return
    db.one("""insert into agent_events (run_id, jurisdiction_id, seq, node, kind, edge_to, summary, detail)
              select %s, coalesce(%s, r.jurisdiction_id),
                     coalesce((select max(seq) from agent_events where run_id = %s), 0) + 1, %s, %s, %s, %s, %s
              from agent_runs r where r.id = %s
              returning id""",
           (run_id, jurisdiction_id, run_id, node, kind, edge_to, (summary or "")[:1000], db.jsonb(detail or {}), run_id))


def set_status(run_id: str | None, status: str, *, node: str | None = None, error: str | None = None,
               jurisdiction_id: str | None = None) -> None:
    if not run_id:
        return
    db.one("""update agent_runs set status=%s, current_node=coalesce(%s, current_node), error=%s, updated_at=now(),
                     last_checkpoint_at=now(), jurisdiction_id=coalesce(jurisdiction_id, %s)
              where id=%s returning id""", (status, node, error, jurisdiction_id, run_id))


def traced(name: str) -> Callable:
    """Wrap a graph node. A node may return '_summary' (str) and '_decisions' (list[dict]) for the trace;
    they are stripped before the update reaches graph state."""
    def deco(fn: Callable[[dict], dict]) -> Callable[[dict], dict]:
        @functools.wraps(fn)
        def wrapper(state: dict) -> dict:
            run_id = state.get("run_id")
            jid = state.get("jurisdiction_id")
            event(run_id, name, "enter", jurisdiction_id=jid)
            t0 = time.monotonic()
            try:
                out = fn(state) or {}
            except Exception as e:
                if type(e).__name__ == "GraphInterrupt":   # interrupt() is control flow, not an error
                    event(run_id, name, "interrupt", "waiting for a human decision", jurisdiction_id=jid)
                    set_status(run_id, "waiting_approval" if "approval" in name else "waiting_evidence", node=name)
                    raise
                event(run_id, name, "error", f"{type(e).__name__}: {str(e)[:200]}", jurisdiction_id=jid)
                raise
            out = dict(out)
            summary = out.pop("_summary", None)
            for dec in out.pop("_decisions", []) or []:
                event(run_id, name, "decision", dec.get("summary"), dec, jurisdiction_id=out.get("jurisdiction_id", jid))
            event(run_id, name, "exit", summary, {"ms": int((time.monotonic() - t0) * 1000)},
                  jurisdiction_id=out.get("jurisdiction_id", jid))
            set_status(run_id, "running", node=name, jurisdiction_id=out.get("jurisdiction_id", jid))
            return out
        return wrapper
    return deco


def edge(run_id: str | None, src: str, dst: str, why: str, jurisdiction_id: str | None = None) -> str:
    event(run_id, src, "edge", why, {"to": dst}, edge_to=dst, jurisdiction_id=jurisdiction_id)
    return dst


def detail_safe(d: dict[str, Any]) -> dict[str, Any]:
    """Drop fields that must never land in traces."""
    return {k: v for k, v in d.items() if k not in ("image_b64", "raw_location", "api_key", "token")}

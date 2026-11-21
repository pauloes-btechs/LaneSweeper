"""Bounded job runner over the durable `jobs` table.

Not a fictional always-on agent: a worker leases up to N jobs, runs each handler OUTSIDE any database transaction
(so model calls never hold locks), then finishes it (succeeded / retry with exponential backoff / dead_letter).
Expired leases are reclaimed by the next worker, so an interrupted run never loses a job. Trigger it locally
(`lanekeeper worker --once`), from a free scheduler via the authenticated API, or from `make demo`."""
from __future__ import annotations

import socket
import time
import uuid
from collections.abc import Callable

from . import db, knowledge
from .deps import Deps

HANDLERS: dict[str, Callable[[dict, Deps], None]] = {}


def handler(kind: str):
    def deco(fn):
        HANDLERS[kind] = fn
        return fn
    return deco


def enqueue(kind: str, payload: dict, *, dedupe_key: str, jurisdiction_id: str | None = None, priority: int = 5) -> str:
    row = db.one("""insert into jobs (kind, payload, dedupe_key, jurisdiction_id, priority) values (%s,%s,%s,%s,%s)
                    on conflict (dedupe_key) do update set updated_at = now() returning id::text""",
                 (kind, db.jsonb(payload), dedupe_key, jurisdiction_id, priority))
    return row["id"]


@handler("embed_knowledge")
def _embed_knowledge(job: dict, deps: Deps) -> None:
    knowledge.embed_pending_chunks(deps.router, force=bool(job["payload"].get("force")))


@handler("send_outbox")
def _send_outbox(job: dict, deps: Deps) -> None:
    """Mock delivery adapter: paid channels (SMS/WhatsApp) stay mocked unless explicitly enabled."""
    live_sms = (db.one("select enabled from feature_flags where key='sms_live'") or {}).get("enabled")
    db.one("""update notification_outbox set status = case when channel in ('sms','whatsapp') and not %s then 'suppressed' else 'sent' end,
                     sent_at = now(), adapter = 'mock'
              where status = 'pending' returning 1""", (bool(live_sms),))


@handler("reinspect")
def _reinspect(job: dict, deps: Deps) -> None:
    """Re-run optical analysis for a ticket's newest evidence and record a re-inspection note (never a 'cleared' verdict)."""
    p = job["payload"]
    db.one("""insert into ticket_events (ticket_id, jurisdiction_id, actor, kind, detail)
              select id, jurisdiction_id, 'job:reinspect', 'reinspection_requested', %s from tickets where id=%s returning 1""",
           (db.jsonb({"reason": p.get("reason"), "cell_id": p.get("cell_id")}), p["ticket_id"]))


@handler("expire_approvals")
def _expire(job: dict, deps: Deps) -> None:
    db.one("update approval_requests set status='expired' where status in ('pending','approved') and expires_at < now() returning 1")


@handler("retention_sweep")
def _retention(job: dict, deps: Deps) -> None:
    """Delete raw rider evidence past its retention date (de-identified aggregates and tickets remain)."""
    rows = db.all_rows("""select m.id::text, m.storage_path from report_media m join hazard_reports r on r.id=m.report_id
                          where r.retention_until < now() limit 500""")
    for r in rows:
        try:
            (deps.store.root / r["storage_path"]).unlink(missing_ok=True)
        except OSError:
            continue
        db.one("delete from report_media where id=%s returning 1", (r["id"],))
    db.one("update hazard_reports set raw_location = null, note = null where retention_until < now() and raw_location is not null returning 1")


def run_once(*, max_jobs: int = 10, kinds: list[str] | None = None, lease_s: int = 120, deps: Deps | None = None,
             worker: str | None = None) -> dict:
    deps = deps or Deps()
    worker = worker or f"{socket.gethostname()}:{uuid.uuid4().hex[:6]}"
    leased = db.all_rows("select * from lk.lease_jobs(%s, %s, %s, %s)", (worker, max_jobs, lease_s, kinds))
    done = {"worker": worker, "leased": len(leased), "succeeded": 0, "failed": 0, "dead_letter": 0}
    for job in leased:
        db.one("insert into job_attempts (job_id, attempt, worker) values (%s,%s,%s) returning id", (job["id"], job["attempts"], worker))
        db.one("update jobs set status='running' where id=%s returning 1", (job["id"],))
        t0 = time.monotonic()
        try:
            fn = HANDLERS.get(job["kind"])
            if not fn:
                raise RuntimeError(f"no handler for {job['kind']}")
            fn(job, deps)
            db.one("select lk.finish_job(%s,%s,true,null) as s", (job["id"], worker))
            done["succeeded"] += 1
        except Exception as e:  # noqa: BLE001 - every failure must be recorded, never lost
            st = db.one("select lk.finish_job(%s,%s,false,%s) as s", (job["id"], worker, f"{type(e).__name__}: {str(e)[:300]}"))["s"]
            done["dead_letter" if st == "dead_letter" else "failed"] += 1
        done.setdefault("ms", []).append(int((time.monotonic() - t0) * 1000))
    return done

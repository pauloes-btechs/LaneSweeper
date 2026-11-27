"""Intake Agent: schema, source, consent, attachments, idempotency, rate limits. Deterministic, no model."""
from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from .. import db
from ..config import get_settings
from ..deps import Deps
from ..graphs.state import InboundReport
from ..hashing import short_hash
from ..storage import EvidenceError, decode_image, fixture_bytes

DEVICE_SOURCES = {"glasses_device", "robot"}
STAFF_SOURCES = {"operator"}


def ingest_event(state: dict, deps: Deps) -> dict:
    try:
        rep = InboundReport.model_validate(state.get("report") or {})
    except ValidationError as e:
        msg = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())[:400]
        return {"outcome": "rejected", "errors": [{"node": "ingest_event", "error": msg}],
                "_summary": f"Rejected: report failed validation ({msg})"}
    data = rep.model_dump(mode="json")
    return {"report": data, "idempotency_key": rep.idempotency_key, "event_id": rep.external_event_id or rep.idempotency_key,
            "source_provenance": rep.provenance, "simulation_mode": True,
            "_summary": f"Accepted {rep.source_kind} report {rep.idempotency_key[:16]}… (schema valid)"}


def validate_identity_and_source(state: dict, deps: Deps) -> dict:
    if state.get("outcome") == "rejected":
        return {}
    rep = state["report"]
    actor = state.get("actor_id")
    kind = rep["source_kind"]
    salt = get_settings().signing_secret
    # Guests are keyed by client IP, never by the client-chosen idempotency key. Only in-process callers have no IP.
    reporter_hash = short_hash(actor or rep.get("device_id") or state.get("client_ip") or rep["idempotency_key"], salt)
    if kind in STAFF_SOURCES and not actor:
        return _reject("operator reports require an authenticated staff member")
    if kind in DEVICE_SOURCES:
        dev = db.one("select trust_level, revoked_at from source_devices where id = %s", (rep.get("device_id"),)) \
            if rep.get("device_id") else None
        if not dev or dev["revoked_at"] or dev["trust_level"] == "unverified":
            return _reject(f"{kind} reports require a registered, unrevoked device")
    if rep.get("agent_assist") and not actor:
        return _reject("agent-assisted reports must come from a signed-in rider or a paired device")
    if not actor:
        n = db.one("""select count(*) as n from hazard_reports
                      where reporter_hash = %s and received_at > now() - interval '1 hour'""", (reporter_hash,))["n"]
        if n >= deps.guest_reports_per_hour:
            return _reject("rate limit: too many reports from this reporter in the last hour")
    role = "authorized_reporter" if actor else "public_rider"
    agent = rep.get("agent_assist")
    via = f"; drafted by the rider's agent, approved by the rider via {agent['channel']}" if agent else ""
    return {"actor_role": state.get("actor_role") or role, "report": {**rep, "reporter_hash": reporter_hash},
            "_summary": f"Source {kind} accepted as {role}{via}; consent recorded for "
                        f"{'image ' if (rep.get('consent') or {}).get('share_image') else ''}"
                        f"{'location' if (rep.get('consent') or {}).get('share_location') else ''}".strip()}


def _reject(reason: str) -> dict:
    return {"outcome": "rejected", "errors": [{"node": "validate_identity_and_source", "error": reason}],
            "_summary": f"Rejected: {reason}"}


def normalize_report(state: dict, deps: Deps) -> dict:
    """Persist the report exactly once (idempotency_key), its consent, and its media."""
    if state.get("outcome") == "rejected":
        return {}
    rep = state["report"]
    loc = rep.get("location") or {}
    with db.tx() as cur:
        cur.execute("select id::text, jurisdiction_id::text, status from hazard_reports where idempotency_key = %s",
                    (rep["idempotency_key"],))
        existing = cur.fetchone()
        if existing:
            return {"report_id": existing["id"], "outcome": "duplicate_request",
                    "_summary": "Same idempotency key seen before: returning the original report, no new work"}
        consent_id = None
        if rep.get("consent"):
            c = rep["consent"]
            cur.execute("""insert into report_consent (subject_hash, policy_version, share_image, share_location, allow_learning, notify_status)
                           values (%s,%s,%s,%s,%s,%s) returning id::text""",
                        (rep["reporter_hash"], c["policy_version"], c["share_image"], c["share_location"],
                         c["allow_learning"], c["notify_status"]))
            consent_id = cur.fetchone()["id"]
        cur.execute(
            """insert into hazard_reports (source_kind, source_device_id, reporter_user_id, reporter_hash, idempotency_key,
                   external_event_id, consent_id, raw_location, accuracy_m, heading_deg, speed_mps, location_source, observed_at,
                   hazard_claimed, note, local_detector, provenance, agent_assisted, rider_approval)
               values (%s,%s,%s,%s,%s,%s,%s,
                       case when %s::float8 is null then null else extensions.st_setsrid(extensions.st_makepoint(%s, %s), 4326)::extensions.geography end,
                       %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               returning id::text""",
            (rep["source_kind"], rep.get("device_id") if rep["source_kind"] in DEVICE_SOURCES else None, state.get("actor_id"),
             rep["reporter_hash"], rep["idempotency_key"], rep.get("external_event_id"), consent_id,
             loc.get("lat"), loc.get("lng"), loc.get("lat"), loc.get("accuracy_m"), loc.get("heading_deg"), loc.get("speed_mps"),
             loc.get("source") if loc else "none", rep.get("observed_at"), rep.get("hazard_claimed"), rep.get("note"),
             db.jsonb(rep.get("local_detector")) if rep.get("local_detector") else None, rep.get("provenance", "SIMULATED"),
             bool(rep.get("agent_assist")), db.jsonb(rep["agent_assist"]) if rep.get("agent_assist") else None))
        report_id = cur.fetchone()["id"]
    evidence_ids, media_note = _store_media(state, deps, report_id)
    return {"report_id": report_id, "evidence_ids": evidence_ids,
            "location": {"lat": loc.get("lat"), "lng": loc.get("lng")} if loc else {},
            "location_accuracy_m": loc.get("accuracy_m"),
            "_summary": f"Report stored once (id {report_id[:8]}); {media_note}"}


def _store_media(state: dict, deps: Deps, report_id: str) -> tuple[list[str], str]:
    rep = state["report"]
    data: bytes | None = None
    mime = rep.get("image_mime")
    if rep.get("image_b64"):
        data = decode_image(rep["image_b64"])
    elif rep.get("fixture_id"):
        fx = fixture_bytes(rep["fixture_id"])
        if fx:
            data, mime = fx
    if data is None:
        return [], "no image attached"
    try:
        path, sha = deps.store.put(data, mime, jurisdiction_slug="unassigned")
    except EvidenceError as e:
        return [], f"image refused ({e})"
    row = db.one("""insert into report_media (report_id, storage_path, mime_type, bytes, sha256, fixture_id)
                    values (%s,%s,%s,%s,%s,%s) on conflict (report_id, sha256) do update set fixture_id = excluded.fixture_id
                    returning id::text""", (report_id, path, mime, len(data), sha, rep.get("fixture_id")))
    label = f"labelled fixture {rep['fixture_id']}" if rep.get("fixture_id") else "uploaded image"
    return [row["id"]], f"{label} stored privately"


def load_images(state: dict, deps: Deps) -> list[Any]:
    """Load the report's images for model calls (bytes are never put into graph state)."""
    from ..models.google import Image
    if not state.get("report_id"):
        return []
    rows = db.all_rows("select storage_path, mime_type from report_media where report_id = %s", (state["report_id"],))
    return [Image(deps.store.get(r["storage_path"]), r["mime_type"]) for r in rows]

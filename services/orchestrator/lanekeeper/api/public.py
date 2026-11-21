"""Public, rider and device endpoints. No private evidence, raw locations or staff notes leave through here."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import ValidationError

from .. import db, devices, workflows
from ..auth import Principal
from ..graphs import glasses
from ..graphs.state import InboundReport
from ..models.offline import fixtures
from ..storage import REPO_ROOT, LocalEvidenceStore
from .common import principal

router = APIRouter()


@router.get("/health")
def health():
    db.one("select 1 as ok")
    from ..config import get_settings
    s = get_settings()
    return {"ok": True, "ai_mode": s.ai_mode, "live_models": s.live_models, "hardware_gateway": False, "provenance": "SIMULATED demo"}


@router.get("/v1/public/jurisdictions")
def jurisdictions():
    return db.all_rows("select id::text, slug, name, synthetic from jurisdictions order by name")


@router.get("/v1/public/hazards")
def public_hazards(jurisdiction: str = "nyc"):
    """Generalised cell-level map: condition per 25 m cell, day-level timestamps, no reports or riders."""
    return db.all_rows("""select h.cell_id, h.condition, h.updated_day, h.centroid, h.provenance
                            from public_hazard_cells h join jurisdictions j on j.id = h.jurisdiction_id
                           where j.slug = %s and h.condition <> 'unknown'""", (jurisdiction,))


@router.get("/v1/public/lanes")
def public_lanes(jurisdiction: str = "nyc"):
    rows = db.all_rows("""select c.id, c.seq, extensions.st_asgeojson(c.geom::extensions.geometry, 6)::jsonb geom
                            from lane_cells c join jurisdictions j on j.id = c.jurisdiction_id where j.slug=%s order by c.seq""",
                       (jurisdiction,))
    return {"type": "FeatureCollection",
            "features": [{"type": "Feature", "id": r["id"], "properties": {"cell_id": r["id"], "seq": r["seq"]}, "geometry": r["geom"]}
                         for r in rows]}


@router.get("/v1/public/run-reports")
def run_reports(limit: int = 20):
    return db.all_rows("select slug, service_date, summary, provenance, generated_at from public_run_reports "
                       "order by generated_at desc limit %s", (min(limit, 100),))


@router.get("/v1/public/run-reports/{slug}")
def run_report(slug: str):
    r = db.one("select slug, service_date, summary, provenance, generated_at from public_run_reports where slug=%s", (slug,))
    if not r:
        raise HTTPException(404, "not found")
    return r


@router.get("/v1/fixtures")
def list_fixtures():
    return [{"id": f["id"], "description": f["description"], "url": f"/v1/fixtures/{f['id']}.png", "provenance": "SIMULATED"}
            for f in fixtures().values()]


@router.get("/v1/fixtures/{fid}.png")
def fixture_image(fid: str):
    if fid not in fixtures():
        raise HTTPException(404, "unknown fixture")
    return FileResponse(REPO_ROOT / "fixtures" / "images" / f"{fid}.png", media_type="image/png")


@router.post("/v1/reports")
def submit_report(body: dict, p: Principal = Depends(principal)):
    """Rider app / guest report. Validated, rate-limited, idempotent. Returns status, never other people's data."""
    try:
        rep = InboundReport.model_validate(body)
    except ValidationError as e:
        raise HTTPException(422, json.loads(e.json(include_input=False))) from e
    if rep.source_kind not in ("rider_app", "rider_guest"):
        raise HTTPException(400, "this endpoint accepts rider reports only")
    r = workflows.submit_report(rep.model_dump(mode="json"), p)
    return {"report_id": r["report_id"], "status": "rejected" if r["outcome"] == "rejected" else "received",
            "ticket_number": r["ticket_number"], "needs_more_evidence": bool(r["interrupt"] and r["interrupt"].get("awaiting")),
            "errors": [e["error"] for e in r["errors"]] if r["outcome"] == "rejected" else []}


def _preview(body: dict) -> dict:
    from ..rider_assist import preview
    try:
        return preview(float(body["lat"]), float(body["lng"]),
                       None if body.get("accuracy_m") is None else float(body["accuracy_m"]), body.get("hazard_claimed"))
    except (KeyError, TypeError, ValueError) as e:
        raise HTTPException(422, f"lat, lng (and optional accuracy_m, hazard_claimed) required: {e}") from e


@router.post("/v1/reports/preview")
def preview_report(body: dict, p: Principal = Depends(principal)):
    """Read-only: where a rider-agent draft would land and whether it is already reported. Signed-in riders only."""
    if p.anonymous:
        raise HTTPException(401, "sign in to use the reporting assistant")
    return _preview(body)


@router.get("/v1/reports/mine")
def my_reports(p: Principal = Depends(principal)):
    if p.anonymous:
        raise HTTPException(401, "sign in to see report history")
    return db.all_rows("""select r.id::text, r.received_at, r.hazard_claimed, r.status, r.match_status, t.number ticket, t.status ticket_status
                            from hazard_reports r left join incident_report_links l on l.report_id = r.id
                            left join tickets t on t.incident_id = l.incident_id
                           where r.reporter_user_id = %s order by r.received_at desc limit 50""", (p.user_id,))


@router.post("/v1/devices/register")
def register_device(body: dict, p: Principal = Depends(principal)):
    if p.anonymous:
        raise HTTPException(401, "sign in to pair a device")
    return devices.register(p, body.get("kind", "glasses_sim"), body.get("label", "glasses"), body.get("capabilities") or {})


@router.post("/v1/devices/{device_id}/events")
async def device_event(device_id: str, request: Request):
    raw = await request.body()
    devices.verify(device_id, request.headers.get("x-lk-timestamp"), request.headers.get("x-lk-signature"), raw)
    try:
        body = json.loads(raw)
        body["device_id"] = device_id
        rep = InboundReport.model_validate(body)
    except (ValueError, ValidationError) as e:
        raise HTTPException(422, str(e)[:300]) from e
    owner = db.one("select owner_user_id::text from source_devices where id=%s", (device_id,))
    return glasses.handle_device_event(rep.model_dump(mode="json"), owner and owner["owner_user_id"])


@router.post("/v1/devices/{device_id}/drafts/preview")
async def device_draft_preview(device_id: str, request: Request):
    """Read-only preview for a draft the device's agent made; same signature scheme as device events."""
    raw = await request.body()
    devices.verify(device_id, request.headers.get("x-lk-timestamp"), request.headers.get("x-lk-signature"), raw)
    try:
        body = json.loads(raw)
    except ValueError as e:
        raise HTTPException(422, "invalid JSON") from e
    return _preview(body)


@router.get("/v1/devices/{device_id}/hud")
async def device_hud(device_id: str, request: Request, since: str | None = None):
    devices.verify(device_id, request.headers.get("x-lk-timestamp"), request.headers.get("x-lk-signature"), b"")
    return db.all_rows("""select id::text, body, created_at from notification_outbox where channel='glasses_hud' and recipient_ref=%s
                          and (%s::timestamptz is null or created_at > %s) order by created_at limit 20""", (device_id, since, since))


@router.get("/v1/evidence/{path:path}")
def evidence(path: str, exp: int, sig: str):
    if not LocalEvidenceStore.check_url(path, exp, sig):
        raise HTTPException(403, "expired or invalid link")
    root = LocalEvidenceStore().root.resolve()
    target = (root / path).resolve()
    if root not in target.parents or not target.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(target)

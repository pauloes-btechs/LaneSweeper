"""Rider-agent reports: the device agent drafts, the rider authorises, the server enforces the authorisation."""
import json
import time
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from pydantic import ValidationError
from test_api import H, client, rider_report  # noqa: F401  (fixtures/helpers shared with the API tests)

from lanekeeper import db, devices
from lanekeeper.graphs.state import InboundReport

pytestmark = pytest.mark.db


def approval(drafted_ago_s=60, approved_ago_s=5, channel="phone_tap", **kw):
    now = datetime.now(UTC)
    a = {"draft_id": f"draft-{uuid.uuid4().hex[:10]}", "drafted_at": (now - timedelta(seconds=drafted_ago_s)).isoformat(),
         "approved_at": (now - timedelta(seconds=approved_ago_s)).isoformat(), "channel": channel, "auto_captured_frames": 3}
    a.update(kw)
    return a


def agent_report(seq=150, **kw):
    return rider_report(seq=seq, source_kind="rider_app", agent_assist=approval(), **kw)


@pytest.mark.parametrize("bad, why", [
    (dict(drafted_ago_s=10, approved_ago_s=60), "before it was drafted"),
    (dict(drafted_ago_s=20 * 60, approved_ago_s=1), "expired"),
    (dict(drafted_ago_s=60, approved_ago_s=-600), "future"),
    (dict(channel="automatic"), "channel"),
])
def test_rider_approval_must_be_real_and_timely(bad, why):
    with pytest.raises(ValidationError, match=why):
        InboundReport.model_validate(rider_report(source_kind="rider_app", agent_assist=approval(**bad)))


def test_naive_timestamps_rejected():
    a = approval()
    a["approved_at"] = a["approved_at"].replace("+00:00", "")
    with pytest.raises(ValidationError, match="timezone"):
        InboundReport.model_validate(rider_report(source_kind="rider_app", agent_assist=a))


def test_guest_source_cannot_be_agent_assisted():
    with pytest.raises(ValidationError, match="rider app or paired glasses"):
        InboundReport.model_validate(rider_report(source_kind="rider_guest", agent_assist=approval()))


def test_anonymous_agent_report_is_rejected(client):  # noqa: F811
    out = client.post("/v1/reports", json=agent_report(seq=151)).json()
    assert out["status"] == "rejected" and "signed-in rider" in out["errors"][0]


def test_signed_in_rider_approved_agent_report_is_stored_with_its_approval(client):  # noqa: F811
    body = agent_report(seq=152)
    out = client.post("/v1/reports", json=body, headers=H("rider")).json()
    assert out["status"] == "received" and out["ticket_number"]
    row = db.one("select agent_assisted, rider_approval from hazard_reports where id=%s", (out["report_id"],))
    assert row["agent_assisted"] is True
    assert row["rider_approval"]["channel"] == "phone_tap" and row["rider_approval"]["approver"] == "rider"
    assert row["rider_approval"]["draft_id"] == body["agent_assist"]["draft_id"]


def test_database_refuses_agent_report_without_rider_approval(fresh_db):
    for bad in (None, {"channel": "phone_tap", "approver": "rider"}, {"drafted_at": "x", "approved_at": "y", "channel": "voice",
                                                                    "approver": "agent"}):
        with pytest.raises(psycopg.errors.CheckViolation):
            with db.tx() as cur:
                cur.execute("""insert into hazard_reports (source_kind, idempotency_key, agent_assisted, rider_approval)
                               values ('rider_app', %s, true, %s)""", (f"chk-{uuid.uuid4().hex[:10]}", db.jsonb(bad) if bad else None))


def _signed(device_id, payload):
    body = json.dumps(payload).encode()
    ts = int(time.time())
    return body, {"x-lk-timestamp": str(ts), "x-lk-signature": devices.sign(device_id, ts, body), "content-type": "application/json"}


def test_paired_glasses_agent_report_with_glasses_button_approval(client):  # noqa: F811
    reg = client.post("/v1/devices/register", json={"kind": "glasses_sim", "label": "SIMULATED GLASSES"}, headers=H("rider")).json()
    ev = rider_report(seq=154, source_kind="glasses_sim", agent_assist=approval(channel="glasses_button"),
                      local_detector={"label": "possible_glass", "score": 0.8, "consecutive_frames": 4, "warning_issued": True})
    body, hdr = _signed(reg["device_id"], ev)
    out = client.post(f"/v1/devices/{reg['device_id']}/events", content=body, headers=hdr).json()
    assert out["ticket_number"]
    row = db.one("select agent_assisted, rider_approval->>'channel' ch from hazard_reports where idempotency_key=%s",
                 (ev["idempotency_key"],))
    assert row["agent_assisted"] and row["ch"] == "glasses_button"


def test_preview_is_read_only_and_finds_existing_report(client):  # noqa: F811
    lat, lng = db.one("""select extensions.st_y(extensions.st_centroid(geom::extensions.geometry)) lat,
                                extensions.st_x(extensions.st_centroid(geom::extensions.geometry)) lng
                           from lane_cells where id='MN-1AV-0160'""").values()
    q = {"lat": lat, "lng": lng, "accuracy_m": 8, "hazard_claimed": "glass"}
    assert client.post("/v1/reports/preview", json=q).status_code == 401           # anonymous: no assistant
    n0 = db.one("select count(*) n from hazard_reports")["n"]
    first = client.post("/v1/reports/preview", json=q, headers=H("rider")).json()
    assert first["in_network"] and first["cell_id"] and first["nearby_open"] is None
    assert db.one("select count(*) n from hazard_reports")["n"] == n0              # preview wrote nothing
    client.post("/v1/reports", json=agent_report(seq=160), headers=H("rider"))
    again = client.post("/v1/reports/preview", json=q, headers=H("rider")).json()
    assert again["nearby_open"]["ticket_number"].startswith("LK-") and "Already reported" in again["advice"]
    off = client.post("/v1/reports/preview", json={"lat": 40.70, "lng": -74.0}, headers=H("rider")).json()
    assert off["in_network"] is False
    assert client.post("/v1/reports/preview", json={"lat": "x"}, headers=H("rider")).status_code == 422


def test_device_preview_requires_valid_signature(client):  # noqa: F811
    reg = client.post("/v1/devices/register", json={"kind": "glasses_sim", "label": "SIMULATED GLASSES"}, headers=H("rider")).json()
    body, hdr = _signed(reg["device_id"], {"lat": 40.7584, "lng": -73.9621, "accuracy_m": 10})
    assert client.post(f"/v1/devices/{reg['device_id']}/drafts/preview", content=body, headers=hdr).json()["in_network"]
    bad = {**hdr, "x-lk-signature": "0" * 64}
    assert client.post(f"/v1/devices/{reg['device_id']}/drafts/preview", content=body, headers=bad).status_code == 401

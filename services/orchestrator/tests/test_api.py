"""HTTP API: authentication, tenant isolation, public data minimisation, rider/device intake, approvals over HTTP."""
import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from lanekeeper import auth, db, devices

from conftest import USERS

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def client(fresh_db):
    import os
    os.environ["LK_DEMO_MODE"] = "1"
    from lanekeeper.api.app import create_app
    return TestClient(create_app())


def H(who):
    return {"authorization": f"Bearer {auth.mint_dev_token(USERS[who])}"}


@pytest.fixture(autouse=True)
def _fleet(fresh_db):
    db.one("update robot_status set state='docked', connectivity='online', battery_pct=100, hopper_pct=0, mission_id=null returning 1")
    db.one("update municipal_memberships set status='active' returning 1")


def rider_report(seq=145, **kw):
    lat, lng = db.one("""select extensions.st_y(extensions.st_centroid(geom::extensions.geometry)) lat,
                                extensions.st_x(extensions.st_centroid(geom::extensions.geometry)) lng from lane_cells where id=%s""",
                      (f"MN-1AV-{seq:04d}",)).values()
    r = {"idempotency_key": f"api-{uuid.uuid4().hex[:12]}", "source_kind": "rider_guest", "hazard_claimed": "glass",
         "note": "glass after the light", "fixture_id": "glass-1av-dawn-01",
         "location": {"lat": lat, "lng": lng, "accuracy_m": 7, "source": "phone_gps"},
         "consent": {"policy_version": "rider-2026.11", "share_image": True, "share_location": True}}
    r.update(kw)
    return r


def test_health_reports_no_hardware(client):
    r = client.get("/health").json()
    assert r["ok"] and r["hardware_gateway"] is False


def test_staff_routes_require_auth_and_ignore_forged_claims(client):
    assert client.get("/v1/tickets").status_code == 401
    import jwt
    forged = jwt.encode({"sub": USERS["rider"], "aud": "authenticated", "iss": "lanekeeper-dev", "role": "service_role",
                         "app_role": "city_admin"}, "not-the-secret")
    assert client.get("/v1/tickets", headers={"authorization": f"Bearer {forged}"}).status_code == 401
    assert client.get("/v1/tickets", headers=H("rider")).status_code == 403


def test_guest_rider_report_and_public_map_minimisation(client):
    r = client.post("/v1/reports", json=rider_report()).json()
    assert r["status"] == "received" and r["ticket_number"]
    pub = client.get("/v1/public/hazards?jurisdiction=nyc").json()
    assert pub and set(pub[0]) == {"cell_id", "condition", "updated_day", "centroid", "provenance"}
    blob = json.dumps(pub)
    assert "note" not in blob and "glass after the light" not in blob


def test_report_without_location_consent_is_rejected_by_schema(client):
    bad = rider_report()
    bad["consent"]["share_location"] = False
    assert client.post("/v1/reports", json=bad).status_code == 422


def test_rider_endpoint_refuses_spoofed_source(client):
    assert client.post("/v1/reports", json=rider_report(source_kind="robot")).status_code == 400


def test_cross_tenant_ticket_access_is_404_and_jurisdiction_param_checked(client):
    client.post("/v1/reports", json=rider_report(seq=146))
    num = db.one("select number from tickets where jurisdiction_id='11111111-1111-4111-8111-111111111111' order by created_at desc limit 1")["number"]
    assert client.get(f"/v1/tickets/{num}", headers=H("nyc_operator")).status_code == 200
    assert client.get(f"/v1/tickets/{num}", headers=H("harbor_supervisor")).status_code == 404
    r = client.get("/v1/tickets?jurisdiction=11111111-1111-4111-8111-111111111111", headers=H("harbor_supervisor"))
    assert r.status_code == 403


def test_viewer_sees_ticket_but_not_evidence(client):
    num = db.one("select number from tickets where jurisdiction_id='11111111-1111-4111-8111-111111111111' order by created_at desc limit 1")["number"]
    t = client.get(f"/v1/tickets/{num}", headers=H("nyc_viewer")).json()
    assert t["reports"] == []
    t2 = client.get(f"/v1/tickets/{num}", headers=H("nyc_operator")).json()
    assert t2["reports"] and t2["reports"][0]["media"][0]["url"].startswith("/v1/evidence/")


def test_signed_evidence_url_works_and_tampered_fails(client):
    num = db.one("select number from tickets where jurisdiction_id='11111111-1111-4111-8111-111111111111' order by created_at desc limit 1")["number"]
    url = client.get(f"/v1/tickets/{num}", headers=H("nyc_operator")).json()["reports"][0]["media"][0]["url"]
    assert client.get(url).status_code == 200
    assert client.get(url.replace("sig=", "sig=0")).status_code == 403


def test_full_approval_flow_over_http(client):
    client.post("/v1/reports", json=rider_report(seq=175))
    appr = client.get("/v1/approvals", headers=H("nyc_supervisor")).json()
    a = next(x for x in appr if x["status"] == "pending")
    assert a["estimates"]["label"].startswith("ESTIMATE")
    assert client.post(f"/v1/approvals/{a['id']}/decide", json={"approve": True, "reason": "x"}, headers=H("nyc_supervisor")).status_code == 422
    assert client.post(f"/v1/approvals/{a['id']}/decide", json={"approve": True, "reason": "op cannot approve"},
                       headers=H("nyc_operator")).status_code == 403
    ok = client.post(f"/v1/approvals/{a['id']}/decide", json={"approve": True, "reason": "estimates reviewed"}, headers=H("nyc_supervisor"))
    assert ok.status_code == 200
    out = client.post(f"/v1/threads/{a['thread_id']}/resume", json={"kind": "approval"}, headers=H("nyc_supervisor")).json()
    assert out["outcome"] == "verified_closed"
    m = client.get(f"/v1/missions/{out['mission_id']}", headers=H("nyc_supervisor")).json()
    assert m["telemetry"] and m["verdicts"][0]["verdict"] == "cleared"
    assert m["thread_id"] == a["thread_id"]        # console needs it to resolve safety pauses
    viewer_view = client.get(f"/v1/missions/{out['mission_id']}", headers=H("nyc_viewer")).json()
    assert "telemetry" not in viewer_view          # viewers lack telemetry.view
    slug = client.get("/v1/missions", headers=H("nyc_supervisor")).json()[0]["report_slug"]
    rep = client.get(f"/v1/public/run-reports/{slug}").json()
    assert rep["provenance"] == "SIMULATED" and "approved_by" not in json.dumps(rep)
    run_id = db.one("select id::text from agent_runs where thread_id=%s", (a["thread_id"],))["id"]
    ev = client.get(f"/v1/runs/{run_id}/events", headers=H("nyc_operator")).json()
    assert any(e["kind"] == "interrupt" for e in ev["events"])


def test_signed_device_event_flow(client):
    reg = client.post("/v1/devices/register", json={"kind": "glasses_sim", "label": "SIMULATED GLASSES"}, headers=H("rider")).json()
    ev = rider_report(seq=110, source_kind="glasses_sim", local_detector={"label": "possible_glass", "score": 0.8,
                                                                         "consecutive_frames": 4, "warning_issued": True})
    body = json.dumps(ev).encode()
    ts = int(time.time())
    hdr = {"x-lk-timestamp": str(ts), "x-lk-signature": devices.sign(reg["device_id"], ts, body), "content-type": "application/json"}
    assert client.post(f"/v1/devices/{reg['device_id']}/events", content=body, headers={**hdr, "x-lk-signature": "0" * 64}).status_code == 401
    out = client.post(f"/v1/devices/{reg['device_id']}/events", content=body, headers=hdr).json()
    assert out["ticket_number"]


def test_demo_token_endpoint_gives_identity_only(client):
    tok = client.post("/v1/demo/token/viewer").json()["token"]
    r = client.post("/v1/fleet/plan", json={}, headers={"authorization": f"Bearer {tok}"})
    assert r.status_code == 403


def test_console_safety_resume_path_over_http(client):
    run = client.post("/v1/demo/scenario/G").json()       # twin scenario "disconnected"
    a = next(x for x in client.get("/v1/approvals", headers=H("nyc_supervisor")).json()
             if x["status"] == "pending" and x["thread_id"] == run["thread_id"])
    client.post(f"/v1/approvals/{a['id']}/decide", json={"approve": True, "reason": "estimates reviewed"}, headers=H("nyc_supervisor"))
    paused = client.post(f"/v1/threads/{a['thread_id']}/resume", json={"kind": "approval"}, headers=H("nyc_supervisor")).json()
    m = client.get(f"/v1/missions/{paused['mission_id']}", headers=H("nyc_supervisor")).json()
    assert m["status"] == "paused_safety"
    body = {"kind": "safety", "action": "resume", "link_restored": True, "jam_cleared": False}
    assert client.post(f"/v1/threads/{m['thread_id']}/resume", json=body, headers=H("nyc_operator")).status_code == 403
    done = client.post(f"/v1/threads/{m['thread_id']}/resume", json=body, headers=H("nyc_supervisor")).json()
    assert done["status"] == "done"

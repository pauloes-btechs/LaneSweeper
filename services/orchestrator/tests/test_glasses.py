"""Glasses device link, consent gating, debounce, de-identification and handoff to the ticket graph."""
import base64
import io
import time
import uuid

import pytest
from PIL import Image

from conftest import USERS
from lanekeeper import auth, db, devices
from lanekeeper.graphs import glasses
from lanekeeper.privacy import minimise_frame

pytestmark = pytest.mark.db


def event(seq=150, *, frames=4, score=0.8, image=True, location=True, notify=True, fixture="glass-1av-dawn-01"):
    lat, lng = db.one("""select extensions.st_y(extensions.st_centroid(geom::extensions.geometry)) lat,
                                extensions.st_x(extensions.st_centroid(geom::extensions.geometry)) lng
                           from lane_cells where id=%s""", (f"MN-1AV-{seq:04d}",)).values()
    e = {"idempotency_key": f"g-{uuid.uuid4().hex[:12]}", "source_kind": "glasses_sim", "hazard_claimed": "glass",
         "consent": {"policy_version": "rider-2026.11", "share_image": image, "share_location": location, "notify_status": notify},
         "local_detector": {"label": "possible_glass", "score": score, "consecutive_frames": frames, "model": "lk-local-heuristic-v1",
                            "warning_issued": frames >= 3 and score >= 0.55}}
    if location:
        e["location"] = {"lat": lat, "lng": lng, "accuracy_m": 9, "source": "phone_gps", "speed_mps": 0.0}
    if image and fixture:
        e["fixture_id"] = fixture
    return e


@pytest.fixture(autouse=True)
def _fleet(fresh_db):
    db.one("update robot_status set state='docked', connectivity='online', battery_pct=100 returning 1")


def test_device_signature_roundtrip_and_tamper():
    owner = auth.Principal(USERS["rider"])
    reg = devices.register(owner, "glasses_sim", "SIMULATED GLASSES #1", {"hud": True, "audio": True})
    body = b'{"hello":"lane"}'
    ts = int(time.time())
    sig = devices.sign(reg["device_id"], ts, body)
    assert devices.verify(reg["device_id"], ts, sig, body)["kind"] == "glasses_sim"
    with pytest.raises(devices.DeviceAuthError, match="signature"):
        devices.verify(reg["device_id"], ts, sig, body + b" ")
    with pytest.raises(devices.DeviceAuthError, match="window"):
        devices.verify(reg["device_id"], ts - 3600, devices.sign(reg["device_id"], ts - 3600, body), body)
    devices.revoke(reg["device_id"], "lost device")
    with pytest.raises(devices.DeviceAuthError, match="revoked"):
        devices.verify(reg["device_id"], ts, sig, body)
    assert db.one("select key_hash from source_devices where id=%s", (reg["device_id"],))["key_hash"] != reg["device_key"]


def test_no_location_consent_stays_local_only():
    r = glasses.handle_device_event(event(location=False, image=False))
    assert r["outcome"] == "local_only" and r["ticket_number"] is None


def test_debounced_glasses_event_creates_ticket_and_queues_hud_status():
    r = glasses.handle_device_event(event(seq=150))
    assert r["route"] == "robotic_remediation_candidate" and r["ticket_number"]
    hud = db.one("select body from notification_outbox where channel='glasses_hud' order by created_at desc limit 1")
    assert hud["body"]["display"] == "after_stop_only"
    ev = [e["summary"] for e in db.all_rows("select summary from agent_events where run_id=%s and kind='exit' order by seq", (r["run_id"],))]
    assert any("Possible debris ahead" in s for s in ev)


def test_undebounced_candidate_gets_no_distracting_alert():
    r = glasses.handle_device_event(event(seq=155, frames=1, score=0.4))
    nodes = [e["node"] for e in db.all_rows("select node from agent_events where run_id=%s and kind='exit'", (r["run_id"],))]
    assert "no_distracting_alert" in nodes and "conservative_local_audio_or_HUD_warning" not in nodes


def test_replayed_device_event_is_idempotent():
    e = event(seq=158)
    a = glasses.handle_device_event(e)
    b = glasses.handle_device_event(e)
    assert b["replayed"] and b["ticket_number"] == a["ticket_number"]


def test_minimise_frame_crops_road_band_and_strips_exif():
    im = Image.new("RGB", (1280, 960), (10, 120, 60))
    buf = io.BytesIO()
    exif = Image.Exif()
    exif[0x8825] = {1: "N"}  # GPS IFD marker
    im.save(buf, format="JPEG", exif=exif.tobytes())
    out, meta = minimise_frame(buf.getvalue())
    o = Image.open(io.BytesIO(out))
    assert o.size == (960, int(960 * 528 / 1280))
    assert not o.getexif()
    assert meta["exif_stripped"]


def test_uploaded_glasses_frame_is_deidentified_before_storage():
    png = open("fixtures/images/glass-1av-dawn-01.png", "rb").read()
    e = event(seq=162, fixture=None)
    e["image_b64"], e["image_mime"] = base64.b64encode(png).decode(), "image/png"
    r = glasses.handle_device_event(e)
    m = db.one("select mime_type from report_media where report_id=(select id from hazard_reports where idempotency_key=%s)",
               (e["idempotency_key"],))
    assert m["mime_type"] == "image/jpeg"   # re-encoded crop, not the raw upload
    assert r["ticket_thread"]

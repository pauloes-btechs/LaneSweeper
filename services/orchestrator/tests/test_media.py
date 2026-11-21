"""AI demo media: request shapes, gating (two-key billable switch, admin only), storage and signed links."""
import base64
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from lanekeeper import auth, config, db, media
from lanekeeper.models.google import GoogleClient, media_output

from conftest import USERS

pytestmark = pytest.mark.db
PNG = b"\x89PNG\r\n\x1a\nfake-4k-png"
MP4 = b"\x00\x00\x00\x18ftypmp42fake-video"


def live(monkeypatch, tmp_path):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setenv("AI_MODE", "live")
    monkeypatch.setenv("BILLABLE_CALLS_ENABLED", "true")
    monkeypatch.setenv("LK_MEDIA_DIR", str(tmp_path))
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    config.get_settings.cache_clear()
    db.one("update feature_flags set enabled=true where key='billable_calls' returning 1")
    db.one("delete from media_assets returning 1")


def mock(handler):
    return GoogleClient("test-key", http=httpx.Client(transport=httpx.MockTransport(handler)))


def H(who):
    return {"authorization": f"Bearer {auth.mint_dev_token(USERS[who])}"}


def test_media_output_finds_nested_blocks():
    out = {"steps": [{"type": "thought"}, {"type": "model_output", "content": [
        {"type": "text", "text": "here"}, {"type": "video", "data": base64.b64encode(MP4).decode(), "mime_type": "video/mp4"}]}]}
    assert media_output(out, "video") == (MP4, "video/mp4")
    assert media_output(out, "image") is None


def test_image_generation_request_shape_and_local_store(fresh_db, monkeypatch, tmp_path):
    live(monkeypatch, tmp_path)
    seen = {}

    def handler(req):
        seen["url"], seen["body"] = str(req.url), json.loads(req.content)
        return httpx.Response(200, json={"status": "completed", "steps": [{"type": "model_output", "content": [
            {"type": "image", "data": base64.b64encode(PNG).decode(), "mime_type": "image/png"}]}]})

    r = media.generate("tex-asphalt", USERS["nyc_admin"], client=mock(handler))
    assert r["status"] == "ready"
    assert seen["url"].endswith("/interactions")
    assert seen["body"]["model"] == media.IMAGE_MODEL
    assert seen["body"]["response_format"] == {"type": "image", "mime_type": "image/jpeg", "aspect_ratio": "1:1", "image_size": "4K"}
    item = next(a for a in media.catalog() if a["id"] == "tex-asphalt")
    assert item["status"] == "ready" and item["provenance"] == "AI_GENERATED" and item["url"].startswith("/v1/media/file/")


def test_video_runs_in_background_then_poll_saves(fresh_db, monkeypatch, tmp_path):
    live(monkeypatch, tmp_path)
    calls = []

    def handler(req):
        calls.append((req.method, str(req.url)))
        if req.method == "POST":
            body = json.loads(req.content)
            assert body["background"] is True
            assert body["response_format"] == {"type": "video", "aspect_ratio": "16:9", "resolution": "4k"}
            return httpx.Response(200, json={"id": "int_abc123", "status": "in_progress"})
        return httpx.Response(200, json={"id": "int_abc123", "status": "completed", "steps": [{"type": "model_output",
                              "content": [{"type": "video", "data": base64.b64encode(MP4).decode(), "mime_type": "video/mp4"}]}]})

    c = mock(handler)
    assert media.generate("pov-dawn-glass", USERS["nyc_admin"], resolution="4k", client=c)["status"] == "generating"
    assert media.generate("pov-dawn-glass", USERS["nyc_admin"], client=c)["status"] == "generating"   # no double spend
    assert len([x for x in calls if x[0] == "POST"]) == 1
    assert media.poll("pov-dawn-glass", client=c)["status"] == "ready"
    assert calls[-1] == ("GET", "https://generativelanguage.googleapis.com/v1beta/interactions/int_abc123")
    row = db.one("select status, resolution, bytes, label from media_assets where id='pov-dawn-glass'")
    assert row == {"status": "ready", "resolution": "4k", "bytes": len(MP4), "label": "glass"}


def test_generation_refused_when_billable_switch_off(fresh_db, monkeypatch, tmp_path):
    live(monkeypatch, tmp_path)
    db.one("update feature_flags set enabled=false where key='billable_calls' returning 1")
    try:
        with pytest.raises(media.MediaError, match="billable"):
            media.generate("tex-asphalt", None)
    finally:
        db.one("update feature_flags set enabled=true where key='billable_calls' returning 1")


def test_offline_mode_refuses(fresh_db, monkeypatch, tmp_path):
    monkeypatch.setenv("LK_MEDIA_DIR", str(tmp_path))
    with pytest.raises(media.MediaError, match="live AI is off"):
        media.generate("tex-asphalt", None)


def test_oversize_output_refused(fresh_db, monkeypatch, tmp_path):
    live(monkeypatch, tmp_path)
    monkeypatch.setattr(media, "MAX_BYTES", 4)

    def handler(req):
        return httpx.Response(200, json={"steps": [{"type": "image", "data": base64.b64encode(PNG).decode()}]})

    with pytest.raises(media.MediaError, match="50 MB"):
        media.generate("tex-asphalt", None, client=mock(handler))


def test_catalog_is_labelled_and_prompts_avoid_real_identifiers():
    for a in media.CATALOG:
        assert a.kind in ("video", "image", "texture")
        if a.use_case in ("glasses_pov", "evidence"):
            assert a.label and "Fictional location" in a.prompt


def test_api_admin_only_generation_and_signed_file(fresh_db, monkeypatch, tmp_path):
    live(monkeypatch, tmp_path)
    import os
    os.environ["LK_DEMO_MODE"] = "1"
    from lanekeeper.api.app import create_app
    client = TestClient(create_app())
    assert client.get("/v1/media").status_code == 401
    assert client.post("/v1/admin/media/tex-asphalt/generate", json={}, headers=H("nyc_operator")).status_code == 403
    assert client.post("/v1/admin/media/tex-asphalt/generate", json={}, headers=H("rider")).status_code == 403
    assert client.post("/v1/admin/media/nope/generate", json={}, headers=H("nyc_admin")).status_code in (403, 409)

    media.MediaStore().put("twin_texture/x.png", PNG, "image/png")
    url = media.MediaStore().signed_url("twin_texture/x.png")
    assert client.get(url).content == PNG
    assert client.get(url.replace("sig=", "sig=0")).status_code == 403
    assert client.get("/v1/media/file/../../etc/passwd?exp=9999999999&sig=x").status_code in (403, 404)
    listed = client.get("/v1/media", headers=H("rider")).json()
    assert listed["provenance"].startswith("AI_GENERATED") and len(listed["assets"]) == len(media.CATALOG)


def test_video_falls_back_to_sync_when_background_unsupported(fresh_db, monkeypatch, tmp_path):
    live(monkeypatch, tmp_path)
    bodies = []

    def handler(req):
        body = json.loads(req.content)
        bodies.append(body)
        if body.get("background"):
            return httpx.Response(400, json={"error": {"message": "background is not supported for this model"}})
        return httpx.Response(200, json={"steps": [{"type": "video", "data": base64.b64encode(MP4).decode()}]})

    assert media.generate("pov-day-clear", None, client=mock(handler))["status"] == "ready"
    assert [b.get("background") for b in bodies] == [True, None]

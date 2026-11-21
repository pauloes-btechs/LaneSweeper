"""Model layer: request shapes, parsing, fallbacks, budgets, breaker, kill switch. No network."""
import json

import httpx
import pytest

from lanekeeper import config, db
from lanekeeper.models import budget, offline
from lanekeeper.models.google import GoogleClient, Image, ModelError, _interaction_text
from lanekeeper.models.router import ModelRouter, parse_vision_json

pytestmark = pytest.mark.db

Q = {
    "is_glass": {"type": "noul", "instructions": "Does the image show broken glass?", "fixture_key": "glass"},
    "category": {"type": "choice", "instructions": "Hazard type", "criteria": {"glass": "", "pothole": "", "other": ""}},
}


def mock_client(handler):
    return GoogleClient("test-key", http=httpx.Client(transport=httpx.MockTransport(handler)))


def live(monkeypatch):
    """Simulate a configured key. Safe in CI: every test passes a MockTransport, so no request leaves the process."""
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setenv("AI_MODE", "live")
    monkeypatch.setenv("BILLABLE_CALLS_ENABLED", "true")
    config.get_settings.cache_clear()


def test_decisions_request_shape_and_parse(fresh_db, monkeypatch):
    live(monkeypatch)
    seen = {}

    def handler(req: httpx.Request):
        seen["url"], seen["key"] = str(req.url), req.headers.get("x-goog-api-key")
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"answers": {
            "is_glass": {"type": "noul", "noul": 0.91, "confidence": 0.91},
            "category": {"type": "choice", "choice": "glass", "probabilities": {"glass": 0.9, "pothole": 0.05, "other": 0.05},
                         "confidence": 0.9}}, "usage": {"total_input_tokens": 812}})

    r = ModelRouter(mock_client(handler)).decide("rider says glass", Q, images=[Image(b"\xff\xd8jpeg")])
    assert seen["url"].endswith("/v1beta/decisions")
    assert seen["key"] == "test-key"
    assert seen["body"]["model"] == "gemini-decision-1"
    assert seen["body"]["input"][1]["type"] == "image"
    assert "fixture_key" not in seen["body"]["questions"]["is_glass"]   # internal hints never sent
    assert r.provenance.mode == "live" and r.provenance.input_tokens == 812
    assert r.answers["is_glass"].advisory_probability == 0.91
    assert r.answers["category"].choice == "glass"


def test_decision_choice_outside_options_is_rejected_and_falls_back(fresh_db, monkeypatch):
    live(monkeypatch)

    def handler(req):
        return httpx.Response(200, json={"answers": {
            "is_glass": {"noul": 0.9, "confidence": 0.9},
            "category": {"choice": "dispatch_now", "probabilities": {}, "confidence": 0.99}}})

    r = ModelRouter(mock_client(handler)).decide("x", Q, fixture_id="glass-1av-dawn-01")
    assert r.provenance.mode == "offline"
    assert r.provenance.fallback_reason.startswith("malformed")
    assert r.answers["category"].choice == "glass"


def test_quota_error_falls_back_offline_and_is_logged(fresh_db, monkeypatch):
    live(monkeypatch)

    def handler(req):
        return httpx.Response(429, json={"error": {"code": 429, "message": "quota"}})

    r = ModelRouter(mock_client(handler)).decide("glass", Q, fixture_id="glass-1av-dawn-01")
    assert r.provenance.mode == "offline" and r.provenance.fixture_id == "glass-1av-dawn-01"
    row = db.one("select outcome from model_calls order by id desc limit 1")
    assert row["outcome"] == "quota"


def test_no_key_means_offline_without_network(fresh_db):
    def handler(req):
        raise AssertionError("network must not be touched")
    r = ModelRouter(None).decide("glass on 1st ave", Q)
    assert r.provenance.mode == "offline" and r.provenance.fallback_reason in ("offline_mode", "no_api_key")


def test_kill_switch_db_flag_blocks_live_calls(fresh_db, monkeypatch):
    live(monkeypatch)
    db.one("update feature_flags set enabled=false where key='billable_calls' returning 1")
    try:
        def handler(req):
            raise AssertionError("billable call made while disabled")
        r = ModelRouter(mock_client(handler)).decide("x", Q)
        assert r.provenance.fallback_reason == "billable_calls_disabled"
    finally:
        db.one("update feature_flags set enabled=true where key='billable_calls' returning 1")


def test_env_kill_switch_blocks_live_calls(fresh_db, monkeypatch):
    live(monkeypatch)
    monkeypatch.setenv("BILLABLE_CALLS_ENABLED", "false")
    config.get_settings.cache_clear()

    def handler(req):
        raise AssertionError("billable call made while disabled")
    assert ModelRouter(mock_client(handler)).decide("x", Q).provenance.mode == "offline"


def test_breaker_opens_after_repeated_failures(fresh_db, monkeypatch):
    live(monkeypatch)
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(500, json={"error": {"message": "boom"}})

    router = ModelRouter(mock_client(handler))
    for _ in range(8):
        router.decide("x", Q)
    assert calls["n"] == budget.Breaker().threshold   # stopped calling once open


def test_per_minute_cap_enforced(fresh_db, monkeypatch):
    live(monkeypatch)
    monkeypatch.setenv("AI_PER_MINUTE_CAP", "2")
    config.get_settings.cache_clear()
    db.one("delete from usage_counters returning 1")
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(200, json={"answers": {"is_glass": {"noul": 0.5, "confidence": 0.5},
                                                     "category": {"choice": "other", "probabilities": {"other": 1}, "confidence": 1}}})
    router = ModelRouter(mock_client(handler))
    modes = [router.decide("x", Q).provenance.mode for _ in range(4)]
    assert calls["n"] == 2 and modes[2:] == ["offline", "offline"]


def test_vision_parser_keeps_observations_and_discards_motion_fields():
    v, pts, n, notes = parse_vision_json('```json\n{"verdict":"glass","points":[{"point":[612,488],"label":"shard"},'
                                         '{"point":[2000,-5],"label":"x"}],"est_count":9,'
                                         '"waypoints":[[1,2]],"speed":3.0}\n```')
    assert v == "glass" and n == 9
    assert (pts[1].y, pts[1].x) == (1000, 0)   # clamped to image space
    assert "waypoints" in notes and "speed" in notes


def test_vision_unknown_verdict_becomes_unsure():
    assert parse_vision_json('{"verdict":"definitely safe"}')[0] == "unsure"


def test_interactions_request_and_text_extraction(fresh_db, monkeypatch):
    live(monkeypatch)
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"id": "i1", "outputs": [
            {"type": "thought", "text": "thinking..."},
            {"type": "text", "text": '{"verdict":"glass","points":[{"point":[600,500],"label":"broken_glass"}],"est_count":5}'}]})

    r = ModelRouter(mock_client(handler)).vision("Find glass", images=[Image(b"png", "image/png")])
    assert seen["url"].endswith("/v1beta/interactions")
    assert seen["body"]["model"] == "gemini-robotics-er-2-preview"
    assert seen["body"]["generation_config"]["thinking_level"] == "medium"
    assert r.verdict == "glass" and r.points[0].label == "broken_glass" and r.provenance.mode == "live"


def test_interaction_text_skips_thoughts():
    assert _interaction_text({"outputs": [{"type": "thought", "text": "a"}, {"type": "text", "text": "b"}]}) == "b"
    with pytest.raises(ModelError):
        _interaction_text({"outputs": [{"type": "thought", "text": "only thinking"}]})


def test_embedding_dimension_is_validated(fresh_db, monkeypatch):
    live(monkeypatch)

    def handler(req):
        body = json.loads(req.content)
        assert body["output_dimensionality"] == 768
        return httpx.Response(200, json={"embedding": {"values": [0.1] * 512}})   # wrong size

    r = ModelRouter(mock_client(handler)).embed("glass")
    assert r.model == offline.HASH_MODEL and r.provenance.fallback_reason.startswith("malformed")


def test_hash_embedder_is_deterministic_normalised_and_similarity_aware():
    a, b, c = (offline.hash_embed(t) for t in ("broken glass 1st ave", "broken glass on 1st ave", "pothole in lane"))
    assert a == offline.hash_embed("broken glass 1st ave") and len(a) == 768
    assert abs(sum(x * x for x in a) - 1) < 1e-9
    dot = lambda u, v: sum(x * y for x, y in zip(u, v, strict=True))  # noqa: E731
    assert dot(a, b) > dot(a, c)


def test_offline_vision_without_fixture_never_invents_detections():
    r = offline.vision("vision.localize", None)
    assert r.verdict == "unsure" and r.points == []


def test_client_repr_does_not_leak_key():
    assert "test-key" not in repr(GoogleClient("test-key"))


def test_403_hint_mentions_key_restriction(fresh_db, monkeypatch):
    def handler(req):
        return httpx.Response(403, json={"error": {"message": "forbidden"}})
    with pytest.raises(ModelError) as e:
        mock_client(handler).interact("gemini-robotics-er-2-preview", "x")
    assert e.value.kind == "auth" and "unrestricted" in str(e.value)

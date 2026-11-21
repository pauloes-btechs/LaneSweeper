"""Hosting adapters: database URL resolution for the Vercel-Supabase integration, and the Supabase evidence store."""
import io
import urllib.error

import pytest

from lanekeeper import config, storage


def test_database_url_prefers_explicit_then_integration(monkeypatch):
    for k in ("DATABASE_URL", "POSTGRES_URL", "POSTGRES_PRISMA_URL"):
        monkeypatch.delenv(k, raising=False)
    assert config._database_url().startswith("postgresql://postgres:postgres@localhost")
    monkeypatch.setenv("POSTGRES_URL", "postgres://u:p@aws-0-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require&supa=base-pooler.x")
    assert config._database_url() == "postgres://u:p@aws-0-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
    monkeypatch.setenv("DATABASE_URL", "postgresql://x@localhost:5432/y")
    assert config._database_url() == "postgresql://x@localhost:5432/y"


def test_session_mode_moves_supabase_pooler_off_transaction_mode():
    t = "postgres://u:p@aws-0-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require&pgbouncer=true"
    assert config.session_mode(t) == "postgres://u:p@aws-0-us-east-1.pooler.supabase.com:5432/postgres?sslmode=require"
    assert config.session_mode("postgresql://postgres@localhost:6543/x") == "postgresql://postgres@localhost:6543/x"   # not Supabase


def test_evidence_store_choice(monkeypatch, tmp_path):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setenv("LK_EVIDENCE_DIR", str(tmp_path))
    assert isinstance(storage.evidence_store(), storage.LocalEvidenceStore)
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-key-secret")
    st = storage.evidence_store()
    assert isinstance(st, storage.SupabaseEvidenceStore) and "service-key-secret" not in repr(st)
    with pytest.raises(storage.EvidenceError):
        storage.SupabaseEvidenceStore("http://abc.supabase.co", "k")


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_supabase_store_uploads_privately_and_reads_back(monkeypatch):
    calls = []
    blob = {}

    def fake(req, timeout):
        calls.append((req.get_method(), req.full_url, dict(req.header_items())))
        if req.get_method() == "POST":
            if req.full_url in blob:
                raise urllib.error.HTTPError(req.full_url, 409, "exists", {}, None)
            blob[req.full_url] = req.data
            return _Resp(b"{}")
        if req.full_url not in blob:
            raise urllib.error.HTTPError(req.full_url, 404, "missing", {}, None)
        return _Resp(blob[req.full_url])

    monkeypatch.setattr(storage.urllib.request, "urlopen", fake)
    st = storage.SupabaseEvidenceStore("https://abc.supabase.co", "svc")
    jpg = b"\xff\xd8\xff" + b"0" * 100
    rel, sha = st.put(jpg, "image/jpeg", jurisdiction_slug="nyc")
    assert rel.startswith("nyc/") and rel.endswith(".jpeg")
    assert calls[0][1] == f"https://abc.supabase.co/storage/v1/object/evidence/{rel}"
    assert calls[0][2]["Authorization"] == "Bearer svc"
    assert st.put(jpg, "image/jpeg", jurisdiction_slug="nyc") == (rel, sha)       # idempotent (409 ignored)
    assert st.get(rel) == jpg
    with pytest.raises(storage.EvidenceError):
        st.get("nyc/00/missing.jpeg")
    with pytest.raises(storage.EvidenceError):
        st.get("../secrets")
    with pytest.raises(storage.EvidenceError):
        st.put(b"not an image", "image/jpeg", jurisdiction_slug="nyc")


def test_secrets_derive_from_service_key_and_never_default_when_hosted(monkeypatch):
    for k in ("LK_SIGNING_SECRET", "SUPABASE_SERVICE_ROLE_KEY", "VERCEL"):
        monkeypatch.delenv(k, raising=False)
    assert config._secret("LK_SIGNING_SECRET", "signing") == "change-me-dev-only"
    monkeypatch.setenv("VERCEL", "1")
    with pytest.raises(RuntimeError):
        config._secret("LK_SIGNING_SECRET", "signing")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc-key")
    a, b = config._secret("LK_SIGNING_SECRET", "signing"), config._secret("LK_DEVICE_LINK_SECRET", "device-link")
    assert len(a) == 64 and a != b and "svc-key" not in a
    monkeypatch.setenv("LK_SIGNING_SECRET", "explicit")
    assert config._secret("LK_SIGNING_SECRET", "signing") == "explicit"


def test_hosted_demo_identities_need_an_explicit_second_flag(monkeypatch):
    for k in ("LK_DEMO_MODE", "SUPABASE_JWT_SECRET", "LK_DEMO_ALLOW_HOSTED"):
        monkeypatch.delenv(k, raising=False)
    assert not config.demo_identities_allowed()
    monkeypatch.setenv("LK_DEMO_MODE", "1")
    assert config.demo_identities_allowed()                     # local demo
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "real-project-secret")
    assert not config.demo_identities_allowed()                 # hosted: off unless the owner opts in
    monkeypatch.setenv("LK_DEMO_ALLOW_HOSTED", "1")
    assert config.demo_identities_allowed()

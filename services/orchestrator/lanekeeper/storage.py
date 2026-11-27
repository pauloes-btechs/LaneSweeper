"""Evidence storage. Images never go into graph checkpoints; only their content hash and path do.

LocalEvidenceStore writes to .lk/evidence (git-ignored) for development and the demo. When SUPABASE_URL and
SUPABASE_SERVICE_ROLE_KEY are set (hosted, e.g. via the Vercel-Supabase integration), SupabaseEvidenceStore keeps
images in the private 'evidence' bucket instead. Either way staff get short-lived links signed by this server,
and the image bytes are streamed through the API, so the bucket never needs to be public.
"""
from __future__ import annotations

import base64
import hashlib
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from .config import get_settings
from .hashing import sign, verify_signature

MAX_BYTES = 4 * 1024 * 1024
MAGIC = {"image/jpeg": b"\xff\xd8\xff", "image/png": b"\x89PNG", "image/webp": b"RIFF"}
REPO_ROOT = Path(__file__).resolve().parents[3]


class EvidenceError(ValueError):
    pass


class EvidenceStore:
    """Validation, content addressing and signed links shared by every backend."""

    def _check(self, data: bytes, mime: str) -> tuple[str, str]:
        if len(data) == 0 or len(data) > MAX_BYTES:
            raise EvidenceError("image must be between 1 byte and 4 MB")
        if not data.startswith(MAGIC.get(mime, b"\x00\x00\x00\x00")):
            raise EvidenceError("image content does not match declared type")
        return hashlib.sha256(data).hexdigest(), mime.split("/")[1]

    @staticmethod
    def _rel_ok(rel: str) -> None:
        if rel.startswith("/") or ".." in rel.split("/"):
            raise EvidenceError("invalid evidence path")

    def put(self, data: bytes, mime: str, *, jurisdiction_slug: str) -> tuple[str, str]:
        sha, ext = self._check(data, mime)
        rel = f"{jurisdiction_slug}/{sha[:2]}/{sha}.{ext}"
        self._write(rel, data, mime)
        return rel, sha

    def _write(self, rel: str, data: bytes, mime: str) -> None:
        raise NotImplementedError

    def get(self, rel: str) -> bytes:
        raise NotImplementedError

    def delete(self, rel: str) -> None:
        """Remove an object. Already gone counts as deleted."""
        raise NotImplementedError

    def signed_url(self, rel: str, ttl_s: int = 120) -> str:
        exp = int(time.time()) + ttl_s
        sig = sign(get_settings().signing_secret, "evidence", rel, exp)
        return f"/v1/evidence/{rel}?exp={exp}&sig={sig}"

    @staticmethod
    def check_url(rel: str, exp: int, sig: str) -> bool:
        return exp >= time.time() and verify_signature(get_settings().signing_secret, sig, "evidence", rel, exp)


class LocalEvidenceStore(EvidenceStore):
    def __init__(self, root: Path | None = None):
        self.root = Path(root or os.environ.get("LK_EVIDENCE_DIR", REPO_ROOT / ".lk" / "evidence"))
        self.root.mkdir(parents=True, exist_ok=True)

    def _write(self, rel: str, data: bytes, mime: str) -> None:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_bytes(data)

    def get(self, rel: str) -> bytes:
        self._rel_ok(rel)
        p = (self.root / rel).resolve()
        if self.root.resolve() not in p.parents or not p.is_file():
            raise EvidenceError("not found")
        return p.read_bytes()

    def delete(self, rel: str) -> None:
        self._rel_ok(rel)
        (self.root / rel).unlink(missing_ok=True)


class SupabaseEvidenceStore(EvidenceStore):
    """Private Supabase Storage bucket, accessed only server-side with the service-role key (never sent to clients)."""

    def __init__(self, url: str, service_key: str, bucket: str = "evidence"):
        if not url.startswith("https://"):
            raise EvidenceError("SUPABASE_URL must be an https:// URL")
        self.base = url.rstrip("/") + "/storage/v1/object"
        self._key = service_key
        self.bucket = bucket

    def __repr__(self) -> str:
        return f"SupabaseEvidenceStore(bucket={self.bucket!r})"

    def _req(self, method: str, rel: str, data: bytes | None = None, mime: str | None = None) -> bytes:
        self._rel_ok(rel)
        headers = {"authorization": f"Bearer {self._key}", "apikey": self._key}
        if mime:
            headers.update({"content-type": mime, "x-upsert": "false", "cache-control": "private, max-age=60"})
        # https enforced in __init__; rel validated above
        req = urllib.request.Request(f"{self.base}/{self.bucket}/{rel}", data=data, method=method, headers=headers)  # noqa: S310
        with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310
            return r.read()

    def _write(self, rel: str, data: bytes, mime: str) -> None:
        try:
            self._req("POST", rel, data, mime)
        except urllib.error.HTTPError as e:
            if e.code not in (400, 409):          # 409/400 "already exists": content-addressed, so identical
                raise EvidenceError(f"evidence upload failed ({e.code})") from e

    def get(self, rel: str) -> bytes:
        try:
            return self._req("GET", rel)
        except urllib.error.HTTPError as e:
            raise EvidenceError("not found") from e

    def delete(self, rel: str) -> None:
        try:
            self._req("DELETE", rel)
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise EvidenceError(f"evidence delete failed ({e.code})") from e


def evidence_store() -> EvidenceStore:
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    return SupabaseEvidenceStore(url, key) if url and key else LocalEvidenceStore()


def decode_image(b64: str) -> bytes:
    try:
        return base64.b64decode(b64, validate=True)
    except Exception as e:
        raise EvidenceError("image is not valid base64") from e


def fixture_bytes(fixture_id: str) -> tuple[bytes, str] | None:
    p = REPO_ROOT / "fixtures" / "images" / f"{fixture_id}.png"
    return (p.read_bytes(), "image/png") if p.exists() else None

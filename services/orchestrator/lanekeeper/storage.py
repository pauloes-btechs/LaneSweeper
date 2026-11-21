"""Evidence storage. Images never go into graph checkpoints; only their content hash and path do.

LocalEvidenceStore writes to .lk/evidence (git-ignored) for development and the demo. A hosted
deployment swaps in Supabase Storage with the private 'evidence' bucket and short signed URLs.
"""
from __future__ import annotations

import base64
import hashlib
import os
import time
from pathlib import Path

from .config import get_settings
from .hashing import sign, verify_signature

MAX_BYTES = 4 * 1024 * 1024
MAGIC = {"image/jpeg": b"\xff\xd8\xff", "image/png": b"\x89PNG", "image/webp": b"RIFF"}
REPO_ROOT = Path(__file__).resolve().parents[3]


class EvidenceError(ValueError):
    pass


class LocalEvidenceStore:
    def __init__(self, root: Path | None = None):
        self.root = Path(root or os.environ.get("LK_EVIDENCE_DIR", REPO_ROOT / ".lk" / "evidence"))
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, data: bytes, mime: str, *, jurisdiction_slug: str) -> tuple[str, str]:
        if len(data) == 0 or len(data) > MAX_BYTES:
            raise EvidenceError("image must be between 1 byte and 4 MB")
        if not data.startswith(MAGIC.get(mime, b"\x00\x00\x00\x00")):
            raise EvidenceError("image content does not match declared type")
        sha = hashlib.sha256(data).hexdigest()
        rel = f"{jurisdiction_slug}/{sha[:2]}/{sha}.{mime.split('/')[1]}"
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_bytes(data)
        return rel, sha

    def get(self, rel: str) -> bytes:
        p = (self.root / rel).resolve()
        if self.root.resolve() not in p.parents:
            raise EvidenceError("path escapes evidence root")
        return p.read_bytes()

    def signed_url(self, rel: str, ttl_s: int = 120) -> str:
        exp = int(time.time()) + ttl_s
        sig = sign(get_settings().signing_secret, "evidence", rel, exp)
        return f"/v1/evidence/{rel}?exp={exp}&sig={sig}"

    @staticmethod
    def check_url(rel: str, exp: int, sig: str) -> bool:
        return exp >= time.time() and verify_signature(get_settings().signing_secret, sig, "evidence", rel, exp)


def decode_image(b64: str) -> bytes:
    try:
        return base64.b64decode(b64, validate=True)
    except Exception as e:
        raise EvidenceError("image is not valid base64") from e


def fixture_bytes(fixture_id: str) -> tuple[bytes, str] | None:
    p = REPO_ROOT / "fixtures" / "images" / f"{fixture_id}.png"
    return (p.read_bytes(), "image/png") if p.exists() else None

"""Canonical hashing and signatures. Approval payload hashes must be byte-stable across processes."""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID


def _default(o: Any) -> Any:
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, (UUID, Decimal)):
        return str(o)
    if hasattr(o, "model_dump"):
        return o.model_dump(mode="json")
    raise TypeError(f"not canonically serializable: {type(o).__name__}")


def canonical_json(obj: Any) -> str:
    """Sorted keys, no whitespace, UTF-8: the same payload always yields the same bytes."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_default)


def payload_hash(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode()).hexdigest()


def sign(secret: str, *parts: Any) -> str:
    msg = "|".join(str(p) for p in parts).encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def verify_signature(secret: str, signature: str, *parts: Any) -> bool:
    return hmac.compare_digest(sign(secret, *parts), signature or "")


def short_hash(value: str, salt: str) -> str:
    """Salted, truncated hash for pseudonymous reporter IDs. Not reversible to the input."""
    return hashlib.sha256(f"{salt}:{value}".encode()).hexdigest()[:32]

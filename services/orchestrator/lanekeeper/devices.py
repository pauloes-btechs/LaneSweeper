"""Device link (glasses / rider phone bridge).

A paired device gets a key derived from LK_DEVICE_LINK_SECRET and its id: the server can recompute it, so no
device secret is stored. Requests are signed HMAC(key, "<unix_ts>.<sha256(body)>"), must be within ±300 s, and
replays are stopped by per-event idempotency keys. Devices never hold Google or Supabase keys."""
from __future__ import annotations

import hashlib
import hmac
import time

from . import db
from .auth import Principal
from .config import get_settings

SKEW_S = 300


class DeviceAuthError(Exception):
    pass


def device_key(device_id: str) -> str:
    return hmac.new(get_settings().device_link_secret.encode(), f"device:{device_id}".encode(), hashlib.sha256).hexdigest()


def register(owner: Principal, kind: str, label: str, capabilities: dict) -> dict:
    if kind not in ("glasses_sim", "glasses_device", "rider_phone"):
        raise DeviceAuthError("unsupported device kind")
    row = db.one("""insert into source_devices (kind, label, owner_user_id, capabilities, trust_level)
                    values (%s,%s,%s,%s,'registered') returning id::text""",
                 (kind, label[:80], owner.user_id, db.jsonb(capabilities)))
    key = device_key(row["id"])
    db.one("update source_devices set key_hash=%s where id=%s returning 1", (hashlib.sha256(key.encode()).hexdigest(), row["id"]))
    return {"device_id": row["id"], "device_key": key, "note": "store on the device only; shown once"}


def sign(device_id: str, ts: int, body: bytes) -> str:
    return hmac.new(device_key(device_id).encode(), f"{ts}.{hashlib.sha256(body).hexdigest()}".encode(), hashlib.sha256).hexdigest()


def verify(device_id: str, ts: str | int, signature: str, body: bytes) -> dict:
    try:
        t = int(ts)
    except (TypeError, ValueError) as e:
        raise DeviceAuthError("bad timestamp") from e
    if abs(time.time() - t) > SKEW_S:
        raise DeviceAuthError("timestamp outside allowed window")
    dev = db.one("select id::text, kind, trust_level, revoked_at, capabilities from source_devices where id=%s", (device_id,))
    if not dev or dev["revoked_at"]:
        raise DeviceAuthError("unknown or revoked device")
    if not hmac.compare_digest(sign(device_id, t, body), signature or ""):
        raise DeviceAuthError("bad signature")
    return dev


def revoke(device_id: str, reason: str) -> None:
    db.one("update source_devices set revoked_at=now() where id=%s returning 1", (device_id,))

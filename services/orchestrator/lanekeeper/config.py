"""Runtime settings, read once from the environment. Secrets are never logged or echoed."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    return default if v in (None, "") else v.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    database_url: str
    google_api_key: str | None = field(repr=False)
    ai_mode: str                      # "live" | "offline"
    billable_calls_enabled: bool
    ai_daily_cap: int
    ai_per_minute_cap: int
    signing_secret: str = field(repr=False)
    supabase_jwt_secret: str | None = field(repr=False)
    device_link_secret: str = field(repr=False)
    hardware_gateway_enabled: bool = False
    api_port: int = 8787

    @property
    def live_models(self) -> bool:
        return self.ai_mode == "live" and self.billable_calls_enabled and bool(self.google_api_key)


def _database_url() -> str:
    """DATABASE_URL wins; otherwise the Vercel-Supabase integration's pooled URL (Vercel has no IPv6 for direct DB)."""
    for name in ("DATABASE_URL", "POSTGRES_URL", "POSTGRES_PRISMA_URL"):
        v = os.environ.get(name)
        if v:
            return v.replace("&supa=base-pooler.x", "").replace("?supa=base-pooler.x&", "?").replace("?supa=base-pooler.x", "")
    return "postgresql://postgres:postgres@localhost:5432/lanekeeper"


def _secret(name: str, purpose: str) -> str:
    """Explicit env var, else derived from the Supabase service-role key (hosted), else a dev-only default.

    Deriving means no new secret has to be created or copied by hand when the Vercel-Supabase integration injects
    SUPABASE_SERVICE_ROLE_KEY. Rotating that key rotates these too (device pairings then need redoing).
    A hosted (Vercel) deployment refuses to start on the dev default."""
    v = os.environ.get(name)
    if v:
        return v
    root = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if root:
        import hashlib
        import hmac
        return hmac.new(root.encode(), f"lanekeeper:{purpose}:v1".encode(), hashlib.sha256).hexdigest()
    if os.environ.get("VERCEL"):
        raise RuntimeError(f"{name} (or SUPABASE_SERVICE_ROLE_KEY) must be set on a hosted deployment")
    return "change-me-dev-only"


def demo_identities_allowed() -> bool:
    """Demo sign-in-as-any-role. Local: LK_DEMO_MODE=1. With real Supabase auth configured (hosted), it also needs
    LK_DEMO_ALLOW_HOSTED=1, an explicit owner decision (here: the site sits behind Vercel Authentication)."""
    if os.environ.get("LK_DEMO_MODE") != "1":
        return False
    return not os.environ.get("SUPABASE_JWT_SECRET") or os.environ.get("LK_DEMO_ALLOW_HOSTED") == "1"


def session_mode(url: str) -> str:
    """Use Supabase's pooler in *session* mode (port 5432): a client keeps its server connection, so session settings
    (the checkpointer's search_path) and prepared statements work. Transaction mode (6543) supports neither."""
    if ".pooler.supabase.com:6543/" in url:
        url = url.replace(":6543/", ":5432/")
    return url.replace("&pgbouncer=true", "").replace("?pgbouncer=true&", "?").replace("?pgbouncer=true", "")


def pool_max(default: int) -> int:
    return int(os.environ.get("LK_DB_POOL_MAX", default))


def hold_connections() -> bool:
    """Keep idle DB connections between requests? Yes for a long-running server. No on serverless (Vercel):
    frozen and superseded instances would keep their sessions, and Supabase's session pooler allows only 15."""
    v = os.environ.get("LK_DB_HOLD_CONNECTIONS")
    if v is not None:
        return v.strip().lower() in ("1", "true", "yes", "on")
    return not os.environ.get("VERCEL")


def make_pool(url: str, *, max_size: int, kwargs: dict, configure=None):
    """A normal pool locally; a NullConnectionPool (connect per use, close on return) on serverless."""
    from psycopg_pool import ConnectionPool, NullConnectionPool
    cls = ConnectionPool if hold_connections() else NullConnectionPool
    return cls(session_mode(url), min_size=0, max_size=max_size, open=True, kwargs=kwargs, configure=configure)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    key = os.environ.get("GOOGLE_API_KEY") or None
    mode = (os.environ.get("AI_MODE") or ("live" if key else "offline")).lower()
    if mode not in ("live", "offline"):
        raise ValueError("AI_MODE must be 'live' or 'offline'")
    hw = _bool("LK_HARDWARE_GATEWAY_ENABLED", False)
    if hw:
        # There is no hardware gateway in this build. Refuse to start rather than pretend.
        raise RuntimeError("LK_HARDWARE_GATEWAY_ENABLED=true is not supported: real robot control is unavailable")
    return Settings(
        database_url=_database_url(),
        google_api_key=key,
        ai_mode=mode,
        billable_calls_enabled=_bool("BILLABLE_CALLS_ENABLED", True),
        ai_daily_cap=int(os.environ.get("AI_DAILY_CALL_CAP", "5000")),
        ai_per_minute_cap=int(os.environ.get("AI_PER_MINUTE_CAP", "60")),
        signing_secret=_secret("LK_SIGNING_SECRET", "signing"),
        supabase_jwt_secret=os.environ.get("SUPABASE_JWT_SECRET") or None,
        device_link_secret=_secret("LK_DEVICE_LINK_SECRET", "device-link"),
        hardware_gateway_enabled=False,
        api_port=int(os.environ.get("LK_API_PORT", "8787")),
    )

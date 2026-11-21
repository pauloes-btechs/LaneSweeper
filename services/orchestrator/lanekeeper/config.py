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
        database_url=os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/lanekeeper"),
        google_api_key=key,
        ai_mode=mode,
        billable_calls_enabled=_bool("BILLABLE_CALLS_ENABLED", True),
        ai_daily_cap=int(os.environ.get("AI_DAILY_CALL_CAP", "5000")),
        ai_per_minute_cap=int(os.environ.get("AI_PER_MINUTE_CAP", "60")),
        signing_secret=os.environ.get("LK_SIGNING_SECRET", "change-me-dev-only"),
        supabase_jwt_secret=os.environ.get("SUPABASE_JWT_SECRET") or None,
        device_link_secret=os.environ.get("LK_DEVICE_LINK_SECRET", "change-me-dev-only"),
        hardware_gateway_enabled=False,
        api_port=int(os.environ.get("LK_API_PORT", "8787")),
    )

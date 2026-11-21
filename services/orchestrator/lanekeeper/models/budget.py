"""Budgets, quotas, circuit breaker and kill switch for every externally billable model call."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from .. import db
from ..config import get_settings


class BudgetExceeded(Exception):
    pass


@dataclass
class Breaker:
    threshold: int = 5
    cooldown_s: float = 120.0
    failures: int = 0
    opened_at: float | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def allow(self) -> bool:
        with self.lock:
            if self.opened_at is None:
                return True
            if time.monotonic() - self.opened_at > self.cooldown_s:
                self.opened_at, self.failures = None, 0   # half-open: try again
                return True
            return False

    def record(self, ok: bool) -> None:
        with self.lock:
            if ok:
                self.failures = 0
                return
            self.failures += 1
            if self.failures >= self.threshold:
                self.opened_at = time.monotonic()


_breakers: dict[str, Breaker] = {}


def breaker(model: str) -> Breaker:
    return _breakers.setdefault(model, Breaker())


def reset_breakers() -> None:
    _breakers.clear()


def billable_enabled() -> bool:
    """Env switch AND the database feature flag must both allow billable calls."""
    if not get_settings().billable_calls_enabled:
        return False
    try:
        row = db.one("select enabled from feature_flags where key = 'billable_calls'")
    except Exception:
        return False
    return bool(row and row["enabled"])


def take(bucket: str, per_minute: int, per_day: int) -> None:
    """Atomically reserve one call in the minute and day windows, or raise BudgetExceeded."""
    s = get_settings()
    per_minute = min(per_minute, s.ai_per_minute_cap)
    with db.tx() as cur:
        for kind, cap, trunc in (("minute", per_minute, "minute"), ("day", per_day, "day")):
            cur.execute(
                """insert into usage_counters (bucket, window_kind, window_start, count)
                   values (%s, %s, date_trunc(%s, now()), 1)
                   on conflict (bucket, window_kind, window_start) do update set count = usage_counters.count + 1
                   returning count""", (bucket, kind, trunc))
            if cur.fetchone()["count"] > cap:
                raise BudgetExceeded(f"{bucket}: {kind} cap {cap} reached")
        cur.execute("""select coalesce(sum(count),0) as n from usage_counters
                       where window_kind='day' and window_start = date_trunc('day', now()) and bucket like 'model:%%'""")
        if cur.fetchone()["n"] > s.ai_daily_cap:
            raise BudgetExceeded(f"global daily cap {s.ai_daily_cap} reached")


def add_tokens(bucket: str, tokens: int) -> None:
    if tokens:
        db.one("""update usage_counters set tokens = tokens + %s
                  where bucket=%s and window_kind='day' and window_start=date_trunc('day', now()) returning 1""",
               (tokens, bucket))

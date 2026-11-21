"""Database access. The orchestrator connects with a server-side role (service role on Supabase);
tenant scoping is therefore enforced explicitly in every query and in the policy engine, and RLS
protects every client-facing path."""
from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from .config import get_settings, pool_max, session_mode

_pool: ConnectionPool | None = None


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(session_mode(get_settings().database_url), min_size=0, max_size=pool_max(8), open=True,
                               kwargs={"row_factory": dict_row, "autocommit": False})
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def tx() -> Iterator[psycopg.Cursor]:
    """One short transaction. Never hold one open across a model call."""
    with pool().connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                yield cur


def one(sql: str, params: Any = None) -> dict | None:
    with tx() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def all_rows(sql: str, params: Any = None) -> list[dict]:
    with tx() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def jsonb(value: Any) -> Jsonb:
    return Jsonb(json.loads(json.dumps(value, default=str)))

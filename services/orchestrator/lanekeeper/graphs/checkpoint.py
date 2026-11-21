"""Postgres-backed LangGraph checkpointer in the private lk_checkpoints schema.

PostgresSaver needs autocommit connections with dict rows and prepare_threshold=0. setup() creates its tables
(explicit, idempotent). Durable state is written at every super-step, so an interrupted workflow survives a
process restart and resumes with Command(resume=...)."""
from __future__ import annotations

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ..config import get_settings, pool_max, session_mode

_pool: ConnectionPool | None = None
_saver: PostgresSaver | None = None


def _private_schema(conn) -> None:
    """Checkpoint tables live in lk_checkpoints (never exposed through the Supabase REST API)."""
    conn.execute("set search_path to lk_checkpoints")


def get_checkpointer() -> PostgresSaver:
    global _pool, _saver
    if _saver is None:
        _pool = ConnectionPool(session_mode(get_settings().database_url), min_size=0, max_size=max(2, pool_max(8) // 2), open=True,
                               configure=_private_schema,
                               kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row})
        _saver = PostgresSaver(_pool)
        _saver.setup()
    return _saver


def reset_checkpointer() -> None:
    """Drop the in-process saver (used to prove restart-and-resume in tests)."""
    global _pool, _saver
    if _pool is not None:
        _pool.close()
    _pool, _saver = None, None

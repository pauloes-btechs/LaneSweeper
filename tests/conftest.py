"""Shared fixtures for SQL-level tests. Rebuilds the LOCAL database once per test session."""
import json
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[1]
DB_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/lanekeeper")

NYC = "11111111-1111-4111-8111-111111111111"
HARBOR = "22222222-2222-4222-8222-222222222222"
USERS = {
    "nyc_operator": "c0000000-0000-4000-8000-000000000001",
    "nyc_dispatcher": "c0000000-0000-4000-8000-000000000002",
    "nyc_supervisor": "c0000000-0000-4000-8000-000000000003",
    "nyc_admin": "c0000000-0000-4000-8000-000000000004",
    "nyc_viewer": "c0000000-0000-4000-8000-000000000005",
    "nyc_tech": "c0000000-0000-4000-8000-000000000006",
    "nyc_supervisor2": "c0000000-0000-4000-8000-000000000007",
    "harbor_supervisor": "d0000000-0000-4000-8000-000000000001",
    "harbor_admin": "d0000000-0000-4000-8000-000000000002",
    "rider": "e0000000-0000-4000-8000-000000000001",
    "platform": "f0000000-0000-4000-8000-000000000001",
}


@pytest.fixture(scope="session")
def fresh_db():
    if os.environ.get("LK_SKIP_DB_RESET") != "1":
        subprocess.run([str(ROOT / "scripts" / "db-reset.sh")], check=True, capture_output=True,
                       env={**os.environ, "DATABASE_URL": DB_URL})
    return DB_URL


@pytest.fixture
def admin(fresh_db):
    """Superuser connection (stands in for the service role). Each test runs in a rolled-back transaction."""
    with psycopg.connect(fresh_db) as conn:
        conn.autocommit = False
        yield conn
        conn.rollback()


@contextmanager
def as_user(conn, user_key=None, role="authenticated", claims_extra=None):
    """Run statements as a Supabase client role with JWT claims, like PostgREST does."""
    claims = {"role": role}
    if user_key:
        claims["sub"] = USERS[user_key]
    claims.update(claims_extra or {})
    with conn.cursor() as cur:
        cur.execute("savepoint as_user")
        cur.execute("select set_config('request.jwt.claims', %s, true)", (json.dumps(claims),))
        cur.execute(f"set local role {role}")
        try:
            yield cur
        finally:
            cur.execute("rollback to savepoint as_user")
            cur.execute("reset role")

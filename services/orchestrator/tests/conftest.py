"""Orchestrator test fixtures: reuse the root DB fixtures and pin settings to deterministic values."""
import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("lk_root_conftest", ROOT / "tests" / "conftest.py")
_root = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_root)

fresh_db = _root.fresh_db
admin = _root.admin
as_user = _root.as_user
USERS, NYC, HARBOR = _root.USERS, _root.NYC, _root.HARBOR

os.environ.setdefault("LK_SIGNING_SECRET", "test-signing-secret")
os.environ.setdefault("LK_DEVICE_LINK_SECRET", "test-device-secret")
os.environ.pop("SUPABASE_JWT_SECRET", None)


@pytest.fixture(autouse=True)
def _fresh_settings(monkeypatch):
    """Each test starts offline with no key unless it opts in."""
    from lanekeeper import config
    from lanekeeper.models import budget
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("AI_MODE", raising=False)
    config.get_settings.cache_clear()
    budget.reset_breakers()
    yield
    config.get_settings.cache_clear()


OPERATIONAL_TABLES = (
    "remediation_verdicts", "inspection_evidence", "public_run_reports", "mission_events", "mission_stops", "robot_telemetry",
    "robot_faults", "missions", "mission_proposals", "work_orders", "assignments", "ticket_events", "tickets",
    "incident_report_links", "incident_embeddings", "hazard_incidents", "report_media", "hazard_reports", "report_consent",
    "tool_invocations", "approval_requests", "agent_events", "agent_runs", "notification_outbox", "job_attempts", "jobs",
    "usage_counters", "model_calls", "memory_entries",
)


@pytest.fixture(scope="module", autouse=True)
def _clean_operational_state(fresh_db):
    """Each test module starts from seed data only: no tickets, reports, missions or approvals left by other modules.
    (audit_log is append-only by design and is not cleared.)"""
    import psycopg
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("truncate " + ", ".join(f"public.{t}" for t in OPERATIONAL_TABLES) + " restart identity cascade")
        c.execute("update lane_cell_status set condition='unknown', last_verified_at=null")
        c.execute("update robot_status set state='docked', connectivity='online', battery_pct=100, hopper_pct=0, mission_id=null, cell_id=null")
        c.execute("update municipal_memberships set status='active', revoked_at=null, valid_until=null")
        c.execute("delete from lk_checkpoints.checkpoint_writes; delete from lk_checkpoints.checkpoint_blobs; delete from lk_checkpoints.checkpoints"
                  if _has_checkpoints(c) else "select 1")
    yield


def _has_checkpoints(conn) -> bool:
    return conn.execute("select to_regclass('lk_checkpoints.checkpoints') is not null").fetchone()[0]

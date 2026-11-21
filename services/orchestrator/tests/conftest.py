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

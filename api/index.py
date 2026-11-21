"""Vercel entrypoint: the LaneKeeper OS FastAPI app as one Python Function.

Requests to /v1/* and /health are rewritten here (vercel.json); FastAPI routes on the original path.
The orchestrator package is bundled from services/orchestrator via `includeFiles`.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "orchestrator"))

from lanekeeper.api.app import app  # noqa: E402

__all__ = ["app"]

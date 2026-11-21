"""Dependencies injected into graph nodes (makes every node testable with fakes)."""
from __future__ import annotations

from dataclasses import dataclass, field

from .models.router import ModelRouter
from .storage import LocalEvidenceStore


@dataclass
class Deps:
    router: ModelRouter = field(default_factory=ModelRouter)
    store: LocalEvidenceStore = field(default_factory=LocalEvidenceStore)
    guest_reports_per_hour: int = 12

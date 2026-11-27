"""Shared, versioned graph state and validated inbound events.

LangGraph keeps LaneKeeperState in Postgres checkpoints. Identity/role/tenant fields in the state are
OUTPUTS of server-side verification (validate_identity_and_source), never inputs trusted from a
browser, device or model.
"""
from __future__ import annotations

import operator
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, Field, field_validator, model_validator

STATE_VERSION = "lk-state-2"
HazardType = Literal["glass", "debris", "branch", "litter", "pothole", "water", "obstruction", "markings", "construction", "other"]
SourceKind = Literal["rider_app", "rider_guest", "glasses_sim", "glasses_device", "sms", "operator", "robot", "robot_sim", "external"]


class LaneKeeperState(TypedDict, total=False):
    # versioning / tracing
    workflow_version: str
    state_version: str
    thread_id: str
    run_id: str
    trace_id: str
    event_id: str
    idempotency_key: str
    replay_mode: bool
    last_checkpoint_at: str
    # identity (server-verified)
    tenant_id: str
    jurisdiction_id: str
    city_id: str
    actor_id: str | None
    client_ip: str | None          # salted hash of the transport peer, for anonymous rate limiting only
    actor_role: str | None
    source_provenance: str
    # report and case
    report: dict
    report_id: str
    ticket_id: str
    ticket_number: str
    incident_id: str
    messages: Annotated[list, operator.add]
    hazard_type: str
    severity: str
    priority: int
    priority_basis: dict
    confidence: float
    evidence_ids: list[str]
    evidence: dict
    model_version: str
    # location
    lane_cell_id: str
    location: dict
    location_accuracy_m: float | None
    match_status: str
    # duplicates
    duplicate_of: str | None
    duplicate_candidates: list[dict]
    # fleet / mission
    candidate_robot_ids: list[str]
    selected_robot_id: str
    proposed_mission_id: str
    mission_id: str
    mission_status: str
    remediation_status: str
    # policy / approval
    policy_version: str
    policy: dict
    route: str
    required_permissions: list[str]
    approval_id: str
    operation_id: str
    approval_payload_hash: str
    approval_status: str
    safety_gate_result: dict
    deadline_at: str
    # control
    pending_tool_calls: list
    errors: Annotated[list, operator.add]
    retries: int
    simulation_mode: bool
    next_action: str
    outcome: str
    scenario: str | None          # digital-twin scenario chosen server-side by the demo controller


class InboundLocation(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, ge=0, le=5000)
    heading_deg: float | None = Field(default=None, ge=0, lt=360)
    speed_mps: float | None = Field(default=None, ge=0, le=40)
    source: Literal["device_gps", "phone_gps", "exif", "pin", "cross_streets", "robot_odometry"] = "phone_gps"


class InboundConsent(BaseModel):
    policy_version: str = Field(min_length=3, max_length=40)
    share_image: bool
    share_location: bool
    allow_learning: bool = False
    notify_status: bool = False


RIDER_APPROVAL_WINDOW_S = 15 * 60      # an agent draft must be approved within 15 minutes of capture
CLOCK_SKEW_S = 120


class RiderApproval(BaseModel):
    """Proof that the rider authorised a report their device's agent drafted. Required on every agent-assisted report."""
    draft_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9:_\-.]+$")
    drafted_at: datetime
    approved_at: datetime
    channel: Literal["phone_tap", "glasses_button", "voice"]
    auto_captured_frames: int = Field(default=0, ge=0, le=5)
    approver: Literal["rider"] = "rider"

    @model_validator(mode="after")
    def approval_is_timely(self) -> RiderApproval:
        if self.drafted_at.tzinfo is None or self.approved_at.tzinfo is None:
            raise ValueError("drafted_at and approved_at must include a timezone")
        gap = (self.approved_at - self.drafted_at).total_seconds()
        if gap < 0:
            raise ValueError("a draft cannot be approved before it was drafted")
        if gap > RIDER_APPROVAL_WINDOW_S:
            raise ValueError("the rider approved this draft after it expired (15 minutes)")
        if (self.approved_at - datetime.now(UTC)).total_seconds() > CLOCK_SKEW_S:
            raise ValueError("approved_at is in the future")
        return self


class InboundReport(BaseModel):
    """Every report, whatever its source, is validated against this before entering a graph."""
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9:_\-.]+$")
    source_kind: SourceKind
    external_event_id: str | None = Field(default=None, max_length=128)
    device_id: str | None = None
    observed_at: datetime | None = None
    location: InboundLocation | None = None
    hazard_claimed: HazardType | None = None
    note: str | None = Field(default=None, max_length=500)
    consent: InboundConsent | None = None
    image_b64: str | None = Field(default=None, max_length=6_000_000)
    image_mime: Literal["image/jpeg", "image/png", "image/webp"] | None = None
    fixture_id: str | None = Field(default=None, max_length=64)
    local_detector: dict[str, Any] | None = None
    provenance: Literal["SIMULATED", "OBSERVED_TEST", "VERIFIED_FIELD"] = "SIMULATED"
    agent_assist: RiderApproval | None = None      # set when the rider's device agent drafted this report

    @field_validator("note")
    @classmethod
    def strip_note(cls, v: str | None) -> str | None:
        return v.strip() if v else v

    @model_validator(mode="after")
    def media_needs_consent(self) -> InboundReport:
        if (self.image_b64 or self.fixture_id) and not (self.consent and self.consent.share_image):
            raise ValueError("an image requires consent.share_image=true")
        if self.location and not (self.consent and self.consent.share_location):
            raise ValueError("a location requires consent.share_location=true")
        if self.image_b64 and not self.image_mime:
            raise ValueError("image_mime is required with image_b64")
        if self.agent_assist and self.source_kind not in ("rider_app", "glasses_sim", "glasses_device"):
            raise ValueError("agent-assisted reports come only from the rider app or paired glasses")
        return self


def make_thread_id(jurisdiction_slug: str, workflow: str, resource: str) -> str:
    """Scoped, collision-resistant, bounded thread ids: '<tenant>:<workflow>:<resource>'."""
    import re
    safe = lambda s: re.sub(r"[^a-z0-9\-]", "-", s.lower())[:60]  # noqa: E731
    return f"{safe(jurisdiction_slug)}:{safe(workflow)}:{safe(resource)}"[:200]

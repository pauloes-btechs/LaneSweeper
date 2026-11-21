"""Simulated LOCAL safety controller. Deterministic rules over sensor observations; independent of any
model, agent or network. It decides stops/yields itself and logs why. Agents can ask it to STOP (more
conservative); nothing remote can make it resume after an unexpected obstacle except its own rules or an
authorised supervisor command that the rules permit."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Action(str, Enum):
    CONTINUE = "continue"
    YIELD = "yield"                 # stop in place, resume automatically once clear for hold_s
    STOP = "stop"                   # stop, resume only via supervisor
    RETURN_TO_DEPOT = "return_to_depot"
    ABORT = "abort"


@dataclass(frozen=True)
class Observation:
    obstacle: str | None = None          # 'cyclist' | 'pedestrian' | 'vehicle' | None
    clear_for_s: float = 0.0             # how long the path has been clear (after an obstacle)
    connection: str = "online"           # 'online' | 'degraded' | 'lost'
    wheel_jam: bool = False
    battery_pct: float = 100.0
    hopper_pct: float = 0.0
    gps_error_m: float = 1.0
    operator_stop: bool = False
    light_level: float = 1.0             # 0 dark .. 1 daylight (affects optics only)


@dataclass(frozen=True)
class SafetyDecision:
    action: Action
    fault: str | None
    reason: str
    auto_resume: bool
    severity: str = "info"


class SafetyController:
    HOLD_S = 3.0
    GPS_STOP_M = 5.0

    def __init__(self, battery_reserve_pct: float = 25.0, hopper_max_pct: float = 100.0):
        self.reserve = battery_reserve_pct
        self.hopper_max = hopper_max_pct

    def decide(self, o: Observation) -> SafetyDecision:
        # Order matters: most conservative first.
        if o.operator_stop:
            return SafetyDecision(Action.STOP, "operator_stop", "operator pressed stop", False, "warning")
        if o.wheel_jam:
            return SafetyDecision(Action.STOP, "wheel_jam", "drive wheel jammed; awaiting technician", False, "critical")
        if o.connection == "lost":
            return SafetyDecision(Action.STOP, "connection_lost", "lost link to operations; safe stop in place", False, "critical")
        if o.obstacle in ("cyclist", "pedestrian"):
            if o.clear_for_s >= self.HOLD_S:
                return SafetyDecision(Action.CONTINUE, None, f"path clear for {o.clear_for_s:.0f}s after {o.obstacle}", True)
            return SafetyDecision(Action.YIELD, f"obstacle_{o.obstacle}", f"{o.obstacle} in path: yield and hold", True, "warning")
        if o.obstacle == "vehicle":
            return SafetyDecision(Action.STOP, "vehicle_blocking", "vehicle blocking the lane; cell skipped", False, "warning")
        if o.gps_error_m > self.GPS_STOP_M:
            return SafetyDecision(Action.YIELD, "gps_drift", f"position error {o.gps_error_m:.0f} m > {self.GPS_STOP_M:.0f} m", True,
                                  "warning")
        if o.battery_pct <= self.reserve:
            return SafetyDecision(Action.RETURN_TO_DEPOT, "low_battery", f"battery {o.battery_pct:.0f}% at reserve", False, "warning")
        if o.hopper_pct >= self.hopper_max:
            return SafetyDecision(Action.RETURN_TO_DEPOT, "hopper_full", "hopper full", False, "warning")
        return SafetyDecision(Action.CONTINUE, None, "nominal", True)

    def may_supervisor_resume(self, fault: str | None, o: Observation) -> tuple[bool, str]:
        """A supervisor resume is still subject to local rules: it cannot override a present hazard."""
        if o.wheel_jam:
            return False, "wheel still jammed"
        if o.connection == "lost":
            return False, "link still lost"
        if o.obstacle and o.clear_for_s < self.HOLD_S:
            return False, f"{o.obstacle} still in path"
        if o.battery_pct <= self.reserve:
            return False, "battery at reserve; recall instead"
        return True, "local checks pass"

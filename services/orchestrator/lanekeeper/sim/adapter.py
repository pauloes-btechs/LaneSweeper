"""Robot adapter boundary.

Perception, planning, mission acceptance, the local safety controller, actuation, telemetry and evidence
are separate concerns. In this build ONLY SimulatedRobotAdapter can execute a mission. HardwareRobotAdapter
is an inert placeholder that refuses at construction and on every call; there is no driver code behind it.
A future physical pilot needs a separately audited hardware gateway, a locally enforced fail-safe
controller, a mechanical E-stop, trained field operators and regulatory review (docs/ROBOT_SAFETY_BOUNDARY.md).
"""
from __future__ import annotations

from typing import Protocol

from ..config import get_settings


class HardwareUnavailable(RuntimeError):
    pass


class RobotAdapter(Protocol):
    kind: str

    def accept_mission(self, mission: dict) -> None: ...
    def stop(self, robot_id: str, reason: str) -> None: ...
    def recall(self, robot_id: str, reason: str) -> None: ...


class SimulatedRobotAdapter:
    kind = "simulated"

    def accept_mission(self, mission: dict) -> None:
        if mission.get("simulation_mode") is not True:
            raise HardwareUnavailable("simulated adapter only accepts simulation_mode=true missions")

    def stop(self, robot_id: str, reason: str) -> None:   # recorded by the runner
        return None

    def recall(self, robot_id: str, reason: str) -> None:
        return None


class HardwareRobotAdapter:
    kind = "hardware"

    def __init__(self, *_, **__):
        # Double guard: the setting cannot even be turned on (config refuses), and this class refuses anyway.
        if get_settings().hardware_gateway_enabled is not False:
            raise HardwareUnavailable("hardware gateway flag is set but no gateway exists in this build")
        raise HardwareUnavailable("real robot control is not available in LaneKeeper OS demo builds")

    def accept_mission(self, mission: dict) -> None:   # pragma: no cover - unreachable
        raise HardwareUnavailable("real robot control is not available")

    stop = recall = accept_mission


def adapter_for(robot_mode: str) -> RobotAdapter:
    if robot_mode == "simulated":
        return SimulatedRobotAdapter()
    return HardwareRobotAdapter()

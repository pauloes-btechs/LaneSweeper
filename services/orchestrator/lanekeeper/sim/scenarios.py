"""Deterministic digital-twin scenarios. Each injects observations at route positions (fractions of the route,
so they apply to any mission length). Same scenario + seed -> identical events and event ids."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Injection:
    at_frac: float                  # 0..1 position along the lane route
    obs: dict                       # Observation overrides
    duration_s: float = 0.0         # how long the condition lasts (sim time)
    label: str = ""


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    injections: tuple[Injection, ...] = ()
    after_fixture: str = "after-clean-13"            # after-sweep frame for target cells
    after_overrides: dict = field(default_factory=dict)  # target index -> fixture id
    before_override: str | None = None               # robot's own before-frame on arrival
    battery_start: float | None = None
    hopper_start: float | None = None
    light_level: float = 1.0


SCENARIOS: dict[str, Scenario] = {s.key: s for s in [
    Scenario("nominal", "Nominal sweep"),
    Scenario("cyclist_enters", "Cyclist enters the robot's path",
             (Injection(0.35, {"obstacle": "cyclist"}, 6.0, "cyclist overtakes into sweep path"),)),
    Scenario("pedestrian", "Pedestrian steps into the lane",
             (Injection(0.5, {"obstacle": "pedestrian"}, 9.0, "pedestrian crossing mid-block"),)),
    Scenario("car_blocking", "Car parked in the protected lane",
             (Injection(0.6, {"obstacle": "vehicle"}, 0.0, "delivery van in lane"),)),
    Scenario("wheel_jam", "Drive wheel jams", (Injection(0.4, {"wheel_jam": True}, 0.0, "debris lodged in drive wheel"),)),
    Scenario("low_battery", "Battery reaches reserve mid-mission", battery_start=31.0),
    Scenario("optical_false_positive", "Robot camera finds the reported 'glass' is a wet reflection",
             before_override="confounder-water-05"),
    Scenario("rain_darkness", "Heavy rain and darkness degrade optics", light_level=0.15, after_fixture="after-dark-15"),
    Scenario("gps_drift", "GPS drift beyond tolerance", (Injection(0.3, {"gps_error_m": 9.0}, 12.0, "urban canyon multipath"),)),
    Scenario("no_coverage", "Cellular dead zone: link degrades then drops",
             (Injection(0.45, {"connection": "degraded"}, 5.0, "weak signal"),
              Injection(0.5, {"connection": "lost"}, 0.0, "no service coverage"))),
    Scenario("disconnected", "Network link lost", (Injection(0.55, {"connection": "lost"}, 0.0, "backhaul outage"),)),
    Scenario("hopper_full", "Hopper fills before route end", hopper_start=96.0),
    Scenario("failed_verification", "Route completes but glass remains at the lane edge",
             after_overrides={0: "after-partial-14"}),
]}


def get(key: str | None) -> Scenario:
    return SCENARIOS.get(key or "nominal", SCENARIOS["nominal"])

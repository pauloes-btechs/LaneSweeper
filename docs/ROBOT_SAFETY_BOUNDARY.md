# Robot safety boundary

**Position: no software path in this repository can move a physical robot.** The LK-01 exists only as a design and a digital twin. Everything below is enforced in code and tested.

## Layers that prevent real-world actuation

| # | Layer | Mechanism | Test |
|---|---|---|---|
| 1 | Configuration | `LK_HARDWARE_GATEWAY_ENABLED=true` makes the orchestrator refuse to start | `config.get_settings` |
| 2 | Feature flag | DB trigger refuses `hardware_gateway = true` | `test_hardware_gateway_flag_cannot_be_enabled` |
| 3 | Permission matrix | `robot.command_real` is granted to no role; the `physical` side effect is always denied, even for admins | `test_physical_side_effect_always_denied_even_for_admins` |
| 4 | Approval ledger | `dispatch_simulated_mission` requires `simulation_mode = true` (CHECK constraint); real dispatch cannot even be requested | `test_real_dispatch_cannot_be_requested`, `test_real_dispatch_cannot_even_be_requested` |
| 5 | Mission table | trigger: the robot must be `mode = 'simulated'` and the approval `consumed`; `simulation_mode` CHECK | `test_mission_on_real_robot_is_impossible`, `test_mission_requires_consumed_approval` |
| 6 | Adapter | `HardwareRobotAdapter` raises at construction; there is no driver code | `sim/adapter.py` |
| 7 | Model output | Robotics ER output is parsed as image-space points only; waypoint, speed, steering, motor, brake, throttle, actuator and command fields are discarded and logged | `test_vision_parser_keeps_observations_and_discards_motion_fields` |
| 8 | Copilot | the tool set excludes dispatch and assignment; injected plans are dropped | `test_injected_plan_cannot_dispatch_or_assign` |

## Simulated local safety controller

`sim/safety.py` is deterministic, has no model input, and decides before every simulated move:

| Condition | Action | Resume |
|---|---|---|
| Operator stop | stop | supervisor only |
| Wheel jam | stop (critical) | after a technician clears it, then a supervisor |
| Lost connectivity | safe stop in place | supervisor, after the link is restored and local checks pass |
| Cyclist / pedestrian in path | yield | automatic only after the path has been clear for 3 s |
| Vehicle blocking the lane | stop; cell skipped | next cell |
| GPS error > 5 m | yield | when the fix recovers |
| Battery ≤ reserve (25 %) / hopper full | return to depot; mission aborted | re-plan |

A supervisor resume is itself checked by `may_supervisor_resume`. It can't override a present hazard. Agents can request a stop, which is always allowed because it's more conservative, but have no way to request a resume.

## What a physical pilot would additionally need (not built)

A separately audited hardware gateway on the robot. A locally enforced fail-safe controller independent of the network. A mechanical E-stop. A geofenced speed governor. Trained field operators on site. Insurance. NYC DOT / DSNY authorisation and New York State vehicle and traffic law review. Third-party safety assessment. A closed-course test programme before any public right-of-way operation. See [PRODUCTION_GAPS.md](PRODUCTION_GAPS.md).

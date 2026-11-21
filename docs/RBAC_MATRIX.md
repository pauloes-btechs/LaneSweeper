# RBAC matrix (generated)

Generated from `role_permissions` (migration `20261121000200`) by `scripts/gen_rbac_matrix.py`. Roles come only from
server-side `municipal_memberships`. Claims in a token are ignored. `robot.command_real` is granted to no role.

| Permission | public rider | authorized reporter | city viewer | city operator | city dispatcher | city supervisor | fleet technician | city admin | platform admin |
|---|---|---|---|---|---|---|---|---|---|
| `admin.budget` | · | · | · | · | · | · | · | ✓ | · |
| `admin.membership` | · | · | · | · | · | · | · | ✓ | · |
| `admin.policy` | · | · | · | · | · | · | · | ✓ | · |
| `analytics.view` | · | · | ✓ | ✓ | ✓ | ✓ | · | ✓ | · |
| `copilot.use` | · | · | · | ✓ | ✓ | ✓ | · | · | · |
| `evidence.view` | · | · | · | ✓ | ✓ | ✓ | · | · | · |
| `fleet.resume` | · | · | · | · | · | ✓ | · | · | · |
| `fleet.service` | · | · | · | · | · | · | ✓ | · | · |
| `fleet.stop` | · | · | · | ✓ | ✓ | ✓ | ✓ | · | · |
| `fleet.view` | · | · | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | · |
| `knowledge.manage` | · | · | · | · | · | ✓ | · | ✓ | · |
| `knowledge.view` | · | · | ✓ | ✓ | ✓ | ✓ | · | ✓ | · |
| `map.view_public` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | · | ✓ | ✓ |
| `mission.approve` | · | · | · | · | ✓ | ✓ | · | · | · |
| `mission.cancel` | · | · | · | ✓ | ✓ | ✓ | · | · | · |
| `mission.propose` | · | · | · | ✓ | ✓ | ✓ | · | · | · |
| `notice.draft` | · | · | · | ✓ | ✓ | ✓ | · | · | · |
| `notice.publish` | · | · | · | · | · | ✓ | · | · | · |
| `platform.health` | · | · | · | · | · | · | · | · | ✓ |
| `report.create` | ✓ | ✓ | · | · | · | · | · | · | · |
| `report.view_own` | · | ✓ | · | · | · | · | · | · | · |
| `robot.command_real` | · | · | · | · | · | · | · | · | · |
| `telemetry.view` | · | · | · | ✓ | ✓ | ✓ | ✓ | · | · |
| `ticket.assign` | · | · | · | ✓ | ✓ | ✓ | · | · | · |
| `ticket.update` | · | · | · | ✓ | ✓ | ✓ | · | · | · |
| `ticket.view` | · | · | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | · |
| `verdict.override` | · | · | · | · | · | ✓ | · | · | · |

Approve-type actions additionally require: an active membership at decide time and again at consume time, separation of
duties (requester ≠ approver), an unexpired single-use approval and an exact payload hash.

"""Generate docs/RBAC_MATRIX.md from the role_permissions table in the local database."""
import os
from pathlib import Path

import psycopg

ROLES = ["public_rider", "authorized_reporter", "city_viewer", "city_operator", "city_dispatcher", "city_supervisor",
         "fleet_technician", "city_admin", "platform_admin"]
with psycopg.connect(os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/lanekeeper")) as c:
    rows = c.execute("select role, permission from role_permissions").fetchall()
perms = sorted({p for _, p in rows} | {"robot.command_real"})
have = {(r, p) for r, p in rows}
lines = ["# RBAC matrix (generated)", "",
         "Generated from `role_permissions` (migration `20261121000200`) by `scripts/gen_rbac_matrix.py`. Roles come only from",
         "server-side `municipal_memberships`. Claims in a token are ignored. `robot.command_real` is granted to no role.", "",
         "| Permission | " + " | ".join(r.replace("_", " ") for r in ROLES) + " |", "|---|" + "---|" * len(ROLES)]
for p in perms:
    lines.append(f"| `{p}` | " + " | ".join("✓" if (r, p) in have else "·" for r in ROLES) + " |")
lines += ["", "Approve-type actions additionally require: an active membership at decide time and again at consume time, separation of",
          "duties (requester ≠ approver), an unexpired single-use approval and an exact payload hash."]
Path(__file__).resolve().parents[1].joinpath("docs/RBAC_MATRIX.md").write_text("\n".join(lines) + "\n")
print(f"{len(perms)} permissions x {len(ROLES)} roles")

"""Local demo helpers. Mounted only when LK_DEMO_MODE=1 and no real Supabase JWT secret is configured.
Seeded identities get dev tokens (identity only; roles still come from memberships). Never deploy with this on."""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException

from .. import db
from ..auth import mint_dev_token
from ..config import get_settings

router = APIRouter(prefix="/v1/demo")

SEEDED = {
    "operator": ("c0000000-0000-4000-8000-000000000001", "NYC city operator"),
    "dispatcher": ("c0000000-0000-4000-8000-000000000002", "NYC dispatcher"),
    "supervisor": ("c0000000-0000-4000-8000-000000000003", "NYC supervisor"),
    "supervisor2": ("c0000000-0000-4000-8000-000000000007", "NYC second supervisor"),
    "admin": ("c0000000-0000-4000-8000-000000000004", "NYC city admin"),
    "viewer": ("c0000000-0000-4000-8000-000000000005", "NYC viewer"),
    "technician": ("c0000000-0000-4000-8000-000000000006", "NYC fleet technician"),
    "harbor_supervisor": ("d0000000-0000-4000-8000-000000000001", "Harbor City supervisor (other tenant)"),
    "rider": ("e0000000-0000-4000-8000-000000000001", "Registered rider"),
}


def enabled() -> bool:
    return os.environ.get("LK_DEMO_MODE") == "1" and not get_settings().supabase_jwt_secret


@router.get("/identities")
def identities():
    return [{"key": k, "label": v[1]} for k, v in SEEDED.items()]


@router.post("/token/{who}")
def token(who: str):
    if who not in SEEDED:
        raise HTTPException(404, "unknown demo identity")
    return {"token": mint_dev_token(SEEDED[who][0]), "user_id": SEEDED[who][0], "label": SEEDED[who][1],
            "note": "local demo token: identity only; permissions come from server-side memberships"}


@router.post("/reset-fleet")
def reset_fleet():
    """Soft reset for live demos: abort unfinished simulated missions and dock robots at full charge."""
    db.one("""update missions set status='aborted', abort_reason='demo reset', ended_at=now()
              where status in ('dispatched','running','paused_safety','returning') returning 1""")
    db.one("""update robot_status rs set state='docked', connectivity='online', battery_pct=100, hopper_pct=0, mission_id=null, cell_id=null,
                     position=(select d.location from robots r join depots d on d.id=r.home_depot_id where r.id=rs.robot_id) returning 1""")
    return {"ok": True}

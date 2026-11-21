"""Local demo helpers. Mounted only when LK_DEMO_MODE=1 and no real Supabase JWT secret is configured.
Seeded identities get dev tokens (identity only; roles still come from memberships). Never deploy with this on."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import db
from ..auth import mint_dev_token
from ..config import demo_identities_allowed

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
    return demo_identities_allowed()


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


SCENARIOS = {
    "A": ("Glass reported through SIMULATED GLASSES on 1st Ave", dict(seq=40, fixture="glass-1av-dawn-01", hazard="glass", source="glasses_sim")),
    "B": ("Pothole: routed to roadway repair, no robot", dict(seq=100, fixture="pothole-09", hazard="pothole", source="rider_guest")),
    "C": ("Second report of the same glass 25 m away", dict(seq=41, fixture="glass-1av-dawn-01", hazard="glass", source="rider_guest")),
    "E": ("Cyclist enters the robot's path during the sweep", dict(seq=60, fixture="glass-1av-dawn-01", hazard="glass", source="rider_guest", scenario="cyclist_enters")),
    "F": ("Route completes but glass remains", dict(seq=150, fixture="glass-wide-04", hazard="glass", source="rider_guest", scenario="failed_verification")),
    "G": ("Robot loses its network link", dict(seq=140, fixture="glass-1av-night-02", hazard="glass", source="rider_guest", scenario="disconnected")),
    "G2": ("Battery reaches reserve on the way", dict(seq=120, fixture="glass-1av-dawn-01", hazard="glass", source="rider_guest", scenario="low_battery")),
    "X": ("Wet reflection reported as glass (robot finds none)", dict(seq=90, fixture="glass-1av-dawn-01", hazard="glass", source="rider_guest", scenario="optical_false_positive")),
}


@router.get("/scenarios")
def scenarios():
    return [{"key": k, "title": v[0], "twin_scenario": v[1].get("scenario")} for k, v in SCENARIOS.items()]


@router.post("/scenario/{key}")
def run_scenario(key: str):
    """Submit the scenario's seeded report. Approvals and resumes then happen in the console like real work."""
    import uuid

    from .. import auth, workflows
    from ..graphs import glasses
    if key not in SCENARIOS:
        raise HTTPException(404, "unknown scenario")
    title, s = SCENARIOS[key]
    c = db.one("""select extensions.st_y(extensions.st_centroid(geom::extensions.geometry)) lat,
                         extensions.st_x(extensions.st_centroid(geom::extensions.geometry)) lng from lane_cells where id=%s""",
               (f"MN-1AV-{s['seq']:04d}",))
    rep = {"idempotency_key": f"scn-{key}-{uuid.uuid4().hex[:10]}", "source_kind": s["source"], "fixture_id": s["fixture"],
           "hazard_claimed": s["hazard"], "note": f"[demo {key}] {title}",
           "location": {"lat": c["lat"], "lng": c["lng"], "accuracy_m": 8, "source": "phone_gps", "speed_mps": 0.0},
           "consent": {"policy_version": "rider-2026.11", "share_image": True, "share_location": True, "notify_status": True}}
    if s["source"] == "glasses_sim":
        rep["local_detector"] = {"label": "possible_glass", "score": 0.86, "consecutive_frames": 5, "model": "lk-local-heuristic-v1",
                                 "warning_issued": True}
        out = glasses.handle_device_event(rep, scenario=s.get("scenario"))
        thread = out.get("ticket_thread")
    else:
        out = workflows.submit_report(rep, auth.ANONYMOUS, scenario=s.get("scenario"))
        thread = out["thread_id"]
    res = workflows.get_thread(thread) if thread else {}
    return {"scenario": key, "title": title, "thread_id": thread, "ticket_number": res.get("ticket_number"), "route": res.get("route"),
            "status": res.get("status"), "run_id": res.get("run_id"), "next": "approve in Agents & Approvals" if res.get("status") == "waiting" else None}


@router.post("/fleet-size/{n}")
def fleet_size(n: int):
    """Adjust the SIMULATED fleet (2..10). Extra robots are LK-SIM-03.. at alternating depots; removal takes them offline."""
    if not 2 <= n <= 10:
        raise HTTPException(400, "fleet size must be 2..10")
    nyc = "11111111-1111-4111-8111-111111111111"
    for k in range(3, 11):
        rid = f"LK-SIM-{k:02d}"
        depot = "NYC-DEPOT-E20" if k % 2 else "NYC-DEPOT-E60"
        if k <= n:
            db.one("""insert into robots (id, jurisdiction_id, name, home_depot_id) values (%s,%s,%s,%s) on conflict (id) do nothing returning 1""",
                   (rid, nyc, f"Simulated robot {k:02d}", depot))
            db.one("insert into robot_capabilities (robot_id) values (%s) on conflict do nothing returning 1", (rid,))
            db.one("""insert into robot_status (robot_id, jurisdiction_id, position)
                      select %s, %s, location from depots where id=%s on conflict (robot_id) do update set connectivity='online', state='docked'
                      returning 1""", (rid, nyc, depot))
        else:
            db.one("update robot_status set connectivity='lost', state='offline' where robot_id=%s returning 1", (rid,))
    return {"simulated_fleet": n}

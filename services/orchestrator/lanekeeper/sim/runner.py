"""Digital-twin mission runner.

Executes an APPROVED, simulation-only mission over real lane cells, in synthetic time. Every step goes through
the local SafetyController. Progress lives in mission_stops/robot_status, so execution can pause (safety stop,
lost link) and resume later from the last completed stop without repeating work. Event ids are deterministic:
'<mission>:<n>' — re-running a completed mission emits nothing new.

Synthetic times (sim_time_s) are simulation estimates; they are reported separately from measured software timings.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import timedelta

from .. import db
from ..deps import Deps
from ..models.google import Image
from ..storage import fixture_bytes
from . import scenarios
from .adapter import adapter_for
from .safety import Action, Observation, SafetyController

CELL_M = 25.0
SPEED_MPS = 1.0
SWEEP_S = 60.0
HOPPER_PER_SWEEP = 8.0


class MissionNotRunnable(Exception):
    pass


@dataclass
class Ctx:
    mission: dict
    robot: dict
    stops: list[dict]
    targets: set[str]
    scenario: scenarios.Scenario
    n: int            # events emitted so far
    t: float          # synthetic seconds elapsed
    fired: set[str]


def _load(mission_id: str) -> Ctx:
    m = db.one("""select m.*, m.id::text as id, m.jurisdiction_id::text as jurisdiction_id, p.work_order_ids, p.route_cell_ids
                    from missions m join mission_proposals p on p.id = m.proposal_id where m.id = %s""", (mission_id,))
    if not m:
        raise MissionNotRunnable("mission not found")
    r = db.one("""select r.id, r.mode, s.*, c.wh_per_km, c.battery_wh from robots r join robot_status s on s.robot_id = r.id
                  join robot_capabilities c on c.robot_id = r.id where r.id = %s""", (m["robot_id"],))
    stops = db.all_rows("select seq, cell_id, status from mission_stops where mission_id=%s order by seq", (mission_id,))
    targets = {row["cell_id"] for row in db.all_rows("select cell_id from work_orders where id = any(%s::uuid[])",
                                                         (m["work_order_ids"],)) if row["cell_id"]}
    ev = db.one("select count(*) n, coalesce(max(sim_time_s),0) t from mission_events where mission_id=%s", (mission_id,))
    fired = {row["detail"].get("label") for row in db.all_rows(
        "select detail from mission_events where mission_id=%s and kind='injection'", (mission_id,))}
    return Ctx(m, r, stops, targets, scenarios.get(m["scenario"]), int(ev["n"]), float(ev["t"]), fired)


def _emit(cx: Ctx, kind: str, actor: str, detail: dict) -> None:
    cx.n += 1
    db.one("""insert into mission_events (mission_id, jurisdiction_id, event_id, at, sim_time_s, kind, actor, detail)
              values (%s,%s,%s,%s,%s,%s,%s,%s) on conflict (event_id) do nothing returning id""",
           (cx.mission["id"], cx.mission["jurisdiction_id"], f"{cx.mission['id']}:{cx.n:05d}",
            cx.mission["dispatched_at"] + timedelta(seconds=cx.t), round(cx.t, 1), kind, actor, db.jsonb(detail)))


def _telemetry(cx: Ctx, cell: str, state: str, speed: float) -> None:
    db.one("""insert into robot_telemetry (robot_id, jurisdiction_id, mission_id, event_id, at, sim_time_s, position, cell_id,
                                          speed_mps, battery_pct, hopper_pct, state)
              select %s,%s,%s,%s,%s,%s, extensions.st_centroid(geom::extensions.geometry)::extensions.geography, id, %s,%s,%s,%s
                from lane_cells where id = %s
              on conflict (event_id) do nothing returning id""",
           (cx.robot["id"], cx.mission["jurisdiction_id"], cx.mission["id"], f"t:{cx.mission['id']}:{cx.n:05d}",
            cx.mission["dispatched_at"] + timedelta(seconds=cx.t), round(cx.t, 1), speed, round(cx.robot["battery_pct"], 2),
            round(cx.robot["hopper_pct"], 2), state, cell))
    db.one("""update robot_status set state=%s, cell_id=%s, battery_pct=%s, hopper_pct=%s, mission_id=%s, updated_at=now(),
                     position=(select extensions.st_centroid(geom::extensions.geometry)::extensions.geography from lane_cells where id=%s)
              where robot_id=%s returning 1""",
           (state, cell, round(cx.robot["battery_pct"], 2), round(cx.robot["hopper_pct"], 2), cx.mission["id"], cell, cx.robot["id"]))


def _fault(cx: Ctx, code: str, severity: str, action: str, detail: dict) -> None:
    db.one("""insert into robot_faults (robot_id, jurisdiction_id, mission_id, code, severity, safety_action, detail)
              values (%s,%s,%s,%s,%s,%s,%s) returning id""",
           (cx.robot["id"], cx.mission["jurisdiction_id"], cx.mission["id"], code, severity, action, db.jsonb(detail)))
    db.one("""insert into notification_outbox (jurisdiction_id, channel, recipient_ref, template, body, dedupe_key)
              values (%s,'console','role:city_supervisor','robot_safety_event',%s,%s) on conflict (dedupe_key) do nothing returning id""",
           (cx.mission["jurisdiction_id"], db.jsonb({"robot": cx.robot["id"], "code": code, "action": action, **detail}),
            f"fault:{cx.mission['id']}:{code}:{cx.n}"))


def _set_mission(cx: Ctx, status: str, reason: str | None = None) -> None:
    db.one("""update missions set status=%s, abort_reason=coalesce(%s, abort_reason),
                     started_at=coalesce(started_at, now()), ended_at=case when %s in ('completed','aborted','failed') then now() end
              where id=%s returning 1""", (status, reason, status, cx.mission["id"]))
    cx.mission["status"] = status


def _drain(cx: Ctx, meters: float) -> None:
    cx.robot["battery_pct"] -= meters / 1000 * cx.robot["wh_per_km"] / cx.robot["battery_wh"] * 100


def _inspect(cx: Ctx, deps: Deps, cell: str, phase: str, fixture: str) -> dict:
    fx = fixture_bytes(fixture)
    images = [Image(*fx)] if fx else []
    prompt = (f"{'Before' if phase == 'before' else 'After'}-sweep inspection frame of a protected bike lane cell. "
              "Is broken glass visible? Distinguish glass from reflections, water, foil, plastic and paint.")
    v = deps.router.vision(prompt, images=images, fixture_id=fixture, route="vision.compare" if phase == "after" else "vision.localize",
                           jurisdiction_id=cx.mission["jurisdiction_id"])
    analysis = {**v.model_dump(), "light_level": cx.scenario.light_level}
    row = db.one("""insert into inspection_evidence (jurisdiction_id, mission_id, cell_id, phase, fixture_id, analysis, provenance)
                    values (%s,%s,%s,%s,%s,%s,'SIMULATED') returning id::text""",
                 (cx.mission["jurisdiction_id"], cx.mission["id"], cell, phase, fixture, db.jsonb(analysis)))
    return {"evidence_id": row["id"], "verdict": v.verdict, "mode": v.provenance.mode}


def _before_fixture(cx: Ctx) -> str:
    if cx.scenario.before_override:
        return cx.scenario.before_override
    r = db.one("""select rm.fixture_id from work_orders w join tickets t on t.id = w.ticket_id
                  join incident_report_links l on l.incident_id = t.incident_id and l.link_kind = 'origin'
                  join report_media rm on rm.report_id = l.report_id
                  where w.id = any(%s::uuid[]) and rm.fixture_id is not null limit 1""", (cx.mission["work_order_ids"],))
    return (r or {}).get("fixture_id") or "glass-1av-dawn-01"


def run(mission_id: str, deps: Deps, *, max_wall_s: float = 20.0) -> dict:
    """Advance the mission until it completes or a safety rule pauses it. Returns a summary."""
    t_wall = time.monotonic()
    cx = _load(mission_id)
    adapter_for(cx.robot["mode"]).accept_mission({"simulation_mode": cx.mission["simulation_mode"]})
    if cx.mission["status"] in ("completed", "aborted", "failed"):
        return summary(mission_id)
    if cx.mission["status"] == "paused_safety":
        raise MissionNotRunnable("mission is paused by the safety controller; a supervisor must resume it")
    pol = (db.one("select body from policy_versions where active and jurisdiction_id=%s", (cx.mission["jurisdiction_id"],))
           or {"body": {}})["body"]
    safety = SafetyController(battery_reserve_pct=float((pol.get("fleet") or {}).get("min_battery_reserve_pct", 25)))
    if cx.n == 0:
        if cx.scenario.battery_start is not None:
            cx.robot["battery_pct"] = cx.scenario.battery_start
        if cx.scenario.hopper_start is not None:
            cx.robot["hopper_pct"] = cx.scenario.hopper_start
        _set_mission(cx, "running")
        _emit(cx, "started", "simulator", {"scenario": cx.scenario.key, "seed": cx.mission["seed"], "provenance": "SIMULATED",
                                           "robot": cx.robot["id"], "route_cells": len(cx.stops)})
    route_len = max(1, len(cx.stops))
    for i, stop in enumerate(cx.stops):
        if stop["status"] != "pending":
            continue
        if time.monotonic() - t_wall > max_wall_s:
            return {**summary(mission_id), "note": "wall-clock budget reached; call run again to continue"}
        cell = stop["cell_id"]
        obs = dict(battery_pct=cx.robot["battery_pct"], hopper_pct=cx.robot["hopper_pct"], light_level=cx.scenario.light_level,
                   connection=cx.robot.get("connectivity") or "online")
        outcome = None
        for inj in cx.scenario.injections:
            if i / route_len >= inj.at_frac and inj.label not in cx.fired:
                cx.fired.add(inj.label)
                outcome = _handle_injection(cx, safety, inj, obs, cell, stop)
                if outcome in ("paused", "blocked"):
                    break
        if outcome == "paused":
            return summary(mission_id)
        if outcome == "blocked":
            continue
        dec = safety.decide(Observation(**obs))
        if dec.action is Action.RETURN_TO_DEPOT:
            _fault(cx, dec.fault, dec.severity, "return_to_depot", {"reason": dec.reason, "cell": cell})
            _emit(cx, "return_to_depot", "safety_controller", {"reason": dec.reason, "cell": cell})
            db.one("""update mission_stops set status='skipped' where mission_id=%s and status='pending' returning 1""",
                   (cx.mission["id"],))
            _set_mission(cx, "aborted", dec.reason)
            db.one("update robot_status set state='returning', updated_at=now() where robot_id=%s returning 1", (cx.robot["id"],))
            _finish_robot(cx)
            return summary(mission_id)
        # move into the cell
        cx.t += CELL_M / SPEED_MPS
        _drain(cx, CELL_M)
        _telemetry(cx, cell, "en_route" if cell not in cx.targets else "sweeping", SPEED_MPS)
        new_status = "arrived"
        if cell in cx.targets:
            before = _inspect(cx, deps, cell, "before", _before_fixture(cx))
            if before["verdict"] in ("not_glass", "clear"):
                _emit(cx, "optical_false_positive", "robot_inspection",
                      {"cell": cell, "verdict": before["verdict"], "note": "no glass seen on arrival; not swept"})
                new_status = "skipped"
            else:
                cx.t += SWEEP_S
                cx.robot["hopper_pct"] = min(100.0, cx.robot["hopper_pct"] + HOPPER_PER_SWEEP)
                db.one("update lane_cell_status set condition='cleaning', updated_at=now() where cell_id=%s returning 1", (cell,))
                idx = sorted(cx.targets).index(cell)
                after_fx = cx.scenario.after_overrides.get(idx, cx.scenario.after_fixture)
                after = _inspect(cx, deps, cell, "after", after_fx)
                _emit(cx, "swept", "simulator", {"cell": cell, "before": before, "after": after})
                db.one("update lane_cell_status set condition='cleared_unverified', updated_at=now() where cell_id=%s returning 1",
                       (cell,))
                new_status = "swept"
        db.one("update mission_stops set status=%s, swept_at=case when %s='swept' then now() end where mission_id=%s and seq=%s returning 1",
               (new_status, new_status, cx.mission["id"], stop["seq"]))
    _emit(cx, "route_completed", "simulator", {"note": "route finished; cleanup NOT yet verified"})
    _set_mission(cx, "completed")
    _finish_robot(cx)
    return summary(mission_id)


def _handle_injection(cx: Ctx, safety: SafetyController, inj, obs: dict, cell: str, stop: dict) -> str:
    """Apply one scenario injection through the safety controller. Returns 'continue' | 'blocked' | 'paused'."""
    _emit(cx, "injection", "scenario", {"label": inj.label, "obs": inj.obs, "cell": cell})
    dec = safety.decide(Observation(**{**obs, **inj.obs}))
    if dec.action is Action.YIELD:
        _fault(cx, dec.fault, dec.severity, "yield", {"reason": dec.reason, "cell": cell})
        _emit(cx, "safety_yield", "safety_controller", {"reason": dec.reason, "cell": cell})
        _telemetry(cx, cell, "paused_safety", 0.0)
        held = max(inj.duration_s, safety.HOLD_S)
        cx.t += held
        cleared = {**obs, **{k: v for k, v in inj.obs.items() if k == "obstacle"}, "clear_for_s": held}
        after = safety.decide(Observation(**cleared))
        if after.action is not Action.CONTINUE:
            _emit(cx, "safety_stop", "safety_controller", {"reason": after.reason, "cell": cell, "auto_resume": False})
            _set_mission(cx, "paused_safety", after.reason)
            return "paused"
        _emit(cx, "safety_resume_auto", "safety_controller", {"reason": after.reason, "cell": cell})
        db.one("update robot_faults set cleared_at=now(), cleared_by='safety_controller' where mission_id=%s "
               "and code=%s and cleared_at is null returning 1", (cx.mission["id"], dec.fault))
        return "continue"
    if dec.action is Action.STOP and dec.fault == "vehicle_blocking":
        _fault(cx, dec.fault, dec.severity, "stop", {"reason": dec.reason, "cell": cell})
        _emit(cx, "cell_blocked", "safety_controller", {"reason": dec.reason, "cell": cell})
        db.one("update mission_stops set status='blocked' where mission_id=%s and seq=%s returning 1", (cx.mission["id"], stop["seq"]))
        return "blocked"
    if dec.action is Action.STOP:
        if inj.obs.get("connection") == "lost":
            db.one("update robot_status set connectivity='lost' where robot_id=%s returning 1", (cx.robot["id"],))
        _fault(cx, dec.fault, dec.severity, "stop", {"reason": dec.reason, "cell": cell})
        _emit(cx, "safety_stop", "safety_controller", {"reason": dec.reason, "cell": cell, "auto_resume": False})
        _telemetry(cx, cell, "paused_safety", 0.0)
        _set_mission(cx, "paused_safety", dec.reason)
        return "paused"
    if inj.obs.get("connection") == "degraded":
        db.one("update robot_status set connectivity='degraded' where robot_id=%s returning 1", (cx.robot["id"],))
        cx.robot["connectivity"] = "degraded"
    return "continue"


def _finish_robot(cx: Ctx) -> None:
    d = db.one("select extensions.st_astext(d.location::extensions.geometry) wkt from robots r join depots d on d.id=r.home_depot_id where r.id=%s",
               (cx.robot["id"],))
    db.one("""update robot_status set state='docked', mission_id=null, cell_id=null, battery_pct=%s, hopper_pct=%s,
                     position=extensions.st_geogfromtext(%s), updated_at=now() where robot_id=%s returning 1""",
           (round(max(cx.robot["battery_pct"], 0), 2), round(cx.robot["hopper_pct"], 2), d["wkt"], cx.robot["id"]))
    _emit(cx, "docked", "simulator", {"battery_pct": round(cx.robot["battery_pct"], 1)})


def summary(mission_id: str) -> dict:
    m = db.one("select id::text, status, robot_id, scenario, abort_reason from missions where id=%s", (mission_id,))
    stops = db.all_rows("select status, count(*) n from mission_stops where mission_id=%s group by status", (mission_id,))
    ev = db.one("select count(*) n, coalesce(max(sim_time_s),0) t from mission_events where mission_id=%s", (mission_id,))
    return {**m, "stops": {r["status"]: r["n"] for r in stops}, "events": ev["n"], "sim_time_s": float(ev["t"]),
            "provenance": "SIMULATED"}


def supervisor_resume(mission_id: str, *, link_restored: bool = True, jam_cleared: bool = False) -> dict:
    """Clear a safety pause. Local rules still apply: a present hazard keeps the robot stopped."""
    cx = _load(mission_id)
    if cx.mission["status"] != "paused_safety":
        raise MissionNotRunnable(f"mission is {cx.mission['status']}, not paused")
    open_faults = db.all_rows("select code from robot_faults where mission_id=%s and cleared_at is null", (mission_id,))
    codes = {f["code"] for f in open_faults}
    obs = Observation(connection="online" if (link_restored or "connection_lost" not in codes) else "lost",
                      wheel_jam="wheel_jam" in codes and not jam_cleared, battery_pct=cx.robot["battery_pct"])
    ok, why = SafetyController().may_supervisor_resume(None, obs)
    if not ok:
        raise MissionNotRunnable(f"local safety controller refuses resume: {why}")
    db.one("update robot_faults set cleared_at=now(), cleared_by='supervisor' where mission_id=%s and cleared_at is null returning 1",
           (mission_id,))
    db.one("update robot_status set connectivity='online' where robot_id=%s returning 1", (cx.robot["id"],))
    db.one("update missions set status='running' where id=%s returning 1", (mission_id,))
    cx.mission["status"] = "running"
    _emit(cx, "supervisor_resume", "supervisor", {"local_check": why})
    return {"status": "running"}


def operator_stop(mission_id: str, reason: str) -> dict:
    """Human stop/abort: always allowed (more conservative), takes effect immediately."""
    cx = _load(mission_id)
    if cx.mission["status"] in ("completed", "aborted", "failed"):
        return summary(mission_id)
    _fault(cx, "operator_stop", "warning", "abort", {"reason": reason})
    _emit(cx, "operator_abort", "operator", {"reason": reason})
    db.one("update mission_stops set status='skipped' where mission_id=%s and status='pending' returning 1", (mission_id,))
    _set_mission(cx, "aborted", f"operator: {reason}")
    _finish_robot(cx)
    return summary(mission_id)

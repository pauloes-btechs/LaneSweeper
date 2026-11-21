"""Deterministic, inspectable mission planner.

Inputs come from the database (lane cells along real centerlines, depots, closures, zones, robot state and
capabilities). Output is a set of mission plans with labelled ESTIMATES and an explicit list of every
constraint checked. An agent may compare and explain plans; it cannot change constraints or coordinates.

Simplifications (documented, not hidden):
- Travel follows the lane cell sequence; depot<->lane legs use straight-line distance x1.3 detour factor.
- Speeds/sweep times are design targets for the simulated LK-01, not measured field values.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime

from .. import db

TRANSIT_MPS = 1.0          # simulated LK-01 cruise speed (design target)
SWEEP_S_PER_TARGET = 60    # dwell to sweep a hazard cell (design target)
DETOUR = 1.3               # depot leg: straight line -> street path factor (estimate)
CELL_M = 25.0


@dataclass
class WorkItem:
    work_order_id: str
    ticket_id: str
    cell_id: str
    priority: int
    hazard_type: str


@dataclass
class RobotState:
    robot_id: str
    depot_id: str
    depot_lat: float
    depot_lng: float
    state: str
    connectivity: str
    battery_pct: float
    hopper_pct: float
    battery_wh: float
    wh_per_km: float
    hazard_types: list[str]
    mode: str


@dataclass
class MissionPlan:
    robot_id: str
    work_order_ids: list[str]
    target_cell_ids: list[str]
    route_cell_ids: list[str]
    estimates: dict
    constraints: list[dict]
    rationale: str


@dataclass
class PlanResult:
    plans: list[MissionPlan] = field(default_factory=list)
    unassigned: list[dict] = field(default_factory=list)
    excluded_robots: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"plans": [asdict(p) for p in self.plans], "unassigned": self.unassigned, "excluded_robots": self.excluded_robots}


def _haversine_m(lat1, lng1, lat2, lng2) -> float:
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def load_cells(jurisdiction_id: str) -> dict[str, dict]:
    rows = db.all_rows("""select id, segment_id, seq, extensions.st_y(extensions.st_centroid(geom::extensions.geometry)) lat,
                                 extensions.st_x(extensions.st_centroid(geom::extensions.geometry)) lng
                            from lane_cells where jurisdiction_id = %s""", (jurisdiction_id,))
    return {r["id"]: r for r in rows}


def load_robots(jurisdiction_id: str) -> list[RobotState]:
    rows = db.all_rows("""select r.id, r.mode, r.home_depot_id, extensions.st_y(d.location::extensions.geometry) dlat,
                                 extensions.st_x(d.location::extensions.geometry) dlng, s.state, s.connectivity, s.battery_pct,
                                 s.hopper_pct, c.battery_wh, c.wh_per_km, c.hazard_types
                            from robots r join depots d on d.id = r.home_depot_id
                            join robot_status s on s.robot_id = r.id join robot_capabilities c on c.robot_id = r.id
                           where r.jurisdiction_id = %s order by r.id""", (jurisdiction_id,))
    return [RobotState(r["id"], r["home_depot_id"], r["dlat"], r["dlng"], r["state"], r["connectivity"], r["battery_pct"],
                       r["hopper_pct"], r["battery_wh"], r["wh_per_km"], list(r["hazard_types"]), r["mode"]) for r in rows]


def blocked_cells(jurisdiction_id: str, start: datetime, end: datetime) -> dict[str, str]:
    out = {}
    for r in db.all_rows("""select unnest(cell_ids) cell, reason from closures
                             where jurisdiction_id=%s and starts_at < %s and ends_at > %s""", (jurisdiction_id, end, start)):
        out[r["cell"]] = f"closure: {r['reason']}"
    for r in db.all_rows("""select c.id cell, z.name from lane_cells c join restricted_zones z
                              on z.jurisdiction_id = c.jurisdiction_id and z.active and extensions.st_intersects(z.geom, c.geom)
                             where c.jurisdiction_id=%s and z.kind in ('no_robot','school_zone','event')""", (jurisdiction_id,)):
        out[r["cell"]] = f"restricted zone: {r['name']}"
    return out


def plan(jurisdiction_id: str, items: list[WorkItem], window_start: datetime, window_end: datetime, *,
         policy: dict | None = None, robots: list[RobotState] | None = None, max_robots: int | None = None) -> PlanResult:
    policy = policy or {}
    fleet_pol = policy.get("fleet") or {}
    reserve = float(fleet_pol.get("min_battery_reserve_pct", 25))
    max_hopper = float(fleet_pol.get("max_hopper_pct", 85))
    window_s = (window_end - window_start).total_seconds()
    cells = load_cells(jurisdiction_id)
    blocked = blocked_cells(jurisdiction_id, window_start, window_end)
    robots = robots if robots is not None else load_robots(jurisdiction_id)
    res = PlanResult()

    ready: list[RobotState] = []
    for r in robots:
        why = None
        if r.mode != "simulated":
            why = "not a simulated robot (real robots unavailable)"
        elif r.connectivity != "online":
            why = f"connectivity {r.connectivity}"
        elif r.state not in ("docked", "idle", "charging"):
            why = f"busy or faulted ({r.state})"
        elif r.battery_pct <= reserve:
            why = f"battery {r.battery_pct:.0f}% ≤ reserve {reserve:.0f}%"
        elif r.hopper_pct >= max_hopper:
            why = f"hopper {r.hopper_pct:.0f}% ≥ {max_hopper:.0f}%"
        (res.excluded_robots.append({"robot_id": r.robot_id, "reason": why}) if why else ready.append(r))
    if max_robots:
        for r in ready[max_robots:]:
            res.excluded_robots.append({"robot_id": r.robot_id, "reason": f"fleet size limited to {max_robots}"})
        ready = ready[:max_robots]

    valid: list[WorkItem] = []
    for it in sorted(items, key=lambda i: (i.priority, i.cell_id)):
        if it.cell_id not in cells:
            res.unassigned.append({"work_order_id": it.work_order_id, "reason": "cell not in this jurisdiction"})
        elif it.cell_id in blocked:
            res.unassigned.append({"work_order_id": it.work_order_id, "reason": blocked[it.cell_id]})
        else:
            valid.append(it)
    if not ready:
        res.unassigned += [{"work_order_id": i.work_order_id, "reason": "no robot available"} for i in valid]
        return res

    # Assign each item to the capable robot whose depot is nearest (deterministic tie-break by robot id).
    buckets: dict[str, list[WorkItem]] = {r.robot_id: [] for r in ready}
    for it in valid:
        c = cells[it.cell_id]
        capable = [r for r in ready if it.hazard_type in r.hazard_types]
        if not capable:
            res.unassigned.append({"work_order_id": it.work_order_id, "reason": f"no robot can handle {it.hazard_type}"})
            continue
        best = min(capable, key=lambda r: (_haversine_m(r.depot_lat, r.depot_lng, c["lat"], c["lng"]), r.robot_id))
        buckets[best.robot_id].append(it)

    for r in ready:
        todo = buckets[r.robot_id]
        while todo:
            p, dropped = _route_for(r, todo, cells, blocked, reserve, window_s)
            if p:
                res.plans.append(p)
                break
            # Drop the lowest-priority item and retry, so constraints are never relaxed.
            res.unassigned.append({"work_order_id": dropped.work_order_id, "reason": f"would exceed limits for {r.robot_id}"})
            todo = [t for t in todo if t is not dropped]
    return res


def _route_for(r: RobotState, todo: list[WorkItem], cells: dict, blocked: dict, reserve: float, window_s: float):
    seqs = sorted(cells[i.cell_id]["seq"] for i in todo)
    seg = cells[todo[0].cell_id]["segment_id"]
    by_seq = {c["seq"]: cid for cid, c in cells.items() if c["segment_id"] == seg}
    lo, hi = seqs[0], seqs[-1]
    route = [by_seq[s] for s in range(lo, hi + 1) if s in by_seq]
    traversed_blocked = [c for c in route if c in blocked]
    first, last = cells[route[0]], cells[route[-1]]
    leg_in = _haversine_m(r.depot_lat, r.depot_lng, first["lat"], first["lng"]) * 1.3
    leg_out = _haversine_m(r.depot_lat, r.depot_lng, last["lat"], last["lng"]) * 1.3
    lane_m = len(route) * CELL_M
    dist = leg_in + lane_m + leg_out
    dur_s = dist / TRANSIT_MPS + SWEEP_S_PER_TARGET * len(todo)
    batt_used = (dist / 1000) * r.wh_per_km / r.battery_wh * 100
    batt_end = r.battery_pct - batt_used
    checks = [
        {"check": "battery_reserve", "ok": batt_end >= reserve, "detail": f"est. end {batt_end:.1f}% vs reserve {reserve:.0f}%"},
        {"check": "operating_window", "ok": dur_s <= window_s, "detail": f"est. {dur_s/60:.0f} min vs window {window_s/60:.0f} min"},
        {"check": "closures_and_zones", "ok": not traversed_blocked, "detail": f"{len(traversed_blocked)} blocked cell(s) on route"},
        {"check": "capability", "ok": all(i.hazard_type in r.hazard_types for i in todo), "detail": ",".join(r.hazard_types)},
        {"check": "simulation_only", "ok": r.mode == "simulated", "detail": r.mode},
    ]
    if not all(c["ok"] for c in checks):
        worst = max(todo, key=lambda i: (i.priority, -abs(cells[i.cell_id]["seq"] - lo)))
        return None, worst
    est = {"label": "ESTIMATE (simulated LK-01 design targets, not field measurements)", "distance_m": round(dist),
           "depot_leg_in_m": round(leg_in), "lane_m": round(lane_m), "depot_leg_out_m": round(leg_out),
           "duration_min": round(dur_s / 60, 1), "battery_used_pct": round(batt_used, 1), "battery_end_pct": round(batt_end, 1)}
    rationale = (f"{r.robot_id} from {r.depot_id}: {len(todo)} target cell(s) {route[0]}→{route[-1]} "
                 f"(priorities {sorted({i.priority for i in todo})}); nearest capable depot.")
    return MissionPlan(r.robot_id, [i.work_order_id for i in todo], [i.cell_id for i in todo], route, est, checks, rationale), None

"""Scripted end-to-end demo: A (glasses glass report) → B (pothole) → C (duplicate) → D (nightly plan, approve one /
deny one) → E (cyclist stop) → F (false cleanup claim) → G (lost link, resume) → H (cross-tenant + revocation)
→ J (copilot) → public run report.

Prints measured SOFTWARE timings separately from SYNTHETIC twin times. Works fully offline (Scenario I) and uses
live Gemini routes automatically when a key is configured."""
from __future__ import annotations

import time
import uuid
from pathlib import Path

from . import auth, db, knowledge, workflows
from .api.demo import SEEDED
from .config import get_settings
from .graphs import copilot, fleet, glasses
from .policy import approvals
from .policy.engine import PolicyDenied

NYC = "11111111-1111-4111-8111-111111111111"
LOG: list[str] = []


def P(who: str) -> auth.Principal:
    uid = SEEDED[who][0]
    return auth.Principal(uid, auth.load_memberships(uid))


def say(msg: str = "") -> None:
    print(msg, flush=True)
    LOG.append(msg)


def cell(seq: int) -> tuple[float, float]:
    r = db.one("""select extensions.st_y(extensions.st_centroid(geom::extensions.geometry)) lat,
                         extensions.st_x(extensions.st_centroid(geom::extensions.geometry)) lng from lane_cells where id=%s""",
               (f"MN-1AV-{seq:04d}",))
    return r["lat"], r["lng"]


def event(seq: int, fixture: str, hazard: str, note: str, *, source="glasses_sim", frames=4, score=0.82) -> dict:
    lat, lng = cell(seq)
    return {"idempotency_key": f"demo-{uuid.uuid4().hex[:10]}", "source_kind": source, "fixture_id": fixture, "hazard_claimed": hazard,
            "note": note, "location": {"lat": lat, "lng": lng, "accuracy_m": 8, "source": "phone_gps", "speed_mps": 0.0},
            "consent": {"policy_version": "rider-2026.11", "share_image": True, "share_location": True, "notify_status": True},
            "local_detector": {"label": "possible_glass" if hazard == "glass" else f"possible_{hazard}", "score": score,
                               "consecutive_frames": frames, "model": "lk-local-heuristic-v1", "warning_issued": frames >= 3}}


def trace(run_id: str, kinds=("exit",), limit=40) -> None:
    for e in db.all_rows("select node, kind, summary from agent_events where run_id=%s and kind = any(%s) order by seq limit %s",
                         (run_id, list(kinds), limit)):
        say(f"    · {e['node']:<38} {e['summary'][:130]}")


def step(title: str):
    say(f"\n━━ {title}")
    return time.perf_counter()


def done(t0: float, synthetic_s: float | None = None) -> None:
    say(f"    ⏱ software {1000*(time.perf_counter()-t0):.0f} ms" + (f" | twin (SYNTHETIC) {synthetic_s:.0f} s" if synthetic_s is not None else ""))


def run_demo(*, fast: bool = False, out: Path | None = None) -> None:
    s = get_settings()
    say("LaneKeeper OS — scripted demo (SIMULATED GLASSES · SIMULATED ROBOTS · synthetic data)")
    say(f"AI mode: {s.ai_mode} ({'live Gemini routes' if s.live_models else 'offline fixtures/templates, clearly labelled'})")
    db.one("""update robot_status set state='docked', connectivity='online', battery_pct=100, hopper_pct=0, mission_id=null returning 1""")
    knowledge.embed_pending_chunks()

    t = step("A · Rider on 1st Ave sees possible glass through SIMULATED GLASSES")
    say("    device: local detector 4 consecutive frames ≥0.55 → audio 'Possible debris ahead — slow down' (on-device, no cloud wait)")
    a = glasses.handle_device_event(event(40, "glass-1av-dawn-01", "glass", "possible glass ahead"))
    thread_a = a["ticket_thread"]
    ta = workflows.get_thread(thread_a)
    trace(ta["run_id"])
    say(f"    → ticket {ta['ticket_number']} ({ta['severity']} {ta['hazard_type']}, P{ta['priority']}); graph waiting at {ta['next']}")
    done(t)

    t = step("B · Pothole reported in the same inbox")
    b = workflows.submit_report({**event(100, "pothole-09", "pothole", "deep pothole by the bus stop", source="rider_guest"),
                                 "local_detector": None}, auth.ANONYMOUS)
    tb = db.one("select t.number, t.remediation_path, d.code from tickets t left join departments d on d.id=t.department_id where t.id=%s",
                (b["ticket_id"],))
    say(f"    route {b['route']} → {tb['number']} to {tb['code']} ({tb['remediation_path']}); no robot proposed")
    done(t)

    t = step("C · Second rider reports the same glass from 20 m away")
    c = glasses.handle_device_event(event(41, "glass-1av-dawn-01", "glass", "glass again, near the hydrant"))
    links = db.all_rows("select link_kind, rule_score, vector_score from incident_report_links where incident_id=%s order by created_at",
                        (ta["incident_id"],))
    say(f"    incident links: {[(x['link_kind'], x['rule_score'], x['vector_score']) for x in links]} (both reports kept)")
    say(f"    second report joined ticket {c['ticket_number']}")
    done(t)

    t = step("A (cont.) · Supervisor reviews the signed, simulation-only dispatch request and approves")
    approvals.decide(P("supervisor"), ta["approval_id"], True, "Estimates and window reviewed; lane cell matched")
    ra = workflows.resume_after_approval(thread_a, P("supervisor"))
    ms = db.one("select coalesce(max(sim_time_s),0) t from mission_events where mission_id=%s", (ra["mission_id"],))["t"]
    trace(ra["run_id"], kinds=("exit",), limit=60)
    say(f"    → outcome {ra['outcome']}")
    done(t, ms)

    t = step("D · Nightly planning: dispatcher plans; supervisor approves LK-01-A, denies LK-01-B")
    for seq in (12, 170):
        workflows.submit_report({**event(seq, "glass-1av-night-02", "glass", "glass", source="rider_guest"), "local_detector": None},
                                auth.ANONYMOUS)
    db.one("""update work_orders set status='open' where status='planned' and kind='robot_sweep' returning 1""")
    db.one("""update approval_requests set status='expired' where status='pending' returning 1""")
    d = fleet.start(P("dispatcher"), NYC, label=f"demo-{uuid.uuid4().hex[:6]}")
    for ap in d["approvals"] or []:
        ok = ap["robot_id"] == "LK-01-A"
        approvals.decide(P("supervisor"), ap["approval_id"], ok, "south route OK" if ok else "street event on the north block")
        say(f"    {ap['robot_id']}: {'APPROVED' if ok else 'DENIED'} (approval {ap['approval_id'][:8]})")
    dd = fleet.resume(d["thread_id"], P("supervisor"), {})
    say(f"    missions dispatched: {len(dd['missions'] or [])}; not dispatched: {[x['robot_id'] for x in dd['not_dispatched'] or []]}")
    done(t)

    t = step("E · During a sweep a cyclist enters the robot's path")
    e = workflows.submit_report({**event(60, "glass-1av-dawn-01", "glass", "glass", source="rider_guest"), "local_detector": None},
                                auth.ANONYMOUS, scenario="cyclist_enters")
    approvals.decide(P("supervisor"), e["approval_id"], True, "approved for E")
    re_ = workflows.resume_after_approval(e["thread_id"], P("supervisor"))
    for ev in db.all_rows("select sim_time_s, kind, detail->>'reason' r from mission_events where mission_id=%s and kind like 'safety%%' order by id",
                          (re_["mission_id"],)):
        say(f"    t+{ev['sim_time_s']:>5.0f}s (SIMULATED)  {ev['kind']:<20} {ev['r']}")
    say("    the LLM has no path to resume the robot: the local safety controller decided")
    done(t)

    t = step("F · Route completes but the after-frame still shows glass")
    f = workflows.submit_report({**event(170 if fast else 150, "glass-wide-04", "glass", "lots of glass", source="rider_guest"),
                                 "local_detector": None}, auth.ANONYMOUS, scenario="failed_verification")
    if f.get("approval_id"):
        approvals.decide(P("supervisor"), f["approval_id"], True, "approved for F")
        rf = workflows.resume_after_approval(f["thread_id"], P("supervisor"))
        v = db.one("select verdict, reason from remediation_verdicts where mission_id=%s", (rf["mission_id"],))
        say(f"    verdict: {v['verdict']} — {v['reason']}; ticket reopened; public report will NOT say cleared")
    done(t)

    t = step("G · Robot loses its network link mid-route")
    g = workflows.submit_report({**event(140, "glass-1av-night-02", "glass", "glass", source="rider_guest"), "local_detector": None},
                                auth.ANONYMOUS, scenario="disconnected")
    if g.get("approval_id"):
        approvals.decide(P("supervisor"), g["approval_id"], True, "approved for G")
        rg = workflows.resume_after_approval(g["thread_id"], P("supervisor"))
        say(f"    mission paused by the safety controller: {rg['interrupt'] and rg['interrupt']['kind']}")
        rg2 = workflows.resolve_safety_pause(g["thread_id"], P("supervisor"), "resume")
        say(f"    supervisor resumed after link restored → {rg2['outcome']}; no stop repeated")
    done(t)

    t = step("H · Another city's supervisor tries to approve; then a supervisor is revoked mid-approval")
    h = workflows.submit_report({**event(120, "glass-1av-dawn-01", "glass", "glass", source="rider_guest"), "local_detector": None},
                                auth.ANONYMOUS)
    if h.get("approval_id"):
        try:
            approvals.decide(P("harbor_supervisor"), h["approval_id"], True, "cross-tenant attempt")
        except PolicyDenied as ex:
            say(f"    Harbor City supervisor → DENIED ({ex})")
        approvals.decide(P("supervisor"), h["approval_id"], True, "approved, then revoked")
        db.one("update municipal_memberships set status='revoked' where user_id=%s returning 1", (SEEDED["supervisor"][0],))
        rh = workflows.resume_after_approval(h["thread_id"], P("supervisor2"))
        say(f"    resume after revocation → mission {rh['mission_id']} / outcome {rh['outcome']} (blocked)")
        db.one("update municipal_memberships set status='active' where user_id=%s returning 1", (SEEDED["supervisor"][0],))
    done(t)

    t = step("J · Operator asks the copilot")
    for q in [f"Why does {ta['ticket_number']} have its current priority?", "What conditions would force you to stop the robots?"]:
        r = copilot.ask(P("operator"), NYC, q, session="demo")
        say(f"    Q: {q}\n    A: " + r["answer"].replace("\n", "\n       ")[:700])
    done(t)

    step("Public run reports (open from QR codes)")
    for rr in db.all_rows("select slug, summary from public_run_reports order by generated_at desc limit 6"):
        sm = rr["summary"]
        say(f"    /r/{rr['slug']}: {sm['lane_cells_swept']}/{sm['lane_cells_planned']} cells swept, "
            f"{sm['verified_cleared']} verified cleared, {sm['unverified_or_remaining']} unverified · {sm['provenance']}")
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(LOG))
        say(f"\ntranscript → {out}")

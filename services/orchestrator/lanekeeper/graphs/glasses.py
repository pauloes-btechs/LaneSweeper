"""Smart-glasses rider-assistance graph (server side).

The protective part (local candidate detection, debounce, conservative audio/HUD warning) runs ON THE DEVICE
(packages/glasses) and never waits for this service or any cloud model. This graph handles what follows, when the
link is available:

START → capture_frame_or_simulated_event → enforce_consent_and_privacy → local_fast_candidate_detector (record device output)
      → temporal_debounce (re-check) → assess_location_quality_and_riding_context
      ─┬ probable_imminent_obstruction → conservative_local_audio_or_HUD_warning (record that the device warned)
       └ uncertain → no_distracting_alert
      → select_minimal_evidence_frame → deidentify_and_upload_when_authorized → create_ticket (ticket graph: vision
        verification, cell match, dedupe) → notify_rider_of_report_status → END
"""
from __future__ import annotations

import base64
from functools import partial

from langgraph.graph import END, START, StateGraph

from .. import WORKFLOW_VERSION, db, workflows
from ..auth import Principal
from ..deps import Deps
from ..privacy import minimise_frame
from ..tracing import set_status, start_run, traced
from .state import LaneKeeperState, make_thread_id

MIN_CONSECUTIVE_FRAMES = 3      # device-side debounce requirement, re-checked here
MIN_LOCAL_SCORE = 0.55
MAX_SPEED_FOR_PROMPT_MPS = 1.0  # never ask a rider to interact unless (nearly) stopped


def capture_frame_or_simulated_event(state: dict, deps: Deps) -> dict:
    ev = state["report"]
    src = "SIMULATED GLASSES" if ev.get("source_kind") == "glasses_sim" else ev.get("source_kind")
    return {"source_provenance": ev.get("provenance", "SIMULATED"), "_summary": f"Device event from {src}"}


def enforce_consent_and_privacy(state: dict, deps: Deps) -> dict:
    c = state["report"].get("consent") or {}
    if not c.get("share_location"):
        return {"outcome": "local_only", "_summary": "Rider has not consented to share location: warning stays on-device, no report"}
    return {"_summary": f"Consent: location yes, image {'yes' if c.get('share_image') else 'no'}, learning "
                        f"{'yes' if c.get('allow_learning') else 'no'}"}


def local_fast_candidate_detector(state: dict, deps: Deps) -> dict:
    ld = state["report"].get("local_detector") or {}
    return {"confidence": float(ld.get("score", 0)), "_summary": f"Device detector: {ld.get('label', 'none')} score "
            f"{float(ld.get('score', 0)):.2f} over {ld.get('consecutive_frames', 0)} frame(s) ({ld.get('model', 'unknown')})"}


def temporal_debounce(state: dict, deps: Deps) -> dict:
    ld = state["report"].get("local_detector") or {}
    ok = int(ld.get("consecutive_frames", 0)) >= MIN_CONSECUTIVE_FRAMES and float(ld.get("score", 0)) >= MIN_LOCAL_SCORE
    return {"next_action": "probable" if ok else "uncertain",
            "_summary": f"Debounce {'passed' if ok else 'not met'} (need ≥{MIN_CONSECUTIVE_FRAMES} frames ≥{MIN_LOCAL_SCORE})"}


def assess_location_quality_and_riding_context(state: dict, deps: Deps) -> dict:
    loc = state["report"].get("location") or {}
    acc, spd = loc.get("accuracy_m"), loc.get("speed_mps") or 0
    note = f"GPS ±{acc} m (phone)" if acc is not None else "no GPS fix"
    return {"location_accuracy_m": acc, "_summary": f"{note}; rider speed {spd:.1f} m/s; distance to hazard not claimed "
                                                     "(no calibrated depth/IMU)"}


def conservative_local_audio_or_HUD_warning(state: dict, deps: Deps) -> dict:  # noqa: N802 - name mirrors the spec
    w = (state["report"].get("local_detector") or {}).get("warning_issued")
    return {"_summary": f"Device {'issued' if w else 'did not issue'} the conservative warning "
                        "'Possible debris ahead — slow down' (local; never claims the lane is clear)"}


def no_distracting_alert(state: dict, deps: Deps) -> dict:
    return {"_summary": "Uncertain candidate: no alert while riding; optional prompt only after the rider stops"}


def select_minimal_evidence_frame(state: dict, deps: Deps) -> dict:
    rep = state["report"]
    if not (rep.get("consent") or {}).get("share_image"):
        rep = {**rep, "image_b64": None, "image_mime": None, "fixture_id": None}
        return {"report": rep, "_summary": "No image consent: report goes text + location only"}
    return {"_summary": "Selected the single best-scoring frame (device-chosen) as minimal evidence"}


def deidentify_and_upload_when_authorized(state: dict, deps: Deps) -> dict:
    rep = state["report"]
    if rep.get("image_b64"):
        data, meta = minimise_frame(base64.b64decode(rep["image_b64"]))
        rep = {**rep, "image_b64": base64.b64encode(data).decode(), "image_mime": "image/jpeg"}
        return {"report": rep, "evidence": {"deidentification": meta},
                "_summary": f"De-identified frame: {meta['method']} {meta['original_size']}→{meta['output_size']}, EXIF stripped"}
    return {"_summary": "No uploaded frame to de-identify (fixture or text-only)"}


def create_ticket(state: dict, deps: Deps) -> dict:
    if state.get("outcome") == "local_only":
        return {}
    rep = {k: v for k, v in state["report"].items() if k not in ("reporter_hash",)}
    rep["source_kind"] = rep.get("source_kind") or "glasses_sim"
    res = workflows.submit_report(rep, Principal(state.get("actor_id")) if state.get("actor_id") else Principal(None),
                                  scenario=state.get("scenario"), deps=None)
    return {"ticket_id": res.get("ticket_id"), "ticket_number": res.get("ticket_number"), "report_id": res.get("report_id"),
            "route": res.get("route"), "evidence": {**(state.get("evidence") or {}), "ticket_thread": res["thread_id"],
                                                    "ticket_status": res["status"]},
            "_summary": f"Handed to the ticket graph: route {res.get('route')}, ticket {res.get('ticket_number') or 'pending evidence'}"}


def notify_rider_of_report_status(state: dict, deps: Deps) -> dict:
    rep = state["report"]
    if not (rep.get("consent") or {}).get("notify_status") or state.get("outcome") == "local_only":
        return {"outcome": state.get("outcome") or "reported", "_summary": "Rider did not opt into status updates"}
    msg = "Report received. Thanks — ride safe." if not state.get("ticket_number") else \
        f"Report {state['ticket_number']} logged. City staff will review it."
    db.one("""insert into notification_outbox (channel, recipient_ref, template, body, dedupe_key, jurisdiction_id)
              values ('glasses_hud', %s, 'report_status', %s, %s, (select jurisdiction_id from hazard_reports where id=%s))
              on conflict (dedupe_key) do nothing returning id""",
           (rep.get("device_id") or "sim-glasses", db.jsonb({"text": msg, "display": "after_stop_only"}),
            f"hud:{state.get('report_id')}", state.get("report_id")))
    return {"outcome": "reported", "_summary": f"Queued HUD status for when the rider is stopped: '{msg}'"}


ORDER = ["capture_frame_or_simulated_event", "enforce_consent_and_privacy", "local_fast_candidate_detector", "temporal_debounce",
         "assess_location_quality_and_riding_context"]
TAIL = ["select_minimal_evidence_frame", "deidentify_and_upload_when_authorized", "create_ticket", "notify_rider_of_report_status"]


def build(deps: Deps | None = None, checkpointer=None):
    deps = deps or Deps()
    g = StateGraph(LaneKeeperState)
    for n in ORDER + TAIL + ["conservative_local_audio_or_HUD_warning", "no_distracting_alert"]:
        g.add_node(n, traced(n)(partial(lambda f, d, s: f(s, d), globals()[n], deps)))
    g.add_edge(START, ORDER[0])
    g.add_conditional_edges("enforce_consent_and_privacy", lambda s: END if s.get("outcome") == "local_only" else ORDER[2],
                            {END: END, ORDER[2]: ORDER[2]})
    g.add_edge(ORDER[0], ORDER[1])
    for a, b in zip(ORDER[2:], ORDER[3:], strict=False):
        g.add_edge(a, b)
    g.add_conditional_edges("assess_location_quality_and_riding_context",
                            lambda s: "conservative_local_audio_or_HUD_warning" if s.get("next_action") == "probable" else "no_distracting_alert",
                            {"conservative_local_audio_or_HUD_warning": "conservative_local_audio_or_HUD_warning",
                             "no_distracting_alert": "no_distracting_alert"})
    g.add_edge("conservative_local_audio_or_HUD_warning", TAIL[0])
    g.add_edge("no_distracting_alert", TAIL[0])
    for a, b in zip(TAIL, TAIL[1:], strict=False):
        g.add_edge(a, b)
    g.add_edge(TAIL[-1], END)
    return g.compile(checkpointer=checkpointer)


def handle_device_event(event: dict, actor_id: str | None = None, *, scenario: str | None = None, deps: Deps | None = None) -> dict:
    from .checkpoint import get_checkpointer
    graph = build(deps, get_checkpointer())
    thread_id = make_thread_id("device", "glasses", event.get("idempotency_key", "missing"))
    cfg = {"configurable": {"thread_id": thread_id}}
    snap = graph.get_state(cfg)
    if snap.values:
        v = snap.values
        return {"thread_id": thread_id, "replayed": True, "outcome": v.get("outcome"), "ticket_number": v.get("ticket_number")}
    run_id, trace_id = start_run(workflow="glasses", workflow_version=WORKFLOW_VERSION, thread_id=thread_id,
                                 jurisdiction_id=None, actor_id=actor_id)
    out = graph.invoke({"report": event, "actor_id": actor_id, "run_id": run_id, "trace_id": trace_id, "scenario": scenario,
                        "errors": [], "messages": []}, cfg)
    set_status(run_id, "succeeded")
    return {"thread_id": thread_id, "run_id": run_id, "outcome": out.get("outcome"), "ticket_number": out.get("ticket_number"),
            "ticket_id": out.get("ticket_id"), "route": out.get("route"), "ticket_thread": (out.get("evidence") or {}).get("ticket_thread")}

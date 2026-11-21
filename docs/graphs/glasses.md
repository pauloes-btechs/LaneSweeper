# `glasses` graph (generated)

Generated from the compiled LangGraph with `scripts/export_graphs.py`. Dotted edges are conditional.

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	capture_frame_or_simulated_event(capture_frame_or_simulated_event)
	enforce_consent_and_privacy(enforce_consent_and_privacy)
	local_fast_candidate_detector(local_fast_candidate_detector)
	temporal_debounce(temporal_debounce)
	assess_location_quality_and_riding_context(assess_location_quality_and_riding_context)
	select_minimal_evidence_frame(select_minimal_evidence_frame)
	deidentify_and_upload_when_authorized(deidentify_and_upload_when_authorized)
	create_ticket(create_ticket)
	notify_rider_of_report_status(notify_rider_of_report_status)
	conservative_local_audio_or_HUD_warning(conservative_local_audio_or_HUD_warning)
	no_distracting_alert(no_distracting_alert)
	__end__([<p>__end__</p>]):::last
	__start__ --> capture_frame_or_simulated_event;
	assess_location_quality_and_riding_context -.-> conservative_local_audio_or_HUD_warning;
	assess_location_quality_and_riding_context -.-> no_distracting_alert;
	capture_frame_or_simulated_event --> enforce_consent_and_privacy;
	conservative_local_audio_or_HUD_warning --> select_minimal_evidence_frame;
	create_ticket --> notify_rider_of_report_status;
	deidentify_and_upload_when_authorized --> create_ticket;
	enforce_consent_and_privacy -.-> __end__;
	enforce_consent_and_privacy -.-> local_fast_candidate_detector;
	local_fast_candidate_detector --> temporal_debounce;
	no_distracting_alert --> select_minimal_evidence_frame;
	select_minimal_evidence_frame --> deidentify_and_upload_when_authorized;
	temporal_debounce --> assess_location_quality_and_riding_context;
	notify_rider_of_report_status --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```

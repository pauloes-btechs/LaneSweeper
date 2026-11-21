# `fleet` graph (generated)

Generated from the compiled LangGraph with `scripts/export_graphs.py`. Dotted edges are conditional.

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	load_pending_work_orders(load_pending_work_orders)
	load_geospatial_constraints(load_geospatial_constraints)
	check_robot_availability(check_robot_availability)
	compute_candidate_routes(compute_candidate_routes)
	estimate_resources_and_risk(estimate_resources_and_risk)
	create_mission_proposal(create_mission_proposal)
	verify_jurisdiction_and_mission_policy(verify_jurisdiction_and_mission_policy)
	human_approval_interrupt(human_approval_interrupt)
	revalidate_approval_and_robot_state(revalidate_approval_and_robot_state)
	simulation_dispatch_gateway(simulation_dispatch_gateway)
	monitor_telemetry(monitor_telemetry)
	operator_review(operator_review)
	independent_cleanup_verification(independent_cleanup_verification)
	publish_reports(publish_reports)
	__end__([<p>__end__</p>]):::last
	__start__ --> load_pending_work_orders;
	check_robot_availability --> compute_candidate_routes;
	compute_candidate_routes -.-> __end__;
	compute_candidate_routes -.-> estimate_resources_and_risk;
	create_mission_proposal --> verify_jurisdiction_and_mission_policy;
	estimate_resources_and_risk --> create_mission_proposal;
	human_approval_interrupt --> revalidate_approval_and_robot_state;
	independent_cleanup_verification --> publish_reports;
	load_geospatial_constraints --> check_robot_availability;
	load_pending_work_orders --> load_geospatial_constraints;
	monitor_telemetry -.-> independent_cleanup_verification;
	monitor_telemetry -.-> operator_review;
	operator_review --> independent_cleanup_verification;
	revalidate_approval_and_robot_state --> simulation_dispatch_gateway;
	simulation_dispatch_gateway --> monitor_telemetry;
	verify_jurisdiction_and_mission_policy -.-> __end__;
	verify_jurisdiction_and_mission_policy -.-> human_approval_interrupt;
	publish_reports --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```

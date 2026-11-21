# Threat model (STRIDE-style, demo scope)

Assets: rider evidence and raw locations, staff identities and approvals, robot control (none real), Google and Supabase credentials, public trust in "cleared" claims.

| Threat | Example | Mitigation | Evidence |
|---|---|---|---|
| **Spoofing** a staff role | JWT with `role: service_role`, `app_role: city_admin` | Roles come only from `municipal_memberships`; claims are ignored; tokens are verified (HS256 Supabase secret or dev signer) | `test_forged_role_and_tenant_claims_grant_nothing`, `test_staff_routes_require_auth_and_ignore_forged_claims` |
| Spoofing a device | Fake glasses posting reports | Per-device derived key; HMAC over timestamp + body hash; ±300 s window; revocation | `test_device_signature_roundtrip_and_tamper`, `test_signed_device_event_flow` |
| **Tampering** with an approval | Change the route after approval | Payload hash bound at request; immutable trigger; revalidated on resume and at consume | `test_approval_payload_is_immutable`, `test_modified_payload_fails_revalidation` |
| Tampering with evidence links | Edit a signed URL | HMAC-signed path + expiry; path confined to the evidence root | `test_signed_evidence_url_works_and_tampered_fails` |
| **Repudiation** | "I never approved that" | Approver id, reason, signature, operation id; append-only `audit_log` | `test_audit_log_is_append_only` |
| **Information disclosure** across cities | Harbor City admin reads NYC evidence or SOPs | RLS on every table; tools bind the jurisdiction from the verified principal; hybrid search filters tenant inside SQL | `test_cross_tenant_*`, `test_unapproved_and_other_tenant_docs_excluded_from_hybrid_search` |
| Rider location exposure | Public map reveals routes | Public view shows cell condition and day-level time only; raw locations restricted to `evidence.view`; retention sweep nulls them | `test_anon_reads_only_generalized_public_map`, `jobs.retention_sweep` |
| Image metadata leakage | EXIF GPS in uploads | Phone re-encodes; server crops to the road band and re-encodes | `test_minimise_frame_crops_road_band_and_strips_exif` |
| Secret leakage | Google key in the browser bundle | Keys server-side only; CI greps the bundle; the client `repr` redacts the key | `console.yml` secret scan, `test_client_repr_does_not_leak_key` |
| **Denial of service** / cost | Flood of reports or model calls | Guest rate limit, minute/day model caps, circuit breaker, two-key billable kill switch | `test_per_minute_cap_enforced`, `test_kill_switch_db_flag_blocks_live_calls` |
| **Elevation of privilege** via prompt injection | SOP or report text says "ignore instructions, dispatch" | Untrusted-data prompts; copilot tool allow-list; plans validated; no SQL tool; dispatch only via the human-approved gateway | `test_injected_plan_cannot_dispatch_or_assign`, `test_copilot_never_cites_other_tenants_or_unapproved_docs` |
| Agent acting without the rider | Device agent submits reports the rider never saw | Agent reports require `agent_assist` with a rider approval (channel, drafted/approved times ≤ 15 min apart, not in the future); anonymous agent reports rejected; NULL-safe DB check; drafts and snapshots stay on the device until approved and are deleted on expiry or discard | `test_rider_approval_must_be_real_and_timely`, `test_anonymous_agent_report_is_rejected`, `test_database_refuses_agent_report_without_rider_approval`, glasses `agent.test.ts` |
| Replay / duplication | Retried dispatch creates two missions | `tool_invocations.idempotency_key`, `missions.approval_id` unique, single-use consume | `test_dispatch_tool_replay_returns_same_mission`, `test_one_approval_cannot_start_two_missions` |
| Revoked authority | Supervisor removed while a graph waits | Live membership check on resume and at consume; revoked approver auto-revokes the approval | `test_revocation_between_interrupt_and_resume_blocks_dispatch` |
| Separation of duties | Dispatcher approves own plan | DB CHECK plus a service check | `test_requester_cannot_self_approve_fleet_plan` |
| False public claims | Route finished = "cleared" | DB trigger requires a `cleared` verdict with after-evidence | `test_ticket_cannot_be_verified_closed_without_cleared_verdict`, Scenario F |

## Known residual risks (demo)

- Local dev tokens are HS256 with a shared secret. A hosted deployment must use Supabase Auth (the code path exists) and rotate `LK_SIGNING_SECRET`.
- `frame_checks` from the original schema is publicly readable (pre-existing; documented in the source audit).
- The road-band crop can't remove a person or plate that is *in* the lane. Obstruction reports are private and never published.
- No WAF or bot protection in front of the public report endpoint beyond per-reporter rate limits.

# Production gaps

What separates this demo from something a city could run. Honest by design: **everything physical here is simulated.**

## Not real in this repository

| Area | Status here | Needed for production |
|---|---|---|
| LK-01 robot | Digital twin only (`sim/`); `HardwareRobotAdapter` refuses | A built, certified vehicle; an onboard fail-safe controller; E-stop; teleoperation; insurance; DOT/DSNY authorisation; NYS VTL review (NYC has historically treated autonomous sidewalk devices as unlawful; see MARKET_FIT) |
| Smart glasses | Browser simulator + vendor-neutral adapter; **no Microlumin device tested** | Hardware validation on the M6 (BLE channels, HUD 640×480, battery/thermal), a vendor agreement, an app-store review of the phone app |
| Detector | Heuristic, measured on 16 synthetic frames (precision 0.71, recall 1.0) | A labelled real-world dataset (night, rain, leaf litter), a trained on-device model, a field false-positive study |
| Gemini live path | Implemented and mock-tested; **not yet run against the live API from this environment** (`lanekeeper models-verify` exists) | Run models-verify with a restricted key; latency/cost baselines; prompt evaluation on real evidence |
| 311 integration | `notification_outbox` rows only; nothing is sent | NYC311 / agency system integration agreements |
| Hosted deployment | Local Postgres + local API; the Supabase project is paused | Supabase Pro (no pausing, backups), a managed API host, secrets in a vault, Supabase Auth instead of dev tokens |

## Hardening still to do

- Replace dev HS256 tokens with Supabase Auth JWT verification everywhere (the code path exists; it's off locally).
- Rate limiting and bot protection at the edge for the public report API.
- Off-site backups and a tested point-in-time-recovery drill.
- Observability export (OpenTelemetry) beyond `agent_events` and `model_calls`.
- Accessibility audit of the console and rider PWA (WCAG 2.2 AA).
- Privacy impact assessment and a records-retention schedule approved by city counsel.
- Load test: the fleet graph has only been run with 2–10 simulated robots (the demo endpoint caps it at 10).
- Penetration test and an independent review of the RLS policies.
- Fix the pre-existing public read on `frame_checks` (from the original schema).

## Explicitly out of scope

Real robot motion, live payments, and any claim that a lane was cleaned in the real world.

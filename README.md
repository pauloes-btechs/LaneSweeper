# LaneKeeper OS

An agent-orchestrated operating system for bike-lane maintenance. Riders and smart glasses report hazards. LangGraph agents verify, deduplicate and prioritise them. City staff approve simulated robot missions. A digital twin of the LK-01 sweeper carries them out, and independent verification decides what can be published as cleared.

> **Status: simulation-first alpha.** There is no physical robot or glasses integration in the field. Every robot, glasses and mission event in the demo is labelled `SIMULATED`. No software path in this repository can move a physical robot ([why](docs/ROBOT_SAFETY_BOUNDARY.md)).

**Detect → Report → Verify → Classify → Prioritise → Authorise → Dispatch → Remediate → Verify → Close → Learn**

LaneKeeper OS builds on the original [LaneKeeper](https://github.com/pauloes-btechs/lanekeeper) concept (website, LK-01 model, Supabase schema, Gemini glass check). That work is preserved unchanged under `apps/site/` and `supabase/`.

## Hosted demo

**https://lanekeeper-os-btechs-org.vercel.app** is private to the Btechs-Org Vercel team (Vercel Authentication). It runs the console on Vercel, the orchestrator as a Vercel Python Function, and data on the Supabase `lanekeeper` project (original LaneKeeper tables untouched, plus the LaneKeeper OS schema and a fictional demo tenant). AI runs offline until a Google key is added ([how](docs/OPERATIONS_RUNBOOK.md#hosted-vercel--supabase)).

## Quick start (no API keys needed)

Requires Python 3.11+, Node 20+, and Postgres 16 with PostGIS and pgvector (or Docker: `make db-docker`).

```bash
make setup          # Python venv + npm workspaces
make demo           # rebuild the local DB and run Scenarios A→J offline (~3 s)
make serve          # API on :8787 (terminal 1)
make console        # console, rider app and glasses simulator on :5173 (terminal 2)
make test           # SQL/RLS, orchestrator, glasses, console
```

Without `GOOGLE_API_KEY` everything runs on labelled offline fixtures. With a key, set `BILLABLE_CALLS_ENABLED=true` **and** enable the `billable_calls` DB flag to allow live Gemini calls, then run `make models-verify` ([budget](docs/FREE_TIER_BUDGET.md)).

## What's inside

| | |
|---|---|
| `services/orchestrator` | Python: 4 LangGraph graphs (ticket, fleet, glasses, copilot) with Postgres checkpoints, policy engine, signed single-use approval ledger, Gemini model router with offline fallbacks, 14 typed tools, digital twin + deterministic safety controller, job runner, FastAPI |
| `apps/console` | React: operator console, rider PWA, smart-glasses simulator, 3D twin, public run reports |
| `packages/glasses` | `SmartGlassesAdapter` contract, simulator, on-device detector, warning policy, offline queue, signed device link |
| `supabase/migrations` | 6 original + 11 additive migrations: tenancy, RLS, PostGIS lane cells, pgvector knowledge, approvals, missions, audit |
| `apps/site` | Original LaneKeeper website (unchanged) |

Models: `gemini-decision-1` (evidence decisions), `gemini-robotics-er-2-preview` (localisation and before/after), `gemini-4-hydrogen` (reasoning), `gemini-3.5-flash-lite` (fast text), `gemini-embedding-2` (768-d). All are server-side only ([matrix](docs/MODEL_CAPABILITY_MATRIX.md)).

## Verified in this build

157/157 automated tests and 8/8 browser E2E steps passed on 2026-11-21 ([acceptance matrix](docs/ACCEPTANCE_MATRIX.md)). Live Gemini calls and all hardware are **not** verified here ([production gaps](docs/PRODUCTION_GAPS.md)).

## Docs

| Start here | Engineering | Safety & security | Product |
|---|---|---|---|
| [Demo guide](docs/DEMO_GUIDE.md) | [Architecture](docs/ARCHITECTURE.md) | [Robot safety boundary](docs/ROBOT_SAFETY_BOUNDARY.md) | [Market fit](docs/MARKET_FIT.md) |
| [Agent contract](AGENTS.md) | [Graph diagrams](docs/graphs/ticket.md) | [Threat model](docs/THREAT_MODEL.md) | [Production gaps](docs/PRODUCTION_GAPS.md) |
| [Runbook](docs/OPERATIONS_RUNBOOK.md) | [Model matrix](docs/MODEL_CAPABILITY_MATRIX.md) | [RBAC matrix](docs/RBAC_MATRIX.md) | [Budget](docs/FREE_TIER_BUDGET.md) |
| [Acceptance matrix](docs/ACCEPTANCE_MATRIX.md) | [Source audit](docs/SOURCE_AUDIT.md) | [Glasses SDK audit](docs/SMART_GLASSES_SDK_AUDIT.md) | [Video brief](demo-media/README.md) |

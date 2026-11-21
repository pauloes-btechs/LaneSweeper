# Operations runbook (local demo)

## Start and stop

| Task | Command |
|---|---|
| First setup | `make setup` |
| Rebuild the local DB (shim → migrations → seed → embeddings) | `make db` (refuses any non-localhost `DATABASE_URL`) |
| API on :8787 | `make serve` |
| Job runner | `make worker` |
| Console on :5173 | `make console` |
| Scripted A–J demo | `make demo` → `demo-output/` |
| Check which Google models your key can call | `make models-verify` |
| Everything off, local outputs removed | `make teardown` |

## Routine checks

```sql
-- stuck runs
select workflow, status, count(*) from agent_runs group by 1,2;
-- pending approvals that will expire
select id, action, expires_at from approval_requests where status='pending' order by expires_at;
-- dead-lettered jobs
select kind, last_error, attempts from jobs where status='dead_letter';
-- model fallbacks today
select route, model, mode, count(*) from model_calls where at > now()-interval '1 day' group by 1,2,3;
```

## Incidents

| Symptom | Likely cause | Action |
|---|---|---|
| Every model call is `offline-*` | kill switch off, quota reached, or breaker open | Check `BILLABLE_CALLS_ENABLED`, the `billable_calls` flag and `usage_counters`. The breaker closes itself after 120 s. |
| Robotics ER returns 403 | unrestricted API key | Use a key restricted to the Generative Language API (see MODEL_CAPABILITY_MATRIX) |
| Run stuck `interrupted` | waiting on a human | Approve or deny in Console → Agents. Expired approvals are swept by the worker. |
| Resume rejected "payload changed" | the plan was edited after approval | Expected. Re-plan, which creates a new approval. |
| Twin mission `paused_safety` | jam or lost link | Console → Missions → mission → **Supervisor resume** or **Abort** (API: `POST /v1/threads/{thread}/resume` with `kind: safety`). The controller refuses a resume while a hazard is present. A jam needs the technician confirmation first. |
| Job in `dead_letter` | `max_attempts` (default 5) reached | Read `job_attempts.error`, fix, then `update jobs set status='pending', attempts=0 where id=…` |
| Glasses reports rejected 401 | clock skew > 300 s or revoked device | Sync device time; re-pair from the Glasses simulator (**Pair**) |

## Emergency stop

Anyone holding `fleet.stop` in the mission's jurisdiction (see RBAC_MATRIX): Console → Missions → **Stop / abort mission**, or `POST /v1/missions/{id}/stop`. The local safety controller can also stop on its own at any time. Resume requires a supervisor and a clear controller.

## Hosted (Vercel + Supabase)

| Piece | Where |
|---|---|
| Site + API | Vercel project `lanekeeper-os` (team Btechs-Org), production `lanekeeper-os-btechs-org.vercel.app`, deploys on push to `main`, Vercel Authentication on all `*.vercel.app` URLs |
| Database + evidence | Supabase project `lanekeeper` (`fvvxmvtoozpxmdkdumrx`), connected via the Vercel–Supabase integration (injects `POSTGRES_URL`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`) |
| App settings (Vercel env) | `LK_DEMO_MODE=1`, `LK_DEMO_ALLOW_HOSTED=1`, `AI_MODE=offline`, `BILLABLE_CALLS_ENABLED=false`, `LK_DB_POOL_MAX=3`; signing secrets are derived from the service-role key |

**Turn on Gemini:** add `GOOGLE_API_KEY` (restricted to the Generative Language API) in Vercel → Settings → Environment Variables, set `AI_MODE=live` and `BILLABLE_CALLS_ENABLED=true`, keep the `billable_calls` DB flag on, redeploy. Every call is still capped by `routes.yaml`.

**New migrations:** apply them to the hosted project in order (Supabase dashboard SQL editor, `supabase db push`, or the Supabase MCP) before merging code that needs them. Never run `scripts/db-reset.sh` there.

**Not applied on purpose:** the original `20261010200000_hazard_includes_rider_reports.sql` (it rebuilds a view and was awaiting the owner's approval before this work began).

**Free-plan caveat:** the project pauses after about a week without activity; restore it from the Supabase dashboard (data is kept).

## Hosted Supabase (history)

The `lanekeeper` project is paused (Free plan inactivity). Restore it from the Supabase dashboard. Apply migrations with `supabase db push` and take a backup first. Never run `scripts/db-reset.sh` against it: the script refuses non-local hosts.

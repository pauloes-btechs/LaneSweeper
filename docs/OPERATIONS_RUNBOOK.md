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
| Twin mission `safety_paused` | jam, lost link or operator stop | A supervisor resumes from Missions once the controller allows it. Technicians clear jams first. |
| Job in `dead_letter` | `max_attempts` (default 5) reached | Read `job_attempts.error`, fix, then `update jobs set status='pending', attempts=0 where id=…` |
| Glasses reports rejected 401 | clock skew > 300 s or revoked device | Sync device time; re-pair in Settings |

## Emergency stop

Anyone holding `fleet.stop` in the mission's jurisdiction (see RBAC_MATRIX): Console → Missions → **Stop / abort mission**, or `POST /v1/missions/{id}/stop`. The local safety controller can also stop on its own at any time. Resume requires a supervisor and a clear controller.

## Hosted Supabase (when restored)

The `lanekeeper` project is paused (Free plan inactivity). Restore it from the Supabase dashboard. Apply migrations with `supabase db push` and take a backup first. Never run `scripts/db-reset.sh` against it: the script refuses non-local hosts.

# Source audit — LaneKeeper 2.0

Audited 2026-11-21. Every statement here was checked against the source named; anything not verified is marked so.

## 1. Original repository `pauloes-btechs/lanekeeper`

Imported at commit `1dab9d9` (2026-11-11). Nothing in the original repository was changed; this repo copies it under these paths:

| Original | Here | Status |
|---|---|---|
| `web/` (Vite site, 3D LK-01, city maps, estimator, demo form) | `apps/site/` | Copied verbatim. Baseline run 2026-11-21: `npm test` 18/18 pass, `npm run lint` clean, `npm run build` OK |
| `supabase/migrations/*` (6 files) | `supabase/migrations/` | Copied verbatim; never edited. New work is additive migrations after them |
| `supabase/functions/verify-glass` | same | Preserved; `ROBOT_API_KEY` header check retained |
| `supabase/functions/sms-glass` | same | Preserved; `X-Twilio-Signature` check retained |
| `supabase/functions/request-demo` | same | Preserved |
| `analysis/cost_model.py` | same | Runs. Base case $18.28 vs $44.34 per lane-mile — **assumptions, not verified savings** |
| `docs/prototype`, `prototype/` | same | Copied |

### What already works (and is kept)

- PostGIS lane segments and stable 25 m `lane_cells` for the 1st Ave pilot (seed).
- Robot `detections`, `remediations`, `runs`, automatic `run_reports` built by trigger on run end.
- `rider_reports` with snap-to-nearest-cell within 30 m (`internal.snap_rider_report`).
- Private `rider-photos` storage bucket, `frame_checks` via `pg_net` → `verify-glass`.
- The website and its regression tests, city lane data for 9 cities (computed from official open data, never hand-typed).

### Known issues found in the original

1. `20261010200000_hazard_includes_rider_reports.sql` is documented as **not applied** to the live project. Not verified directly: the live project is currently **paused** (`INACTIVE`, checked via Supabase API 2026-11-21), so its migration history could not be read. This repo does **not** re-run it; LaneKeeper 2.0 computes hazard priority in its own additive function instead.
2. `frame_checks` is publicly readable (`using (true)`), including the `error` column. Acceptable for the original demo; new evidence tables are private by default.
3. `internal.check_frame` embeds the project's anon key in a migration. That key is public by design (RLS protects data), so it is not a secret leak, but it hard-codes one project ref. New code reads project URLs from configuration.
4. `verify-glass` used `gemini-robotics-er-1.6-preview`, which Google lists as **shut down** (deprecated Aug 31 2026). LaneKeeper 2.0 adds a backward-compatible model adapter defaulting to `gemini-robotics-er-2-preview`; the edge function's guard is unchanged.

### Supabase project

- Ref `fvvxmvtoozpxmdkdumrx`, name `lanekeeper`, us-east-1, Postgres 17 — **status INACTIVE (paused)**.
- No writes were made. Restoring the project is a live-infrastructure change and needs the owner's go-ahead.
- Development and CI use a local Postgres 16 + PostGIS 3.4 + pgvector 0.6 with a small Supabase compatibility shim (`supabase/tests/shim.sql`) that provides the `anon`/`authenticated`/`service_role` roles, `auth.uid()`/`auth.jwt()`, `storage.buckets` and the `supabase_realtime` publication, so the same migrations and RLS policies run unmodified.

## 2. Concept artifact

The Claude artifact link in the brief could not be opened from this session, and `lanekeeper-artifact.rtfd.zip` was not supplied. The original README, presentation (`apps/site/public/presentation/`) and run report page carry the same concept and were used instead.

## 3. MongoDB GenAI DevDay notebooks

Inspected `solutions/ai-agents-lab.ipynb` (93 cells), `agent-memory-lab.ipynb` (48), `ai-rag-lab.ipynb` (80), `vector-search-lab.ipynb` (56).

| Workshop pattern | Where it appears | LaneKeeper implementation |
|---|---|---|
| Typed graph state, nodes, conditional edges, START/END | agents lab steps 6–9 | `services/orchestrator/lanekeeper/graphs/*` with `TypedDict` state validated by Pydantic on every inbound event |
| `bind_tools` + tool node | agents lab step 5 | Typed LangChain tools with per-tool permission class; no MongoDB toolkit / MCP open-ended DB tools — those are replaced by restricted server-side tools |
| `MongoDBSaver` checkpointer, `thread_id` | agents lab step 10 | `langgraph-checkpoint-postgres` `PostgresSaver` in schema `lk_checkpoints`; thread IDs are namespaced `tenant:workflow:resource` |
| `interrupt` + `Command(resume=...)` with `approved: bool` | agents lab step 11 | Same primitives, but the resume value is an approval **ID** that is re-verified against the `approval_requests` ledger (principal, role, tenant, payload hash, expiry, single use) — never a boolean |
| `MongoDBStore` long-term memory keyed by `user_id` | agents lab step 12, memory lab | Tenant-scoped `memory_entries` with consent flag, retention and deletion; pgvector embeddings |
| Short-term chat history collections | memory lab step 3, RAG lab step 8 | Checkpoint messages per thread plus `agent_events` |
| Chunk → embed → vector index → pre-filtered search | RAG + vector labs | `knowledge_chunks` + `incident_embeddings` (pgvector HNSW), filters on tenant/jurisdiction/review status |
| `$rankFusion` hybrid search | vector lab step 8 | `tsvector` full-text + cosine similarity combined by explicit weighted reciprocal-rank fusion; both rank contributions are returned |
| Voyage AI embeddings | all labs | Gemini Embedding 2 (768-dim) with an offline deterministic embedder for zero-key demos |

MongoDB is **not** a dependency of this project.

## 4. Microlumin smart-glasses kit

See [SMART_GLASSES_SDK_AUDIT.md](SMART_GLASSES_SDK_AUDIT.md). The kit is marked confidential by its distributor, so no kit files are committed and the public audit is a capability summary.

## 5. Google models

See [MODEL_CAPABILITY_MATRIX.md](MODEL_CAPABILITY_MATRIX.md).

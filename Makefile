# LaneKeeper OS — local developer and demo commands. Nothing here spends money; billable model calls only
# happen if GOOGLE_API_KEY is set AND BILLABLE_CALLS_ENABLED is true AND the billable_calls flag is on.
SHELL := /bin/bash
PY ?= python3
VENV ?= .venv
ACT := source $(VENV)/bin/activate
export DATABASE_URL ?= postgresql://postgres:postgres@localhost:5432/lanekeeper
export LK_SIGNING_SECRET ?= dev-only-signing-secret-change-me-0001
export LK_DEVICE_LINK_SECRET ?= dev-only-device-secret-change-me-0002

.PHONY: help setup db db-docker test test-sql lint demo serve worker console models-verify fixtures clean teardown

help:            ## list commands
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed -E 's/:.*## /\t/'

setup:           ## create the Python venv and install orchestrator + console deps
	$(PY) -m venv $(VENV) && $(ACT) && pip install -q -e "services/orchestrator[dev]"
	npm install --no-audit --no-fund

db-docker:       ## start Postgres 16 + PostGIS + pgvector in Docker (alternative to a local install)
	docker compose up -d postgres && sleep 4

db:              ## rebuild the LOCAL database: Supabase shim → all migrations → synthetic seed → embeddings
	./scripts/db-reset.sh
	$(ACT) && lanekeeper seed-embeddings

test-sql:        ## migrations from zero + RLS + approval ledger + SQL function tests
	$(ACT) && python -m pytest tests -m db -p no:warnings

test:            ## everything: SQL/RLS, orchestrator (graphs, policy, models, sim, API), console unit tests
	$(ACT) && python -m pytest -p no:warnings
	npm test --workspaces --if-present

lint:
	$(ACT) && ruff check services/orchestrator
	npm run lint --workspaces --if-present

demo: db         ## scripted A→J demo, fully offline unless a Google key is configured
	$(ACT) && lanekeeper demo --out demo-output/demo-transcript.txt

serve:           ## API on :8787 with local demo identities enabled
	$(ACT) && LK_DEMO_MODE=1 lanekeeper serve --port 8787

worker:          ## bounded job runner (outbox mock delivery, approval expiry, retention)
	$(ACT) && lanekeeper worker --housekeeping --rounds 3

console:         ## operator console + rider app + simulators on :5173 (needs `make serve`)
	npm run dev -w apps/console

models-verify:   ## probe configured Gemini models with minimal calls → .lk/models-verify.json
	$(ACT) && lanekeeper models-verify

fixtures:        ## re-render the synthetic fixture frames
	$(ACT) && python scripts/gen_fixture_images.py

teardown:        ## stop dockerised Postgres and delete local evidence/demo output (keeps your code)
	-docker compose down -v
	rm -rf .lk demo-output

clean: teardown
	rm -rf $(VENV) node_modules apps/*/node_modules packages/*/node_modules

# Google model capability matrix

Checked 2026-11-21 against Google's public docs. "Callable" can only be confirmed with the owner's key: run `make models-verify`, which writes `.lk/models-verify.json`, and paste the result into the last column.

| Role | Configured ID | API / endpoint | Inputs → output | Doc status | Used for | Offline substitute | Callable with owner key |
|---|---|---|---|---|---|---|---|
| Advisory decisions | `gemini-decision-1` | `POST /v1beta/decisions` (Decisions API; SDK `client.decisions.create`, SDK ≥ 3.2) | text, image, audio, video, PDF → `noul` / `choice` / `score` answers with probabilities + `confidence` | Documented on the Decisions API page; **not** in the models list | Is it glass? Is it a real hazard? Category, severity score, duplicate likelihood, need more evidence? | `FixtureDecider`: deterministic answers keyed by fixture ID, labelled `fixture` | not yet run |
| Optical / spatial | `gemini-robotics-er-2-preview` | `POST /v1beta/interactions` | text, image, video, audio → text (JSON points `[y,x]` normalised 0–1000) | Preview. Unrestricted API keys get 403 | Glass localisation, before/after comparison, cleanup-complete judgement | `FixtureVision`: stored points per fixture | not yet run |
| Optical streaming | `gemini-robotics-er-2-streaming-preview` | Live API only | streaming | Preview | Evaluated separately; not used in protective paths | — | not yet run |
| Agent reasoning | `gemini-4-hydrogen` | `generateContent` (also Interactions) | multimodal → text, tools, structured output | Released 2026-11-12; model card published; rolling out to all tiers | Copilot answers, ticket summaries, operator recommendations, tool selection | `TemplateReasoner`: rule-based summaries that cite the same retrieved rows | not yet run |
| Fast / high-frequency | `gemini-3.5-flash-lite` | `generateContent` | multimodal → text | Stable | Text extraction from rider notes, rider status messages, short summaries | `TemplateReasoner` | not yet run |
| Embeddings | `gemini-embedding-2` (alias `gemini-embedding-2-preview`) | `POST /v1beta/models/{id}:embedContent` | text + up to 6 images → vector; `output_dimensionality` 128–3072 | The embeddings guide says stable `gemini-embedding-2`; the models list says `gemini-embedding-2-preview`. **Both are configured; verify picks the one that answers** | Incident similarity, SOP retrieval, memory | `HashEmbedder`: deterministic 768-dim feature hashing, labelled `offline-hash-768` | not yet run |
| Optional | `gemini-4-carbon`, `gemini-4-argon`, Neon | generateContent | — | Paid / limited | Off by default; explicit flag only | — | — |

## Decisions in detail

- **768 dimensions.** Matches the original target. Gemini Embedding 2 auto-normalises truncated output (per its guide). Every row stores `model`, `dim`, `version` and `source`. pgvector columns are `vector(768)`; a different dimension needs a new column and the `reembed` job, never a silent mix.
- **No `task_type` for Embedding 2.** The guide says the field isn't accepted, so queries get a `task: search result | query:` text prefix instead.
- **Decision 1 is advisory.** Its probability fields are stored as `advisory_probability`, never as a certified hazard probability. Policy gates apply their own thresholds, and anything below them goes to human review.
- **Robotics ER never controls hardware.** Outputs are parsed as image-space points and labels only. Waypoints, speeds and motor commands in a response are rejected by the parser.
- **Routing is data.** `services/orchestrator/lanekeeper/models/routes.yaml` maps task → model → fallback. Code never hard-codes a model ID.
- **Budgets.** Per-route per-minute and per-day caps, a global circuit breaker after 5 consecutive failures, and an `AI_MODE` switch (`live` | `offline`). Mode defaults to `live` when `GOOGLE_API_KEY` is set (owner request: use the account fully). Caps are generous but enforced, and `BILLABLE_CALLS_ENABLED=false` hard-stops every outbound call.
- **Logging.** Every call logs model, purpose, input provenance, latency, token usage and failure mode to `model_calls`. Prompts and images are never logged.

## Sources

- Decisions API: https://aistudio.google.com/docs/decisions-api
- Models list: https://aistudio.google.com/docs/models
- Robotics ER: https://ai.google.dev/gemini-api/docs/robotics-overview
- Embeddings: https://ai.google.dev/gemini-api/docs/embeddings
- Interactions API: https://ai.google.dev/gemini-api/docs/interactions
- Gemini 4 Hydrogen model card: https://deepmind.google/models/model-cards/gemini-4-hydrogen/

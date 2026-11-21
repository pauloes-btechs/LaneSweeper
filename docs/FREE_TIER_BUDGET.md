# Free-tier and model budget

The user has an unlimited Google API account, so model *access* isn't the constraint. Budgets still exist: they protect against runaway loops, make demo costs predictable, and keep the system working when a provider fails. Every number below comes from `models/routes.yaml` or `config.py`.

## Model call budgets (per route)

| Route | Model (primary → fallback) | API | Per minute | Per day | Timeout | Offline substitute |
|---|---|---|---|---|---|---|
| `evidence.decide` | gemini-decision-1 | Decisions | 30 | 2,000 | 20 s | `fixture_decider` |
| `vision.localize` | gemini-robotics-er-2-preview | Interactions | 20 | 1,000 | 45 s | `fixture_vision` |
| `vision.compare` | gemini-robotics-er-2-preview | Interactions | 20 | 1,000 | 45 s | `fixture_vision` |
| `reason.agent` | gemini-4-hydrogen → gemini-3.6-flash | generateContent | 30 | 2,000 | 60 s | `template_reasoner` |
| `text.fast` | gemini-3.5-flash-lite | generateContent | 60 | 5,000 | 20 s | `template_reasoner` |
| `embed` | gemini-embedding-2 → -preview | embedContent | 120 | 10,000 | 20 s | `hash_embedder` (768-d) |
| `reason.deep` (optional) | gemini-4-carbon / argon | — | off unless an owner enables it per request | | | |

Global guards, applied on top of the per-route caps: `AI_PER_MINUTE_CAP` (the lower of the route and global caps wins), `AI_DAILY_CAP` across all routes, and a circuit breaker per model (opens after 5 failures, 120 s cooldown, then falls back).

**Two-key kill switch.** A live call needs *both* `BILLABLE_CALLS_ENABLED=true` in the environment *and* the `billable_calls` DB flag set. Either one off means every call goes to the labelled offline substitute. CI runs with both off.

## Expected calls per demo run (offline vs live)

| Flow | Calls per report / mission |
|---|---|
| Rider report (ticket graph) | 1 decide + 1 localise + 1 embed + 1 fast summary |
| Robot verification | 1 compare + 1 decide on the after-frame |
| Fleet plan explanation | 1 reason per plan |
| Copilot question | 1 embed + 1 reason |

Scenarios A–J make roughly 60 calls in total, far below any cap. Offline, all of them are deterministic and cost nothing.

## Infrastructure free tiers (verified 2026-11-21)

| Service | Free tier | Effect on this project |
|---|---|---|
| Supabase Free | 500 MB database per project, 2 active projects, 1 GB file storage, 5 GB egress; projects pause after about a week of inactivity | The existing `lanekeeper` project is currently **paused**. Demo data is well under 50 MB. A paused project keeps its data but needs a manual restore. |
| Vercel Hobby | Free, but restricted to **non-commercial, personal use** | Fine for a personal portfolio demo. A municipal pilot or anything sold needs Pro or Enterprise. |
| OpenFreeMap | Keyless vector tiles | Console basemap; there's an offline plain-grid fallback. |
| GitHub Actions | Free for public repositories | The 7 workflows run on every PR. |

Sources: [Supabase pricing](https://supabase.com/pricing), [Supabase project pausing](https://supabase.com/docs/guides/platform/free-project-pausing), [Vercel Hobby plan](https://vercel.com/docs/plans/hobby), [Vercel fair use](https://vercel.com/docs/limits/fair-use-guidelines).

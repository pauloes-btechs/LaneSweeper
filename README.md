# LaneKeeper OS

An agent-orchestrated operating system for bike-lane maintenance. Riders and smart glasses report hazards. LangGraph agents verify, deduplicate and prioritise them. City staff approve simulated robot missions. A digital twin of the LK-01 sweeper carries them out, and independent verification decides what can be published as cleared.

> **Status: simulation-first alpha.** There is no physical robot or glasses integration in the field. Every robot, glasses and mission event in the demo is labelled `SIMULATED`.

**Detect → Report → Verify → Classify → Prioritise → Authorise → Dispatch → Remediate → Verify → Close → Learn**

LaneKeeper OS builds on the original [LaneKeeper](https://github.com/pauloes-btechs/lanekeeper) concept (website, LK-01 model, Supabase schema, Gemini glass check), which is preserved under `apps/site/` and `supabase/`.

## Docs

- [Source audit](docs/SOURCE_AUDIT.md)
- [Smart-glasses SDK audit](docs/SMART_GLASSES_SDK_AUDIT.md)
- [Model capability matrix](docs/MODEL_CAPABILITY_MATRIX.md)
- [Agent operating contract](AGENTS.md)

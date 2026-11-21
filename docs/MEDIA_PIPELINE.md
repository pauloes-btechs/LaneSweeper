# AI demo media and the official demo video

LaneKeeper OS can generate photoreal demo media with the server-side Google key, and joins it with real recordings of the
running software into one public demo video. Everything generated is labelled **AI-GENERATED · SIMULATED** and shows a
fictional street (prompts forbid real names, readable signs, logos, plates and identifiable faces).

## What gets generated (Settings → AI demo media, city admin only)

| Kind | Model | Output | Stored |
|---|---|---|---|
| 6 rider POV clips | `gemini-omni-1.1-flash` (Interactions API, background + poll) | 16:9, 10 s, 4K (upscaled) | 4K master (re-encoded under 50 MB) + 1080p web version |
| 3 evidence stills | `gemini-nano-banana-2.1` | 4:3, 4K JPEG | 4K master + 2K WebP |
| 5 twin textures | `gemini-nano-banana-2.1` | 1:1, 4K JPEG, tileable | 4K master + 2K WebP |

Each POV clip has a ground-truth label (`glass`, `clear`, `confounder_water`, `confounder_markings`, `debris_other`).
The Glasses simulator can run the on-device detector on any clip and shows its detection or false-alarm rate against
that label. It's an honest check of the heuristic, not a benchmark: AI footage isn't real-world validation.

**Gates.** Generation needs `AI_MODE=live`, a key, *both* billable switches, the `admin.budget` permission, and the
per-day media caps (`model:media:*`: video 2/min, 12/day; images 6/min, 60/day). A clip already generating is never
re-requested. Files live in the private `media` bucket and reach browsers only through 1-hour signed links.

**Cost.** Estimates are shown in the panel before anything runs (about $1.20 per 4K clip and $0.24 per 4K image at the
time of writing; check your Google billing for actual charges). Web versions, re-encodes and the demo video make no
model calls.

## The official demo video

`POST /v1/admin/media/reel/build` (Settings → **Build public demo video**) assembles, in order:

1. Title card
2. AI POV clip with the simulated HUD warning (and, if generated, a wet-reflection test clip)
3. Screen recordings of the real console: assistant draft → rider approval; supervisor approval of the simulated dispatch
4. The digital-twin render
5. AI before/after stills (verification)
6. Recordings: verified ticket with audit trail; public run report
7. Optional clear-lane clip, then the end card

The non-AI parts are rendered by `scripts/demo/` against a local demo stack (`record-walkthrough.mjs`, `render-twin.mjs`,
`make_parts.py`) and committed to `demo-media/reel/` with a `reel.json` manifest; they ship inside the Vercel function.
The server validates the manifest, cuts and captions the AI parts with ffmpeg, stream-copies the join, and uploads one 1080p MP4 to the
public `demo` bucket (that bucket holds only the reel):

`https://<project>.supabase.co/storage/v1/object/public/demo/lanekeeper-os-demo.mp4`

To refresh it: regenerate any assets, re-render `demo-media/reel/` if the UI changed, then press the button again.

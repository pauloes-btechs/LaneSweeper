# Demo guide

Everything in the demo is **SIMULATED**. No real robot, no real glasses, no messages sent, no real city data. The UI labels every simulated element.

## Two ways to run it

**1. Scripted, in a terminal (about 3 s of software time):**

```bash
make setup && make demo      # rebuilds the local DB, runs Scenarios A→J, writes demo-output/demo-transcript.txt
```

**2. Live, in the console (5–8 minutes, recommended for an audience):**

```bash
make db && make serve        # terminal 1: API on :8787 with demo identities
make console                 # terminal 2: http://localhost:5173
```

The identity switcher (top right) swaps between seeded people: operator, dispatcher, supervisor, admin, viewer, technician, Harbor City supervisor and rider. Permissions always come from the server, never from the switcher.

## The 5–8 minute story (A → D → E → F → public report)

| Time | Who | Do this | Point out |
|---|---|---|---|
| 0:00 | Rider | **Glasses** page: **Start ride**, wait for the advisory, stop, **Pair simulated glasses**, **Report hazard** | Detection and the warning run on the device, offline. The wording is cautious ("Possible debris ahead — slow down"). Reporting is offered only when stopped, and it sends a signed report. |
| 1:00 | Operator | **Tickets** → new LK ticket → evidence | Region-cropped image, geo match to the lane cell, advisory model verdicts labelled with provenance (`offline-fixture` without a key) |
| 2:00 | Operator | **Fleet** → run Scenario C | Duplicate report linked to the same incident. The sources stay distinct. |
| 2:30 | Supervisor | **Agents & Approvals** → pending dispatch | Signed, payload-hashed, simulation-only request. Approving resumes the paused graph. The dispatcher could not have approved it. |
| 3:30 | Supervisor | **Missions** → the new mission → **Play** | Twin replay, battery and hopper, before/after evidence, verdict `cleared` *only* because the after-frame was clean |
| 4:30 | Dispatcher → Supervisor | **Fleet** → plan the night (Scenario D); approve one robot, deny the other | Denial moves nothing. Each approval records its operation id. |
| 5:30 | Supervisor | Run Scenario E | The local controller yields for a cyclist and auto-resumes after 3 s of clear path. No model is involved. |
| 6:15 | Supervisor | Run Scenario F | The route finishes, but the glass is still in the after-frame → `partially_cleared`, the ticket reopens, the public map does *not* say cleared |
| 7:00 | Anyone | Missions → **public ↗** (the report page shows a QR code) | What happened, labelled SIMULATED, with no staff names, raw locations or rider data |

## The other scenarios

| Scenario | How | Expected result |
|---|---|---|
| B pothole | Fleet → B | Routed to DOT roadway repair; no robot |
| G lost link | Fleet → G | Mission `paused_safety` → Missions → **Supervisor resume**. No cell is swept twice. |
| G2 low battery | Fleet → G2 | Returns to depot at the 25 % reserve; mission aborted, re-plan suggested |
| X false positive | Fleet → X | Robot finds nothing → `unverified`, never "cleared" |
| H cross-tenant | Switch to the Harbor City supervisor | NYC tickets are invisible. Approving an NYC request is refused. |
| I no AI account | Leave `GOOGLE_API_KEY` unset (default) | The whole demo runs on labelled fixtures and still exercises the real graphs and Postgres |
| J copilot | **Knowledge** → ask "Why is this hazard priority 1?" | Cited answer from approved SOPs only. It can draft a work order but cannot dispatch. |

**Reset between audiences:** Fleet → *Reset fleet* aborts unfinished simulated missions and docks the robots at 100 %. For a full reset, run `make db`.

## Timing honesty

The twin reports **synthetic** mission time (for example, "t+340 s"). The demo script also prints the **measured** software time for each step. The two are never mixed. Each step in `demo-output/demo-transcript.txt` shows `software … ms | twin (SYNTHETIC) … s`.

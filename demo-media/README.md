# Demo media: storyboard and Veo / AI Studio prompt library

**No video was generated for this repository.** This folder is a production brief. If you generate clips (Veo through AI Studio or Vertex, or your own footage), put them in `apps/console/public/media/` with the filenames below. The Glasses simulator and the console pick them up without code changes, and they fall back to the built-in synthetic frames when the files are missing.

## Ground rules for every shot

- Every frame carries a burned-in caption: **"SIMULATION · FICTIONAL SCENE · NOT A REAL CITY SERVICE"**.
- The robot is the fictional **LK-01**: a low, sage-green body (about 1.1 m long), a white roof strip, a black brush skirt, small orange side brushes and a white "LK-01" decal. No real brand marks. It looks the same in every shot.
- No identifiable faces, no legible licence plates, no real logos or NYC agency insignia.
- Glass is always called **"possible debris"**. Shots never show the system as certain.
- Aspect ratio 16:9, 1920×1080, 24 fps, 6–8 s per shot unless noted.

## Shot list

| # | File | Story beat | Duration |
|---|---|---|---|
| 1 | `shot1-dawn-lane.mp4` | Dawn on a fictional protected lane; glass glints | 6 s |
| 2 | `shot2-glasses-pov.mp4` | Rider POV, cautious HUD advisory | 8 s |
| 3 | *(screen capture)* | Ticket appears; LangGraph nodes light up | 10 s |
| 4 | *(screen capture)* | Supervisor reviews and approves the signed simulation request | 10 s |
| 5 | `shot5-twin-yield.mp4` or the 3D twin capture | LK-01 travels; a cyclist enters; the robot yields | 8 s |
| 6 | *(screen capture)* | Before/after evidence: one cleared, one *partially cleared* | 8 s |
| 7 | *(screen capture)* | Public run report with the SIMULATED label and QR code | 6 s |

Shots 3, 4, 6 and 7 must be **real screen recordings of the running console** (`make serve` + `make console`, Scenarios A, D, F), not generated imagery. That way the video shows the actual software.

## Copy-paste prompts

**Shot 1:**
> Cinematic wide shot, 16:9, of a fictional city avenue at dawn with a green-painted two-way protected bike lane separated by low concrete barriers. Cool blue-hour light with a warm sunrise rim on the buildings. A small scatter of broken bottle glass near the lane edge catches the light. Empty lane, no people, no readable signs or logos. Slow dolly forward at bike-handlebar height. Photorealistic, light mist. Burned-in lower caption: "SIMULATION · FICTIONAL SCENE · NOT A REAL CITY SERVICE".

**Shot 2:**
> First-person POV from a cyclist wearing smart glasses, riding slowly along the same green protected lane at dawn, 16:9. A minimal monochrome heads-up display in the lower-right of the view: a small amber triangle and the text "Possible debris ahead — slow down". The rider gently slows and steers to the left side of the lane. Hands on handlebars visible, gloves, no face. Slight natural head bob. No brand marks. Burned-in caption as in shot 1.

**Shot 5 (if not using the console's three.js twin):**
> Low three-quarter tracking shot, 16:9, of a small autonomous sweeper robot "LK-01" (low sage-green body about 1.1 m long, white roof strip, black brush skirt, small orange side brushes, white "LK-01" decal) moving slowly along a green protected bike lane at early morning. A cyclist enters from the left at a distance. The robot stops smoothly, an amber light on its roof pulses, and it waits until the cyclist has fully passed before continuing. Calm, realistic, no people's faces. Burned-in caption as in shot 1, plus a top-left tag "DIGITAL TWIN · SIMULATED".

**Continuity:** same avenue, same light direction (sunrise from camera-right), same robot design. Never show the robot on a sidewalk or in a car lane.

## Voice-over script (≈ 60 s)

1. "This is a simulation. On many city bike lanes, broken glass shows up between scheduled sweeps."
2. "A rider's glasses flag *possible* debris. Detection runs on the device, and the rider decides whether to report it."
3. "The report becomes a ticket. Agents check location, evidence and duplicates. Every step is logged."
4. "A supervisor reviews a signed, simulation-only plan and approves it. Agents can't do that for them."
5. "In the twin, a cyclist enters the robot's path. A local safety controller yields. No AI model can override it."
6. "Verification compares before and after. When glass remains, the lane is *not* marked clean."
7. "The public sees what happened, clearly labelled as simulated."

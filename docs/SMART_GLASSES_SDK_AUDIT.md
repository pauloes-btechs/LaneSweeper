# Smart-glasses SDK audit (public summary)

Audited 2026-11-21 from the `Microlumin_Ship` kit supplied by the owner (local copy, ~17 GB).

> **Confidentiality.** The kit's own README marks it confidential and asks that it not be uploaded to a public repository. This file is a capability summary only. No SDK binaries, headers, documents or sample code are committed. Vendor-specific adapter code and the detailed interface audit are kept in a git-ignored `vendor-private/` folder until the owner confirms they can be published.

No physical glasses were available. Nothing below has been exercised on hardware, so the best status any capability can have is `documented_unverified`.

## What the kit contains

| Part | Form | Inspected |
|---|---|---|
| Setup guide, Android SDK guide, iOS integration guide, camera-stream note | docx / pdf / pptx | Read |
| Android phone SDK | Gradle project with AAR modules plus some source (connection, message channels, file transfer, app manager, assistant, album, recorder, translator, OTA) | Read docs and module notes; not built (needs Android Studio + JDK 17) |
| iOS phone SDK | Prebuilt `.framework` bundles with headers | Read headers; not built (needs Xcode + a real iPhone) |
| iOS demo app | Xcode workspace with pods checked in | Read README |
| Two glasses APKs (launcher, screen capture) | Binaries | Not executed (no device; unknown binaries) |
| Product introductions (L5, M5, M6) | pdf | Read |

**Licence.** No open-source licence file was found for the vendor SDK. Treat it as proprietary, distributed under the hackathon's terms.

## Device facts (from vendor docs)

- MLVision M6 waveguide AR glasses, ~36.8–48 g, Sony IMX681 12 MP camera.
- Glasses run **Android 9 (API 28)** on arm64, **640×480 display at 160 dpi**. Black pixels are see-through.
- Input: temple touchpad (tap, double tap, long press, swipes), Bluetooth ring, voice, phone app.
- Phone ↔ glasses: **Bluetooth** for control, messages and audio; **Wi-Fi** for media files.

## Capability matrix

| Capability | Status | Evidence / notes |
|---|---|---|
| Connect / disconnect / scan from a phone | `documented_unverified` | Phone SDK on iOS and Android. Known vendor bug: auto-connects to the last paired glasses |
| Custom JSON message channels phone ↔ glasses | `documented_unverified` | Channel IDs reserved for third-party use. **This is the integration point LaneKeeper uses** |
| Device info: battery, volume, brightness, name | `documented_unverified` | Battery is read-only from the phone |
| Show a custom HUD on the glasses | `documented_unverified` | No phone-side "draw on HUD" call. Possible via a **plain Android app installed on the glasses** (Developer Mode + adb) |
| Spoken / audio alerts | `documented_unverified` | TTS through the voice-assistant path; a glasses app can also play audio with plain Android APIs |
| Take photo / record video on the glasses | `documented_unverified` | Built-in camera app, triggered by message or broadcast intent |
| Transfer photos to the phone | `documented_unverified` | Over Wi-Fi, file-sync flow |
| **Live camera frames to the phone** | `not_supported` in this kit | The note describes a UDP stream, but the phone-side module it refers to is **missing from the kit** |
| Camera inside a glasses app | `unknown` | No glasses-side SDK in the kit. Plain Android camera APIs may work; unverified |
| On-device ML inference | `unknown` | arm64 Android 9 could run a small TFLite model; no vendor support documented, no compute/thermal figures |
| IMU / head pose | `unknown` | Not documented in the kit |
| GPS | `not_supported` on the glasses | Not documented. **Location comes from the paired phone** |
| Glasses-side SDK (app manager, camera manager, AIDL) | `not_supported` in this kit | Described in the Android guide; libraries not included |
| Microphone / speech recognition | `documented_unverified` | Assistant, recording and transcription (vendor cloud services) |
| OTA | `documented_unverified` | Out of scope for LaneKeeper |

## Consequences for LaneKeeper

1. **Two-hop architecture.** LaneKeeper never talks to the glasses directly. Data path: glasses app (HUD, alerts) ⇄ Wi-Fi WebSocket or BLE custom channel ⇄ rider's phone (GPS, connectivity, report upload) ⇄ LaneKeeper API. The phone is the trust and network boundary, and holds no Google API keys.
2. **Local detection can't run on the glasses camera today.** The live stream is missing, and on-glasses camera access is unverified. So the rider demo runs the local candidate detector on the **phone or browser** against prerecorded or uploaded frames, then pushes alerts to the HUD.
3. **Alerts must stay low-distraction.** Short audio ("Possible debris ahead — slow down") plus a single high-contrast HUD glyph on black. No taps are needed while riding.
4. **The simulator is the MVP.** `SimulatedGlassesAdapter` implements the full contract. The vendor adapter implements only the operations marked `documented_unverified` above and reports everything else as unsupported through `getCapabilities()`.

## Hardware integration checklist (when a pair arrives)

1. Charge, power on, pair through the vendor iOS demo app, and enable Developer Mode. Confirm `adb devices -l` shows the glasses.
2. Record firmware/ROM version, phone OS and SDK versions in `vendor-private/DEVICE_LOG.md`.
3. Build the phone bridge against the real SDK. Verify the custom channel round trip and record latency (p50/p95 over 200 messages).
4. Install a minimal glasses HUD app (black background, one glyph). Measure time from phone message to rendered alert.
5. Test audio alert audibility at 15/25 km/h with wind and traffic noise, with a stationary observer.
6. Check whether glasses-side camera access works in a plain app. If it does, measure frame rate, resolution, latency and battery drain over a 30-minute ride.
7. Calibration: camera intrinsics, mounting offset, and phone GPS accuracy versus surveyed points along the 1st Ave lane.
8. Field protocol: closed course first, then supervised daytime rides. Label ground truth independently. Measure false-warning rate per rider-hour.
9. Battery: full-charge runtime with the HUD app and the link active.
10. Privacy: confirm frames are discarded on-device unless the rider opts into a report, and that uploads are de-identified.

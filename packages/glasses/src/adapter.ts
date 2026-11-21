/**
 * SmartGlassesAdapter — LaneKeeper's APPLICATION contract for rider glasses.
 *
 * It is not a claim about any vendor SDK. Every implementation reports, per operation, whether it is
 * `verified`, `documented_unverified`, `not_supported` or `unknown` on that device, and callers must
 * check getCapabilities() before relying on an operation. Protective warnings never wait for the network.
 */
export type CapabilityStatus = 'verified' | 'documented_unverified' | 'not_supported' | 'unknown';

export interface Capabilities {
  adapter: string;
  simulated: boolean;
  frames: CapabilityStatus;      // camera frames delivered to the app
  hud: CapabilityStatus;         // draw a glyph/text on the display
  audio: CapabilityStatus;       // speak/play a short warning
  pose: CapabilityStatus;        // head pose / IMU
  location: CapabilityStatus;    // GPS on the glasses (usually from the paired phone instead)
  battery: CapabilityStatus;
  customMessages: CapabilityStatus;
  display?: { width: number; height: number; transparentBlack: boolean };
}

export interface Frame {
  width: number;
  height: number;
  /** RGBA bytes, row-major (like ImageData.data) */
  data: Uint8ClampedArray;
  capturedAt: number;
  /** label of the source, e.g. fixture id or 'camera' */
  source: string;
}

export interface Pose { yawDeg: number; pitchDeg: number; rollDeg: number; at: number }

export type ConnectionStatus = 'connected' | 'connecting' | 'disconnected';

export interface HudAlert {
  /** short text; rider-facing warnings must be conservative and never claim the lane is clear */
  text: string;
  glyph: 'caution' | 'info' | 'none';
  /** milliseconds to keep on the HUD */
  ttlMs: number;
  priority: 'warning' | 'status';
}

export interface SmartGlassesAdapter {
  readonly name: string;
  connect(): Promise<void>;
  disconnect(): Promise<void>;
  getCapabilities(): Capabilities;
  getConnectionStatus(): ConnectionStatus;
  /** Returns an unsubscribe function. Throws if frames are not_supported. */
  subscribeFrames(onFrame: (f: Frame) => void, fps?: number): () => void;
  getPose(): Pose | null;
  showAlert(alert: HudAlert): Promise<void>;
  playAudio(text: string): Promise<void>;
  getBattery(): number | null;
  /** Send an app-level JSON message to the paired phone/backend bridge. */
  sendReport(payload: Record<string, unknown>): Promise<void>;
}

export class UnsupportedOperation extends Error {
  constructor(op: string, adapter: string) {
    super(`${op} is not supported by ${adapter}`);
    this.name = 'UnsupportedOperation';
  }
}

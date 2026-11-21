/**
 * MicroluminAdapter (public, vendor-neutral shell).
 *
 * The vendor kit is confidential, so this file contains no vendor class names, message ids or protocol details.
 * It declares the capability statuses established by docs/SMART_GLASSES_SDK_AUDIT.md and delegates every device
 * operation to an injected `GlassesBridge`. The real bridge (phone app over the vendor's BLE custom message
 * channel, or a glasses-side Android app over Wi-Fi) is implemented in git-ignored vendor-private/ and compiled only
 * where the owner has the SDK. Without a bridge every call raises UnsupportedOperation — it never pretends.
 */
import { type Capabilities, type ConnectionStatus, type Frame, type HudAlert, type Pose, type SmartGlassesAdapter, UnsupportedOperation } from './adapter';

export interface GlassesBridge {
  connect(): Promise<void>;
  disconnect(): Promise<void>;
  status(): ConnectionStatus;
  /** app-level JSON to the glasses-side LaneKeeper HUD app (custom channel) */
  sendAppMessage(payload: Record<string, unknown>): Promise<void>;
  /** text-to-speech via the device's documented TTS path, if the bridge implements it */
  speak?(text: string): Promise<void>;
  batteryPercent?(): Promise<number | null>;
}

export const MICROLUMIN_M6_CAPABILITIES: Omit<Capabilities, 'adapter' | 'simulated'> = {
  frames: 'not_supported',          // live phone-side camera stream module absent from the kit
  hud: 'documented_unverified',     // via a plain Android app on the glasses (640x480, black = transparent)
  audio: 'documented_unverified',   // device TTS / glasses app audio
  pose: 'unknown',                  // IMU not documented in the kit
  location: 'not_supported',        // use the paired phone's GPS
  battery: 'documented_unverified', // read-only battery level
  customMessages: 'documented_unverified',
  display: { width: 640, height: 480, transparentBlack: true },
};

export class MicroluminAdapter implements SmartGlassesAdapter {
  readonly name = 'MicroLumin M6 (bridge)';
  private lastBattery: number | null = null;
  constructor(private bridge: GlassesBridge | null) {}

  private need(op: string): GlassesBridge {
    if (!this.bridge) throw new UnsupportedOperation(op, `${this.name}: no vendor bridge installed (no hardware in this demo)`);
    return this.bridge;
  }

  async connect() { await this.need('connect').connect(); }
  async disconnect() { if (this.bridge) await this.bridge.disconnect(); }
  getConnectionStatus(): ConnectionStatus { return this.bridge ? this.bridge.status() : 'disconnected'; }
  getCapabilities(): Capabilities { return { adapter: this.name, simulated: false, ...MICROLUMIN_M6_CAPABILITIES }; }
  subscribeFrames(_: (f: Frame) => void): () => void { throw new UnsupportedOperation('subscribeFrames', this.name); }
  getPose(): Pose | null { return null; }

  async showAlert(alert: HudAlert) {
    await this.need('showAlert').sendAppMessage({ type: 'lk.hud', text: alert.text, glyph: alert.glyph, ttl_ms: alert.ttlMs });
  }

  async playAudio(text: string) {
    const b = this.need('playAudio');
    if (!b.speak) throw new UnsupportedOperation('playAudio', this.name);
    await b.speak(text);
  }

  getBattery() {
    void this.bridge?.batteryPercent?.().then((v) => { this.lastBattery = v; });
    return this.lastBattery;
  }

  async sendReport(payload: Record<string, unknown>) {
    await this.need('sendReport').sendAppMessage({ type: 'lk.report', ...payload });
  }
}

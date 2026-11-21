/**
 * SimulatedGlassesAdapter — implements the whole contract for demos and tests. Frames come from a provider
 * (fixture images, an uploaded clip or the console's canvas). Everything it emits is labelled SIMULATED GLASSES.
 */
import type { Capabilities, ConnectionStatus, Frame, HudAlert, Pose, SmartGlassesAdapter } from './adapter';

export interface SimOptions {
  frameProvider: () => Frame | null;
  speak?: (text: string) => void;
  onHud?: (alert: HudAlert | null) => void;
  onReport?: (payload: Record<string, unknown>) => Promise<void>;
  batteryDrainPerMin?: number;
  now?: () => number;
}

export class SimulatedGlassesAdapter implements SmartGlassesAdapter {
  readonly name = 'SIMULATED GLASSES';
  private status: ConnectionStatus = 'disconnected';
  private battery = 100;
  private timers = new Set<ReturnType<typeof setInterval>>();
  readonly hudLog: HudAlert[] = [];
  readonly spoken: string[] = [];
  online = true;   // toggled by the console to simulate loss of the phone link

  constructor(private opts: SimOptions) {}

  async connect() { this.status = 'connected'; }
  async disconnect() { this.status = 'disconnected'; for (const t of this.timers) clearInterval(t); this.timers.clear(); }
  getConnectionStatus() { return this.status; }

  getCapabilities(): Capabilities {
    return { adapter: this.name, simulated: true, frames: 'verified', hud: 'verified', audio: 'verified', pose: 'not_supported',
             location: 'not_supported', battery: 'verified', customMessages: 'verified',
             display: { width: 640, height: 480, transparentBlack: true } };
  }

  subscribeFrames(onFrame: (f: Frame) => void, fps = 4) {
    const t = setInterval(() => {
      const f = this.opts.frameProvider();
      if (f) onFrame(f);
      this.battery = Math.max(0, this.battery - (this.opts.batteryDrainPerMin ?? 0.4) / 60 / fps);
    }, 1000 / fps);
    this.timers.add(t);
    return () => { clearInterval(t); this.timers.delete(t); };
  }

  getPose(): Pose | null { return null; }   // no IMU in the simulator; distance is never claimed

  async showAlert(alert: HudAlert) {
    this.hudLog.push(alert);
    this.opts.onHud?.(alert);
    setTimeout(() => this.opts.onHud?.(null), alert.ttlMs);
  }

  async playAudio(text: string) { this.spoken.push(text); this.opts.speak?.(text); }
  getBattery() { return Math.round(this.battery); }

  async sendReport(payload: Record<string, unknown>) {
    if (!this.online) throw new Error('phone link offline');
    await this.opts.onReport?.(payload);
  }
}

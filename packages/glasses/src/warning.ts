/**
 * Rider warning policy: low distraction, conservative, never reassuring.
 * - Only one fixed caution phrase while moving; nothing to read, tap or answer.
 * - Cooldown so repeated frames don't nag.
 * - Uncertain candidates produce no alert while riding (an optional prompt waits until the rider has stopped).
 * - There is intentionally NO "lane clear" message: absence of a detection is not evidence of safety.
 */
import type { HudAlert } from './adapter';

export const CAUTION_TEXT = 'Possible debris ahead — slow down';
export const STOPPED_MPS = 1.0;

export interface WarningDecision {
  alert: HudAlert | null;
  speak: string | null;
  promptWhenStopped: boolean;
  reason: string;
}

export class WarningPolicy {
  private lastAlertAt = -Infinity;
  constructor(readonly cooldownMs = 8000) {}

  decide(stable: boolean, speedMps: number | null, now: number): WarningDecision {
    const moving = speedMps == null || speedMps > STOPPED_MPS;
    if (!stable) {
      return { alert: null, speak: null, promptWhenStopped: false, reason: 'candidate not stable: no distracting alert' };
    }
    if (now - this.lastAlertAt < this.cooldownMs) {
      return { alert: null, speak: null, promptWhenStopped: !moving, reason: 'within cooldown' };
    }
    this.lastAlertAt = now;
    return {
      alert: { text: CAUTION_TEXT, glyph: 'caution', ttlMs: 3500, priority: 'warning' },
      speak: CAUTION_TEXT,
      promptWhenStopped: !moving,
      reason: moving ? 'stable candidate while riding: audio + single glyph' : 'stable candidate, rider stopped: may offer a report prompt',
    };
  }
}

/** Guard used by tests and the HUD renderer: rider-facing text must never claim safety. */
export function isConservative(text: string): boolean {
  return !/\b(clear|safe|no (glass|debris|hazard)|all good)\b/i.test(text);
}

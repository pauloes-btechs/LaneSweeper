// Contract tests: every adapter reports capabilities honestly and refuses what it cannot do.
import { MicroluminAdapter, SimulatedGlassesAdapter, UnsupportedOperation, type GlassesBridge } from '../src';

const frame = () => ({ width: 2, height: 2, data: new Uint8ClampedArray(16), capturedAt: Date.now(), source: 'test' });

describe('SimulatedGlassesAdapter', () => {
  it('implements the whole contract and is labelled simulated', async () => {
    const sent: unknown[] = [];
    const a = new SimulatedGlassesAdapter({ frameProvider: frame, onReport: async (p) => { sent.push(p); } });
    await a.connect();
    expect(a.getCapabilities()).toMatchObject({ simulated: true, frames: 'verified', pose: 'not_supported' });
    expect(a.name).toBe('SIMULATED GLASSES');
    await a.showAlert({ text: 'Possible debris ahead — slow down', glyph: 'caution', ttlMs: 10, priority: 'warning' });
    await a.playAudio('Possible debris ahead — slow down');
    expect(a.spoken).toHaveLength(1);
    await a.sendReport({ idempotency_key: 'x' });
    expect(sent).toHaveLength(1);
    a.online = false;
    await expect(a.sendReport({ idempotency_key: 'y' })).rejects.toThrow('offline');
    expect(a.getPose()).toBeNull();
    await a.disconnect();
  });
});

describe('MicroluminAdapter', () => {
  it('declares audited statuses and refuses everything without a bridge', async () => {
    const m = new MicroluminAdapter(null);
    const c = m.getCapabilities();
    expect(c).toMatchObject({ simulated: false, frames: 'not_supported', location: 'not_supported', hud: 'documented_unverified' });
    expect(() => m.subscribeFrames(() => {})).toThrow(UnsupportedOperation);
    await expect(m.connect()).rejects.toThrow(UnsupportedOperation);
    await expect(m.showAlert({ text: 't', glyph: 'caution', ttlMs: 1, priority: 'warning' })).rejects.toThrow(UnsupportedOperation);
  });

  it('routes HUD and reports through an injected bridge as app messages', async () => {
    const msgs: Record<string, unknown>[] = [];
    const bridge: GlassesBridge = { connect: async () => {}, disconnect: async () => {}, status: () => 'connected',
                                    sendAppMessage: async (p) => { msgs.push(p); } };
    const m = new MicroluminAdapter(bridge);
    await m.connect();
    await m.showAlert({ text: 'Possible debris ahead — slow down', glyph: 'caution', ttlMs: 3000, priority: 'warning' });
    await m.sendReport({ idempotency_key: 'k' });
    expect(msgs.map((x) => x.type)).toEqual(['lk.hud', 'lk.report']);
    await expect(m.playAudio('x')).rejects.toThrow(UnsupportedOperation);   // bridge has no speak()
  });
});

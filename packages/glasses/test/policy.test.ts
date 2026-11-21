import { CAUTION_TEXT, Debouncer, isConservative, MemoryKV, OfflineQueue, signBody, WarningPolicy } from '../src';

describe('debounce', () => {
  it('requires consecutive frames above threshold', () => {
    const d = new Debouncer(3, 0.55);
    expect(d.push(0.9).stable).toBe(false);
    expect(d.push(0.9).stable).toBe(false);
    expect(d.push(0.2).stable).toBe(false);   // streak broken
    d.push(0.7); d.push(0.7);
    expect(d.push(0.7)).toMatchObject({ stable: true, consecutive: 3 });
  });
});

describe('warning policy', () => {
  it('only warns on stable candidates, with cooldown, and never reassures', () => {
    const w = new WarningPolicy(8000);
    expect(w.decide(false, 5, 0).alert).toBeNull();
    const a = w.decide(true, 5, 1000);
    expect(a.alert?.text).toBe(CAUTION_TEXT);
    expect(a.promptWhenStopped).toBe(false);
    expect(w.decide(true, 5, 2000).alert).toBeNull();      // cooldown
    expect(w.decide(true, 0.2, 20000).promptWhenStopped).toBe(true);
    expect(isConservative(CAUTION_TEXT)).toBe(true);
    for (const bad of ['Lane clear', 'All good', 'No glass ahead', 'Safe to ride']) expect(isConservative(bad)).toBe(false);
  });
});

describe('offline queue', () => {
  it('keeps reports while offline and flushes idempotently', async () => {
    const q = new OfflineQueue(new MemoryKV());
    q.enqueue({ idempotency_key: 'k-1', a: 1 });
    q.enqueue({ idempotency_key: 'k-1', a: 2 });          // same key replaces, never duplicates
    q.enqueue({ idempotency_key: 'k-2', a: 3 });
    expect(q.items()).toHaveLength(2);
    expect(await q.flush(async () => false)).toEqual({ sent: 0, kept: 2 });
    expect(q.items()[0]!.attempts).toBe(1);
    expect(await q.flush(async () => true)).toEqual({ sent: 2, kept: 0 });
  });
});

describe('device link signature', () => {
  it('matches the orchestrator (Python) HMAC scheme byte for byte', async () => {
    const body = '{"idempotency_key":"k-12345678","x":1}';
    expect(await signBody('0123456789abcdef', 1763740800, body))
      .toBe('af0ed47044adc7aaaa20fc437a918a68b0635296900f41dc9f2d08bae585d10e');
  });
});

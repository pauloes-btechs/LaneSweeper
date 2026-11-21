/**
 * Offline report queue: the glasses/phone keep working without connectivity. Reports are stored locally with
 * their idempotency keys and flushed when the link returns; the server de-duplicates by key, so retries are safe.
 */
export interface KV {
  get(key: string): string | null;
  set(key: string, value: string): void;
}

export class MemoryKV implements KV {
  private m = new Map<string, string>();
  get(k: string) { return this.m.get(k) ?? null; }
  set(k: string, v: string) { this.m.set(k, v); }
}

export interface QueuedReport { idempotency_key: string; body: Record<string, unknown>; attempts: number; queuedAt: number }

export class OfflineQueue {
  constructor(private kv: KV = new MemoryKV(), private key = 'lk.glasses.queue', readonly maxItems = 50) {}

  items(): QueuedReport[] {
    try { return JSON.parse(this.kv.get(this.key) ?? '[]') as QueuedReport[]; } catch { return []; }
  }

  private save(items: QueuedReport[]) { this.kv.set(this.key, JSON.stringify(items.slice(-this.maxItems))); }

  enqueue(body: Record<string, unknown> & { idempotency_key: string }): void {
    const items = this.items().filter((i) => i.idempotency_key !== body.idempotency_key);
    items.push({ idempotency_key: body.idempotency_key, body, attempts: 0, queuedAt: Date.now() });
    this.save(items);
  }

  /** Try to send everything; keep what fails. Returns counts. */
  async flush(send: (body: Record<string, unknown>) => Promise<boolean>): Promise<{ sent: number; kept: number }> {
    const keep: QueuedReport[] = [];
    let sent = 0;
    for (const it of this.items()) {
      let ok = false;
      try { ok = await send(it.body); } catch { ok = false; }
      if (ok) sent++;
      else keep.push({ ...it, attempts: it.attempts + 1 });
    }
    this.save(keep);
    return { sent, kept: keep.length };
  }
}

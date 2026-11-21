/**
 * Device link client: signs requests like the orchestrator expects —
 * HMAC_SHA256(device_key, "<unix_ts>.<sha256_hex(body)>") in x-lk-signature, with x-lk-timestamp.
 * The device holds only its own pairing key. It never holds Google or Supabase keys.
 */
const enc = new TextEncoder();

function hex(buf: ArrayBuffer): string {
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

export async function sha256Hex(data: Uint8Array<ArrayBuffer> | string): Promise<string> {
  const bytes = typeof data === 'string' ? enc.encode(data) : data;
  return hex(await crypto.subtle.digest('SHA-256', bytes));
}

export async function signBody(deviceKey: string, ts: number, body: string): Promise<string> {
  const key = await crypto.subtle.importKey('raw', enc.encode(deviceKey), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  return hex(await crypto.subtle.sign('HMAC', key, enc.encode(`${ts}.${await sha256Hex(body)}`)));
}

export interface LinkConfig { baseUrl: string; deviceId: string; deviceKey: string; fetchImpl?: typeof fetch }

export class DeviceLink {
  constructor(private cfg: LinkConfig) {}

  async postEvent(event: Record<string, unknown>): Promise<Record<string, unknown>> {
    const body = JSON.stringify(event);
    const ts = Math.floor(Date.now() / 1000);
    const sig = await signBody(this.cfg.deviceKey, ts, body);
    const f = this.cfg.fetchImpl ?? fetch;
    const r = await f(`${this.cfg.baseUrl}/v1/devices/${this.cfg.deviceId}/events`, {
      method: 'POST', body, headers: { 'content-type': 'application/json', 'x-lk-timestamp': String(ts), 'x-lk-signature': sig },
    });
    if (!r.ok) throw new Error(`device event rejected: ${r.status}`);
    return (await r.json()) as Record<string, unknown>;
  }

  /** Read-only: where an agent draft would land and whether it is already reported. */
  async previewDraft(q: { lat: number; lng: number; accuracy_m?: number; hazard_claimed?: string }): Promise<Record<string, unknown>> {
    const body = JSON.stringify(q);
    const ts = Math.floor(Date.now() / 1000);
    const sig = await signBody(this.cfg.deviceKey, ts, body);
    const f = this.cfg.fetchImpl ?? fetch;
    const r = await f(`${this.cfg.baseUrl}/v1/devices/${this.cfg.deviceId}/drafts/preview`, {
      method: 'POST', body, headers: { 'content-type': 'application/json', 'x-lk-timestamp': String(ts), 'x-lk-signature': sig },
    });
    if (!r.ok) throw new Error(`preview rejected: ${r.status}`);
    return (await r.json()) as Record<string, unknown>;
  }

  async pollHud(since?: string): Promise<Array<{ id: string; body: { text: string; display: string }; created_at: string }>> {
    const ts = Math.floor(Date.now() / 1000);
    const sig = await signBody(this.cfg.deviceKey, ts, '');
    const f = this.cfg.fetchImpl ?? fetch;
    const q = since ? `?since=${encodeURIComponent(since)}` : '';
    const r = await f(`${this.cfg.baseUrl}/v1/devices/${this.cfg.deviceId}/hud${q}`, {
      headers: { 'x-lk-timestamp': String(ts), 'x-lk-signature': sig },
    });
    if (!r.ok) throw new Error(`hud poll rejected: ${r.status}`);
    return (await r.json()) as Array<{ id: string; body: { text: string; display: string }; created_at: string }>;
  }
}

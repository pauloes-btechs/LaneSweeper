import type { KV } from '@lanekeeper/glasses';

/** Browser storage for the device-side queue and agent drafts; falls back to memory in private mode. */
const mem = new Map<string, string>();
export const lsKV: KV = {
  get: (k) => { try { return localStorage.getItem(k); } catch { return mem.get(k) ?? null; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch { mem.set(k, v); } },
};

export const DRAFTS_KEY = 'lk.rider.drafts';

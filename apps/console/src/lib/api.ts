// Thin API client. The browser never holds Google, Supabase service-role or signing keys — only a user session token.
const BASE = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, '') ?? '';

let memToken: string | null = null;
export const session = {
  get(): string | null {
    try { return sessionStorage.getItem('lk.token') ?? memToken; } catch { return memToken; }
  },
  set(t: string | null, label?: string) {
    memToken = t;
    try {
      if (t) { sessionStorage.setItem('lk.token', t); sessionStorage.setItem('lk.label', label ?? ''); }
      else { sessionStorage.removeItem('lk.token'); sessionStorage.removeItem('lk.label'); }
    } catch { /* storage unavailable: memory only */ }
    window.dispatchEvent(new Event('lk-session'));
  },
  label(): string { try { return sessionStorage.getItem('lk.label') ?? ''; } catch { return ''; } },
};

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function req<T>(method: string, path: string, body?: unknown, auth = true): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers['content-type'] = 'application/json';
  const t = session.get();
  if (auth && t) headers.authorization = `Bearer ${t}`;
  const r = await fetch(`${BASE}${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try {
      const j = await r.json();
      msg = typeof j.detail === 'string' ? j.detail : j.detail ? JSON.stringify(j.detail).slice(0, 300) : j.error ?? msg;
    } catch { /* not JSON */ }
    throw new ApiError(r.status, msg);
  }
  return (await r.json()) as T;
}

export const api = {
  get: <T,>(p: string) => req<T>('GET', p),
  post: <T,>(p: string, b?: unknown) => req<T>('POST', p, b ?? {}),
  publicGet: <T,>(p: string) => req<T>('GET', p, undefined, false),
  publicPost: <T,>(p: string, b: unknown) => req<T>('POST', p, b, false),
  url: (p: string) => `${BASE}${p}`,
};

import { useEffect, useState } from 'react';
import { api, session } from '../lib/api';

interface Ident { key: string; label: string }

/** Local demo sign-in as a seeded identity. Tokens carry identity only; the server decides roles from memberships. */
export function IdentityPicker({ compact = false }: { compact?: boolean }) {
  const [ids, setIds] = useState<Ident[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [label, setLabel] = useState(session.label());
  useEffect(() => {
    api.publicGet<Ident[]>('/v1/demo/identities').then(setIds).catch(() => setErr('Demo sign-in is disabled on this server.'));
    const on = () => setLabel(session.label());
    window.addEventListener('lk-session', on);
    return () => window.removeEventListener('lk-session', on);
  }, []);
  async function pick(key: string) {
    if (!key) { session.set(null); return; }
    const r = await api.publicPost<{ token: string; label: string }>(`/v1/demo/token/${key}`, {});
    session.set(r.token, r.label);
  }
  if (err) return <span className="small muted">{err}</span>;
  return (
    <label className="row small" style={{ gap: 6 }}>
      {!compact && <span className="muted">Signed in as</span>}
      <select aria-label="Demo identity" value={ids?.find((i) => i.label === label)?.key ?? ''} onChange={(e) => void pick(e.target.value)}>
        <option value="">— not signed in —</option>
        {ids?.map((i) => <option key={i.key} value={i.key}>{i.label}</option>)}
      </select>
    </label>
  );
}

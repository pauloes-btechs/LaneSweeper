import { IdentityPicker } from '../components/Identity';
import { MediaPanel } from '../components/MediaPanel';
import { RequireStaff } from '../components/RequireStaff';
import { Chip, ErrorState, Loading, ago } from '../components/ui';
import { useApi } from '../lib/useApi';
import type { Me } from '../lib/types';

interface ModelStatus {
  ai_mode: string; live: boolean; billable_enabled: boolean; daily_cap: number; per_minute_cap: number;
  usage_today: Array<{ bucket: string; count: number; tokens: number }>;
  routes: Record<string, { candidates: string[]; api: string; offline: string }>;
  recent_calls: Array<{ at: string; route: string; model: string; mode: string; outcome: string; latency_ms: number | null }>;
}

function Body({ me }: { me: Me }) {
  const s = useApi<ModelStatus>('/v1/models/status', { poll: 6000 });
  return (
    <div className="stack" style={{ gap: 16 }}>
      <h1>Settings</h1>
      <div className="grid g2">
        <div className="card stack"><h3>You</h3><div className="small">{me.email}</div>
          <table className="data small"><thead><tr><th>Jurisdiction</th><th>Role</th></tr></thead><tbody>{me.memberships.map((m) => <tr key={m.id + m.role}><td>{m.name}</td><td>{m.role}</td></tr>)}</tbody></table>
          <IdentityPicker /><p className="small muted">Roles come from server-side memberships. Changing a token’s claims cannot grant access.</p></div>
        <div className="card stack"><h3>AI models</h3>
          {s.error ? <ErrorState error={s.error} /> : !s.data ? <Loading /> : <>
            <div className="row"><span>Mode</span><Chip v={s.data.live ? 'online' : 'offline'} /><span className="small muted">{s.data.live ? 'Live Gemini routes within caps' : 'AI Offline Mode: labelled fixtures/templates'}</span></div>
            <div className="small">Billable calls switch: <strong>{s.data.billable_enabled ? 'ON' : 'OFF'}</strong> · caps {s.data.per_minute_cap}/min, {s.data.daily_cap}/day</div>
            <table className="data small"><thead><tr><th>Route</th><th>Models</th><th>API</th><th>Offline</th></tr></thead><tbody>
              {Object.entries(s.data.routes).map(([k, r]) => <tr key={k}><td className="mono">{k}</td><td className="mono">{r.candidates.join(', ')}</td><td>{r.api}</td><td>{r.offline}</td></tr>)}</tbody></table></>}
        </div>
      </div>
      {me.memberships.some((m) => m.role === 'city_admin') && <MediaPanel jurisdiction={me.memberships.find((m) => m.role === 'city_admin')?.id} />}
      <div className="card"><h3>Recent model calls (no prompts or images are logged)</h3>
        {!s.data ? <Loading /> : s.data.recent_calls.length === 0 ? <p className="muted small">None yet.</p> :
          <table className="data small"><thead><tr><th>When</th><th>Route</th><th>Model</th><th>Mode</th><th>Outcome</th><th>Latency</th></tr></thead><tbody>
            {s.data.recent_calls.map((c, i) => <tr key={i}><td>{ago(c.at)}</td><td className="mono">{c.route}</td><td className="mono">{c.model}</td><td>{c.mode}</td><td><Chip v={c.outcome === 'ok' ? 'succeeded' : c.outcome} /></td><td>{c.latency_ms ?? '—'} ms</td></tr>)}</tbody></table>}
      </div>
    </div>
  );
}

export default function Settings() { return <RequireStaff>{(me) => <Body me={me} />}</RequireStaff>; }

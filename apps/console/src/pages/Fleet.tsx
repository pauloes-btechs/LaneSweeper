import { useState } from 'react';
import { Link } from 'react-router-dom';
import { LaneMap } from '../components/LaneMap';
import { RequireStaff } from '../components/RequireStaff';
import { Chip, ErrorState, Loading, Prov } from '../components/ui';
import { api } from '../lib/api';
import { useApi } from '../lib/useApi';
import type { Overview } from '../lib/types';

interface Scn { key: string; title: string; twin_scenario: string | null }

function Body() {
  const o = useApi<Overview>('/v1/overview', { poll: 3000 });
  const scn = useApi<Scn[]>('/v1/demo/scenarios', { isPublic: true });
  const [size, setSize] = useState(2);
  const [msg, setMsg] = useState<{ tone: string; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  async function run<T>(fn: () => Promise<T>, ok: (r: T) => string) {
    setBusy(true); setMsg(null);
    try { const r = await fn(); setMsg({ tone: 'info', text: ok(r) }); o.reload(); }
    catch (e) { setMsg({ tone: 'err', text: (e as Error).message }); } finally { setBusy(false); }
  }
  if (o.error) return <ErrorState error={o.error} />;
  if (!o.data) return <Loading />;
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="row between"><h1>Fleet</h1><Prov /></div>
      <div className="callout warn small">All robots here are simulated. Movement happens only after an authorised supervisor approves a specific, hashed, simulation-only mission.
        The local safety controller can stop a robot at any time, and no model or agent can override it.</div>
      <div className="grid g-side">
        <div className="card"><h3>Positions</h3><LaneMap robots={o.data.robots} label="Simulated robot positions" /></div>
        <div className="stack">
          <div className="card"><h3>Robots</h3>
            <table className="data small"><thead><tr><th>Robot</th><th>State</th><th>Battery</th><th>Hopper</th><th>Link</th><th>Cell</th></tr></thead>
              <tbody>{o.data.robots.map((r) => <tr key={r.id}><td className="mono">{r.id}</td><td><Chip v={r.state} /></td><td>{Math.round(r.battery_pct)}%</td>
                <td>{Math.round(r.hopper_pct)}%</td><td><Chip v={r.connectivity} /></td><td className="mono">{r.cell_id ?? 'depot'}</td></tr>)}</tbody></table>
          </div>
          <div className="card stack"><h3>Nightly planning (Scenario D)</h3>
            <p className="small muted" style={{ margin: 0 }}>Plans every open robot-sweep work order with the deterministic planner (battery reserve, hopper, closures, zones, window) and opens one approval per mission.</p>
            <div className="row"><button className="primary" disabled={busy} onClick={() => void run(() => api.post<{ approvals?: unknown[] }>('/v1/fleet/plan', { immediate: true }),
              (r) => `Planned ${(r.approvals ?? []).length} mission(s). Review them in Agents & Approvals.`)}>Plan sweep now (simulated window)</button>
              <Link to="/console/agents">Review approvals →</Link></div>
            <label className="field">Simulated fleet size: {size}<input type="range" min={2} max={10} value={size} onChange={(e) => setSize(Number(e.target.value))}
              onMouseUp={() => void run(() => api.publicPost(`/v1/demo/fleet-size/${size}`, {}), () => `Simulated fleet set to ${size}.`)}
              onKeyUp={() => void run(() => api.publicPost(`/v1/demo/fleet-size/${size}`, {}), () => `Simulated fleet set to ${size}.`)} /></label>
          </div>
        </div>
      </div>
      <div className="card"><h3>Scenario launcher</h3>
        <p className="small muted" style={{ marginTop: 0 }}>Each scenario submits a seeded, labelled report through the real graphs. Approvals and resumes then happen in the console.</p>
        <div className="row">{scn.data?.map((s) => <button key={s.key} disabled={busy} onClick={() => void run(() => api.publicPost<{ ticket_number: string | null; route: string | null; next: string | null }>(`/v1/demo/scenario/${s.key}`, {}),
          (r) => `${s.key}: ${r.route ?? ''} → ${r.ticket_number ?? 'awaiting evidence'}${r.next ? ` · ${r.next}` : ''}`)}>{s.key} · {s.title}</button>)}
          <button disabled={busy} onClick={() => void run(() => api.publicPost('/v1/demo/reset-fleet', {}), () => 'Fleet docked and reset.')}>Reset fleet</button></div>
        {msg && <div className={`callout ${msg.tone} small`} style={{ marginTop: 10 }} role="status">{msg.text}</div>}
      </div>
    </div>
  );
}

export default function Fleet() { return <RequireStaff>{() => <Body />}</RequireStaff>; }

import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { LaneMap } from '../components/LaneMap';
import { RequireStaff } from '../components/RequireStaff';
import { Twin3D } from '../components/Twin3D';
import { Chip, Empty, ErrorState, Loading, Prov, ago, short } from '../components/ui';
import { api } from '../lib/api';
import { useApi } from '../lib/useApi';

interface MissionRow { id: string; robot_id: string; status: string; scenario: string | null; dispatched_at: string; ended_at: string | null;
  abort_reason: string | null; operation_id: string; approved_by: string; route_cell_ids: string[]; estimates: Record<string, number | string>; report_slug: string | null }
interface Tele { sim_time_s: number; cell_id: string; speed_mps: number; battery_pct: number; hopper_pct: number; state: string; lat: number; lng: number }
interface MissionFull {
  id: string; robot_id: string; status: string; scenario: string | null; abort_reason: string | null; operation_id: string; dispatched_at: string; thread_id: string | null;
  stops: Array<{ seq: number; cell_id: string; planned_eta_s: number; status: string }>;
  events: Array<{ event_id: string; sim_time_s: number; kind: string; actor: string; detail: Record<string, unknown> }>;
  faults: Array<{ code: string; severity: string; safety_action: string; opened_at: string; cleared_at: string | null; cleared_by: string | null }>;
  telemetry?: Tele[]; evidence: Array<{ cell_id: string; phase: string; fixture_id: string | null; analysis: { verdict: string; provenance?: { mode: string; model: string } } }>;
  verdicts: Array<{ cell_id: string; verdict: string; reason: string; confidence: number | null }>;
}

export function MissionList() {
  return <RequireStaff>{() => <ListBody />}</RequireStaff>;
}

function ListBody() {
  const m = useApi<MissionRow[]>('/v1/missions', { poll: 5000 });
  const nav = useNavigate();
  return (
    <div className="stack" style={{ gap: 14 }}>
      <div className="row between"><h1>Missions</h1><Prov /></div>
      <p className="muted small" style={{ margin: 0 }}>Every mission below exists only because a named supervisor approved a specific, hashed, simulation-only proposal.</p>
      {m.error ? <ErrorState error={m.error} /> : !m.data ? <Loading /> : m.data.length === 0 ? <Empty>No missions yet.</Empty> : (
        <div className="table-wrap"><table className="data"><thead><tr><th>Mission</th><th>Robot</th><th>Status</th><th>Scenario</th><th>Cells</th><th>Estimate</th><th>Approved by</th><th>Dispatched</th><th>Report</th></tr></thead>
          <tbody>{m.data.map((x) => <tr key={x.id} className="click" tabIndex={0} onClick={() => nav(`/console/missions/${x.id}`)}>
            <td className="mono">{short(x.id)}</td><td className="mono">{x.robot_id}</td><td><Chip v={x.status} /></td><td className="small">{x.scenario ?? 'nominal'}</td>
            <td>{x.route_cell_ids.length}</td><td className="small">{x.estimates.duration_min} min (est.)</td><td className="mono small">{short(x.approved_by)}</td>
            <td className="small">{ago(x.dispatched_at)}</td><td>{x.report_slug ? <Link to={`/r/${x.report_slug}`} onClick={(e) => e.stopPropagation()}>public ↗</Link> : '—'}</td></tr>)}</tbody></table></div>)}
    </div>
  );
}

export function MissionDetail() {
  return <RequireStaff>{() => <DetailBody />}</RequireStaff>;
}

function DetailBody() {
  const { id } = useParams();
  const m = useApi<MissionFull>(`/v1/missions/${id}`, { poll: 4000 });
  const [i, setI] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [jamCleared, setJamCleared] = useState(false);
  const tele = m.data?.telemetry ?? [];
  useEffect(() => {
    if (!playing) return;
    const t = setInterval(() => setI((x) => { if (x >= tele.length - 1) { setPlaying(false); return x; } return x + 1; }), 350);
    return () => clearInterval(t);
  }, [playing, tele.length]);
  const conditions = useMemo(() => {
    const out: Record<string, string> = {};
    for (const s of m.data?.stops ?? []) out[s.cell_id] = 'scheduled';
    for (const v of m.data?.verdicts ?? []) out[v.cell_id] = v.verdict === 'cleared' ? 'cleared_verified' : v.verdict === 'unverified' ? 'cleared_unverified' : 'partially_cleared';
    return out;
  }, [m.data]);
  if (m.error) return <ErrorState error={m.error} onRetry={m.reload} />;
  if (!m.data) return <Loading rows={8} />;
  const d = m.data;
  const cur = tele[Math.min(i, Math.max(0, tele.length - 1))];
  const evAt = d.events.filter((e) => cur && e.sim_time_s <= cur.sim_time_s);
  async function resolveSafety(action: 'resume' | 'abort') {
    if (!d.thread_id) { setMsg('No ticket thread is linked to this mission.'); return; }
    try {
      const r = await api.post<{ status: string; outcome?: string }>(`/v1/threads/${encodeURIComponent(d.thread_id)}/resume`,
        { kind: 'safety', action, link_restored: true, jam_cleared: jamCleared });
      setMsg(action === 'resume' ? `Resume accepted by the local safety controller → ${r.outcome ?? r.status}` : 'Mission aborted after safety pause.');
      m.reload();
    } catch (e) { setMsg(`Refused: ${(e as Error).message}`); }
  }
  async function stop() {
    try { await api.post(`/v1/missions/${d.id}/stop`, { reason: 'operator pressed stop in console' }); setMsg('Mission aborted; robot returned to depot.'); m.reload(); }
    catch (e) { setMsg(`Refused: ${(e as Error).message}`); }
  }
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="row between">
        <div><Link to="/console/missions" className="small">← Missions</Link>
          <h1 className="row">Mission <span className="mono">{short(d.id)}</span> <Chip v={d.status} /> <Prov /></h1>
          <div className="muted small">{d.robot_id} · scenario {d.scenario ?? 'nominal'} · operation <span className="mono">{short(d.operation_id)}</span>{d.abort_reason && ` · ${d.abort_reason}`}</div></div>
        <button className="danger" onClick={() => void stop()} disabled={['completed', 'aborted', 'failed'].includes(d.status)}>Stop / abort mission</button>
      </div>
      {d.status === 'paused_safety' && <div className="callout warn small stack" role="region" aria-label="Safety pause">
        <strong>Paused by the local safety controller.</strong>
        <span>Supervisors may resume only when the controller allows it; a jam must be cleared by a technician first. Agents cannot resume.</span>
        <div className="row">
          <label className="row small"><input type="checkbox" checked={jamCleared} onChange={(e) => setJamCleared(e.target.checked)} /> Technician confirmed jam cleared</label>
          <button onClick={() => void resolveSafety('resume')}>Supervisor resume</button>
          <button className="danger" onClick={() => void resolveSafety('abort')}>Abort mission</button>
        </div></div>}
      {msg && <div className="callout info small" role="status">{msg}</div>}
      <div className="grid g-side">
        <div className="card stack">
          <div className="row between"><h3>Telemetry replay (SIMULATED, synthetic time)</h3>
            <div className="row"><button onClick={() => setPlaying((p) => !p)} disabled={!tele.length}>{playing ? 'Pause' : 'Play'}</button>
              <button onClick={() => setI((x) => Math.min(tele.length - 1, x + 1))} disabled={!tele.length}>Step</button>
              <button onClick={() => { setI(0); setPlaying(false); }}>Reset</button></div></div>
          {tele.length === 0 ? <p className="muted small">Your role cannot view telemetry, or the mission has not moved.</p> : <>
            <input type="range" min={0} max={tele.length - 1} value={i} onChange={(e) => setI(Number(e.target.value))} aria-label="Replay position" />
            <LaneMap conditions={conditions} path={tele.map((t) => ({ lat: t.lat, lng: t.lng }))} cursor={cur ? { lat: cur.lat, lng: cur.lng } : null}
              focusCells={d.stops.map((s) => s.cell_id)} label="Mission route and replay position" />
            {cur && <div className="row small"><span>t+{cur.sim_time_s.toFixed(0)} s</span><Chip v={cur.state} /><span className="mono">{cur.cell_id}</span>
              <span>battery {cur.battery_pct.toFixed(1)}%</span><span>hopper {cur.hopper_pct.toFixed(0)}%</span><span>{cur.speed_mps} m/s</span></div>}</>}
        </div>
        <div className="stack">
          <div className="card"><h3>Digital twin</h3>
            <Twin3D state={cur?.state ?? d.status} progress={tele.length ? i / Math.max(1, tele.length - 1) : 0} sweeping={cur?.state === 'sweeping'} /></div>
          <div className="card"><h3>Safety controller events</h3>
            {d.faults.length === 0 ? <p className="muted small">None.</p> : d.faults.map((f, k) => <div key={k} className="small"><Chip v={f.safety_action} /> {f.code.replace(/_/g, ' ')} · {f.cleared_at ? `cleared by ${f.cleared_by}` : 'open'}</div>)}
          </div>
        </div>
      </div>
      <div className="grid g2">
        <div className="card"><h3>Before / after evidence and verdicts</h3>
          {d.evidence.length === 0 && <p className="muted small">No inspection frames.</p>}
          <div className="grid g2">{d.evidence.map((e, k) => <figure key={k} style={{ margin: 0 }}>
            {e.fixture_id && <img src={api.url(`/v1/fixtures/${e.fixture_id}.png`)} alt={`${e.phase} frame ${e.cell_id}`} style={{ width: '100%', borderRadius: 6 }} />}
            <figcaption className="small">{e.phase} · {e.cell_id} · vision <strong>{e.analysis.verdict}</strong> <span className="faint">({e.analysis.provenance?.mode}, {e.analysis.provenance?.model})</span></figcaption></figure>)}</div>
          {d.verdicts.map((v, k) => <div key={k} className="small" style={{ marginTop: 6 }}><Chip v={v.verdict} /> {v.cell_id}: {v.reason}</div>)}
        </div>
        <div className="card"><h3>Event log ({evAt.length}/{d.events.length} at replay position)</h3>
          <ul className="trace">{d.events.map((e) => <li key={e.event_id} className={e.kind.startsWith('safety') ? 'k-interrupt' : ''} style={{ opacity: evAt.includes(e) || !tele.length ? 1 : 0.4 }}>
            <div className="node">t+{e.sim_time_s?.toFixed(0)}s · {e.kind.replace(/_/g, ' ')}</div><div className="sum">{e.actor}{typeof e.detail.reason === 'string' ? ` · ${e.detail.reason}` : ''}</div></li>)}</ul>
        </div>
      </div>
    </div>
  );
}

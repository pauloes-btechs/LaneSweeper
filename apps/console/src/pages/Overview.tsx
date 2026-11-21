import { Link } from 'react-router-dom';
import { LaneMap } from '../components/LaneMap';
import { RequireStaff } from '../components/RequireStaff';
import { Chip, ErrorState, Kpi, Loading, Prov, ago, short } from '../components/ui';
import { useApi } from '../lib/useApi';
import type { Overview as O } from '../lib/types';

interface Mission { id: string; robot_id: string; status: string; dispatched_at: string; scenario: string | null; report_slug: string | null }
interface Run { id: string; workflow: string; status: string; current_node: string | null; started_at: string }

function Body() {
  const o = useApi<O>('/v1/overview', { poll: 4000 });
  const cells = useApi<Array<{ cell_id: string; condition: string }>>('/v1/lane-status', { poll: 4000 });
  const missions = useApi<Mission[]>('/v1/missions', { poll: 6000 });
  const runs = useApi<Run[]>('/v1/runs?limit=8', { poll: 4000 });
  if (o.error) return <ErrorState error={o.error} onRetry={o.reload} />;
  if (!o.data) return <Loading rows={6} />;
  const d = o.data;
  const open = d.tickets_by_status.filter((s) => !['verified_closed', 'dismissed'].includes(s.status)).reduce((a, s) => a + s.n, 0);
  const cond = Object.fromEntries((cells.data ?? []).map((c) => [c.cell_id, c.condition]));
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="row between"><h1>Overview</h1><Prov v={d.provenance} /></div>
      <div className="grid g4">
        <Kpi label="Active hazard cells" value={d.active_hazards} note="25 m cells with reported/confirmed/partial hazards" />
        <Kpi label="Open tickets" value={open} />
        <Kpi label="Pending approvals" value={<Link to="/console/agents">{d.pending_approvals}</Link>} note="simulated dispatch requests" />
        <Kpi label="Response target (median)" value={d.median_minutes_to_respond_estimate != null ? `${d.median_minutes_to_respond_estimate} min` : '—'} note="from SLA policy, an estimate" />
      </div>
      <div className="grid g-side">
        <div className="card"><h2>Protected lanes and simulated robots <span className="small muted">1st Ave, 25 m cells</span></h2>
          <LaneMap conditions={cond} robots={d.robots} label="Overview map of lane conditions and simulated robots" /></div>
        <div className="stack">
          <div className="card"><h3>Open tickets by priority</h3>
            {d.tickets_by_priority.length === 0 ? <p className="muted small">No open tickets.</p> :
              <table className="data"><tbody>{d.tickets_by_priority.map((p) => <tr key={p.priority}><td>P{p.priority}</td><td>{p.n}</td></tr>)}</tbody></table>}
          </div>
          <div className="card"><h3>Robot health <Prov /></h3>
            <table className="data"><thead><tr><th>Robot</th><th>State</th><th>Battery</th><th>Link</th></tr></thead>
              <tbody>{d.robots.map((r) => <tr key={r.id}><td className="mono">{r.id}</td><td><Chip v={r.state} /></td><td>{Math.round(r.battery_pct)}%</td><td><Chip v={r.connectivity} /></td></tr>)}</tbody></table>
          </div>
        </div>
      </div>
      <div className="grid g2">
        <div className="card"><h3>Today’s cleaning missions</h3>
          {!missions.data?.length ? <p className="muted small">No missions yet. Approve a dispatch request in Agents & Approvals.</p> :
            <table className="data"><thead><tr><th>Mission</th><th>Robot</th><th>Status</th><th>When</th><th>Report</th></tr></thead><tbody>
              {missions.data.slice(0, 6).map((m) => <tr key={m.id}><td><Link className="mono" to={`/console/missions/${m.id}`}>{short(m.id)}</Link></td>
                <td className="mono">{m.robot_id}</td><td><Chip v={m.status} /></td><td>{ago(m.dispatched_at)}</td>
                <td>{m.report_slug ? <Link to={`/r/${m.report_slug}`}>public ↗</Link> : '—'}</td></tr>)}</tbody></table>}
        </div>
        <div className="card"><h3>Autonomous workflows</h3>
          {!runs.data?.length ? <p className="muted small">No agent runs yet.</p> :
            <table className="data"><thead><tr><th>Graph</th><th>Status</th><th>Now at</th><th>Started</th></tr></thead><tbody>
              {runs.data.map((r) => <tr key={r.id}><td><Link to={`/console/agents?run=${r.id}`}>{r.workflow}</Link></td><td><Chip v={r.status} /></td>
                <td className="mono small">{r.current_node ?? '—'}</td><td>{ago(r.started_at)}</td></tr>)}</tbody></table>}
        </div>
      </div>
    </div>
  );
}

export default function Overview() { return <RequireStaff>{() => <Body />}</RequireStaff>; }

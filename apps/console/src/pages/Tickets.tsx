import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { RequireStaff } from '../components/RequireStaff';
import { Chip, Empty, ErrorState, Loading, Prio, Prov, ago } from '../components/ui';
import { useApi } from '../lib/useApi';
import type { Ticket } from '../lib/types';

const STATUSES = ['', 'new', 'needs_evidence', 'triaged', 'assigned', 'in_progress', 'reopened', 'escalated', 'verified_closed', 'dismissed'];
const HAZARDS = ['', 'glass', 'debris', 'litter', 'pothole', 'water', 'branch', 'obstruction', 'markings', 'construction'];

function Body() {
  const [status, setStatus] = useState('');
  const [hazard, setHazard] = useState('');
  const q = new URLSearchParams({ ...(status && { status }), ...(hazard && { hazard }) }).toString();
  const t = useApi<Ticket[]>(`/v1/tickets${q ? `?${q}` : ''}`, { poll: 5000 });
  const nav = useNavigate();
  return (
    <div className="stack" style={{ gap: 14 }}>
      <div className="row between"><h1>Tickets</h1><Prov /></div>
      <div className="row">
        <label className="field">Status<select value={status} onChange={(e) => setStatus(e.target.value)}>{STATUSES.map((s) => <option key={s} value={s}>{s ? s.replace(/_/g, ' ') : 'any'}</option>)}</select></label>
        <label className="field">Hazard<select value={hazard} onChange={(e) => setHazard(e.target.value)}>{HAZARDS.map((s) => <option key={s} value={s}>{s || 'any'}</option>)}</select></label>
      </div>
      {t.error ? <ErrorState error={t.error} onRetry={t.reload} /> : !t.data ? <Loading rows={6} /> : t.data.length === 0 ? <Empty>No tickets match. Report a hazard from the rider app or the glasses simulator.</Empty> : (
        <div className="table-wrap"><table className="data">
          <thead><tr><th>Ticket</th><th>Priority</th><th>Hazard</th><th>Status</th><th>Path</th><th>Owner</th><th>Cell</th><th>Reports</th><th>Opened</th></tr></thead>
          <tbody>{t.data.map((x) => (
            <tr key={x.id} className="click" tabIndex={0} onClick={() => nav(`/console/tickets/${x.number}`)} onKeyDown={(e) => e.key === 'Enter' && nav(`/console/tickets/${x.number}`)}>
              <td className="mono">{x.number}</td><td><Prio p={x.priority} /></td><td>{x.severity} {x.hazard_type}</td><td><Chip v={x.status} /></td>
              <td className="small">{x.remediation_path.replace(/_/g, ' ')}</td><td className="small">{x.department ?? 'triage'}</td>
              <td className="mono small">{x.cell_id ?? '—'}</td><td>{x.report_count ?? 1}</td><td className="small">{ago(x.created_at)}</td></tr>))}
          </tbody></table></div>)}
    </div>
  );
}

export default function Tickets() { return <RequireStaff>{() => <Body />}</RequireStaff>; }

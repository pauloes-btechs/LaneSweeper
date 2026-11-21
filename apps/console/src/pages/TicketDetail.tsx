import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { LaneMap } from '../components/LaneMap';
import { RequireStaff } from '../components/RequireStaff';
import { Chip, ErrorState, Loading, Prio, Prov, ago, short } from '../components/ui';
import { api } from '../lib/api';
import { useApi } from '../lib/useApi';

interface Report {
  id: string; source_kind: string; received_at: string; accuracy_m: number | null; match_status: string | null; match_distance_m: number | null;
  cell_id: string | null; note: string | null; hazard_claimed: string | null; link_kind: string; rule_score: number | null; vector_score: number | null;
  local_detector: { label: string; score: number; consecutive_frames: number; model: string } | null;
  media: Array<{ url: string; fixture_id: string | null; mime: string }>;
}
interface TicketFull {
  id: string; number: string; hazard_type: string; severity: string; priority: number; status: string; remediation_path: string; summary: string;
  priority_basis: Record<string, unknown>; created_at: string; respond_by: string | null; resolve_by: string | null; cell_id: string | null;
  report_count: number | null; department: string | null; department_name: string | null; provenance: string;
  events: Array<{ at: string; actor: string; kind: string; from_status: string | null; to_status: string | null; detail: Record<string, unknown> }>;
  verdicts: Array<{ verdict: string; reason: string; method: string; confidence: number | null; created_at: string; mission_id: string | null }>;
  work_orders: Array<{ id: string; kind: string; status: string; mission_id: string | null }>;
  reports: Report[]; agent_run: { id: string; thread_id: string; status: string } | null;
}

function Body() {
  const { ref } = useParams();
  const t = useApi<TicketFull>(`/v1/tickets/${ref}`, { poll: 5000 });
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function act(tool: string, args: Record<string, unknown>) {
    setBusy(true); setMsg(null);
    try {
      const r = await api.post<{ result: unknown }>(`/v1/tools/${tool}`, args);
      setMsg(`${tool.replace(/_/g, ' ')}: ${JSON.stringify(r.result).slice(0, 220)}`);
      t.reload();
    } catch (e) { setMsg(`Refused: ${(e as Error).message}`); } finally { setBusy(false); }
  }
  if (t.error) return <ErrorState error={t.error} onRetry={t.reload} />;
  if (!t.data) return <Loading rows={8} />;
  const d = t.data;
  const b = d.priority_basis as Record<string, string | number | null>;
  const robotWO = d.work_orders.find((w) => w.kind === 'robot_sweep');
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="row between">
        <div><Link to="/console/tickets" className="small">← Tickets</Link><h1 className="row">{d.number} <Prio p={d.priority} /> <Chip v={d.status} /></h1>
          <div className="muted">{d.severity} {d.hazard_type} · cell <span className="mono">{d.cell_id ?? 'unmatched'}</span> · {d.department_name ?? 'triage'} · opened {ago(d.created_at)}</div></div>
        <Prov v={d.provenance} />
      </div>
      <div className="grid g-side">
        <div className="stack">
          <div className="card"><h3>Why this priority</h3>
            <p style={{ margin: '0 0 8px' }}>{d.summary}</p>
            <table className="data"><tbody>
              <tr><td>Rule applied</td><td>{String(b.rule ?? '—')}</td></tr>
              <tr><td>Severity / confidence</td><td>{String(b.severity)} · {String(b.confidence)} <span className="faint small">(advisory model output + deterministic rubric)</span></td></tr>
              <tr><td>Independent reports</td><td>{String(b.independent_reports ?? 1)} · {String(b.recurrence_30d ?? 0)} in this cell in 30 days</td></tr>
              <tr><td>Policy</td><td className="mono small">{String(b.policy_version ?? '—')}</td></tr>
              <tr><td>SLA</td><td>respond by {d.respond_by ? new Date(d.respond_by).toLocaleString() : '—'} · resolve by {d.resolve_by ? new Date(d.resolve_by).toLocaleString() : '—'}</td></tr>
            </tbody></table>
            {d.agent_run && <p className="small" style={{ marginBottom: 0 }}>Full agent reasoning: <Link to={`/console/agents?run=${d.agent_run.id}`}>graph trace {short(d.agent_run.id)}</Link></p>}
          </div>
          <div className="card"><h3>Evidence ({d.reports.length} report{d.reports.length === 1 ? '' : 's'})</h3>
            {d.reports.length === 0 && <p className="muted small">Your role can see the ticket but not rider evidence.</p>}
            {d.reports.map((r) => (
              <div key={r.id} className="grid g2" style={{ borderTop: '1px solid var(--line-2)', paddingTop: 10, marginTop: 10 }}>
                <div>{r.media.map((m) => <img key={m.url} src={api.url(m.url)} alt={`Evidence frame ${m.fixture_id ?? ''}`} style={{ width: '100%', borderRadius: 8 }} />)}
                  {r.media.length === 0 && <p className="muted small">No image (text/location only).</p>}</div>
                <div className="small stack" style={{ gap: 4 }}>
                  <div><Chip v={r.link_kind} /> {r.source_kind.replace(/_/g, ' ')} · {ago(r.received_at)}</div>
                  <div>Location: <strong>{r.match_status ?? '—'}</strong>{r.match_distance_m != null && ` · ${r.match_distance_m.toFixed(1)} m from cell`}{r.accuracy_m != null && ` · GPS ±${r.accuracy_m} m`}</div>
                  {r.rule_score != null && <div>Duplicate link: rules {r.rule_score.toFixed(2)} · vector {r.vector_score?.toFixed(2) ?? '—'}</div>}
                  {r.local_detector && <div>On-device detector: {r.local_detector.label} {r.local_detector.score} over {r.local_detector.consecutive_frames} frames <span className="faint">({r.local_detector.model})</span></div>}
                  {r.note && <div>Rider note (unverified): “{r.note}”</div>}
                  {r.media[0]?.fixture_id && <div className="faint">Labelled synthetic fixture: {r.media[0].fixture_id}</div>}
                </div>
              </div>))}
          </div>
        </div>
        <div className="stack">
          <div className="card"><h3>Location</h3><LaneMap conditions={d.cell_id ? { [d.cell_id]: d.status === 'verified_closed' ? 'cleared_verified' : 'hazard_confirmed' } : {}} focusCells={d.cell_id ? [d.cell_id] : []} label={`Map of ticket ${d.number}`} /></div>
          <div className="card"><h3>Actions</h3>
            <p className="small muted" style={{ marginTop: 0 }}>Buttons call the same policy-checked tools as the agents. Your role decides what succeeds.</p>
            <div className="row">
              {!robotWO && ['glass', 'debris', 'litter'].includes(d.hazard_type) && <button disabled={busy} onClick={() => void act('draft_work_order', { ticket: d.number, kind: 'robot_sweep' })}>Draft robot-sweep work order</button>}
              {robotWO?.status === 'open' && <button disabled={busy} onClick={() => void act('request_dispatch_approval', { tickets: [d.number], use_next_sweep_window: false })}>Request simulated dispatch approval</button>}
              <button disabled={busy} onClick={() => void act('propose_assignment', { ticket: d.number, assignee_kind: 'department', assignee_ref: d.department ?? 'triage', reason: 'assigned from console' })}>Assign to {d.department ?? 'triage'}</button>
              <button disabled={busy} onClick={() => void act('request_verification', { ticket: d.number, reason: 'operator requested re-inspection' })}>Request re-inspection</button>
              <button disabled={busy} onClick={() => void act('draft_rider_notice', { ticket: d.number, situation: 'Crews are reviewing this report.' })}>Draft rider notice</button>
            </div>
            {msg && <div className="callout info small" style={{ marginTop: 10 }} role="status">{msg}</div>}
          </div>
          <div className="card"><h3>Verification</h3>
            {d.verdicts.length === 0 ? <p className="muted small">No cleanup verdict yet. A finished route alone never closes a ticket.</p> :
              d.verdicts.map((v, i) => <div key={i} className="small" style={{ marginBottom: 6 }}><Chip v={v.verdict} /> {v.reason} <span className="faint">({v.method}{v.confidence != null ? `, ${v.confidence}` : ''})</span>
                {v.mission_id && <> · <Link to={`/console/missions/${v.mission_id}`}>mission</Link></>}</div>)}
          </div>
          <div className="card"><h3>Audit trail</h3>
            <ul className="trace">{d.events.map((e, i) => <li key={i}><div className="node">{e.kind.replace(/_/g, ' ')} {e.to_status && <Chip v={e.to_status} />}</div>
              <div className="sum">{e.actor} · {new Date(e.at).toLocaleString()}</div></li>)}</ul>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function TicketDetail() { return <RequireStaff>{() => <Body />}</RequireStaff>; }

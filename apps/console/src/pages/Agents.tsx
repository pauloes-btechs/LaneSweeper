import { useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { RequireStaff } from '../components/RequireStaff';
import { Chip, Empty, ErrorState, Loading, Prov, ago, short } from '../components/ui';
import { api } from '../lib/api';
import { useApi } from '../lib/useApi';
import type { Approval, TraceEvent } from '../lib/types';

interface Run { id: string; workflow: string; thread_id: string; status: string; current_node: string | null; started_at: string; error: string | null }
interface Shape { nodes: string[]; edges: Array<{ source: string; target: string; conditional: boolean }> }

function ApprovalCard({ a, onDone }: { a: Approval; onDone: (outcome: { tone: string; text: string }) => void }) {
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ tone: string; text: string } | null>(null);
  const expiresIn = Math.round((new Date(a.expires_at).getTime() - Date.now()) / 60000);
  async function decide(approve: boolean) {
    setBusy(true); setMsg(null);
    try {
      await api.post(`/v1/approvals/${a.id}/decide`, { approve, reason });
      let text: string;
      if (approve && a.thread_id) {
        const r = await api.post<{ outcome?: string; status: string }>(`/v1/threads/${encodeURIComponent(a.thread_id)}/resume`, { kind: 'approval' });
        text = `Approved and graph resumed → ${r.outcome ?? r.status}`;
      } else if (!approve && a.thread_id) {
        await api.post(`/v1/threads/${encodeURIComponent(a.thread_id)}/resume`, { kind: 'approval' });
        text = 'Denied. No robot movement; ticket preserved and escalated.';
      } else text = approve ? 'Approved.' : 'Denied.';
      onDone({ tone: 'info', text: `${a.robot_id ?? ''} ${text}`.trim() });   // the card unmounts on refresh; the page keeps the outcome
    } catch (e) { setMsg({ tone: 'err', text: (e as Error).message }); } finally { setBusy(false); }
  }
  const payload = a.payload as { robot_id?: string; route_cell_ids?: string[]; window_start?: string; window_end?: string; simulation_mode?: boolean };
  return (
    <div className="card stack">
      <div className="row between"><strong>{a.action.replace(/_/g, ' ')}</strong><Chip v={a.status} /></div>
      <div className="small muted">Requested by {a.requested_by} · {ago(a.created_at)} · expires in {expiresIn} min · operation <span className="mono">{short(a.operation_id)}</span></div>
      <table className="data small"><tbody>
        <tr><td>Robot</td><td className="mono">{payload.robot_id}</td></tr>
        <tr><td>Route</td><td className="mono">{payload.route_cell_ids?.[0]} → {payload.route_cell_ids?.at(-1)} ({payload.route_cell_ids?.length} cells)</td></tr>
        <tr><td>Window</td><td>{payload.window_start && new Date(payload.window_start).toLocaleString()} – {payload.window_end && new Date(payload.window_end).toLocaleTimeString()}</td></tr>
        <tr><td>Simulation only</td><td>{String(payload.simulation_mode)} <Prov /></td></tr>
        {a.estimates && <tr><td>Estimates</td><td>{a.estimates.distance_m} m · {a.estimates.duration_min} min · battery → {a.estimates.battery_end_pct}% <span className="faint">(ESTIMATES)</span></td></tr>}
        <tr><td>Payload hash</td><td className="mono small">{a.payload_hash.slice(0, 20)}…</td></tr>
      </tbody></table>
      {a.constraints && <div className="row small">{a.constraints.map((c) => <span key={c.check} className={`chip ${c.ok ? 'c-green' : 'c-red'}`} title={c.detail}>{c.check.replace(/_/g, ' ')}</span>)}</div>}
      {a.rationale && <div className="small">{a.rationale}</div>}
      {a.status === 'pending' && <>
        <label className="field">Reason (recorded in the audit log)<textarea value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Reviewed window, battery and route; no closures" /></label>
        <div className="row"><button className="approve" disabled={busy || reason.trim().length < 3} onClick={() => void decide(true)}>Approve simulated dispatch</button>
          <button className="danger" disabled={busy || reason.trim().length < 3} onClick={() => void decide(false)}>Deny</button></div></>}
      {msg && <div className={`callout ${msg.tone} small`} role="status">{msg.text}</div>}
    </div>
  );
}

function Trace({ runId }: { runId: string }) {
  const ev = useApi<{ workflow: string; status: string; thread_id: string; events: TraceEvent[] }>(`/v1/runs/${runId}/events`, { poll: 3000 });
  const shape = useApi<Shape>(ev.data ? `/v1/graphs/${ev.data.workflow}` : null);
  const visited = useMemo(() => new Set((ev.data?.events ?? []).filter((e) => e.kind === 'exit').map((e) => e.node)), [ev.data]);
  const last = ev.data?.events.at(-1)?.node;
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  if (ev.error) return <ErrorState error={ev.error} />;
  if (!ev.data) return <Loading />;
  async function resume(kind: string, extra: Record<string, unknown>) {
    setBusy(true); setErr(null);
    try { await api.post(`/v1/threads/${encodeURIComponent(ev.data!.thread_id)}/resume`, { kind, ...extra }); ev.reload(); }
    catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  return (
    <div className="stack">
      <div className="row between"><h3>{ev.data.workflow} graph · <span className="mono">{short(runId)}</span></h3><Chip v={ev.data.status} /></div>
      {shape.data && <div className="graphmap" aria-label="Graph nodes (green = executed, amber = current)">
        {shape.data.nodes.map((n) => <span key={n} className={n === last && ev.data!.status.startsWith('waiting') ? 'now' : visited.has(n) ? 'on' : ''}>{n}</span>)}</div>}
      {ev.data.status === 'waiting_evidence' && <div className="row"><span className="small muted">Waiting for evidence:</span>
        <button disabled={busy} onClick={() => void resume('evidence', { decision: 'confirm', reason: 'operator confirmed on patrol' })}>Confirm hazard</button>
        <button disabled={busy} onClick={() => void resume('evidence', { decision: 'dismiss', reason: 'no hazard found' })}>Dismiss</button></div>}
      {ev.data.status === 'waiting_approval' && last === 'await_safety_resolution' && <div className="row"><span className="small muted">Safety pause:</span>
        <button disabled={busy} onClick={() => void resume('safety', { action: 'resume', link_restored: true })}>Resume (local rules still apply)</button>
        <button className="danger" disabled={busy} onClick={() => void resume('safety', { action: 'abort' })}>Abort mission</button></div>}
      {err && <div className="callout err small" role="alert">{err}</div>}
      <ul className="trace">{ev.data.events.filter((e) => e.kind !== 'enter').map((e) => (
        <li key={e.seq} className={`k-${e.kind}`}><div className="node">{e.node} <span className="faint">· {e.kind}{e.edge_to ? ` → ${e.edge_to}` : ''}</span></div>
          {e.summary && <div className="sum">{e.summary}</div>}</li>))}</ul>
    </div>
  );
}

function Body() {
  const [params, setParams] = useSearchParams();
  const approvals = useApi<Approval[]>('/v1/approvals?status=pending', { poll: 4000 });
  const runs = useApi<Run[]>('/v1/runs?limit=40', { poll: 4000 });
  const sel = params.get('run') ?? runs.data?.[0]?.id ?? null;
  const [outcome, setOutcome] = useState<{ tone: string; text: string } | null>(null);
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="row between"><h1>Agents & Approvals</h1><span className="small muted">Every approval is bound to one action, one payload hash, one approver and an expiry; it is re-checked on resume and at the tool.</span></div>
      <div className="grid g2">
        <div className="stack">
          <h2>Pending approvals</h2>
          {outcome && <div className={`callout ${outcome.tone}`} role="status">{outcome.text}</div>}
          {approvals.error ? <ErrorState error={approvals.error} /> : !approvals.data ? <Loading /> : approvals.data.length === 0 ? <Empty>Nothing waiting for a decision.</Empty> :
            approvals.data.map((a) => <ApprovalCard key={a.id} a={a} onDone={(o) => { setOutcome(o); approvals.reload(); runs.reload(); }} />)}
          <div className="card"><h3>Recent graph runs</h3>
            {!runs.data ? <Loading /> : <table className="data small"><thead><tr><th>Run</th><th>Graph</th><th>Status</th><th>At</th></tr></thead><tbody>
              {runs.data.map((r) => <tr key={r.id} className="click" onClick={() => setParams({ run: r.id })}><td className="mono">{short(r.id)}</td><td>{r.workflow}</td><td><Chip v={r.status} /></td><td>{ago(r.started_at)}</td></tr>)}</tbody></table>}
          </div>
        </div>
        <div className="card">{sel ? <Trace key={sel} runId={sel} /> : <Empty>No graph runs yet. <Link to="/glasses">Report a hazard</Link>.</Empty>}</div>
      </div>
    </div>
  );
}

export default function Agents() { return <RequireStaff>{() => <Body />}</RequireStaff>; }

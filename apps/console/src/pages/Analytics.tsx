import { useEffect, useState } from 'react';
import { RequireStaff } from '../components/RequireStaff';
import { ErrorState, Kpi, Loading, Prov } from '../components/ui';
import { api } from '../lib/api';

interface Metrics {
  window_days: number; provenance: string; reports: number; safety_events: number; median_minutes_to_triage: number | null;
  tickets_by_status: Array<{ status: string; n: number }>; missions: Array<{ status: string; n: number }>; verdicts: Array<{ verdict: string; n: number }>;
}

function Bars({ rows, k }: { rows: Array<Record<string, string | number>>; k: string }) {
  const max = Math.max(1, ...rows.map((r) => Number(r.n)));
  return <table className="data small"><tbody>{rows.map((r) => <tr key={String(r[k])}><td style={{ width: 170 }}>{String(r[k]).replace(/_/g, ' ')}</td>
    <td><div style={{ background: 'var(--blue)', height: 10, borderRadius: 3, width: `${(Number(r.n) / max) * 100}%`, minWidth: 2 }} aria-hidden="true" /></td><td style={{ width: 40 }}>{r.n}</td></tr>)}</tbody></table>;
}

function Body() {
  const [days, setDays] = useState(30);
  const [m, setM] = useState<Metrics | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api.post<{ result: Metrics }>('/v1/tools/export_aggregate_metrics', { days }).then((r) => setM(r.result)).catch((e) => setErr((e as Error).message)); }, [days]);
  if (err) return <ErrorState error={err} />;
  if (!m) return <Loading />;
  const v = Object.fromEntries(m.verdicts.map((x) => [x.verdict, x.n]));
  const verified = v.cleared ?? 0;
  const judged = m.verdicts.reduce((a, x) => a + x.n, 0);
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="row between"><h1>Analytics</h1><div className="row"><label className="field">Window<select value={days} onChange={(e) => setDays(Number(e.target.value))}>
        {[1, 7, 30, 90].map((d) => <option key={d} value={d}>{d} days</option>)}</select></label><Prov v="SYNTHETIC" /></div></div>
      <div className="callout info small">These are counts from this demo’s synthetic and simulated runs, with their denominators. They are pipeline measurements, not field performance or city statistics.</div>
      <div className="grid g4">
        <Kpi label="Reports received" value={m.reports} note={`last ${m.window_days} days`} />
        <Kpi label="Verified removal rate" value={judged ? `${Math.round((100 * verified) / judged)}%` : '—'} note={`${verified} cleared of ${judged} verified cells`} />
        <Kpi label="Safety stops / yields" value={m.safety_events} note="simulated controller events" />
        <Kpi label="Median time to triage" value={m.median_minutes_to_triage != null ? `${m.median_minutes_to_triage} min` : '—'} note="report → ticket (software time)" />
      </div>
      <div className="grid g3">
        <div className="card"><h3>Tickets by status</h3><Bars rows={m.tickets_by_status} k="status" /></div>
        <div className="card"><h3>Cleanup verdicts</h3><Bars rows={m.verdicts} k="verdict" /></div>
        <div className="card"><h3>Missions</h3><Bars rows={m.missions} k="status" /></div>
      </div>
    </div>
  );
}

export default function Analytics() { return <RequireStaff>{() => <Body />}</RequireStaff>; }

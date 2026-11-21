import type { ReportDraft, RiderReportAgent } from '@lanekeeper/glasses';
import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import { Chip } from './ui';

function thumb(d: ReportDraft): string | null {
  const s = d.snapshots[0];
  if (!s) return null;
  return s.imageB64 ? `data:image/jpeg;base64,${s.imageB64}` : api.url(`/v1/fixtures/${s.frameId}.png`);
}

function left(ms: number): string {
  if (ms <= 0) return 'expired';
  const m = Math.floor(ms / 60000), s = Math.floor((ms % 60000) / 1000);
  return `${m}:${String(s).padStart(2, '0')} left`;
}

/**
 * The rider's assistant: shows reports the agent drafted (auto snapshots + location + filled form), and lets the
 * rider approve or discard. Phone approval needs the rider to be stopped; nothing is sent without approval.
 */
export function AgentDrafts({ agent, speedMps, version, onChange, compact = false }: {
  agent: RiderReportAgent; speedMps: number; version: number; onChange: () => void; compact?: boolean;
}) {
  const [now, setNow] = useState(Date.now());
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, []);
  const all = agent.all();
  const pending = agent.pending();
  const recent = all.filter((d) => d.status !== 'pending').slice(-4).reverse();
  const stopped = agent.canPrompt(speedMps);
  void version;

  async function approve(d: ReportDraft) {
    setMsg(null);
    try {
      const r = await agent.approve(d.id, 'phone_tap', speedMps);
      setMsg(r.status === 'submitted' ? `Submitted${r.ticket ? ` as ${r.ticket}` : ''}. City staff review every report.`
        : `Approved. No connection, so it is queued on this device and will be sent automatically.`);
    } catch (e) { setMsg((e as Error).message); }
    onChange();
  }

  return (
    <section className="card stack" aria-labelledby="assistant-h">
      <div className="row between"><h3 id="assistant-h">Rider assistant</h3><Chip v={stopped ? 'stopped' : 'riding'} /></div>
      <p className="small muted" style={{ margin: 0 }}>When your glasses spot possible glass, the assistant saves snapshots and your location and fills in the
        report. You approve it when you’ve stopped; nothing is sent before that. Unapproved drafts are deleted after 15 minutes.</p>
      {pending.length === 0 && <p className="small muted">No drafts waiting.</p>}
      {pending.map((d) => <div key={d.id} className="row" style={{ alignItems: 'flex-start', gap: 12, borderTop: '1px solid var(--line)', paddingTop: 10 }}
        role="group" aria-label="Drafted report">
        {thumb(d) && <img src={thumb(d)!} alt="Snapshot captured by the assistant" width={compact ? 96 : 128} style={{ borderRadius: 6 }} />}
        <div className="stack" style={{ gap: 4, flex: 1 }}>
          <strong>Possible glass{d.preview?.place ? ` · ${d.preview.place}` : ''}</strong>
          <span className="small">{d.snapshots.length} snapshot(s) · detector {d.detector.score.toFixed(2)} over {d.detector.consecutive_frames} frames
            {d.location ? ` · location ±${Math.round(d.location.accuracy_m ?? 0)} m` : ' · no location'} · {left(d.expiresAt - now)}</span>
          {d.preview && <span className="small">{d.preview.advice}</span>}
          <div className="row">
            <button className="primary" disabled={!stopped} title={stopped ? '' : 'Stop first: approve on the phone only when stopped'}
              onClick={() => void approve(d)}>{stopped ? 'Approve & submit' : 'Stop to approve'}</button>
            <button onClick={() => { agent.decline(d.id); onChange(); setMsg('Discarded; snapshots deleted.'); }}>Discard</button>
          </div>
        </div>
      </div>)}
      {msg && <div className="callout info small" role="status">{msg}</div>}
      {recent.length > 0 && <table className="data small"><tbody>{recent.map((d) =>
        <tr key={d.id}><td><Chip v={d.status} /></td><td>{d.ticket ?? '—'}</td><td>{d.channel?.replace('_', ' ') ?? ''}</td></tr>)}</tbody></table>}
    </section>
  );
}

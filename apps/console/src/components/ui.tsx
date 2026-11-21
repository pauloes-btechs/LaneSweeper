import type { ReactNode } from 'react';

const STATUS_TONE: Record<string, string> = {
  new: 'c-blue', needs_evidence: 'c-amber', triaged: 'c-blue', assigned: 'c-violet', scheduled: 'c-violet', in_progress: 'c-violet',
  remediated_unverified: 'c-amber', verified_closed: 'c-green', reopened: 'c-red', escalated: 'c-red', dismissed: 'c-grey',
  pending: 'c-amber', submitted: 'c-green', queued: 'c-amber', declined: 'c-grey', stopped: 'c-green', riding: 'c-amber', approved: 'c-green', denied: 'c-red', revoked: 'c-red', expired: 'c-grey', consumed: 'c-green',
  dispatched: 'c-violet', running: 'c-violet', paused_safety: 'c-red', returning: 'c-amber', completed: 'c-green', aborted: 'c-red',
  docked: 'c-grey', idle: 'c-grey', charging: 'c-blue', en_route: 'c-violet', sweeping: 'c-violet', fault: 'c-red', offline: 'c-red',
  online: 'c-green', degraded: 'c-amber', lost: 'c-red', cleared: 'c-green', partially_cleared: 'c-amber', not_cleared: 'c-red',
  unverified: 'c-amber', waiting_approval: 'c-amber', waiting_evidence: 'c-amber', succeeded: 'c-green', failed: 'c-red',
  hazard_reported: 'c-amber', hazard_confirmed: 'c-red', cleaning: 'c-violet', cleared_unverified: 'c-amber', cleared_verified: 'c-green',
};

export function Chip({ v }: { v: string | null | undefined }) {
  if (!v) return <span className="faint">—</span>;
  return <span className={`chip ${STATUS_TONE[v] ?? 'c-grey'}`}>{v.replace(/_/g, ' ')}</span>;
}

export function Prio({ p }: { p: number }) {
  return <span className={`prio p${p}`} title={`priority ${p} (1 = most urgent)`}>P{p}</span>;
}

export function Prov({ v = 'SIMULATED' }: { v?: string | null }) {
  return <span className="prov" title="Data provenance">{v ?? 'SIMULATED'}</span>;
}

export function Loading({ rows = 3 }: { rows?: number }) {
  return <div className="stack" aria-busy="true" aria-label="Loading">{Array.from({ length: rows }, (_, i) => <div key={i} className="skeleton" />)}</div>;
}

export function ErrorState({ error, onRetry }: { error: string; onRetry?: () => void }) {
  return (
    <div className="callout err" role="alert">
      <strong>Couldn’t load this.</strong> {error}
      {onRetry && <> <button onClick={onRetry} style={{ marginLeft: 8 }}>Retry</button></>}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Kpi({ label, value, note }: { label: string; value: ReactNode; note?: string }) {
  return <div className="card kpi"><div className="l">{label}</div><div className="v">{value}</div>{note && <div className="note">{note}</div>}</div>;
}

export function ago(iso?: string | null): string {
  if (!iso) return '—';
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return `${Math.max(0, Math.round(s))}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return new Date(iso).toLocaleDateString();
}

export function short(id?: string | null, n = 8) { return id ? id.slice(0, n) : '—'; }

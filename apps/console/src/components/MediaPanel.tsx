import { useState } from 'react';
import { api } from '../lib/api';
import { useMedia, type MediaAsset } from '../lib/media';
import { Chip, ErrorState, Loading } from './ui';

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** City-admin panel: spend on generating the AI demo footage, stills and textures. Server enforces admin + caps. */
export function MediaPanel({ jurisdiction }: { jurisdiction?: string }) {
  const m = useMedia(8000);
  const [busy, setBusy] = useState<string | null>(null);
  const [log, setLog] = useState<string[]>([]);
  const [videoRes, setVideoRes] = useState<'4k' | '1080p'>('4k');
  const note = (s: string) => setLog((l) => [`${new Date().toLocaleTimeString()} ${s}`, ...l].slice(0, 30));

  async function one(a: MediaAsset): Promise<boolean> {
    setBusy(a.id);
    note(`${a.title}: generating ${a.kind === 'video' ? videoRes : a.resolution}…`);
    try {
      let r = await api.post<{ status: string; note?: string }>(`/v1/admin/media/${a.id}/generate`,
        { jurisdiction, resolution: a.kind === 'video' ? videoRes : undefined });
      for (let i = 0; r.status === 'generating' && i < 90; i++) {
        await sleep(10000);
        r = await api.post(`/v1/admin/media/${a.id}/poll`, { jurisdiction });
      }
      note(`${a.title}: ${r.status}${r.note ? ` (${r.note})` : ''}`);
      return r.status === 'ready';
    } catch (e) {
      note(`${a.title}: failed: ${(e as Error).message}`);
      return false;
    } finally { setBusy(null); m.reload(); }
  }

  async function all(kinds: MediaAsset['kind'][]) {
    for (const a of (m.data?.assets ?? []).filter((x) => kinds.includes(x.kind) && (x.status !== 'ready' || x.stale))) {
      await one(a);
    }
  }

  async function optimise() {
    for (const a of (m.data?.assets ?? []).filter((x) => x.status === 'ready' && !x.web_bytes)) {
      setBusy(a.id);
      try { await api.post(`/v1/admin/media/${a.id}/poll`, { jurisdiction }); note(`${a.title}: web version ready`); }
      catch (e) { note(`${a.title}: ${(e as Error).message}`); }
    }
    setBusy(null); m.reload();
  }

  const [reelUrl, setReelUrl] = useState<string | null>(null);
  async function buildReel() {
    setBusy('reel'); note('Demo video: building (about 1–3 min, no model calls)…');
    try {
      const r = await api.post<{ url: string; bytes: number; segments: number; build_s: number }>('/v1/admin/media/reel/build', { jurisdiction });
      setReelUrl(r.url); note(`Demo video: published (${(r.bytes / 1048576).toFixed(1)} MB, ${r.segments} parts, ${r.build_s}s) → ${r.url}`);
    } catch (e) { note(`Demo video: failed: ${(e as Error).message}`); }
    finally { setBusy(null); }
  }

  if (m.error) return <ErrorState error={m.error} />;
  if (!m.data) return <Loading />;
  const todo = m.data.assets.filter((a) => a.status !== 'ready' || a.stale);
  const est = todo.reduce((s, a) => s + a.est_usd, 0);
  return (
    <div className="card stack">
      <h3>AI demo media (4K)</h3>
      <p className="small muted">Photoreal rider POV clips ({m.data.models.video}) and 4K stills and 3D textures ({m.data.models.image}),
        generated with the server-side Google key. Every asset is labelled <strong>AI-GENERATED · SIMULATED</strong> and shows a fictional street.
        Rough cost for what is left: <strong>≈ ${est.toFixed(2)}</strong> (estimate; check your Google billing).</p>
      <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
        <label className="small">Video resolution{' '}
          <select value={videoRes} onChange={(e) => setVideoRes(e.target.value as '4k' | '1080p')} disabled={!!busy}>
            <option value="4k">4K (upscaled)</option><option value="1080p">1080p</option></select></label>
        <button className="btn primary" disabled={!!busy || !todo.length} onClick={() => void all(['image', 'texture', 'video'])}>Generate everything left ({todo.length})</button>
        <button className="btn" disabled={!!busy} onClick={() => void all(['image', 'texture'])}>Images + textures only</button>
        <button className="btn" disabled={!!busy} onClick={() => void all(['video'])}>Videos only</button>
        <button className="btn" disabled={!!busy} onClick={() => void optimise()}>Make web versions (2K stills, 1080p clips)</button>
      </div>
      <div className="row small" style={{ gap: 8 }}>
        <button className="btn" disabled={!!busy} onClick={() => void buildReel()}>Build public demo video</button>
        {(reelUrl ?? m.data.reel_url) && <a href={reelUrl ?? m.data.reel_url!} target="_blank" rel="noreferrer">Public demo video link</a>}
        <span className="muted">Joins the generated clips and stills with the recorded walkthrough; published to a public link.</span>
      </div>
      <table className="data small"><thead><tr><th>Asset</th><th>Kind</th><th>Truth</th><th>Status</th><th>Size</th><th /></tr></thead><tbody>
        {m.data.assets.map((a) => (
          <tr key={a.id}>
            <td>{a.url ? <a href={a.url} target="_blank" rel="noreferrer">{a.title}</a> : a.title}</td>
            <td>{a.kind} · {a.resolution}</td><td className="mono">{a.label ?? '—'}</td>
            <td><Chip v={a.status === 'ready' ? (a.stale ? 'stale' : 'succeeded') : a.status} />{a.error && <span className="small muted"> {a.error.slice(0, 80)}</span>}</td>
            <td>{a.bytes ? `${(a.bytes / 1048576).toFixed(1)} MB` : '—'}{a.web_bytes ? ` · web ${(a.web_bytes / 1048576).toFixed(1)} MB` : ''}</td>
            <td><button className="btn small" disabled={!!busy} onClick={() => void one(a)}>{busy === a.id ? '…' : a.status === 'ready' ? 'Redo' : 'Generate'}</button></td>
          </tr>))}
      </tbody></table>
      {log.length > 0 && <pre className="small mono" style={{ maxHeight: 160, overflow: 'auto' }}>{log.join('\n')}</pre>}
    </div>
  );
}

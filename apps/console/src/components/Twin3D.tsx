import { useEffect, useRef, useState } from 'react';
import { useMedia } from '../lib/media';
import type { Quality, TwinScene } from './twin/scene';

interface Props { state: string; progress: number; sweeping: boolean; label?: string }

/** High-fidelity LK-01 digital twin on a protected bike lane. Purely visual: animates replayed telemetry. */
export function Twin3D({ state, progress, sweeping, label = 'LK-01 digital twin (simulated)' }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const twin = useRef<TwinScene | null>(null);
  const [quality, setQuality] = useState<Quality>('hd');
  const [full, setFull] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const media = useMedia();
  const u = (id: string) => { const x = media.byId(id); return (quality === 'ultra' ? x?.url : x?.web_url ?? x?.url) ?? undefined; };
  const texKey = ['tex-asphalt', 'tex-bike-lane', 'tex-concrete', 'tex-facade', 'tex-sidewalk'].map((id) => (u(id) ? id : '')).join() + quality;

  useEffect(() => {
    const host = el.current;
    if (!host || media.loading) return;
    let disposed = false;
    void import('./twin/scene').then(({ TwinScene }) => {
      if (disposed) return;
      try {
        twin.current = new TwinScene(host, { asphalt: u('tex-asphalt'), lane: u('tex-bike-lane'), concrete: u('tex-concrete'),
          facade: u('tex-facade'), sidewalk: u('tex-sidewalk') }, quality);
      } catch { setErr('3D view needs WebGL 2.'); }
    });
    const ro = new ResizeObserver(() => twin.current?.resize());
    ro.observe(host);
    return () => { disposed = true; ro.disconnect(); twin.current?.dispose(); twin.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [texKey, media.loading]);

  useEffect(() => { twin.current?.setQuality(quality); }, [quality]);
  useEffect(() => { if (twin.current) twin.current.frame = { progress, sweeping, state }; });

  const aiTex = ['tex-asphalt', 'tex-bike-lane', 'tex-concrete', 'tex-facade', 'tex-sidewalk'].filter((id) => u(id)).length;
  return (
    <div style={full ? { position: 'fixed', inset: 0, zIndex: 50, background: '#000' } : { position: 'relative' }}>
      <div ref={el} role="img" aria-label={`${label}: ${state}`}
        style={{ width: '100%', height: full ? '100vh' : 420, borderRadius: full ? 0 : 8, overflow: 'hidden', background: '#11161c' }} />
      {err && <div className="small muted" style={{ position: 'absolute', top: 12, left: 12 }}>{err}</div>}
      <div className="row small" style={{ position: 'absolute', top: 8, right: 8, gap: 6 }}>
        <button className="btn small" onClick={() => setQuality(quality === 'hd' ? 'ultra' : 'hd')}>{quality === 'hd' ? 'HD' : 'Ultra (4K)'}</button>
        <button className="btn small" onClick={() => twin.current?.recenter()}>Follow</button>
        <button className="btn small" onClick={() => setFull(!full)}>{full ? 'Exit' : 'Full screen'}</button>
      </div>
      <div className="small" style={{ position: 'absolute', left: 10, bottom: 8, color: '#e8eef5', textShadow: '0 1px 2px #000' }}>
        SIMULATED · {label} · drag to orbit · {aiTex ? `${aiTex}/5 AI textures` : 'procedural textures'}
      </div>
    </div>
  );
}

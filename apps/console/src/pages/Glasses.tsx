import { CAUTION_TEXT, Debouncer, DeviceLink, OfflineQueue, SimulatedGlassesAdapter, WarningPolicy, detect, type Detection, type Frame, type KV } from '@lanekeeper/glasses';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { SimBanner } from '../components/Layout';
import { api, session } from '../lib/api';

// A simulated ride north on 1st Ave: mostly clear lane, with labelled hazards and confounders mixed in.
const RIDE = ['clear-lane-12', 'clear-lane-12', 'glass-1av-dawn-01', 'glass-1av-dawn-01', 'glass-1av-dawn-01', 'glass-1av-dawn-01', 'clear-lane-12',
  'confounder-water-05', 'confounder-water-05', 'clear-lane-12', 'confounder-markings-08', 'confounder-markings-08', 'clear-lane-12',
  'glass-1av-night-02', 'glass-1av-night-02', 'glass-1av-night-02', 'glass-1av-night-02', 'clear-lane-12', 'pothole-09', 'pothole-09'];
const CELL_FOR_STEP = (i: number) => 30 + i * 4;   // advance ~100 m per step along 1st Ave (synthetic)

const lsKV: KV = {
  get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* private mode: in-memory only for this page */ } },
};

interface LogRow { t: string; frame: string; d: Detection; stable: boolean; alert: string | null }

function useImages(ids: string[]) {
  const [imgs, setImgs] = useState<Record<string, HTMLImageElement>>({});
  useEffect(() => {
    for (const id of new Set(ids)) {
      const im = new Image();
      im.crossOrigin = 'anonymous';
      im.onload = () => setImgs((m) => ({ ...m, [id]: im }));
      im.src = api.url(`/v1/fixtures/${id}.png`);
    }
  }, [ids]);
  return imgs;
}

export default function Glasses() {
  const imgs = useImages(RIDE);
  const pov = useRef<HTMLCanvasElement>(null);
  const hudRef = useRef<HTMLCanvasElement>(null);
  const work = useMemo(() => document.createElement('canvas'), []);
  const [running, setRunning] = useState(false);
  const [step, setStep] = useState(0);
  const [kmh, setKmh] = useState(18);
  const [online, setOnline] = useState(true);
  const [voice, setVoice] = useState(true);
  const [consent, setConsent] = useState({ share_location: true, share_image: true, notify_status: true });
  const [hudText, setHudText] = useState<string | null>(null);
  const [log, setLog] = useState<LogRow[]>([]);
  const [last, setLast] = useState<Detection | null>(null);
  const [link, setLink] = useState<DeviceLink | null>(null);
  const [linkMsg, setLinkMsg] = useState<string>('Not paired');
  const [server, setServer] = useState<string[]>([]);
  const queue = useMemo(() => new OfflineQueue(lsKV, 'lk.glasses.queue'), []);
  const [queued, setQueued] = useState(queue.items().length);
  const deb = useMemo(() => new Debouncer(3, 0.55), []);
  const warn = useMemo(() => new WarningPolicy(8000), []);
  const best = useRef<{ frame: string; d: Detection; step: number } | null>(null);

  const adapter = useMemo(() => new SimulatedGlassesAdapter({
    frameProvider: () => null,
    speak: (t) => { if (voice && 'speechSynthesis' in window) { const u = new SpeechSynthesisUtterance(t); u.rate = 1.05; speechSynthesis.speak(u); } },
    onHud: (a) => setHudText(a?.text ?? null),
  }), [voice]);

  async function pair() {
    try {
      if (!session.get()) {
        const r = await api.publicPost<{ token: string; label: string }>('/v1/demo/token/rider', {});
        session.set(r.token, r.label);
      }
      const reg = await api.post<{ device_id: string; device_key: string }>('/v1/devices/register', { kind: 'glasses_sim', label: 'SIMULATED GLASSES (console)', capabilities: adapter.getCapabilities() });
      setLink(new DeviceLink({ baseUrl: api.url(''), deviceId: reg.device_id, deviceKey: reg.device_key }));
      setLinkMsg(`Paired device ${reg.device_id.slice(0, 8)} (key held in this tab only)`);
    } catch (e) { setLinkMsg(`Pairing failed: ${(e as Error).message}`); }
  }

  // one tick = one frame through the on-device pipeline
  useEffect(() => {
    if (!running) return;
    const id = setInterval(() => setStep((s) => (s + 1) % RIDE.length), 700);
    return () => clearInterval(id);
  }, [running]);

  useEffect(() => {
    const fid = RIDE[step]!;
    const im = imgs[fid];
    const c = pov.current;
    if (!im || !c) return;
    const ctx = c.getContext('2d')!;
    const zoom = 1 + (running ? (Date.now() % 700) / 7000 : 0);
    ctx.drawImage(im, (640 - 640 * zoom) / 2, (480 - 480 * zoom) / 2, 640 * zoom, 480 * zoom);
    work.width = 640; work.height = 480;
    const w = work.getContext('2d', { willReadFrequently: true })!;
    w.drawImage(im, 0, 0, 640, 480);
    const data = w.getImageData(0, 0, 640, 480);
    const frame: Frame = { width: 640, height: 480, data: data.data, capturedAt: Date.now(), source: fid };
    const d = detect(frame);
    const db = deb.push(d.score);
    const speed = kmh / 3.6;
    const decision = warn.decide(db.stable, speed, Date.now());
    if (decision.alert) { void adapter.showAlert(decision.alert); void adapter.playAudio(decision.speak!); }
    if (d.label === 'possible_glass' && (!best.current || d.score > best.current.d.score)) best.current = { frame: fid, d, step };
    setLast(d);
    setLog((l) => [{ t: new Date().toLocaleTimeString(), frame: fid, d, stable: db.stable, alert: decision.alert?.text ?? null }, ...l].slice(0, 40));
    // HUD layer: black = see-through on waveguide glasses; one glyph, one line
    const h = hudRef.current?.getContext('2d');
    if (h) {
      h.clearRect(0, 0, 640, 480);
      if (d.box && d.label === 'possible_glass') { h.strokeStyle = '#ffd400'; h.lineWidth = 3; h.strokeRect(d.box.x, d.box.y, d.box.w, d.box.h); }
    }
  }, [step, imgs]); // eslint-disable-line react-hooks/exhaustive-deps

  async function report() {
    const b = best.current;
    if (!b) { setServer((s) => [`No candidate captured yet.`, ...s]); return; }
    const cell = CELL_FOR_STEP(b.step);
    const lanes = await api.publicGet<{ features: Array<{ id: string; geometry: { coordinates: number[][] } }> }>('/v1/public/lanes?jurisdiction=nyc');
    const f = lanes.features.find((x) => x.id === `MN-1AV-${String(cell).padStart(4, '0')}`) ?? lanes.features[cell];
    const c = f!.geometry.coordinates[0]!;
    const event = {
      idempotency_key: `glasses-${crypto.randomUUID()}`, source_kind: 'glasses_sim', fixture_id: consent.share_image ? b.frame : undefined,
      hazard_claimed: 'glass', note: 'Reported hands-free from SIMULATED GLASSES after stopping',
      location: consent.share_location ? { lat: c[1], lng: c[0], accuracy_m: 10, source: 'phone_gps', speed_mps: 0 } : undefined,
      consent: { policy_version: 'rider-2026.11', share_image: consent.share_image, share_location: consent.share_location, notify_status: consent.notify_status },
      local_detector: { label: b.d.label, score: b.d.score, consecutive_frames: 3, model: b.d.model, warning_issued: true },
    };
    if (!link || !online) {
      queue.enqueue(event); setQueued(queue.items().length);
      setServer((s) => [`Link ${link ? 'offline' : 'not paired'}: report queued on device (${queue.items().length} waiting). The warning already happened locally.`, ...s]);
      return;
    }
    try {
      const r = await link.postEvent(event);
      setServer((s) => [`Server: ${String(r.outcome)} · ticket ${String(r.ticket_number ?? '(needs review)')} · route ${String(r.route ?? '—')}`, ...s]);
    } catch (e) { queue.enqueue(event); setQueued(queue.items().length); setServer((s) => [`Send failed (${(e as Error).message}); queued.`, ...s]); }
  }

  async function flush() {
    if (!link || !online) return;
    const r = await queue.flush(async (body) => { try { await link.postEvent(body); return true; } catch { return false; } });
    setQueued(queue.items().length);
    setServer((s) => [`Flushed queue: ${r.sent} sent, ${r.kept} kept`, ...s]);
  }

  useEffect(() => { if (online) void flush(); }, [online]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      <SimBanner />
      <main className="main" style={{ maxWidth: 1240, margin: '0 auto' }}>
        <div className="row between"><h1>Smart-glasses rider simulator</h1><div className="row small"><Link to="/rider">Rider app</Link><Link to="/console">Console →</Link></div></div>
        <div className="callout warn small" style={{ marginBottom: 12 }}><strong>SIMULATED GLASSES.</strong> Frames are labelled synthetic fixtures. Detection runs here on the
          pixels (no cloud), so the warning works offline. Distance to the hazard is never claimed: there's no calibrated depth or IMU. The real M6 glasses’ camera stream isn’t available in the SDK kit (see the SDK audit).</div>
        <div className="grid g-side">
          <div className="card stack">
            <div style={{ position: 'relative', width: '100%', maxWidth: 640, aspectRatio: '4 / 3', background: '#000', borderRadius: 10, overflow: 'hidden' }}>
              <canvas ref={pov} width={640} height={480} style={{ width: '100%', height: '100%' }} aria-label="Rider point of view (synthetic frames)" />
              <canvas ref={hudRef} width={640} height={480} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none' }} aria-hidden="true" />
              <div style={{ position: 'absolute', top: 10, left: 12, font: '600 12px var(--mono)', color: '#ffd400', letterSpacing: '.06em' }}>SIMULATED GLASSES · {kmh} km/h · {online ? 'LINK OK' : 'OFFLINE'}</div>
              {hudText && <div role="alert" style={{ position: 'absolute', left: '50%', bottom: 26, transform: 'translateX(-50%)', background: 'rgba(0,0,0,.78)', color: '#ffd400',
                font: '600 20px "IBM Plex Sans"', padding: '8px 16px', borderRadius: 8, border: '2px solid #ffd400' }}>⚠ {hudText}</div>}
            </div>
            <div className="row">
              <button className="primary" onClick={() => setRunning((r) => !r)}>{running ? 'Pause ride' : 'Start ride'}</button>
              <button onClick={() => setStep((s) => (s + 1) % RIDE.length)}>Step frame</button>
              <label className="field small">Speed<input type="range" min={0} max={30} value={kmh} onChange={(e) => setKmh(Number(e.target.value))} aria-label="Speed km/h" /></label>
              <label className="row small"><input type="checkbox" checked={online} onChange={(e) => setOnline(e.target.checked)} />phone link online</label>
              <label className="row small"><input type="checkbox" checked={voice} onChange={(e) => setVoice(e.target.checked)} />spoken warning</label>
            </div>
            <div className="row">
              <button onClick={() => void report()} disabled={kmh > 3} title={kmh > 3 ? 'Stop first: reporting is only offered when stopped' : ''}>Report hazard (when stopped)</button>
              {!link && <button onClick={() => void pair()}>Pair simulated glasses</button>}
              <span className="small muted">{linkMsg} · queued: {queued}</span>
            </div>
            <div className="row small">
              {(['share_location', 'share_image', 'notify_status'] as const).map((k) => <label key={k} className="row"><input type="checkbox" checked={consent[k]} onChange={(e) => setConsent({ ...consent, [k]: e.target.checked })} />{k.replace(/_/g, ' ')}</label>)}
            </div>
            {server.length > 0 && <div className="callout info small" role="status">{server.map((s, i) => <div key={i}>{s}</div>)}</div>}
          </div>
          <div className="stack">
            <div className="card"><h3>On-device detector</h3>
              {last ? <table className="data small"><tbody>
                <tr><td>Frame</td><td className="mono">{RIDE[step]}</td></tr>
                <tr><td>Label</td><td><strong>{last.label}</strong> (score {last.score}, a heuristic, not a probability)</td></tr>
                <tr><td>Glints / streaks / blobs</td><td>{last.specks} / {last.streaks} / {last.blobs}</td></tr>
                <tr><td>Compute</td><td>{last.ms} ms in this browser · {last.model}</td></tr>
              </tbody></table> : <p className="muted small">Start the ride.</p>}
              <p className="small muted">Warning rule: ≥3 consecutive frames ≥ 0.55 → “{CAUTION_TEXT}”, 8 s cooldown. Uncertain frames → no alert while moving. There is never a “lane clear” message.</p>
            </div>
            <div className="card"><h3>Data path</h3>
              <ol className="small" style={{ margin: 0, paddingLeft: 18 }}>
                <li>Glasses/phone: local detector → debounce → audio + one HUD glyph (no network needed)</li>
                <li>Rider stops → report: minimal frame + approximate location, only with consent</li>
                <li>Signed device link → LaneKeeper glasses graph → de-identify → ticket graph</li>
                <li>Server: Gemini Decision 1 + Robotics ER verification (advisory) → ticket → city operator</li>
                <li>Operator approval → simulated fleet → verified cleanup → status back to the HUD (shown only when stopped)</li>
              </ol></div>
            <div className="card"><h3>Frame log</h3>
              <div className="table-wrap" style={{ maxHeight: 260 }}><table className="data small"><thead><tr><th>Time</th><th>Frame</th><th>Score</th><th>Stable</th><th>Alert</th></tr></thead>
                <tbody>{log.map((r, i) => <tr key={i}><td>{r.t}</td><td className="mono">{r.frame}</td><td>{r.d.score}</td><td>{r.stable ? 'yes' : 'no'}</td><td>{r.alert ? '⚠' : ''}</td></tr>)}</tbody></table></div>
            </div>
          </div>
        </div>
      </main>
    </>
  );
}

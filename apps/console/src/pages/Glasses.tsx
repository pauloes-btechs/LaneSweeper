import { CAUTION_TEXT, Debouncer, DeviceLink, RiderReportAgent, SAVED_PROMPT, SimulatedGlassesAdapter, SubmitRejected, WarningPolicy, detect, type Detection, type DraftLocation, type DraftPreview, type Frame } from '@lanekeeper/glasses';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { AgentDrafts } from '../components/AgentDrafts';
import { SimBanner } from '../components/Layout';
import { useMedia } from '../lib/media';
import { api, session } from '../lib/api';
import { DRAFTS_KEY, lsKV } from '../lib/storage';

// A simulated ride north on 1st Ave: mostly clear lane, with labelled hazards and confounders mixed in.
const RIDE = ['clear-lane-12', 'clear-lane-12', 'glass-1av-dawn-01', 'glass-1av-dawn-01', 'glass-1av-dawn-01', 'glass-1av-dawn-01', 'clear-lane-12',
  'confounder-water-05', 'confounder-water-05', 'clear-lane-12', 'confounder-markings-08', 'confounder-markings-08', 'clear-lane-12',
  'glass-1av-night-02', 'glass-1av-night-02', 'glass-1av-night-02', 'glass-1av-night-02', 'clear-lane-12', 'pothole-09', 'pothole-09'];
const CELL_FOR_STEP = (i: number) => 30 + i * 4;   // advance ~100 m per step along 1st Ave (synthetic)


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

// Footage sources: labelled synthetic fixtures (default), AI-generated POV clips (ground-truth labelled), or a local clip.
const LOCAL_CLIP = '/media/shot2-glasses-pov.mp4';
function useClip(url: string | null) {
  const [clip, setClip] = useState<HTMLVideoElement | null>(null);
  useEffect(() => {
    setClip(null);
    if (!url) return;
    const v = document.createElement('video');
    v.muted = true; v.loop = true; v.playsInline = true; v.preload = 'auto';
    v.crossOrigin = 'anonymous';           // signed storage links send CORS headers; needed to read pixels
    v.onloadeddata = () => setClip(v);
    v.onerror = () => setClip(null);
    v.src = url;
    return () => { v.pause(); v.removeAttribute('src'); v.load(); };
  }, [url]);
  return clip;
}

export default function Glasses() {
  const imgs = useImages(RIDE);
  const media = useMedia();
  const povClips = (media.data?.assets ?? []).filter((a) => a.use_case === 'glasses_pov' && a.status === 'ready' && (a.web_url || a.url));
  const [source, setSource] = useState<string>('synthetic');
  const srcAsset = povClips.find((a) => a.id === source);
  const clipUrl = source === 'local' ? LOCAL_CLIP : srcAsset ? (srcAsset.web_url ?? srcAsset.url ?? null) : null;
  const clip = useClip(clipUrl);
  const useClipMode = source !== 'synthetic' && clip !== null;
  const truth = srcAsset?.label ?? null;
  const [tally, setTally] = useState({ frames: 0, flagged: 0 });
  useEffect(() => setTally({ frames: 0, flagged: 0 }), [source]);
  const videoHost = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const host = videoHost.current;
    if (!host || !clip) return;
    clip.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;object-fit:fill';
    host.appendChild(clip);
    return () => { clip.remove(); };
  }, [clip]);
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
  const deb = useMemo(() => new Debouncer(3, 0.55), []);
  const warn = useMemo(() => new WarningPolicy(8000), []);

  // The rider's assistant: drafts reports from stable detections; the rider approves before anything is sent.
  const linkRef = useRef<DeviceLink | null>(null); linkRef.current = link;
  const onlineRef = useRef(online); onlineRef.current = online;
  const consentRef = useRef(consent); consentRef.current = consent;
  const [draftsVer, setDraftsVer] = useState(0);
  const bump = () => setDraftsVer((v) => v + 1);
  const agent = useMemo(() => new RiderReportAgent({
    sourceKind: 'glasses_sim', kv: lsKV, storageKey: DRAFTS_KEY,
    prefs: () => ({ shareImage: consentRef.current.share_image, shareLocation: consentRef.current.share_location,
                    notifyStatus: consentRef.current.notify_status, policyVersion: 'rider-2026.11' }),
    submit: async (body) => {
      const l = linkRef.current;
      if (!l || !onlineRef.current) throw new Error(l ? 'phone link offline' : 'glasses not paired');
      let r: Record<string, unknown>;
      try { r = await l.postEvent(body); } catch (e) {
        const m = /rejected: (\d{3})/.exec((e as Error).message);
        if (m && m[1]!.startsWith('4') && !['408', '429'].includes(m[1]!)) throw new SubmitRejected((e as Error).message);
        throw e;                                    // network / server error: the agent queues it for retry
      }
      if (r.outcome === 'rejected') throw new SubmitRejected('the server did not accept this report');
      return { ticket_number: (r.ticket_number as string | null | undefined) ?? null };
    },
    preview: async (q) => {
      const l = linkRef.current;
      if (!l || !onlineRef.current) throw new Error('no link');
      return (await l.previewDraft(q)) as unknown as DraftPreview;
    },
  }), []);
  const [lanes, setLanes] = useState<Record<string, number[]>>({});
  useEffect(() => {
    void api.publicGet<{ features: Array<{ id: string; geometry: { coordinates: number[][] } }> }>('/v1/public/lanes?jurisdiction=nyc')
      .then((fc) => setLanes(Object.fromEntries(fc.features.map((f) => [f.id, f.geometry.coordinates[0]!]))))
      .catch(() => setLanes({}));
  }, []);
  /** Simulated phone GPS fix for the current ride position (the glasses have no GPS of their own). */
  function locFor(i: number, speedMps: number): DraftLocation | null {
    const c = lanes[`MN-1AV-${String(CELL_FOR_STEP(i)).padStart(4, '0')}`];
    return c ? { lat: c[1]!, lng: c[0]!, accuracy_m: 8, heading_deg: 29, speed_mps: Math.round(speedMps * 10) / 10, source: 'phone_gps' } : null;
  }
  /** A small JPEG of a real (clip) frame for the draft; fixtures are referenced by id instead. */
  function snapshotB64(src: HTMLCanvasElement): string {
    const t = document.createElement('canvas'); t.width = 320; t.height = 240;
    t.getContext('2d')!.drawImage(src, 0, 0, 320, 240);
    return t.toDataURL('image/jpeg', 0.6).split(',')[1]!;
  }

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
    const clipMode = useClipMode;
    const fid = clipMode ? `${source === 'local' ? 'local-clip' : source}@${clip!.currentTime.toFixed(1)}s` : RIDE[step]!;
    const im: CanvasImageSource | undefined = clipMode ? clip : imgs[RIDE[step]!];
    const c = pov.current;
    if (!im || !c) return;
    const W = 640, H = clipMode ? 360 : 480;
    if (!clipMode) {
      const ctx = c.getContext('2d')!;
      const zoom = 1 + (running ? (Date.now() % 700) / 7000 : 0);
      ctx.drawImage(im, (640 - 640 * zoom) / 2, (480 - 480 * zoom) / 2, 640 * zoom, 480 * zoom);
    }
    work.width = W; work.height = H;
    const w = work.getContext('2d', { willReadFrequently: true })!;
    w.drawImage(im, 0, 0, W, H);
    let data: ImageData;
    try { data = w.getImageData(0, 0, W, H); } catch { return; }   // cross-origin frame without CORS: skip
    const frame: Frame = { width: W, height: H, data: data.data, capturedAt: Date.now(), source: fid };
    const d = detect(frame);
    const db = deb.push(d.score);
    const speed = kmh / 3.6;
    const decision = warn.decide(db.stable, speed, Date.now());
    if (decision.alert) { void adapter.showAlert(decision.alert); void adapter.playAudio(decision.speak!); }
    const cap = agent.observe(d, db.stable, db.consecutive,
      { id: clipMode ? `clip-${Date.now()}` : fid, imageB64: clipMode && db.stable && d.label === 'possible_glass' ? snapshotB64(work) : undefined },
      locFor(step, speed), Boolean(decision.alert));
    if (cap?.created) {
      void adapter.playAudio('Report saved. Confirm when stopped.');
      setServer((sv) => [`Assistant: ${SAVED_PROMPT} (${cap.draft.location ? 'location saved' : 'no location'}, snapshots captured automatically)`, ...sv]);
      void agent.refreshPreview(cap.draft.id).then(bump);
    }
    if (cap) bump();
    setLast(d);
    if (clipMode) setTally((t) => ({ frames: t.frames + 1, flagged: t.flagged + (d.label === 'possible_glass' ? 1 : 0) }));
    setLog((l) => [{ t: new Date().toLocaleTimeString(), frame: fid, d, stable: db.stable, alert: decision.alert?.text ?? null }, ...l].slice(0, 40));
    // HUD layer: black = see-through on waveguide glasses; one glyph, one line
    const h = hudRef.current?.getContext('2d');
    if (h) {
      if (hudRef.current!.height !== H) hudRef.current!.height = H;
      h.clearRect(0, 0, W, H);
      if (d.box && d.label === 'possible_glass') { h.strokeStyle = '#ffd400'; h.lineWidth = 3; h.strokeRect(d.box.x, d.box.y, d.box.w, d.box.h); }
    }
  }, [step, imgs, useClipMode]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { if (!clip) return; if (useClipMode && running) void clip.play(); else clip.pause(); }, [clip, useClipMode, running]);

  /** Simulated hardware button on the glasses: approves the newest draft, hands-free (allowed while riding). */
  async function glassesButton() {
    try {
      const r = await agent.approveLatestByButton(kmh / 3.6);
      setServer((sv) => [!r ? 'Glasses button: no draft waiting.' : r.status === 'submitted'
        ? `Glasses button: you approved the draft; submitted as ${r.ticket ?? '(pending review)'}.`
        : `Glasses button: you approved the draft; queued on the device (${r.error ?? 'offline'}).`, ...sv]);
    } catch (e) { setServer((sv) => [`Glasses button: ${(e as Error).message}`, ...sv]); }
    bump();
  }

  useEffect(() => {
    if (!online || !link) return;
    void agent.flushQueued().then((r) => { if (r.sent) setServer((sv) => [`Sent ${r.sent} approved report(s) that were queued offline.`, ...sv]); bump(); });
  }, [online, link]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      <SimBanner />
      <main className="main" style={{ maxWidth: 1240, margin: '0 auto' }}>
        <div className="row between"><h1>Smart-glasses rider simulator</h1><div className="row small"><Link to="/rider">Rider app</Link><Link to="/console">Console →</Link></div></div>
        <div className="callout warn small" style={{ marginBottom: 12 }}><strong>SIMULATED GLASSES.</strong> Frames are labelled synthetic fixtures. Detection runs here on the
          pixels (no cloud), so the warning works offline. Distance to the hazard is never claimed: there's no calibrated depth or IMU. The M6 live camera stream is documented, but its phone-side receiver isn’t in the SDK kit, so it can’t be used yet (see the SDK audit).</div>
        <div className="grid g-side">
          <div className="card stack">
            <div ref={videoHost} style={{ position: 'relative', width: '100%', maxWidth: useClipMode ? 960 : 640, aspectRatio: useClipMode ? '16 / 9' : '4 / 3', background: '#000', borderRadius: 10, overflow: 'hidden' }}>
              <canvas ref={pov} width={640} height={480} style={{ width: '100%', height: '100%', display: useClipMode ? 'none' : 'block' }} aria-label="Rider point of view (synthetic frames)" />
              <canvas ref={hudRef} width={640} height={480} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none', zIndex: 2 }} aria-hidden="true" />
              {useClipMode && source !== 'local' && <div style={{ position: 'absolute', top: 8, right: 10, zIndex: 3, font: '600 11px var(--mono)', color: '#fff', background: 'rgba(0,0,0,.55)', padding: '2px 6px', borderRadius: 4 }}>AI-GENERATED · SIMULATED · fictional street</div>}
              <div style={{ position: 'absolute', zIndex: 3, top: 30, left: 12, font: '600 12px var(--mono)', color: '#ffd400', letterSpacing: '.06em' }}>SIMULATED GLASSES · {kmh} km/h · {online ? 'LINK OK' : 'OFFLINE'}</div>
              {hudText && <div role="alert" style={{ position: 'absolute', zIndex: 3, left: '50%', bottom: 26, transform: 'translateX(-50%)', background: 'rgba(0,0,0,.78)', color: '#ffd400',
                font: '600 20px "IBM Plex Sans"', padding: '8px 16px', borderRadius: 8, border: '2px solid #ffd400' }}>⚠ {hudText}</div>}
            </div>
            <div className="row">
              <button className="primary" onClick={() => setRunning((r) => !r)}>{running ? 'Pause ride' : 'Start ride'}</button>
              <button onClick={() => setStep((s) => (s + 1) % RIDE.length)}>Step frame</button>
              <label className="field small">Speed<input type="range" min={0} max={30} value={kmh} onChange={(e) => setKmh(Number(e.target.value))} aria-label="Speed km/h" /></label>
              <label className="row small"><input type="checkbox" checked={online} onChange={(e) => setOnline(e.target.checked)} />phone link online</label>
              <label className="row small"><input type="checkbox" checked={voice} onChange={(e) => setVoice(e.target.checked)} />spoken warning</label>
              <label className="field small">Footage
                <select aria-label="Footage source" value={source} onChange={(e) => setSource(e.target.value)}>
                  <option value="synthetic">Synthetic frames (labelled fixtures)</option>
                  {povClips.map((a) => <option key={a.id} value={a.id}>AI 4K: {a.title}</option>)}
                  <option value="local">Local clip (/media)</option>
                </select></label>
              {source !== 'synthetic' && !clip && <span className="small muted">loading footage…</span>}
            </div>
            <div className="row">
              <button onClick={() => void glassesButton()} title="One press on the glasses approves the newest draft; no reading needed">Glasses button: approve latest draft</button>
              {!link && <button onClick={() => void pair()}>Pair simulated glasses</button>}
              <span className="small muted">{linkMsg} · approved & queued: {agent.queue.items().length}</span>
            </div>
            <div className="row small">
              {(['share_location', 'share_image', 'notify_status'] as const).map((k) => <label key={k} className="row"><input type="checkbox" checked={consent[k]} onChange={(e) => setConsent({ ...consent, [k]: e.target.checked })} />{k.replace(/_/g, ' ')}</label>)}
            </div>
            {server.length > 0 && <div className="callout info small" role="status">{server.map((s, i) => <div key={i}>{s}</div>)}</div>}
          </div>
          <div className="stack">
            <AgentDrafts agent={agent} speedMps={kmh / 3.6} version={draftsVer} onChange={bump} compact />
            <div className="card"><h3>On-device detector</h3>
              {last ? <table className="data small"><tbody>
                <tr><td>Frame</td><td className="mono">{log[0]?.frame ?? RIDE[step]}</td></tr>
                <tr><td>Label</td><td><strong>{last.label}</strong> (score {last.score}, a heuristic, not a probability)</td></tr>
                <tr><td>Glints / streaks / blobs</td><td>{last.specks} / {last.streaks} / {last.blobs}</td></tr>
                <tr><td>Compute</td><td>{last.ms} ms in this browser · {last.model}</td></tr>
                {truth && <tr><td>Ground truth</td><td><strong>{truth}</strong> · flagged {tally.flagged}/{tally.frames} frames
                  {tally.frames > 5 && <> ({truth === 'glass' ? 'detection' : 'false-alarm'} rate {Math.round((100 * tally.flagged) / tally.frames)}%)</>}</td></tr>}
              </tbody></table> : <p className="muted small">Start the ride.</p>}
              <p className="small muted">Warning rule: ≥3 consecutive frames ≥ 0.55 → “{CAUTION_TEXT}”, 8 s cooldown. Uncertain frames → no alert while moving. There is never a “lane clear” message.</p>
            </div>
            <div className="card"><h3>Data path</h3>
              <ol className="small" style={{ margin: 0, paddingLeft: 18 }}>
                <li>Glasses/phone: local detector → debounce → audio + one HUD glyph (no network needed)</li>
                <li>Rider assistant (on the device): auto-captures the best snapshots + GPS fix and fills in the report; asks the server where it lands and whether it’s already reported</li>
                <li>Rider approves: one glasses-button press, or a tap once stopped. Nothing is sent before that; drafts expire in 15 min</li>
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

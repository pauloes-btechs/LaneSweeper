import { useState } from 'react';
import { Link } from 'react-router-dom';
import { SimBanner } from '../components/Layout';
import { LaneMap } from '../components/LaneMap';
import { Chip, ago } from '../components/ui';
import { api, session } from '../lib/api';
import { useApi } from '../lib/useApi';

interface Fixture { id: string; description: string; url: string }
interface Result { report_id: string; status: string; ticket_number: string | null; needs_more_evidence: boolean; errors: string[] }

// Demo spots on the 1st Ave pilot lane (approximate cell centroids), for riders without GPS in the demo.
const SPOTS = [
  { label: '1st Ave & E 23rd St', lat: 40.737435, lng: -73.978143 }, { label: '1st Ave & E 34th St', lat: 40.744371, lng: -73.973086 },
  { label: '1st Ave & E 42nd St', lat: 40.749348, lng: -73.969204 }, { label: '1st Ave & E 57th St', lat: 40.758390, lng: -73.962119 },
];

/** Downscale and re-encode on the phone: smaller upload, and the fresh JPEG carries no EXIF/GPS metadata. */
async function shrink(file: File): Promise<string> {
  const bmp = await createImageBitmap(file);
  const scale = Math.min(1, 1280 / Math.max(bmp.width, bmp.height));
  const c = document.createElement('canvas');
  c.width = Math.round(bmp.width * scale); c.height = Math.round(bmp.height * scale);
  c.getContext('2d')!.drawImage(bmp, 0, 0, c.width, c.height);
  return c.toDataURL('image/jpeg', 0.82).split(',')[1]!;
}

export default function Rider() {
  const fixtures = useApi<Fixture[]>('/v1/fixtures', { isPublic: true });
  const hazards = useApi<Array<{ cell_id: string; condition: string }>>('/v1/public/hazards?jurisdiction=nyc', { isPublic: true, poll: 8000 });
  const mine = useApi<Array<{ id: string; received_at: string; hazard_claimed: string; ticket: string | null; ticket_status: string | null }>>(session.get() ? '/v1/reports/mine' : null);
  const [hazard, setHazard] = useState('glass');
  const [fixture, setFixture] = useState('glass-1av-dawn-01');
  const [photo, setPhoto] = useState<string | null>(null);
  const [loc, setLoc] = useState<{ lat: number; lng: number; accuracy_m: number | null; source: string } | null>(null);
  const [note, setNote] = useState('');
  const [consent, setConsent] = useState({ share_image: true, share_location: true, notify_status: false, allow_learning: false });
  const [stopped, setStopped] = useState(false);
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<Result | null>(null);
  const [err, setErr] = useState<string | null>(null);

  function locate() {
    setErr(null);
    if (!navigator.geolocation) { setErr('This device has no location service; pick the nearest spot instead.'); return; }
    navigator.geolocation.getCurrentPosition((p) => setLoc({ lat: p.coords.latitude, lng: p.coords.longitude, accuracy_m: Math.round(p.coords.accuracy), source: 'phone_gps' }),
      () => setErr('Location permission denied. Pick the nearest spot instead.'), { enableHighAccuracy: true, timeout: 10000 });
  }

  async function submit() {
    setBusy(true); setErr(null); setRes(null);
    try {
      const body: Record<string, unknown> = {
        idempotency_key: `rider-${crypto.randomUUID()}`, source_kind: session.get() ? 'rider_app' : 'rider_guest', hazard_claimed: hazard,
        note: note || undefined, consent: { policy_version: 'rider-2026.11', ...consent },
        location: consent.share_location && loc ? { lat: loc.lat, lng: loc.lng, accuracy_m: loc.accuracy_m, source: loc.source } : undefined,
      };
      if (consent.share_image) {
        if (photo) { body.image_b64 = photo; body.image_mime = 'image/jpeg'; } else body.fixture_id = fixture;
      }
      const r = await (session.get() ? api.post<Result>('/v1/reports', body) : api.publicPost<Result>('/v1/reports', body));
      setRes(r); hazards.reload(); mine.reload();
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }

  const cond = Object.fromEntries((hazards.data ?? []).map((h) => [h.cell_id, h.condition]));
  return (
    <>
      <SimBanner />
      <main className="main" style={{ maxWidth: 1100, margin: '0 auto' }}>
        <div className="row between"><h1>Report a bike-lane hazard</h1><Link to="/console" className="small">City staff console →</Link></div>
        <div className="grid g2">
          <section className="card stack" aria-labelledby="report-h">
            <h2 id="report-h">New report</h2>
            <label className="row"><input type="checkbox" checked={stopped} onChange={(e) => setStopped(e.target.checked)} />
              <span><strong>I have stopped somewhere safe.</strong> Please don’t use your phone while riding.</span></label>
            <fieldset disabled={!stopped} className="stack" style={{ border: 0, padding: 0, margin: 0 }}>
              <label className="field">What did you see?<select value={hazard} onChange={(e) => setHazard(e.target.value)}>
                {['glass', 'debris', 'litter', 'pothole', 'water', 'branch', 'obstruction', 'markings', 'construction', 'other'].map((h) => <option key={h}>{h}</option>)}</select></label>
              <label className="field">Photo (optional, after stopping)<input type="file" accept="image/*" capture="environment" onChange={async (e) => {
                const f = e.target.files?.[0]; if (f) setPhoto(await shrink(f)); }} /></label>
              {!photo && <label className="field">…or use a labelled sample image (demo)<select value={fixture} onChange={(e) => setFixture(e.target.value)}>
                {fixtures.data?.map((f) => <option key={f.id} value={f.id}>{f.id}</option>)}</select></label>}
              {!photo && fixtures.data && <img src={api.url(`/v1/fixtures/${fixture}.png`)} alt={`Sample: ${fixtures.data.find((f) => f.id === fixture)?.description}`} style={{ width: '100%', borderRadius: 8 }} />}
              <div className="stack" style={{ gap: 6 }}><span className="small muted">Where?</span>
                <div className="row"><button type="button" onClick={locate}>Use my location</button>
                  <select aria-label="Nearest spot" value="" onChange={(e) => { const s = SPOTS[Number(e.target.value)]; if (s) setLoc({ lat: s.lat, lng: s.lng, accuracy_m: 25, source: 'pin' }); }}>
                    <option value="">or pick the nearest spot…</option>{SPOTS.map((s, i) => <option key={s.label} value={i}>{s.label}</option>)}</select></div>
                {loc && <span className="small">Location set{loc.accuracy_m != null && ` (±${loc.accuracy_m} m)`}. Only the 25 m lane cell is shown publicly.</span>}</div>
              <label className="field">Note (optional)<textarea maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. glass across the whole lane near the bus stop" /></label>
              <fieldset className="stack" style={{ border: '1px solid var(--line)', borderRadius: 8, padding: 10 }}><legend className="small">Consent</legend>
                {([['share_location', 'Share this location with the city (needed to place the report)'], ['share_image', 'Share the photo with city staff (kept privately, deleted after 30 days)'],
                   ['notify_status', 'Notify me about this report’s status'], ['allow_learning', 'Allow de-identified use to evaluate hazard detection']] as const).map(([k, l]) =>
                  <label key={k} className="row small"><input type="checkbox" checked={consent[k]} onChange={(e) => setConsent({ ...consent, [k]: e.target.checked })} />{l}</label>)}
              </fieldset>
              <button className="primary" disabled={busy || !loc || !consent.share_location} onClick={() => void submit()}>Send report</button>
            </fieldset>
            {err && <div className="callout err small" role="alert">{err}</div>}
            {res && <div className="callout info" role="status">
              {res.status === 'rejected' ? <>Report not accepted: {res.errors.join('; ')}</> : res.needs_more_evidence ?
                <>Thanks, we received it. We couldn’t confirm the hazard from this report alone; city staff will review it.</> :
                <>Thanks! Logged as <strong>{res.ticket_number}</strong>. City staff review every report; nothing is dispatched automatically.</>}</div>}
          </section>
          <section className="stack">
            <div className="card"><h2>Public hazard map</h2><LaneMap conditions={cond} label="Public map of generalised lane-cell conditions" />
              <p className="small muted">Shows 25 m cell conditions only. Never individual reports or riders’ routes. Only <Chip v="cleared_verified" /> means a cleanup was confirmed by evidence.</p></div>
            {mine.data && <div className="card"><h2>My reports</h2>
              <table className="data small"><tbody>{mine.data.map((m) => <tr key={m.id}><td>{ago(m.received_at)}</td><td>{m.hazard_claimed}</td><td>{m.ticket ?? '—'}</td><td><Chip v={m.ticket_status} /></td></tr>)}</tbody></table></div>}
          </section>
        </div>
      </main>
    </>
  );
}

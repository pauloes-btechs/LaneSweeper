import QRCode from 'qrcode';
import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ErrorState, Kpi, Loading, Prov } from '../components/ui';
import { useApi } from '../lib/useApi';

interface Report {
  slug: string; service_date: string; provenance: string; generated_at: string;
  summary: { robot: string; mission_status: string; lane_cells_planned: number; lane_cells_swept: number; cells_not_completed: number; approx_lane_km: number;
    verified_cleared: number; unverified_or_remaining: number; safety_events: number; scenario: string; disclaimer: string };
}

export default function RunReport() {
  const { slug } = useParams();
  const r = useApi<Report>(`/v1/public/run-reports/${slug}`, { isPublic: true });
  const [qr, setQr] = useState<string | null>(null);
  useEffect(() => { void QRCode.toDataURL(window.location.href, { margin: 1, width: 160 }).then(setQr); }, [slug]);
  if (r.error) return <main className="main"><ErrorState error={r.error} /></main>;
  if (!r.data) return <main className="main"><Loading /></main>;
  const s = r.data.summary;
  return (
    <main className="main" style={{ maxWidth: 820, margin: '0 auto' }}>
      <div className="row between"><div><div className="small muted">LaneKeeper public run report</div><h1>{r.data.service_date} · {s.robot}</h1></div><Prov v={r.data.provenance} /></div>
      <div className="callout warn" role="note"><strong>{r.data.provenance}.</strong> {s.disclaimer}</div>
      <div className="grid g3" style={{ marginTop: 16 }}>
        <Kpi label="Lane cells verified cleared" value={s.verified_cleared} note="after-evidence confirmed" />
        <Kpi label="Swept but not verified / remaining" value={s.unverified_or_remaining} note="not shown as cleared" />
        <Kpi label="Approx. lane distance" value={`${s.approx_lane_km} km`} note={`${s.lane_cells_planned} cells × 25 m`} />
      </div>
      <div className="card" style={{ marginTop: 16 }}>
        <table className="data"><tbody>
          <tr><td>Mission status</td><td>{s.mission_status}</td></tr>
          <tr><td>Cells swept / planned</td><td>{s.lane_cells_swept} / {s.lane_cells_planned}</td></tr>
          <tr><td>Cells not completed</td><td>{s.cells_not_completed}</td></tr>
          <tr><td>Safety stops / yields</td><td>{s.safety_events}</td></tr>
          <tr><td>Scenario</td><td>{s.scenario}</td></tr>
          <tr><td>Generated</td><td>{new Date(r.data.generated_at).toLocaleString()}</td></tr>
        </tbody></table>
        <p className="small muted">This page shows aggregates only. It contains no rider reports, photos, precise locations or staff records.</p>
      </div>
      <div className="row" style={{ marginTop: 16 }}>{qr && <img src={qr} alt="QR code linking to this report" width={120} height={120} />}
        <div className="small muted">Scan to open this report. <Link to="/rider">Report a hazard</Link></div></div>
    </main>
  );
}

import { LaneMap } from '../components/LaneMap';
import { RequireStaff } from '../components/RequireStaff';
import { Chip, ErrorState, Loading, Prov, ago } from '../components/ui';
import { useApi } from '../lib/useApi';
import type { Overview } from '../lib/types';

interface Cell { cell_id: string; condition: string; updated_at: string; last_verified_at: string | null; provenance: string }

function Body() {
  const cells = useApi<Cell[]>('/v1/lane-status', { poll: 4000 });
  const o = useApi<Overview>('/v1/overview', { poll: 4000 });
  if (cells.error) return <ErrorState error={cells.error} />;
  if (!cells.data) return <Loading />;
  const cond = Object.fromEntries(cells.data.map((c) => [c.cell_id, c.condition]));
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="row between"><h1>Hazard map</h1><Prov /></div>
      <div className="card"><LaneMap tall conditions={cond} robots={o.data?.robots ?? []} label="Hazard map of 1st Ave protected lane cells" /></div>
      <div className="card"><h3>Cells with a known condition ({cells.data.length})</h3>
        <div className="table-wrap"><table className="data small"><thead><tr><th>Cell</th><th>Condition</th><th>Updated</th><th>Last verified</th><th>Provenance</th></tr></thead>
          <tbody>{cells.data.map((c) => <tr key={c.cell_id}><td className="mono">{c.cell_id}</td><td><Chip v={c.condition} /></td><td>{ago(c.updated_at)}</td>
            <td>{c.last_verified_at ? ago(c.last_verified_at) : '—'}</td><td><Prov v={c.provenance} /></td></tr>)}</tbody></table></div>
        <p className="small muted">“Cleared unverified” means swept but not yet confirmed by after-evidence. Only “cleared verified” cells are shown as cleared on the public map.</p>
      </div>
    </div>
  );
}

export default function HazardMap() { return <RequireStaff>{() => <Body />}</RequireStaff>; }

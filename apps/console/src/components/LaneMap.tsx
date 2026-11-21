import maplibregl, { type Map as MLMap, type StyleSpecification } from 'maplibre-gl';
import { useEffect, useRef, useState } from 'react';
import { api } from '../lib/api';

// Free, keyless vector basemap (OpenFreeMap). If unreachable, a plain style keeps the lane layers usable offline.
const BASEMAP = 'https://tiles.openfreemap.org/styles/positron';
const PLAIN: StyleSpecification = { version: 8, sources: {}, layers: [{ id: 'bg', type: 'background', paint: { 'background-color': '#eef1ee' } }] };

export const CONDITION_COLOR: Record<string, string> = {
  unknown: '#9aa5ae', hazard_reported: '#e2a03f', hazard_confirmed: '#c62f22', scheduled: '#6d55c7', cleaning: '#6d55c7',
  cleared_unverified: '#e2a03f', partially_cleared: '#d9622b', cleared_verified: '#2f8f5b',
};

export interface MapRobot { id: string; lat: number; lng: number; state: string }
export interface PathPoint { lat: number; lng: number }

interface Props {
  jurisdiction?: string;
  conditions?: Record<string, string>;     // cell_id -> condition
  robots?: MapRobot[];
  path?: PathPoint[];                       // planned/actual route
  cursor?: PathPoint | null;                // replay position
  tall?: boolean;
  label?: string;
  focusCells?: string[];
}

export function LaneMap({ jurisdiction = 'nyc', conditions = {}, robots = [], path, cursor, tall, label = 'Map of protected lane cells', focusCells }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<MLMap | null>(null);
  const markers = useRef<maplibregl.Marker[]>([]);
  const [ready, setReady] = useState(false);
  const [lanes, setLanes] = useState<GeoJSON.FeatureCollection | null>(null);

  useEffect(() => { api.publicGet<GeoJSON.FeatureCollection>(`/v1/public/lanes?jurisdiction=${jurisdiction}`).then(setLanes).catch(() => setLanes(null)); }, [jurisdiction]);

  useEffect(() => {
    if (!el.current || map.current) return;
    let m: MLMap;
    try {
      m = new maplibregl.Map({ container: el.current, style: BASEMAP, center: [-73.972, 40.748], zoom: 13.4, attributionControl: { compact: true } });
    } catch {
      return; // no WebGL: the page still shows tables
    }
    m.on('error', (e) => { if (String(e.error?.message ?? '').includes('style')) m.setStyle(PLAIN); });
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
    m.on('load', () => setReady(true));
    m.on('style.load', () => setReady(true));
    map.current = m;
    return () => { m.remove(); map.current = null; };
  }, []);

  useEffect(() => {
    const m = map.current;
    if (!m || !ready || !lanes) return;
    const fc: GeoJSON.FeatureCollection = {
      type: 'FeatureCollection',
      features: lanes.features.map((f) => ({ ...f, properties: { ...f.properties, condition: conditions[String(f.id)] ?? 'unknown' } })),
    };
    const src = m.getSource('cells') as maplibregl.GeoJSONSource | undefined;
    if (src) src.setData(fc);
    else {
      m.addSource('cells', { type: 'geojson', data: fc });
      m.addLayer({ id: 'cells-casing', type: 'line', source: 'cells', paint: { 'line-color': '#ffffff', 'line-width': 9 } });
      m.addLayer({ id: 'cells', type: 'line', source: 'cells', paint: {
        'line-color': ['match', ['get', 'condition'], ...Object.entries(CONDITION_COLOR).flat(), '#9aa5ae'] as unknown as string,
        'line-width': ['case', ['==', ['get', 'condition'], 'unknown'], 4, 7] } });
      m.on('click', 'cells', (e) => {
        const p = e.features?.[0]?.properties as { cell_id: string; condition: string } | undefined;
        if (p) new maplibregl.Popup().setLngLat(e.lngLat).setHTML(`<strong>${p.cell_id}</strong><br/>${p.condition.replace(/_/g, ' ')}`).addTo(m);
      });
    }
    if (focusCells?.length) {
      const pts = lanes.features.filter((f) => focusCells.includes(String(f.id))).flatMap((f) => (f.geometry as GeoJSON.LineString).coordinates);
      if (pts.length) {
        const b = pts.reduce((bb, c) => bb.extend(c as [number, number]), new maplibregl.LngLatBounds(pts[0] as [number, number], pts[0] as [number, number]));
        m.fitBounds(b, { padding: 80, maxZoom: 17, duration: 0 });
      }
    }
  }, [ready, lanes, conditions, focusCells]);

  useEffect(() => {
    const m = map.current;
    if (!m || !ready) return;
    const data: GeoJSON.Feature = { type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: (path ?? []).map((p) => [p.lng, p.lat]) } };
    const s = m.getSource('path') as maplibregl.GeoJSONSource | undefined;
    if (s) s.setData(data);
    else if (path?.length) {
      m.addSource('path', { type: 'geojson', data });
      m.addLayer({ id: 'path', type: 'line', source: 'path', paint: { 'line-color': '#5b3fa8', 'line-width': 3, 'line-dasharray': [1.5, 1] } });
    }
  }, [ready, path]);

  useEffect(() => {
    const m = map.current;
    if (!m || !ready) return;
    markers.current.forEach((k) => k.remove());
    markers.current = [];
    const pts: Array<MapRobot | (PathPoint & { id: string; state: string })> = [...robots];
    if (cursor) pts.push({ ...cursor, id: 'replay', state: 'replay' });
    for (const r of pts) {
      if (r.lat == null || r.lng == null) continue;
      const d = document.createElement('div');
      d.setAttribute('role', 'img');
      d.setAttribute('aria-label', `robot ${r.id} ${r.state}`);
      d.style.cssText = `width:16px;height:16px;border-radius:4px;border:2px solid #fff;box-shadow:0 0 0 1px #0f1720;background:${r.state === 'paused_safety' || r.state === 'fault' ? '#c62f22' : '#5b3fa8'}`;
      d.title = `${r.id} · ${r.state} (SIMULATED)`;
      markers.current.push(new maplibregl.Marker({ element: d }).setLngLat([r.lng, r.lat]).addTo(m));
    }
  }, [ready, robots, cursor]);

  return (
    <div>
      <div ref={el} className={`map${tall ? ' tall' : ''}`} role="region" aria-label={label} />
      <div className="legend" aria-hidden="true">
        {Object.entries(CONDITION_COLOR).filter(([k]) => k !== 'scheduled').map(([k, c]) => <span key={k}><i style={{ background: c }} />{k.replace(/_/g, ' ')}</span>)}
        <span><i style={{ background: '#5b3fa8', height: 10, width: 10 }} />simulated robot</span>
      </div>
    </div>
  );
}

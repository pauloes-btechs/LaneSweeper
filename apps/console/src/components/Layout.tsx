import { NavLink, Outlet } from 'react-router-dom';
import { useApi } from '../lib/useApi';
import type { Approval } from '../lib/types';
import { IdentityPicker } from './Identity';

const NAV = [
  ['/console', 'Overview'], ['/console/map', 'Hazard Map'], ['/console/tickets', 'Tickets'], ['/console/missions', 'Missions'],
  ['/console/fleet', 'Fleet'], ['/console/agents', 'Agents & Approvals'], ['/console/analytics', 'Analytics'],
  ['/console/knowledge', 'Knowledge'], ['/console/settings', 'Settings'],
] as const;

export function SimBanner() {
  return (
    <div className="sim-banner" role="note">
      <span>SIMULATION · SIMULATED ROBOTS · SIMULATED GLASSES · SYNTHETIC DATA</span>
      <span>No physical robot can be commanded from this build</span>
    </div>
  );
}

export function ConsoleLayout() {
  const pending = useApi<Approval[]>('/v1/approvals?status=pending', { poll: 5000 });
  const n = pending.data?.length ?? 0;
  return (
    <>
      <SimBanner />
      <div className="shell">
        <nav className="nav" aria-label="Console">
          <div className="brand">
            <svg width="26" height="26" viewBox="0 0 64 64" aria-hidden="true"><path d="M18 52 L28 12 h8 L46 52z" fill="#2f8f5b" /><circle cx="44" cy="20" r="6" fill="#ffa13a" /></svg>
            <div>LaneKeeper OS<small>Bike-lane operations</small></div>
          </div>
          {NAV.map(([to, label]) => (
            <NavLink key={to} to={to} end={to === '/console'}>
              {label}{label === 'Agents & Approvals' && n > 0 && <span className="count" aria-label={`${n} pending`}>{n}</span>}
            </NavLink>
          ))}
          <div className="sep" />
          <NavLink to="/rider">Rider app ↗</NavLink>
          <NavLink to="/glasses">Glasses simulator ↗</NavLink>
        </nav>
        <main className="main">
          <div className="topbar"><span className="small muted">New York City (demo tenant) · 1st Ave protected lane pilot</span><IdentityPicker /></div>
          <Outlet />
        </main>
      </div>
    </>
  );
}

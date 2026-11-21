import { lazy, Suspense } from 'react';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { ConsoleLayout } from './components/Layout';
import { Loading } from './components/ui';

const Overview = lazy(() => import('./pages/Overview'));
const HazardMap = lazy(() => import('./pages/HazardMap'));
const Tickets = lazy(() => import('./pages/Tickets'));
const TicketDetail = lazy(() => import('./pages/TicketDetail'));
const Missions = lazy(() => import('./pages/Missions').then((m) => ({ default: m.MissionList })));
const MissionDetail = lazy(() => import('./pages/Missions').then((m) => ({ default: m.MissionDetail })));
const Fleet = lazy(() => import('./pages/Fleet'));
const Agents = lazy(() => import('./pages/Agents'));
const Analytics = lazy(() => import('./pages/Analytics'));
const Knowledge = lazy(() => import('./pages/Knowledge'));
const Settings = lazy(() => import('./pages/Settings'));
const Rider = lazy(() => import('./pages/Rider'));
const Glasses = lazy(() => import('./pages/Glasses'));
const RunReport = lazy(() => import('./pages/RunReport'));

export default function App() {
  return (
    <BrowserRouter>
      <Suspense fallback={<div className="main"><Loading rows={5} /></div>}>
        <Routes>
          <Route path="/" element={<Navigate to="/console" replace />} />
          <Route path="/console" element={<ConsoleLayout />}>
            <Route index element={<Overview />} />
            <Route path="map" element={<HazardMap />} />
            <Route path="tickets" element={<Tickets />} />
            <Route path="tickets/:ref" element={<TicketDetail />} />
            <Route path="missions" element={<Missions />} />
            <Route path="missions/:id" element={<MissionDetail />} />
            <Route path="fleet" element={<Fleet />} />
            <Route path="agents" element={<Agents />} />
            <Route path="analytics" element={<Analytics />} />
            <Route path="knowledge" element={<Knowledge />} />
            <Route path="settings" element={<Settings />} />
          </Route>
          <Route path="/rider" element={<Rider />} />
          <Route path="/glasses" element={<Glasses />} />
          <Route path="/r/:slug" element={<RunReport />} />
          <Route path="*" element={<div className="main"><h1>Not found</h1></div>} />
        </Routes>
      </Suspense>
    </BrowserRouter>
  );
}

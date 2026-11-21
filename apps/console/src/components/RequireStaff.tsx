import type { ReactNode } from 'react';
import { useApi } from '../lib/useApi';
import type { Me } from '../lib/types';
import { IdentityPicker } from './Identity';
import { Loading } from './ui';

export function RequireStaff({ children }: { children: (me: Me) => ReactNode }) {
  const me = useApi<Me>('/v1/me');
  if (me.loading) return <Loading />;
  if (!me.data || me.data.memberships.length === 0) {
    return (
      <div className="card" style={{ maxWidth: 560 }}>
        <h2>Municipal staff sign-in required</h2>
        <p className="muted">The console shows tickets, evidence and robot controls, so it needs an authenticated city account.
          In this local demo, pick a seeded identity. Each role sees and can do different things (the server enforces it).</p>
        <IdentityPicker />
      </div>
    );
  }
  return <>{children(me.data)}</>;
}

import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api';

export interface Loadable<T> { data: T | null; error: string | null; loading: boolean; reload: () => void }

/** GET with loading/error state, optional polling, and refetch when the session changes. */
export function useApi<T>(path: string | null, { poll, isPublic }: { poll?: number; isPublic?: boolean } = {}): Loadable<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(!!path);
  const alive = useRef(true);
  const load = useCallback(async () => {
    if (!path) return;
    try {
      const d = isPublic ? await api.publicGet<T>(path) : await api.get<T>(path);
      if (alive.current) { setData(d); setError(null); }
    } catch (e) {
      if (alive.current) setError((e as Error).message);
    } finally {
      if (alive.current) setLoading(false);
    }
  }, [path, isPublic]);
  useEffect(() => {
    alive.current = true;
    setLoading(!!path);
    void load();
    const onSession = () => void load();
    window.addEventListener('lk-session', onSession);
    const t = poll ? setInterval(() => void load(), poll) : undefined;
    return () => { alive.current = false; window.removeEventListener('lk-session', onSession); if (t) clearInterval(t); };
  }, [load, poll, path]);
  return { data, error, loading, reload: () => void load() };
}

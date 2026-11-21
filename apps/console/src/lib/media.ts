import { useApi } from './useApi';

export interface MediaAsset {
  id: string; kind: 'video' | 'image' | 'texture'; use_case: string; label: string | null; title: string;
  resolution: string; status: 'missing' | 'generating' | 'ready' | 'failed'; stale: boolean; est_usd: number;
  bytes: number | null; model: string | null; error: string | null; generated_at: string | null; url?: string | null; web_url?: string | null; web_bytes?: number | null;
}
export interface MediaList { assets: MediaAsset[]; provenance: string; reel_url?: string | null; models: { video: string; image: string } }

/** Ready AI-generated media (signed links, ~1 h). Absent or offline: an empty list and the procedural fallbacks run. */
export function useMedia(poll?: number) {
  const m = useApi<MediaList>('/v1/media', { poll });
  const ready = (m.data?.assets ?? []).filter((a) => a.status === 'ready' && a.url);
  return { ...m, ready, byId: (id: string) => ready.find((a) => a.id === id) };
}

import { ago, short } from '../src/components/ui';
import { ApiError, api, session } from '../src/lib/api';

describe('formatting helpers', () => {
  it('formats relative times and ids', () => {
    expect(ago(new Date(Date.now() - 30_000).toISOString())).toBe('30s ago');
    expect(ago(new Date(Date.now() - 5 * 60_000).toISOString())).toBe('5m ago');
    expect(ago(null)).toBe('—');
    expect(short('abcdef123456')).toBe('abcdef12');
  });
});

describe('api client', () => {
  afterEach(() => { vi.restoreAllMocks(); session.set(null); });

  it('sends the session token and surfaces server error detail', async () => {
    session.set('tok-123', 'NYC supervisor');
    const f = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ error: 'forbidden', detail: 'roles lack mission.approve' }), { status: 403 }));
    await expect(api.post('/v1/approvals/x/decide', { approve: true })).rejects.toThrow('roles lack mission.approve');
    const init = f.mock.calls[0]![1] as RequestInit;
    expect((init.headers as Record<string, string>).authorization).toBe('Bearer tok-123');
  });

  it('never attaches the token to public calls', async () => {
    session.set('tok-123');
    const f = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('[]', { status: 200 }));
    await api.publicGet('/v1/public/hazards');
    expect((f.mock.calls[0]![1] as RequestInit).headers).not.toHaveProperty('authorization');
  });

  it('exposes status on errors', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('nope', { status: 401, statusText: 'Unauthorized' }));
    const e = await api.get('/v1/me').catch((x) => x as ApiError);
    expect(e).toBeInstanceOf(ApiError);
    expect((e as ApiError).status).toBe(401);
  });
});

import { afterEach, describe, expect, it, vi } from 'vitest';

describe('local session authorization', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.resetModules();
  });

  it('shows valid server error references without accepting arbitrary header content', async () => {
    const reference='14f3d891-538b-455e-8183-97e82a85e4a5';
    const fetchMock=vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify({detail:'Action failed'}),{status:500,headers:{'x-request-id':reference}}))
      .mockResolvedValueOnce(new Response(JSON.stringify({detail:'Action failed'}),{status:500,headers:{'x-request-id':'PRIVATE_HEADER'}}));
    vi.stubGlobal('fetch',fetchMock);
    const {loadStatus}=await import('./api');
    await expect(loadStatus()).rejects.toThrow(`Action failed Reference: ${reference}.`);
    await expect(loadStatus()).rejects.toThrow(/^Action failed$/);
  });

  it('reads local diagnostics with controller authorization and no browser cache', async () => {
    const fetchMock=vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify({token:'qa-session'})))
      .mockResolvedValueOnce(new Response(JSON.stringify({localOnly:true,items:[]})));
    vi.stubGlobal('fetch',fetchMock);
    const {loadLocalDiagnostics}=await import('./api');
    await loadLocalDiagnostics(false);
    expect(String(fetchMock.mock.calls[1][0])).toBe('/api/v1/diagnostics?errors_only=false&limit=50');
    expect(new Headers(fetchMock.mock.calls[1][1]?.headers).get('X-DeepDeck-Token')).toBe('qa-session');
    expect(fetchMock.mock.calls[1][1]?.cache).toBe('no-store');
  });

  it('reports rendering failures only to the local controller with a fixed category and no exception payload', async () => {
    const fetchMock=vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify({token:'qa-session'})))
      .mockResolvedValueOnce(new Response(JSON.stringify({reference:'qa-reference',recorded:true})));
    vi.stubGlobal('fetch',fetchMock);
    const {reportLocalRenderFailure}=await import('./api');
    await reportLocalRenderFailure();
    expect(String(fetchMock.mock.calls[1][0])).toBe('/api/v1/diagnostics/ui');
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toEqual({kind:'render_error'});
    expect(new Headers(fetchMock.mock.calls[1][1]?.headers).get('X-DeepDeck-Token')).toBe('qa-session');
  });

  it('refreshes an expired controller token and retries the mutation once', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify({ token: 'expired-token' })))
      .mockResolvedValueOnce(new Response(
        JSON.stringify({ detail: 'Invalid local session token.' }),
        { status: 403 },
      ))
      .mockResolvedValueOnce(new Response(JSON.stringify({ token: 'current-token' })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: 'league-job' })));
    vi.stubGlobal('fetch', fetchMock);

    const { startJob } = await import('./api');
    await expect(startJob({ kind: 'matchmaking.agent' })).resolves.toMatchObject({
      id: 'league-job',
    });

    expect(fetchMock).toHaveBeenCalledTimes(4);
    expect(new Headers(fetchMock.mock.calls[1][1]?.headers).get('X-DeepDeck-Token')).toBe(
      'expired-token',
    );
    expect(new Headers(fetchMock.mock.calls[3][1]?.headers).get('X-DeepDeck-Token')).toBe(
      'current-token',
    );
  });

  it('requests the selected training metric window', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ items: [] })),
    );
    vi.stubGlobal('fetch', fetchMock);
    const { loadTrainingStatistics } = await import('./api');

    await loadTrainingStatistics('all');

    expect(String(fetchMock.mock.calls[0][0])).toBe(
      '/api/v1/statistics/training?window=all',
    );
  });

  it('requests the lightweight player history without changing the advanced default', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () => new Response('[]'));
    vi.stubGlobal('fetch', fetchMock);
    const { loadJobs } = await import('./api');
    await loadJobs('player');
    await loadJobs();
    expect(fetchMock.mock.calls.map(call => call[0])).toEqual([
      '/api/v1/jobs?view=player', '/api/v1/jobs',
    ]);
  });
});

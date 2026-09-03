import { afterEach, describe, expect, it, vi } from 'vitest';

describe('local session authorization', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.resetModules();
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
});

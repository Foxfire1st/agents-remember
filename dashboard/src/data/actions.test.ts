import { afterEach, describe, expect, it, vi } from 'vitest';

import { postAttentionDismiss } from './actions';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('postAttentionDismiss', () => {
  it('posts only the stable item id and kind', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response('{}', { status: 202 }));
    vi.stubGlobal('fetch', fetch);

    const status = await postAttentionDismiss({
      itemId: 'blocked-start:/worktrees/group',
      kind: 'blocked-start',
    });

    expect(status).toBe('dismissed');
    expect(fetch).toHaveBeenCalledWith('/api/actions/dismiss', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ itemId: 'blocked-start:/worktrees/group', kind: 'blocked-start' }),
    });
  });

  it('omits target for actionable drift', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response('{}', { status: 202 }));
    vi.stubGlobal('fetch', fetch);

    const status = await postAttentionDismiss({
      itemId: 'actionable-drift:agents-remember:main',
      kind: 'actionable-drift',
    });

    expect(status).toBe('dismissed');
    expect(fetch).toHaveBeenCalledWith('/api/actions/dismiss', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        itemId: 'actionable-drift:agents-remember:main',
        kind: 'actionable-drift',
      }),
    });
  });

  it('maps a non-202 response to error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}', { status: 500 })));
    await expect(postAttentionDismiss({ itemId: 'x', kind: 'blocked-start' })).resolves.toBe(
      'error',
    );
  });
});

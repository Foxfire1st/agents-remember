// Send (MIK-R188 rules 8–10): one POST per target, and what each answer does to the tray.
import { describe, expect, it, vi } from 'vitest';

import { createTrayStore, type TrayDeps } from './trayStore';
import { sendTray, type SendDeps } from './traySend';
import { DEFAULT_BOUNDS } from './trayPreferences';
import type { TrayImageStore } from './trayImages';
import type { TrayDelivery, TrayMessageBody, TrayPickInput } from './types';

function memoryImages(): TrayImageStore {
  const kept = new Map<string, Blob>();
  return {
    put: async (sha, blob) => {
      kept.set(sha, blob);
    },
    get: async (sha) => kept.get(sha) ?? null,
    remove: async (sha) => {
      kept.delete(sha);
    },
  };
}

const invariant: TrayPickInput = {
  kind: 'reference',
  title: 'INV-1ZKP4R · A silent role-agent turn end',
  reference: { kind: 'invariant', repository: 'agents-remember', id: 'INV-1ZKP4R', memoryTree: 'a'.repeat(40) },
};
const passage: TrayPickInput = {
  kind: 'text',
  title: 'No, not forever.',
  text: 'No, not forever.',
  provenance: { source: 'chat', agentId: 'agent-explainer', messageId: 'm-1', messageTime: '2026-10-19T12:05:00Z' },
};

type Answer = Partial<TrayDelivery> & { state: TrayDelivery['state'] };

function fakeFetch(answers: Record<string, Answer | 'network'>) {
  const bodies: { url: string; body: TrayMessageBody }[] = [];
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    const body = JSON.parse(String(init?.body)) as TrayMessageBody;
    bodies.push({ url, body });
    const agent = decodeURIComponent(url.split('/')[3]);
    const answer = answers[agent];
    if (answer === 'network') throw new TypeError('Failed to fetch');
    return new Response(
      JSON.stringify({ messageId: body.messageId, messageFile: `/coord/notes/reports/developer-messages/${agent}.md`, messageFileSha256: 'b'.repeat(64), ...answer }),
      { status: answer.state === 'refused' ? 409 : 200, headers: { 'Content-Type': 'application/json' } },
    );
  });
  return { fetcher: fetcher as unknown as typeof fetch, bodies, calls: fetcher };
}

const trayDeps = (images: TrayImageStore): TrayDeps => ({
  storage: undefined,
  images,
  bounds: () => DEFAULT_BOUNDS,
  shownChat: () => 'agent-explainer',
  now: () => new Date(2026, 9, 19, 13, 2), // local time: the title reads the developer's clock
});

async function trayWithImage() {
  const images = memoryImages();
  const store = createTrayStore(trayDeps(images));
  store.getState().add(invariant);
  store.getState().add(passage);
  await store.getState().addImage(new File([new Uint8Array([137, 80, 78, 71])], 'shot.png', { type: 'image/png' }));
  store.getState().setNote(store.getState().picks[0].id, 'is this a bug?');
  store.getState().setText('Two follow-ups on the risks you listed.');
  return { store, images };
}

function sendDeps(fetcher: typeof fetch, images: TrayImageStore, page: string | null = 'agents-remember'): SendDeps {
  let n = 0;
  return {
    fetcher,
    images,
    bounds: DEFAULT_BOUNDS,
    newId: () => `00000000-0000-4000-8000-00000000000${(n += 1)}`,
    now: () => new Date('2026-10-19T13:05:00Z'),
    pageRepository: () => page,
    targetRepository: () => null,
  };
}

describe('Send', () => {
  it('composes one POST body per target, with every pick, its note and the text', async () => {
    const { store, images } = await trayWithImage();
    store.getState().setTargets(['agent-explainer', 'agent-architect']);
    const { fetcher, bodies } = fakeFetch({ 'agent-explainer': { state: 'delivered' }, 'agent-architect': { state: 'delivered' } });
    expect(await sendTray(store, sendDeps(fetcher, images))).toBe(true);
    expect(bodies.map((call) => call.url)).toEqual([
      '/api/role-chats/agent-explainer/message',
      '/api/role-chats/agent-architect/message',
    ]);
    const [first, second] = bodies.map((call) => call.body);
    expect(first.messageId).not.toBe(second.messageId);
    expect(first).toMatchObject({ repository: 'agents-remember', text: 'Two follow-ups on the risks you listed.' });
    expect(first.picks.map((pick) => [pick.kind, pick.title, pick.note])).toEqual([
      ['reference', invariant.title, 'is this a bug?'],
      ['text', 'No, not forever.', ''],
      ['image', 'Screenshot · pasted 13:02', ''],
    ]);
    expect(first.picks[0].reference).toEqual(invariant.kind === 'reference' ? invariant.reference : null);
    expect(first.picks[1]).toMatchObject({ text: 'No, not forever.', provenance: { source: 'chat', agentId: 'agent-explainer' } });
    expect(first.picks[2].image).toEqual({ mimeType: 'image/png', data: btoa(String.fromCharCode(137, 80, 78, 71)) });
    // A fully sent tray empties.
    expect(store.getState().picks).toEqual([]);
    expect(store.getState().text).toBe('');
    expect(store.getState().targets).toEqual([]);
    expect(store.getState().outcome?.results.map((result) => result.delivery.state)).toEqual(['delivered', 'delivered']);
  });

  it('counts a held target as sent; a refused one keeps the picks and the text and becomes the target', async () => {
    const { store, images } = await trayWithImage();
    store.getState().setTargets(['agent-explainer', 'agent-worker', 'agent-gone']);
    const { fetcher, calls } = fakeFetch({
      'agent-explainer': { state: 'delivered' },
      'agent-worker': { state: 'held', reason: 'recipient-in-turn' },
      'agent-gone': { state: 'refused', reason: 'recipient-archived', detail: 'the agent was archived' },
    });
    await sendTray(store, sendDeps(fetcher, images));
    const state = store.getState();
    expect(state.picks).toHaveLength(3);
    expect(state.text).toBe('Two follow-ups on the risks you listed.');
    expect(state.targets).toEqual(['agent-gone']);
    expect(state.outcome?.results.map((result) => [result.agentId, result.delivery.state, result.delivery.reason])).toEqual([
      ['agent-explainer', 'delivered', undefined],
      ['agent-worker', 'held', 'recipient-in-turn'],
      ['agent-gone', 'refused', 'recipient-archived'],
    ]);
    // Sending again goes to the refused target only: the held one is never sent again.
    calls.mockClear();
    await sendTray(store, sendDeps(fetcher, images));
    expect(calls.mock.calls.map((call) => call[0])).toEqual(['/api/role-chats/agent-gone/message']);
  });

  it('keeps the tray unchanged when the server cannot be reached', async () => {
    const { store, images } = await trayWithImage();
    store.getState().setTargets(['agent-explainer']);
    const before = store.getState().picks;
    const { fetcher } = fakeFetch({ 'agent-explainer': 'network' });
    await sendTray(store, sendDeps(fetcher, images));
    expect(store.getState().picks).toBe(before);
    expect(store.getState().targets).toEqual(['agent-explainer']);
    expect(store.getState().outcome?.results[0].delivery).toMatchObject({ state: 'refused', reason: 'unreachable' });
  });

  it('sends without a repository when none can be named, and keeps the server\'s refusal by name', async () => {
    const images = memoryImages();
    const store = createTrayStore(trayDeps(images));
    store.getState().add(passage);
    store.getState().setTargets(['agent-projects-architect']);
    const detail = 'The message names no repository, and this server has 2: name one.';
    const { fetcher, bodies } = fakeFetch({ 'agent-projects-architect': { state: 'refused', reason: 'unknown-repository', detail } });
    expect(await sendTray(store, sendDeps(fetcher, images, null))).toBe(true);
    expect(bodies).toHaveLength(1);
    expect('repository' in bodies[0].body).toBe(false);
    expect(bodies[0].body).toMatchObject({ text: '', picks: [{ kind: 'text', text: 'No, not forever.' }] });
    expect(store.getState().outcome?.results[0].delivery).toMatchObject({ state: 'refused', reason: 'unknown-repository', detail });
    expect(store.getState().picks).toHaveLength(1);
  });

  it('names an answer without a delivery body by its status and the server\'s detail text', async () => {
    const { store, images } = await trayWithImage();
    store.getState().setTargets(['agent-explainer', 'agent-worker', 'agent-gone']);
    const answers: Record<string, Response> = {
      'agent-explainer': new Response(
        JSON.stringify({
          detail: [
            { loc: ['body', 'picks', 2, 'image', 'mimeType'], msg: "Input should be 'image/png', 'image/jpeg', 'image/gif' or 'image/webp'", type: 'literal_error' },
            { loc: ['body', 'text'], msg: 'Field required', type: 'missing' },
          ],
        }),
        { status: 422, headers: { 'Content-Type': 'application/json' } },
      ),
      'agent-worker': new Response(JSON.stringify({ detail: 'Request body too large' }), { status: 413 }),
      'agent-gone': new Response('Internal Server Error', { status: 500 }),
    };
    const fetcher = vi.fn(async (url: string) => answers[decodeURIComponent(url.split('/')[3])]) as unknown as typeof fetch;
    await sendTray(store, sendDeps(fetcher, images));
    expect(store.getState().outcome?.results.map((result) => [result.delivery.reason, result.delivery.detail])).toEqual([
      [
        'http-422',
        "The dashboard server answered 422: picks.2.image.mimeType: Input should be 'image/png', 'image/jpeg', 'image/gif' or 'image/webp'; text: Field required.",
      ],
      ['http-413', 'The dashboard server answered 413: Request body too large.'],
      ['http-500', 'The dashboard server answered 500 without a delivery answer.'],
    ]);
  });

  it('sends nothing without a target, and refuses a message over a bound by name', async () => {
    const { store, images } = await trayWithImage();
    store.getState().setTargets([]);
    const { fetcher, calls } = fakeFetch({});
    expect(await sendTray(store, sendDeps(fetcher, images))).toBe(false);
    store.getState().setTargets(['agent-explainer']);
    expect(await sendTray(store, { ...sendDeps(fetcher, images), bounds: { ...DEFAULT_BOUNDS, maxPicks: 2 } })).toBe(false);
    expect(store.getState().refusal).toBe(
      "Send is refused: the tray holds 3 picks, over the bound of 2 picks per message (set under Bounds in the tray's foot).",
    );
    expect(calls).not.toHaveBeenCalled();
  });
});

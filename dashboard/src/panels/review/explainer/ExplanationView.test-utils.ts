import { vi } from 'vitest';

import { readExplainerState } from './explainerApi';
import { staleBriefOf, type StaleBrief } from './explainerModel';

export const REPO = 'agents-remember';
export const MASTER = '260928_maintained-invariant-knowledge';
export const LEAF = '260928-MIK-L183';
export const BASE = `/api/explainer/${REPO}/${MASTER}/${LEAF}`;

export interface Call {
  url: string;
  method: string;
  body: Record<string, unknown> | undefined;
}

export const text = (element: Element | null | undefined): string => element?.textContent ?? '';

export const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

export function serve(
  reply: (call: Call, calls: Call[], counter: { sessions: number }) => Response,
): Call[] {
  const calls: Call[] = [];
  const counter = { sessions: 0 };
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const call: Call = {
        url: String(input),
        method: init?.method ?? 'GET',
        body:
          typeof init?.body === 'string'
            ? (JSON.parse(init.body) as Record<string, unknown>)
            : undefined,
      };
      calls.push(call);
      return Promise.resolve(reply(call, calls, counter));
    }),
  );
  return calls;
}

export const channel = (name: string, revision: number) => ({
  state: 'ok',
  session: name,
  revision,
  comparison: 'review-comparison:task/leaf/9',
  entrypoint: 'index.html',
  scripts: false,
  sandbox: 'allow-scripts',
  pageUrl: `/canvas/page/${name}`,
});

export const BINDING9 = { kind: 'recorded', comparison: 9, label: '#9' };

export const SAVED_STATE = {
  state: 'ok',
  seat: 'live',
  retiredAt: null,
  binding: BINDING9,
  explanation: {
    revision: 8,
    at: '2026-10-19T12:36:00Z',
    entrypoint: 'index.html',
    comparison: 'review-comparison:task/leaf/8',
  },
  building: null,
  stale: {
    total: 5,
    parts: [
      {
        id: 'look',
        title: 'Where to look first',
        reason: 'noteTurnEnded and the tracker changed in #9',
      },
      {
        id: 'risks',
        title: 'Risks I see',
        reason: 'the grades of 4 changed blocks',
      },
      {
        id: 'flow',
        title: 'Before and after',
        reason: 'the wait and the count changed in #9',
      },
    ],
  },
  parts: [
    { id: 'answer', title: 'In one page' },
    { id: 'look', title: 'Where to look first' },
    { id: 'risks', title: 'Risks I see' },
  ],
  claims: [
    {
      id: 'c1',
      part: 'answer',
      basis: 'recorded-fact',
      text: 'the curator recorded this as a weaken',
      link: { block: 'b20', label: 'turn_ended.ts L1697–1817' },
    },
    {
      id: 'c2',
      part: 'look',
      basis: 'mechanical-signal',
      text: '13 of 71 locations changed',
    },
    {
      id: 'c3',
      part: 'risks',
      basis: 'presenter-inference',
      text: 'the send to the seat may throw',
    },
    {
      id: 'c4',
      part: 'risks',
      basis: 'intended',
      text: 'a seat is meant to return to its work',
    },
  ],
  moved: null,
  limit: null,
};

export const REVISIONS = {
  state: 'ok',
  pinned: 8,
  total: { used: 41 * 1024 * 1024, limit: 512 * 1024 * 1024 },
  revisions: [
    {
      revision: 7,
      binding: { kind: 'recorded', comparison: 7, label: '#7' },
      at: '2026-10-19T11:20:00Z',
      kind: 'global',
      agreed: false,
      pinned: false,
    },
    {
      revision: 8,
      binding: { kind: 'recorded', comparison: 8, label: '#8' },
      at: '2026-10-19T12:36:00Z',
      kind: 'global',
      agreed: true,
      pinned: true,
    },
    {
      revision: 9,
      binding: { kind: 'recorded', comparison: 9, label: '#9' },
      at: '2026-10-19T13:10:00Z',
      kind: 'focused',
      focus: 'Risks I see',
      agreed: false,
      pinned: false,
    },
  ],
};

function mutationReply(call: Call): Response {
  if (call.url.endsWith('/revoke')) return json({ state: 'ok', revoked: true });
  if (call.url === `${BASE}/update`) return json({ attempt: 'a1', state: {} });
  if (call.url === `${BASE}/remove-revisions`)
    return json({ state: 'ok', removed: call.body?.revisions ?? [] });
  return json({ state: 'refused', reason: 'unmocked', detail: call.url }, 404);
}

export function server(
  over: {
    state?: unknown;
    revisions?: unknown;
    sessions?: (call: Call) => Response;
  } = {},
) {
  return serve((call, _calls, counter) => {
    if (call.url.startsWith(`${BASE}/state`)) return json(over.state ?? SAVED_STATE);
    if (call.url === `${BASE}/revisions`) return json(over.revisions ?? REVISIONS);
    if (call.url === '/api/canvas/sessions') {
      counter.sessions += 1;
      if (over.sessions) return over.sessions(call);
      return json(channel(`s${counter.sessions}`, Number(call.body?.revision ?? 1)));
    }
    return mutationReply(call);
  });
}

export const onExplain = vi.fn();

/**
 * The top line's badge content, from the same answer the view reads (F18).
 *
 * The answer goes through the production client's own parser, so a badge built here is built from
 * what the app would have parsed -- not from a shape invented for the test.
 */
export async function badgeBriefOf(): Promise<StaleBrief> {
  const state = await readExplainerState(
    { repository: REPO, master: MASTER, leaf: LEAF },
    { comparison: 'review-comparison:task/leaf/9', comparisonNumber: 9 },
  );
  const revision = state.explanation?.revision ?? null;
  return staleBriefOf(state, revision, revision !== null);
}

export const props = {
  target: { repository: REPO, master: MASTER, leaf: LEAF },
  comparison: { reference: 'review-comparison:task/leaf/9', number: 9 },
  onExplain,
};

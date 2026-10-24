// Test support for the explainer column: a scripted dashboard server, and the tray's own reset.
import { vi } from 'vitest';

import { trayStore } from '../tray/trayStore';

export interface Call {
  method: string;
  path: string;
  body: Record<string, unknown> | null;
}

type Answer = { status?: number; body: unknown };

const FRAME = { available: false, reason: 'unreachable', detail: 'no Paseo in this test' };

const json = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

/** One path's queued answers, in order; a single answer is its own one-entry queue. */
function queueFor(entry: Answer | Answer[] | undefined): Answer[] {
  if (entry === undefined) return [];
  return Array.isArray(entry) ? entry : [entry];
}

/** The answer a path gives on its nth call: the last one in its queue stands. */
function answerAt(queue: Answer[], index: number): Answer | undefined {
  return queue[Math.min(index, Math.max(0, queue.length - 1))];
}

/** The call's body, as the record a test reads back. */
function bodyOf(init: RequestInit | undefined): Record<string, unknown> | null {
  if (!init?.body) return null;
  return JSON.parse(String(init.body)) as Record<string, unknown>;
}

/**
 * A scripted server: each path answers its queued answers in order, the last one standing. Every
 * call is recorded, so a test can say what was and was not posted.
 */
export function backend(script: Record<string, Answer | Answer[]> = {}) {
  const calls: Call[] = [];
  const seen = new Map<string, number>();
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string, init?: RequestInit) => {
      const url = new URL(String(address), 'http://dashboard.test');
      calls.push({ method: init?.method ?? 'GET', path: url.pathname, body: bodyOf(init) });
      if (url.pathname === '/api/role-launch/frame') return json(FRAME);
      const index = seen.get(url.pathname) ?? 0;
      seen.set(url.pathname, index + 1);
      const answer = answerAt(queueFor(script[url.pathname]), index);
      return answer ? json(answer.body, answer.status ?? 200) : json({});
    }),
  );
  return {
    calls,
    posts: () => calls.filter((call) => call.method === 'POST'),
    paths: () => calls.map((call) => call.path),
    count: (method: string, path: string) =>
      calls.filter((call) => call.method === method && call.path === path).length,
    last: (method: string, path: string) =>
      [...calls].reverse().find((call) => call.method === method && call.path === path) ?? null,
  };
}

export const STATE = '/api/explainer/repo/master/L181/state';
export const BUILD = '/api/explainer/repo/master/L181/build';
export const UPDATE = '/api/explainer/repo/master/L181/update';
export const RETIRE = '/api/explainer/repo/master/L181/retire';

export const ADDRESS = { repository: 'repo', master: 'master', leaf: 'L181' };
export const TASK = 'L181';

export const explainerState = (patch: Record<string, unknown> = {}) => ({
  seat: null,
  agentId: null,
  workspaceId: null,
  generation: 0,
  retiredAt: null,
  binding: null,
  build: null,
  staleParts: [],
  ...patch,
});

/** An empty tray with nothing kept from an earlier test. */
export function resetTray(): void {
  for (const pick of trayStore.getState().picks) trayStore.getState().remove(pick.id);
  trayStore.getState().setText('');
  trayStore.getState().setTargets([]);
}

export const invariantPick = (id: string, title: string) => ({
  kind: 'reference' as const,
  title,
  reference: { kind: 'invariant' as const, repository: 'repo', id, memoryTree: 'mem' },
});

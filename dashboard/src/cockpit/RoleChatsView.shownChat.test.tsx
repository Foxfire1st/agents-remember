// The chat the Chats page shows, as the tray's first pick reads it (MIK-R188 rule 7): an object
// pick takes the chat the column shows as the default target, and a column that shows no chat
// gives none. The frame shows what Paseo's own list moved it to, not only what the launcher asked
// for; the dashboard follows that without steering the frame itself.
import { act, cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { taskEntry, taskSummary } from '../test/fixtures/wire';
import { shownStore } from '../tray/shownChat';
import { EMPTY_TRAY } from '../tray/trayPersistence';
import { trayStore } from '../tray/trayStore';
import type { TrayPickInput } from '../tray/types';
import { RoleChatsPane } from './RoleChats';
import { tasklessRequestStorageKey } from './roleLaunchModel';

const FRAME_ORIGIN = 'http://127.0.0.1:6820';
const AVAILABLE = {
  available: true,
  frameBaseUrl: FRAME_ORIGIN,
  serverId: 'srv_test',
  projectsWorkspaceId: 'wks_projects',
  projectsWorkspaceDetail: null,
};
const ARCHITECT = { agentId: 'agent-architect', workspaceId: 'wks_projects' };
const WORKER = { agentId: 'agent-worker', workspaceId: 'wks_leaf' };
/** What the plug-in of this build says it can do. */
const CURRENT = ['open-agent', 'ping', 'page', 'shown-on-display', 'navigated', 'on-chat', 'load-failed', 'pick'];
const EXECUTION = {
  status: 'running',
  requestId: 'request-architect',
  canStart: true,
  canRevive: false,
  execution: { kind: 'paseo-agent', serverId: 'srv_test', ...ARCHITECT },
};
const CATALOG = { roleDefaults: { agent: 'codex' }, agents: [{ id: 'codex', label: 'Codex', models: [] }], catalogOrigin: 'test' };
const SPRINT = taskEntry({
  id: 'SPRINT-1',
  title: 'Sprint one',
  reference: { repository: 'demo', path: 'sprint-one/task.json' },
  summary: taskSummary({ kind: 'master', form: 'sprint', orchestrates: ['some-master'] }),
});
/** An object pick: a reference, not a passage of a chat. */
const OBJECT_PICK: TrayPickInput = {
  kind: 'reference',
  title: 'INV-1ZKP4R · A silent role-agent turn end',
  reference: { kind: 'invariant', repository: 'demo', id: 'INV-1ZKP4R', memoryTree: 'a'.repeat(40) },
};

/** The dashboard backend with the architect's taskless execution on the launcher. */
function stubBackend(frame: Record<string, unknown>) {
  sessionStorage.setItem(tasklessRequestStorageKey('architect'), JSON.stringify({ requestId: EXECUTION.requestId }));
  const routes: Record<string, (request: Record<string, unknown>) => Record<string, unknown>> = {
    '/api/role-launch/frame': () => frame,
    '/api/role-launch/options': (request) => ({ ...CATALOG, executions: request.role === 'architect' ? [EXECUTION] : [] }),
    '/api/role-launch/result': () => ({ httpStatus: 404, detail: 'no result' }),
  };
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const route = routes[String(input)];
      if (!route) throw new Error('unexpected request ' + String(input));
      const body = route(JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>);
      const status = typeof body.httpStatus === 'number' ? body.httpStatus : 200;
      return { ok: status < 400, status, json: async () => body } as Response;
    }),
  );
}

async function settle(): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}

/** The Chats page's frame, with what the dashboard posts into it and what its plug-in says. */
function chatsFrame(container: HTMLElement) {
  const frame = container.querySelector('iframe');
  if (!frame?.contentWindow) throw new Error('no frame is mounted');
  const posts = vi.fn();
  frame.contentWindow.postMessage = posts as unknown as Window['postMessage'];
  const says = (message: Record<string, unknown>, origin = FRAME_ORIGIN) =>
    act(() => {
      window.dispatchEvent(
        new MessageEvent('message', { data: { source: 'ar-plugin', ...message }, origin, source: frame.contentWindow }),
      );
    });
  const opened = () => posts.mock.calls.filter(([message]) => message.type === 'ar.open').map(([message]) => message.agentId);
  const hello = () => posts.mock.calls.find(([message]) => message.type === 'ar.hello')?.[0];
  return { says, opened, hello };
}

const shownChat = () => shownStore.getState().chat?.agentId ?? null;

beforeEach(() => {
  vi.useFakeTimers();
  sessionStorage.clear();
  trayStore.setState({ ...EMPTY_TRAY, open: false, refusal: null, outcome: null, deferDefault: false, pendingDefault: false });
  shownStore.setState({ chat: null, page: null, repository: null });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe("the Chats page's shown chat for the tray's default", () => {
  it("follows Paseo's own list from A to B, so a fresh tray's first object pick goes to B", async () => {
    stubBackend(AVAILABLE);
    const { container } = render(<RoleChatsPane active tasks={[SPRINT]} />);
    await settle();
    const frame = chatsFrame(container);
    frame.says({ type: 'ready', capabilities: CURRENT });
    await settle();
    expect(frame.opened()).toEqual([ARCHITECT.agentId]);
    frame.says({ type: 'selection', agentIds: [ARCHITECT.agentId] });
    frame.says({ type: 'shown', agentId: ARCHITECT.agentId });
    expect(shownChat()).toBe(ARCHITECT.agentId);

    // He picks the Worker's chat in Paseo's own list: the plug-in reports the visible chat.
    frame.says({ type: 'selection', agentIds: [WORKER.agentId] });
    expect(shownChat()).toBe(WORKER.agentId);
    // The host navigated by itself: the dashboard asks for nothing.
    expect(frame.opened()).toEqual([ARCHITECT.agentId]);
    // A report from any other origin changes nothing.
    frame.says({ type: 'selection', agentIds: ['agent-other'] }, 'http://evil.test');
    expect(shownChat()).toBe(WORKER.agentId);

    act(() => void trayStore.getState().add(OBJECT_PICK));
    expect(trayStore.getState().targets).toEqual([WORKER.agentId]);
  });

  it("follows Paseo's own list from A to B in its compact layout, where no selection is published, and a frame on no chat gives no default", async () => {
    stubBackend(AVAILABLE);
    const { container } = render(<RoleChatsPane active tasks={[SPRINT]} />);
    await settle();
    const frame = chatsFrame(container);
    // The plug-in says it tells the chat it shows; below Paseo's 720 px breakpoint the tab strip
    // is gone, so its published selection stays empty (MIK-R95).
    frame.says({ type: 'ready', capabilities: [...CURRENT, 'showing'] });
    await settle();
    expect(frame.opened()).toEqual([ARCHITECT.agentId]);
    frame.says({ type: 'selection', agentIds: [] });
    frame.says({ type: 'showing', agentId: ARCHITECT.agentId });
    frame.says({ type: 'shown', agentId: ARCHITECT.agentId });
    expect(shownChat()).toBe(ARCHITECT.agentId);

    // He picks the Worker's chat in Paseo's own list: the plug-in tells the chat it now shows.
    frame.says({ type: 'showing', agentId: WORKER.agentId });
    expect(shownChat()).toBe(WORKER.agentId);
    expect(frame.opened()).toEqual([ARCHITECT.agentId]);
    // A report from any other origin changes nothing; nor does the empty selection.
    frame.says({ type: 'showing', agentId: 'agent-other' }, 'http://evil.test');
    frame.says({ type: 'selection', agentIds: [] });
    expect(shownChat()).toBe(WORKER.agentId);
    act(() => void trayStore.getState().add(OBJECT_PICK));
    expect(trayStore.getState().targets).toEqual([WORKER.agentId]);

    // He leaves the chats for a screen with none: a fresh tray's first object pick has no default.
    frame.says({ type: 'showing', agentId: null });
    expect(shownChat()).toBeNull();
    act(() => {
      trayStore.setState({ ...EMPTY_TRAY, open: false, refusal: null, outcome: null, deferDefault: false, pendingDefault: false });
      void trayStore.getState().add(OBJECT_PICK);
    });
    expect(trayStore.getState().targets).toEqual([]);
    // The dashboard said it reads what the frame shows: the plug-in tells only such a dashboard.
    expect(frame.hello()?.capabilities).toContain('showing');
  });

  it('names no shown chat while the column shows a placeholder, so the first object pick has no default', async () => {
    stubBackend({ available: false, reason: 'unreachable', detail: 'connection refused' });
    const { getByTestId } = render(<RoleChatsPane active tasks={[SPRINT]} />);
    await settle();
    // The launcher shows the architect's execution, but the column shows the placeholder.
    expect(getByTestId('paseo-frame-unavailable')).toBeTruthy();
    expect(shownChat()).toBeNull();

    act(() => void trayStore.getState().add(OBJECT_PICK));
    expect(trayStore.getState().targets).toEqual([]);
  });
});

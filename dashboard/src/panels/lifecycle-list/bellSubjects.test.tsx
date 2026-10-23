// The bell's rows in the Operations column, mounted with the real consumers they open
// (L190-R1-F08 and L190-R1-F09): the task page beside the list, and — for an agent row — the real
// Cockpit's Chats view with its embedded frame. A mock receiving a string proves nothing about
// either, so each case here follows the production route to the surface that actually changes;
// no test supplies a handler, a frame target or a stub frame of its own.
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { CockpitShell } from '../../cockpit/Cockpit';
import { frameTrayStore } from '../../cockpit/frameTrayChannel';
import { tasklessRequestStorageKey } from '../../cockpit/roleLaunchModel';
import { dashboardStore } from '../../data/store';
import { taskEntrySelectionKey } from '../../data/taskReference';
import { analytics, attentionItem, projection } from '../../test/fixtures/wire';
import { DetailPanel } from '../detail-panel/DetailPanel';
import { stubCounters } from '../detail-panel/test-utils';
import { LifecycleList } from './LifecycleList';
import { installListCleanup, task, taskSummary } from './test-utils';
import type { AttentionItem } from '../../types/projection';

installListCleanup();

// A sprint, its master, a leaf that is still running, and a leaf that landed and whose enclosure
// was cleaned up afterwards: the terminal leaf has no worktree left, and lives in the closed
// completed fold of the tree.
function operationsTree() {
  const stalled = task('M-L1', { title: 'Stalled leaf', stage: 'started', activity: 'works' });
  const landed = task('M-L2', {
    title: 'Landed leaf',
    stage: 'completed',
    activity: undefined,
    worktrees: [],
    summary: taskSummary({ kind: 'subTask', form: 'task', objective: 'The landed leaf.' }),
  });
  const master = task('M', {
    title: 'Series master',
    summary: taskSummary({ kind: 'master', form: 'master' }),
    reference: { repository: 'repo', path: 'master/task.json' },
    children: [stalled.key, landed.key],
    counts: { planned: 0, started: 1, completed: 1, aborted: 0, works: 1, paused: 0, unknown: 0 },
  });
  const sprint = task('S', {
    title: 'Sprint',
    summary: taskSummary({ kind: 'master', form: 'sprint' }),
    reference: { repository: 'repo', path: 'sprint/task.json' },
    children: [master.key],
    counts: { planned: 0, started: 1, completed: 1, aborted: 0, works: 1, paused: 0, unknown: 0 },
  });
  master.parent = sprint.key;
  stalled.parent = master.key;
  landed.parent = master.key;
  return { sprint, master, stalled, landed, entries: [sprint, master, stalled, landed] };
}

const tree = operationsTree();

const stalled = attentionItem({
  id: `task-stalled:${tree.stalled.key}`,
  kind: 'task-stalled',
  severity: 'alarm',
  lane: 'task',
  title: 'Leaf M-L1 is stalled',
  taskKey: tree.stalled.key,
  taskReference: tree.stalled.reference,
  itemClass: 'action',
  dismissible: false,
});

const landed = attentionItem({
  id: `task-landed:${tree.landed.key}:occ-1`,
  kind: 'task-landed',
  severity: 'info',
  lane: 'task',
  title: 'Leaf M-L2 landed',
  taskKey: tree.landed.key,
  taskReference: tree.landed.reference,
  occurrenceId: 'occ-1',
  itemClass: 'notice',
  dismissible: true,
});

// A held message at a seat with no task document: never a fake task.
const tasklessHeld = attentionItem({
  id: 'held-message:agent-7:m-1',
  kind: 'held-message',
  severity: 'warn',
  lane: 'agent',
  title: 'Message held for Worker',
  detail: 'the chat is in a turn',
  agentId: 'agent-7',
  agentRole: 'worker',
  itemClass: 'action',
  dismissible: false,
});

// The same kind of row at a seat that has a task document.
const taskHeld = attentionItem({
  id: 'held-message:agent-8:m-2',
  kind: 'held-message',
  severity: 'warn',
  lane: 'agent',
  title: 'Message held for Worker on a leaf',
  agentId: 'agent-8',
  agentRole: 'worker',
  taskKey: tree.stalled.key,
  taskReference: tree.stalled.reference,
  itemClass: 'action',
  dismissible: false,
});

function seed(queue: AttentionItem[]) {
  dashboardStore
    .getState()
    .applySnapshot(
      projection({ tasks: tree.entries, analytics: analytics({ attentionQueue: queue }) }),
    );
}

beforeEach(() => {
  stubCounters();
  frameTrayStore.setState({
    chat: {},
    composer: {},
    pickNotice: {},
    traySpace: {},
    pickListeners: 0,
  });
});

afterEach(() => {
  cleanup();
  dashboardStore.getState().reset();
  frameTrayStore.setState({
    chat: {},
    composer: {},
    pickNotice: {},
    traySpace: {},
    pickListeners: 0,
  });
});

// The Operations column as the shell composes it: one selection state shared by the task list and
// the task page (Cockpit's `actions.open` and `selectedId`). An agent row's handler is the shell's,
// which is why every agent case below mounts the Cockpit itself.
function Operations() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  return (
    <>
      <LifecycleList selectedId={selectedId} onSelect={setSelectedId} />
      <DetailPanel selectedId={selectedId} />
    </>
  );
}

function openBell(ui: ReturnType<typeof render>) {
  fireEvent.click(ui.getByTestId('attention-bell'));
  return ui.getByTestId('attention-queue');
}

function rowFor(scope: HTMLElement, title: string): HTMLElement {
  const row = [...scope.querySelectorAll<HTMLElement>("[data-testid='attn-item']")].find((item) =>
    item.textContent?.includes(title),
  );
  if (!row) throw new Error(`no attention row for ${title}`);
  return row;
}

describe('a bell task row opens the exact canonical leaf (L190-R1-F08)', () => {
  it('selects the leaf the row names, and the Operations page opens that document', async () => {
    seed([stalled]);
    const ui = render(<Operations />);
    expect(
      ui.getByText('Select a task to inspect its steps, review, requirements and reports.'),
    ).toBeTruthy();
    fireEvent.click(ui.getByTestId('attention-bell'));
    fireEvent.click(
      rowFor(ui.getByTestId('attention-queue'), stalled.title).querySelector(
        "[data-testid='attn-open']",
      )!,
    );

    await waitFor(() => expect(ui.getByTestId('doc-title').textContent).toBe('Stalled leaf'));
    expect(ui.getByTestId('doc-id').textContent).toContain('M-L1');
    // The selection the page was opened from is the tree's own identity, not the raw key.
    const option = ui.container.querySelector<HTMLElement>(
      `[role='option'][data-key='${taskEntrySelectionKey(tree.stalled.key)}']`,
    );
    if (!option) throw new Error('the chosen task has no row');
    expect(option.getAttribute('aria-selected')).toBe('true');

    // The tree's own rows keep their selection path: a plain row click opens that page too.
    const masterRow = () =>
      ui.container.querySelector<HTMLElement>(
        `[role='option'][data-key='${taskEntrySelectionKey(tree.master.key)}']`,
      )!;
    fireEvent.click(masterRow());
    await waitFor(() => expect(ui.getByTestId('doc-title').textContent).toBe('Series master'));
    // And the keyboard the column already had still closes a parent: ← on an open row.
    fireEvent.keyDown(masterRow(), { key: 'ArrowLeft' });
    await waitFor(() => expect(masterRow().dataset.expanded).toBe('false'));
  });

  it('opens a terminal leaf after its enclosure was cleaned up, out of the closed fold', async () => {
    seed([landed]);
    const ui = render(<Operations />);
    // The completed fold starts closed: the terminal leaf has no row of its own yet.
    expect(
      ui.container.querySelector(
        `[role='option'][data-key='${taskEntrySelectionKey(tree.landed.key)}']`,
      ),
    ).toBeNull();
    fireEvent.click(ui.getByTestId('attention-bell'));
    fireEvent.click(
      rowFor(ui.getByTestId('attention-queue'), landed.title).querySelector(
        "[data-testid='attn-open']",
      )!,
    );

    await waitFor(() => expect(ui.getByTestId('doc-title').textContent).toBe('Landed leaf'));
    const option = ui.container.querySelector<HTMLElement>(
      `[role='option'][data-key='${taskEntrySelectionKey(tree.landed.key)}']`,
    );
    if (!option) throw new Error('the landed leaf was not revealed by its selection');
    expect(option.getAttribute('aria-selected')).toBe('true');
    // Nothing of its enclosure is left; the leaf still opens.
    expect(tree.landed.worktrees).toEqual([]);
  });
});

// The Chats view's chat target, as the real shell drives it (L190-R1-F09): the pane's selection
// resolves an execution, `paseoAgentTarget` names that execution's agent, and the embedded
// application is asked for it (`ar.open`). A bell row names an exact served agent, and that agent
// is what the frame is asked for. The chat the frame reports showing is written only by the
// application's own answer — never by the click.
const FRAME_ORIGIN = 'http://127.0.0.1:6820';
const AVAILABLE = {
  available: true,
  frameBaseUrl: FRAME_ORIGIN,
  serverId: 'srv_test',
  projectsWorkspaceId: 'wks_projects',
  projectsWorkspaceDetail: null,
};
/** The chat the Chats pane shows before any bell row is clicked: the architect's taskless run. */
const ARCHITECT = { agentId: 'agent-architect', workspaceId: 'wks_projects' };
const ARCHITECT_EXECUTION = {
  status: 'running',
  requestId: 'request-architect',
  canStart: true,
  canRevive: false,
  execution: { kind: 'paseo-agent', serverId: 'srv_test', ...ARCHITECT },
};
const CATALOG = {
  roleDefaults: { agent: 'codex' },
  agents: [{ id: 'codex', label: 'Codex', models: [] }],
  catalogOrigin: 'test',
};

function answer(status: number, body: unknown): Response {
  return { ok: status < 400, status, json: async () => body } as Response;
}

/**
 * The dashboard backend the Cockpit talks to. The architect's request is the pane's opening chat.
 * No other scope has a receipt the dashboard could open a chat from — a bell row's seat is not a
 * launcher selection the host resolves — so the agent asked for can only be the row's own.
 */
function stubDashboard() {
  sessionStorage.setItem(
    tasklessRequestStorageKey('architect'),
    JSON.stringify({ requestId: ARCHITECT_EXECUTION.requestId }),
  );
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === '/api/role-launch/frame') return answer(200, AVAILABLE);
    if (url === '/api/role-launch/options') {
      const body = JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>;
      return answer(
        200,
        body.role === 'architect' ? { ...CATALOG, executions: [ARCHITECT_EXECUTION] } : CATALOG,
      );
    }
    if (url === '/api/role-launch/result') return answer(404, { detail: 'no result' });
    if (url.startsWith('/api/role-launch/reports'))
      return answer(200, { reports: [], total: 0, explorer: null });
    if (url.startsWith('/api/task-document')) return answer(200, {});
    return answer(200, {
      counters: {
        code: { files: 0, insertions: 0, deletions: 0 },
        memory: { files: 0, insertions: 0, deletions: 0 },
      },
    });
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

async function settle(ms = 0): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

const plugin = (message: Record<string, unknown>) => ({ source: 'ar-plugin', ...message });
/** What the plug-in of this build says it can do. */
const CURRENT = [
  'open-agent',
  'ping',
  'page',
  'shown-on-display',
  'navigated',
  'on-chat',
  'load-failed',
  'pick',
];

/** The Chats page's own frame, with what the dashboard posts into it and what its plug-in says. */
function chatsFrame(container: HTMLElement) {
  const frame = container.querySelector('iframe');
  if (!frame?.contentWindow) throw new Error('the chats frame is not mounted');
  const posts = vi.fn();
  frame.contentWindow.postMessage = posts as unknown as Window['postMessage'];
  const says = (message: Record<string, unknown>) => {
    act(() => {
      window.dispatchEvent(
        new MessageEvent('message', {
          data: plugin(message),
          origin: FRAME_ORIGIN,
          source: frame.contentWindow,
        }),
      );
    });
  };
  const opened = () =>
    posts.mock.calls
      .filter(([message]) => (message as { type?: string }).type === 'ar.open')
      .map(([message]) => (message as { agentId?: string }).agentId);
  return { says, opened };
}

/** The chat the frame reported showing: the only source of that fact. */
const observedChat = () => frameTrayStore.getState().chat.chats;

// The bell's popover keeps the rest of the page out of the accessibility tree while it is open, so
// the view the shell switched to and the launcher's own controls are read through the document.
function shownView(ui: ReturnType<typeof render>): string | null {
  return ui.container.querySelector('main.viewport')?.getAttribute('data-view') ?? null;
}

function viewTab(ui: ReturnType<typeof render>, name: string): HTMLElement {
  const bar = ui.container.querySelector<HTMLElement>("[aria-label='Views']");
  const tab = [...(bar?.querySelectorAll<HTMLElement>("[role='radio']") ?? [])].find(
    (item) => item.textContent === name,
  );
  if (!tab) throw new Error(`no ${name} tab`);
  return tab;
}

function roleSelect(ui: ReturnType<typeof render>): HTMLSelectElement {
  const select = ui.container.querySelector<HTMLSelectElement>("select[aria-label='Role']");
  if (!select) throw new Error('the launcher has no role select');
  return select;
}

/** The selection the chat frame is steered for: the seat's own role and document. */
function frameScope(ui: ReturnType<typeof render>): Record<string, unknown> {
  const frame = ui.container.querySelector<HTMLElement>("[data-testid='paseo-frame']");
  const scope = frame?.dataset.scope;
  if (!scope) throw new Error('the chat frame names no scope');
  return JSON.parse(scope) as Record<string, unknown>;
}

function clickAgentLink(ui: ReturnType<typeof render>, title: string) {
  openBell(ui);
  fireEvent.click(
    rowFor(ui.getByTestId('attention-queue'), title).querySelector(
      "[data-testid='attn-open-agent']",
    )!,
  );
}

describe('a bell agent row opens the exact agent chat in the real Cockpit (L190-R1-F09)', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    sessionStorage.clear();
  });

  // The shell as it boots on the Chats view: the pane's own chat (the architect's run) is on
  // screen, and the frame has said so.
  async function cockpitShowingArchitect() {
    const ui = render(<CockpitShell initialView="chats" />);
    await settle(10);
    const frame = chatsFrame(ui.container);
    frame.says({ type: 'ready', capabilities: CURRENT });
    await settle(10);
    expect(frame.opened()).toEqual([ARCHITECT.agentId]);
    frame.says({ type: 'shown', agentId: ARCHITECT.agentId });
    await settle(10);
    expect(observedChat()).toBe(ARCHITECT.agentId);
    // Operations: the rail with the bell, from the same shell.
    fireEvent.click(ui.getByRole('radio', { name: 'Operations' }));
    await settle(10);
    return { ui, frame };
  }

  it('opens a taskless seat through the Chats view, and the shown chat follows the frame', async () => {
    seed([tasklessHeld]);
    const fetchMock = stubDashboard();
    const { ui, frame } = await cockpitShowingArchitect();

    clickAgentLink(ui, tasklessHeld.title);
    await settle(10);

    // The shell switched to its Chats view, and the launcher and the chat frame are the seat's:
    // its own role, and — for a taskless seat — no document made up for it.
    expect(shownView(ui)).toBe('chats');
    expect(viewTab(ui, 'Chats').getAttribute('aria-checked')).toBe('true');
    expect(roleSelect(ui).value).toBe('worker');
    expect(frameScope(ui)).toEqual({ role: 'worker' });
    // The frame was asked for the row's exact agent, not the chat that was on screen before it.
    expect(frame.opened()).toEqual([ARCHITECT.agentId, tasklessHeld.agentId]);
    // The click is a request, not an observation: the chat on screen is still the frame's last
    // answer. Nothing launched either.
    expect(observedChat()).toBe(ARCHITECT.agentId);
    const asked = fetchMock.mock.calls.map(([url]) => String(url));
    expect(asked).not.toContain('/api/role-launch/dispatch');

    frame.says({ type: 'shown', agentId: tasklessHeld.agentId });
    await settle(10);
    expect(observedChat()).toBe(tasklessHeld.agentId);
  });

  it('opens a task-bound seat with its role and document, and the shown chat follows the frame', async () => {
    seed([taskHeld]);
    const fetchMock = stubDashboard();
    const { ui, frame } = await cockpitShowingArchitect();

    clickAgentLink(ui, taskHeld.title);
    await settle(10);

    expect(shownView(ui)).toBe('chats');
    expect(viewTab(ui, 'Chats').getAttribute('aria-checked')).toBe('true');
    expect(roleSelect(ui).value).toBe('worker');
    // The seat's document is the chat's own scope: this leaf, for this worker.
    expect(frameScope(ui)).toEqual({ role: 'worker', taskDocumentRef: tree.stalled.reference });
    expect(frame.opened()).toEqual([ARCHITECT.agentId, taskHeld.agentId]);
    expect(observedChat()).toBe(ARCHITECT.agentId);
    expect(fetchMock.mock.calls.map(([url]) => String(url))).not.toContain(
      '/api/role-launch/dispatch',
    );

    frame.says({ type: 'shown', agentId: taskHeld.agentId });
    await settle(10);
    expect(observedChat()).toBe(taskHeld.agentId);
  });
});

// MIK-R89 v2 rule 12 and evidence 14: the review page renders inside the shell's railed grid beside
// the one chat frame. The column opens closed for every review and is preloaded while closed; it
// shows the review's own task's chat, never the Projects architect's; leaving the page keeps the
// frame. The review surface is replaced by a stand-in that exposes what the shell hands it (its
// chat toggle and Back), so these cases read the shell alone.
import { act, cleanup, fireEvent, render, waitFor, type RenderResult } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { dashboardStore } from '../data/store';
import { chatLeaf, chatMaster, chatSprint } from '../test/fixtures/documentChat';
import { projection } from '../test/fixtures/wire';
import { CockpitShell } from './Cockpit';
import type { DocumentAgent } from './document-chat/model';
import { PICK_ABILITY } from './paseoFrameModel';

vi.mock('../panels/review/ReviewSurface', () => ({
  ReviewSurface: (props: {
    repo: string;
    master: string;
    leaf: string;
    onBack: () => void;
    chat?: { open: boolean; toggle: () => void };
  }) => (
    <div data-testid="review-surface" data-review-target={`${props.repo}/${props.master}/${props.leaf}`}>
      <button type="button" data-testid="review-back" onClick={props.onBack}>
        Back
      </button>
      <button
        type="button"
        data-testid="review-chat-toggle"
        aria-expanded={props.chat?.open}
        onClick={() => props.chat?.toggle()}
      >
        Chat
      </button>
    </div>
  ),
}));

const ORIGIN = 'https://host.example';
const FRAME = { available: true, frameBaseUrl: ORIGIN, serverId: 's', projectsWorkspaceId: 'wks_projects' };
// The leaf's review address as its task document's entry writes it: the master by its folder.
const REVIEW = '#review?repo=repo&master=master&leaf=LEAF';

const agent = (agentId: string, workspaceId: string, labels: Record<string, string>): DocumentAgent => ({
  agentId,
  workspaceId,
  archivedAt: null,
  createdAt: '2026-01-01',
  labels,
});
const ARCHITECT = [agent('a-arch', 'wks_projects', { 'ar.role': 'architect' })];
const WORKER = [agent('a-wrk', 'wks_leaf', { 'ar.role': 'worker', 'ar.task-ref': 'repo/master/leaf.json' })];
// The launcher's options for a task without a chat: one harness, no execution.
// Everything the dashboard uses of the plug-in, so it reports no missing ability.
const PLUGIN = ['open-agent', 'ping', 'page', 'shown-on-display', 'navigated', 'on-chat', 'load-failed', PICK_ABILITY];
const OPTIONS = {
  roleDefaults: { agent: 'codex' },
  agents: [{ id: 'codex', label: 'Codex', models: [] }],
  catalogOrigin: 'test',
  executions: [],
};

interface Call {
  path: string;
  body: Record<string, unknown> | null;
}

const EXPLAINER = {
  state: 'ok',
  seat: 'live',
  agentId: 'a-exp',
  workspaceId: 'wks_leaf',
  generation: 1,
};

function backend({
  frame = FRAME as unknown,
  worker = WORKER,
  explainer = EXPLAINER as unknown,
} = {}) {
  const calls: Call[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string, init?: RequestInit) => {
      const url = new URL(String(address), 'http://dashboard.test');
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null;
      calls.push({ path: url.pathname, body });
      let value: unknown = {};
      if (url.pathname === '/api/role-launch/frame') value = frame;
      else if (url.pathname === '/api/role-launch/options') value = OPTIONS;
      else if (url.pathname === '/api/role-launch/document-chats')
        value = { agents: body?.role === 'worker' ? worker : body?.role === 'architect' ? ARCHITECT : [] };
      else if (url.pathname === '/api/explainer/repo/master/LEAF/state') value = explainer;
      else if (url.pathname === '/api/task-document')
        value = dashboardStore.getState().tasks[url.searchParams.get('path') ?? '']?.summary ?? {};
      return new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
    }),
  );
  return {
    calls,
    count: (path: string) => calls.filter((call) => call.path === path).length,
    roles: () =>
      calls.filter((call) => call.path === '/api/role-launch/document-chats').map((call) => call.body?.role),
  };
}

function hydrate() {
  dashboardStore.getState().applySnapshot(projection({ tasks: [chatSprint, chatMaster, chatLeaf] }));
}

const frameOf = (view: RenderResult) => view.container.querySelector<HTMLIFrameElement>("iframe[title='Role chats']");
const generation = (view: RenderResult) =>
  view.container.querySelector('[data-testid="paseo-frame"]')?.getAttribute('data-generation') ?? null;
const chatColumn = (view: RenderResult) => view.container.querySelector<HTMLElement>('.rail--right')!;

// The application in the frame reports ready; its posted messages are recorded.
async function readyFrame(view: RenderResult) {
  await waitFor(() => expect(frameOf(view)).not.toBeNull());
  const frame = frameOf(view)!;
  const posts = vi.fn();
  if (frame.contentWindow) frame.contentWindow.postMessage = posts as unknown as Window['postMessage'];
  act(() =>
    window.dispatchEvent(
      new MessageEvent('message', {
        origin: ORIGIN,
        source: frame.contentWindow,
        data: { source: 'ar-plugin', type: 'ready' },
      }),
    ),
  );
  return { frame, posts };
}

const posted = (posts: ReturnType<typeof vi.fn>, type: string) =>
  posts.mock.calls
    .map(([message]) => message as { type: string; agentId?: string })
    .filter((message) => message.type === type);
const opens = (posts: ReturnType<typeof vi.fn>) => posted(posts, 'ar.open').map((message) => message.agentId);

// The rail's own frame, apart from the Chats page's frame (which comes first in the page).
const railFrame = (view: RenderResult) => chatColumn(view).querySelector<HTMLIFrameElement>("iframe[title='Role chats']");
const railGeneration = (view: RenderResult) =>
  chatColumn(view).querySelector('[data-testid="paseo-frame"]')?.getAttribute('data-generation') ?? null;

// A stand-in for the plug-in in the rail's frame. Like a Paseo client, which says hello to its host
// once per load, it reports ready (with what it can do) once for every load it is shown: a new
// element or a new generation. The dashboard's posts to it are recorded.
function railPlugin(view: RenderResult) {
  const posts = vi.fn();
  const answered = new WeakMap<HTMLIFrameElement, string | null>();
  let loads = 0;
  async function answer() {
    await waitFor(() => expect(railFrame(view)).not.toBeNull());
    const frame = railFrame(view)!;
    const loaded = railGeneration(view);
    if (answered.has(frame) && answered.get(frame) === loaded) return frame;
    answered.set(frame, loaded);
    loads += 1;
    if (frame.contentWindow) frame.contentWindow.postMessage = posts as unknown as Window['postMessage'];
    act(() =>
      window.dispatchEvent(
        new MessageEvent('message', {
          origin: ORIGIN,
          source: frame.contentWindow,
          data: { source: 'ar-plugin', type: 'ready', capabilities: PLUGIN },
        }),
      ),
    );
    return frame;
  }
  return { posts, answer, loads: () => loads, hellos: () => posted(posts, 'ar.hello').length };
}

const originalMedia = window.matchMedia;

beforeEach(() => vi.stubGlobal('CSS', { escape: (value: string) => value }));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.matchMedia = originalMedia;
  dashboardStore.getState().reset();
  window.localStorage.clear();
  window.history.replaceState(null, '', '/');
});

describe('the review page keeps the one chat frame (rule 12, evidence 14)', () => {
  it('walks Operations → task document → review → open column → Back on the same frame, loaded once', async () => {
    window.localStorage.setItem('cockpit.rail-chat', '1');
    hydrate();
    const routes = backend();
    const view = render(<CockpitShell initialView="operations" />);
    // The chat is shown on Operations: its frame is loaded and ready before the walk starts.
    const plugin = railPlugin(view);
    const frame = await plugin.answer();
    const loaded = railGeneration(view);
    expect(plugin.hellos()).toBe(1);

    fireEvent.click(await view.findByText('Leaf'));
    await waitFor(() => expect(opens(plugin.posts)).toContain('a-wrk'));
    const before = opens(plugin.posts).length;

    fireEvent.click(await view.findByTestId('open-intent-review'));
    await view.findByTestId('review-surface');
    await act(async () => {});
    await plugin.answer();
    const body = view.container.querySelector<HTMLElement>('.shell__body[data-page=review]')!;
    // The review is a slot of the railed grid just before the rail; the rail is the same element.
    expect([...body.children].map((node) => node.tagName)).toEqual(['ASIDE', 'MAIN', 'MAIN', 'ASIDE']);
    expect(chatColumn(view).style.display).toBe('none');
    expect(chatColumn(view).getAttribute('aria-hidden')).toBe('true');
    expect(body.style.gridTemplateColumns).toBe('minmax(0, 1fr) 0px');
    expect(body.style.columnGap).toBe('14px');
    // The column is the task's chat only: no River|Chat switch and no river on the review page.
    expect(view.queryByTestId('rail-toggle')).toBeNull();
    expect(view.queryByTestId('event-river')).toBeNull();
    expect(railFrame(view)).toBe(frame);

    fireEvent.click(view.getByTestId('review-chat-toggle'));
    expect(chatColumn(view).style.display).toBe('flex');
    expect(body.style.gridTemplateColumns).toBe('minmax(0, 1fr) 500px');
    expect(view.getByTestId('rail-chat-keepalive').getAttribute('aria-hidden')).toBe('false');
    await waitFor(() => expect(opens(plugin.posts)).toContain('a-exp'));
    await plugin.answer();
    // The review column selects the leaf's explainer in the same frame, without reloading it.
    expect(opens(plugin.posts)).toHaveLength(before + 1);
    expect(railFrame(view)).toBe(frame);
    expect(railGeneration(view)).toBe(loaded);

    fireEvent.click(view.getByTestId('review-back'));
    expect(view.queryByTestId('review-surface')).toBeNull();
    expect([...view.container.querySelector('.shell__body')!.children].map((node) => node.tagName)).toEqual([
      'ASIDE',
      'MAIN',
      'ASIDE',
    ]);
    expect(view.getByTestId('rail-toggle')).toBeTruthy();
    await act(async () => {});
    await plugin.answer();
    expect(railFrame(view)).toBe(frame);
    expect(railGeneration(view)).toBe(loaded);
    // Over the whole walk: one descriptor read, one load, one hello.
    expect(routes.count('/api/role-launch/frame')).toBe(1);
    expect(plugin.loads()).toBe(1);
    expect(plugin.hellos()).toBe(1);
  });

  it('walks from the Chats page over Operations with the river shown: the review preloads the rail frame once', async () => {
    hydrate();
    const routes = backend();
    // The dashboard opens on the Chats page, whose own frame reads its descriptor (MIK-R122 leaves it apart).
    const view = render(<CockpitShell />);
    await waitFor(() => expect(routes.count('/api/role-launch/frame')).toBe(1));
    fireEvent.click(view.getByRole('radio', { name: 'Operations' }));
    fireEvent.click(await view.findByText('Leaf'));
    await act(async () => {});
    // The river is shown: the rail's chat is not read and its frame not loaded yet.
    expect(railFrame(view)).toBeNull();
    expect(routes.count('/api/role-launch/frame')).toBe(1);

    fireEvent.click(await view.findByTestId('open-intent-review'));
    await view.findByTestId('review-surface');
    const plugin = railPlugin(view);
    const frame = await plugin.answer();
    const loaded = railGeneration(view);
    // The review page preloads the rail's frame: its one descriptor read, without an agent.
    expect(routes.count('/api/role-launch/frame')).toBe(2);
    expect(frame.getAttribute('src')).not.toContain('open=agent');

    fireEvent.click(view.getByTestId('review-back'));
    await act(async () => {});
    await plugin.answer();
    expect(railFrame(view)).toBe(frame);
    expect(railGeneration(view)).toBe(loaded);
    expect(routes.count('/api/role-launch/frame')).toBe(2);
    expect(plugin.loads()).toBe(1);
    expect(plugin.hellos()).toBe(1);
    // A page-wide first frame is the Chats page's, not the rail's.
    expect(view.container.querySelector('[data-testid="paseo-frame"]')).not.toBe(
      chatColumn(view).querySelector('[data-testid="paseo-frame"]'),
    );
  });

  it('preloads a cold review closed, reads nothing else, and opens the leaf’s own chat by one switch', async () => {
    window.history.replaceState(null, '', '/' + REVIEW);
    hydrate();
    const routes = backend();
    const view = render(<CockpitShell />);
    await view.findByTestId('review-surface');
    const { frame, posts } = await readyFrame(view);
    expect(chatColumn(view).style.display).toBe('none');
    expect(chatColumn(view).contains(frame)).toBe(true);
    // The preload loads the frame without an agent's address.
    expect(frame.getAttribute('src')).not.toContain('open=agent');
    await act(async () => {});
    expect(routes.count('/api/role-launch/frame')).toBe(1);
    for (const path of [
      '/api/role-launch/document-chats',
      '/api/role-launch/document-chats/tree',
      '/api/role-launch/warmup',
      '/api/role-launch/dispatch',
    ])
      expect(routes.count(path)).toBe(0);
    expect(opens(posts)).toEqual([]);

    fireEvent.click(view.getByTestId('review-chat-toggle'));
    await waitFor(() => expect(opens(posts)).toEqual(['a-exp']));
    expect(routes.count('/api/explainer/repo/master/LEAF/state')).toBe(1);
    expect(routes.count('/api/role-launch/document-chats')).toBe(0);
    expect(routes.roles()).not.toContain('architect');
    expect(frameOf(view)).toBe(frame);
    expect(generation(view)).toBe('1');
    expect(routes.count('/api/role-launch/frame')).toBe(1);
  });

  it('holds a review opened before the task list arrives and never binds the architect', async () => {
    window.history.replaceState(null, '', '/' + REVIEW);
    const routes = backend();
    const view = render(<CockpitShell />);
    await view.findByTestId('review-surface');
    const { posts } = await readyFrame(view);
    fireEvent.click(view.getByTestId('review-chat-toggle'));
    expect(await view.findByText("Waiting for the task list to name this review's task…")).toBeTruthy();
    await act(async () => {});
    expect(routes.count('/api/role-launch/document-chats')).toBe(0);
    expect(routes.count('/api/explainer/repo/master/LEAF/state')).toBe(0);
    expect(opens(posts)).toEqual([]);

    act(() => hydrate());
    await waitFor(() => expect(opens(posts)).toEqual(['a-exp']));
    expect(routes.roles()).not.toContain('architect');
  });

  it('names a review whose task the task list does not hold, and reads no chat for it', async () => {
    window.history.replaceState(null, '', '/#review?repo=repo&master=260928-MIK&leaf=LEAF');
    hydrate();
    const routes = backend();
    const view = render(<CockpitShell />);
    await view.findByTestId('review-surface');
    const { posts } = await readyFrame(view);
    fireEvent.click(view.getByTestId('review-chat-toggle'));
    expect(await view.findByText(/The task list holds no task LEAF in repo\/260928-MIK/)).toBeTruthy();
    await act(async () => {});
    expect(routes.count('/api/role-launch/document-chats')).toBe(0);
    expect(routes.count('/api/explainer/repo/260928-MIK/LEAF/state')).toBe(0);
    expect(opens(posts)).toEqual([]);
  });

  it('shows the bound launcher for a task without a chat and starts nothing', async () => {
    window.history.replaceState(null, '', '/' + REVIEW);
    hydrate();
    const routes = backend({ worker: [], explainer: { state: 'ok', seat: null } });
    const view = render(<CockpitShell />);
    await view.findByTestId('review-surface');
    await readyFrame(view);
    fireEvent.click(view.getByTestId('review-chat-toggle'));
    await waitFor(() =>
      expect(frameOf(view)!.closest("div[aria-hidden='true']")).not.toBeNull(),
    );
    expect(chatColumn(view).querySelector('[data-testid="explainer-placeholder"]')).not.toBeNull();
    expect(routes.count('/api/role-launch/dispatch')).toBe(0);
  });

  it('keeps the review usable when Paseo cannot be reached, with the frame’s notice and Retry', async () => {
    window.history.replaceState(null, '', '/' + REVIEW);
    hydrate();
    backend({ frame: { available: false, reason: 'unreachable', detail: 'connection refused' } });
    const view = render(<CockpitShell />);
    await view.findByTestId('review-surface');
    fireEvent.click(view.getByTestId('review-chat-toggle'));
    const notice = await view.findByTestId('paseo-frame-unavailable');
    expect(notice.dataset.reason).toBe('unreachable');
    expect(notice.querySelector('button')?.textContent).toBe('Retry');
    fireEvent.click(view.getByTestId('review-back'));
    expect(view.queryByTestId('review-surface')).toBeNull();
    expect(window.location.hash).toBe('');
  });

  it('opens closed again after a reload with it open, and stores nothing for the chat side', async () => {
    window.history.replaceState(null, '', '/' + REVIEW);
    hydrate();
    backend();
    const first = render(<CockpitShell />);
    fireEvent.click(await first.findByTestId('review-chat-toggle'));
    expect(chatColumn(first).style.display).toBe('flex');
    first.unmount();

    const second = render(<CockpitShell />);
    await second.findByTestId('review-surface');
    expect(chatColumn(second).style.display).toBe('none');
    expect(second.getByTestId('review-chat-toggle').getAttribute('aria-expanded')).toBe('false');
    expect(Object.keys(window.localStorage).filter((key) => key.includes('review') && !key.includes('chat-w'))).toEqual(
      [],
    );
  });
});

describe('the review chat column’s widths (rules 1 and 11, W26)', () => {
  function media(matching: string[]) {
    window.matchMedia = ((query: string) => ({
      matches: matching.includes(query),
      media: query,
      addEventListener() {},
      removeEventListener() {},
    })) as unknown as typeof window.matchMedia;
  }

  it('lies over the reading column below 1440 px as the same element, at its width class', async () => {
    media(['(max-width: 1600px)', '(max-width: 1439.98px)']);
    window.history.replaceState(null, '', '/' + REVIEW);
    hydrate();
    backend();
    const view = render(<CockpitShell />);
    await view.findByTestId('review-surface');
    const column = chatColumn(view);
    const body = view.container.querySelector<HTMLElement>('.shell__body')!;
    expect(body.style.columnGap).toBe('12px');
    fireEvent.click(view.getByTestId('review-chat-toggle'));
    expect(chatColumn(view)).toBe(column);
    expect(column.style.position).toBe('absolute');
    expect(column.style.width).toBe('420px');
    // The grid keeps the closed track: the reading column keeps its width under the overlay.
    expect(body.style.gridTemplateColumns).toBe('minmax(0, 1fr) 0px');
    // The column covers the review's own toggle, so it brings one at its edge.
    fireEvent.click(view.getByTestId('review-chat-close'));
    expect(column.style.display).toBe('none');
  });

  it('never makes the narrow page scroll sideways', async () => {
    media(['(max-width: 40rem)', '(max-width: 1600px)', '(max-width: 1439.98px)']);
    window.history.replaceState(null, '', '/' + REVIEW);
    hydrate();
    backend();
    const view = render(<CockpitShell />);
    await view.findByTestId('review-surface');
    fireEvent.click(view.getByTestId('review-chat-toggle'));
    const column = chatColumn(view);
    expect(column.style.position).toBe('fixed');
    expect(column.style.width).toBe('420px');
    expect(column.style.maxWidth).toBe('calc(100vw - 1.6rem)');
    expect(view.container.querySelector<HTMLElement>('.shell__body')!.style.gridTemplateColumns).toBe(
      'minmax(0, 1fr)',
    );
  });

  it('remembers a dragged width per width class', async () => {
    window.history.replaceState(null, '', '/' + REVIEW);
    hydrate();
    backend();
    const view = render(<CockpitShell />);
    await view.findByTestId('review-surface');
    fireEvent.click(view.getByTestId('review-chat-toggle'));
    fireEvent.keyDown(view.getByTestId('review-chat-resize'), { key: 'ArrowLeft' });
    expect(window.localStorage.getItem('cockpit.review.chat-w.wide')).toBe('524');
    expect(window.localStorage.getItem('cockpit.review.chat-w.narrow')).toBeNull();
    // The Operations rail's own width and handle are untouched and still named apart.
    expect(window.localStorage.getItem('cockpit.rail-right-w')).toBeNull();
    expect(view.getByTestId('rail-resize-left')).toBeTruthy();
  });
});

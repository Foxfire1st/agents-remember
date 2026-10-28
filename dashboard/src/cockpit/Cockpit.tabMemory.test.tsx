// D575: every bottom tab keeps the page it was last on and restores it on return. A review left
// open in Operations stays mounted and hidden while another tab is on screen, so returning shows
// the reader's page as it was left -- selection, visited invariants' revealed changes, scroll --
// with no request. Its address leaves the URL while hidden and comes back on return; an address the
// URL names wins over the remembered one. A remembered review whose leaf is gone falls back to the
// task document. The memory is the session's: a reload opens what the URL names, as today.
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { dashboardStore } from '../data/store';
import { resetCardCachesForTests } from '../data/reviewTrees';
import type { TaskEntry } from '../types/projection';
import { replyFor, target } from '../panels/review/l86.test-utils';
import { CockpitShell } from './Cockpit';

const address = (leaf: string) =>
  `#review?repo=${target.repo}&master=${target.master}&leaf=${leaf}&history=recorded`;
const HASH = address(target.leaf);

beforeEach(() => {
  resetCardCachesForTests();
  localStorage.clear();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  dashboardStore.getState().reset();
  window.history.replaceState(null, '', '/');
});

// The captured L37 review for every review read; anything else the shell asks is refused by name.
function serveReview() {
  const asked: URL[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (location: string) => {
      const url = new URL(location, window.location.origin);
      asked.push(url);
      if (url.pathname === '/api/files/repos') return new Response(JSON.stringify({ repos: [] }));
      try {
        return new Response(JSON.stringify(replyFor(url)), { status: 200 });
      } catch {
        return new Response(JSON.stringify({ detail: 'Not served here.' }), { status: 404 });
      }
    }),
  );
  return { reviewReads: () => asked.filter((url) => /^\/api\/(review|explainer)\//.test(url.pathname)).length };
}

async function openReview(hash = HASH) {
  window.history.replaceState(null, '', `/${hash}`);
  const server = serveReview();
  const view = render(<CockpitShell />);
  await view.findByTestId('review-tree');
  return { view, server };
}

const tab = (view: ReturnType<typeof render>, name: string) =>
  fireEvent.click(view.getByRole('radio', { name }));
const shownReview = (view: ReturnType<typeof render>) => {
  const surface = view.queryByTestId('review-surface');
  return surface !== null && surface.closest('main')?.style.display !== 'none' ? surface : null;
};

// Three invariants visited in the tree: each one's changed locations are revealed under its row.
async function visitThree(view: ReturnType<typeof render>) {
  const members = await waitFor(() => {
    const rows = view.getAllByTestId('review-family-member-open');
    expect(rows.length).toBeGreaterThanOrEqual(3);
    return rows;
  });
  const visited = members.slice(0, 3).map((row) => row.closest('[data-subject-key]')?.getAttribute('data-subject-key') ?? row.textContent);
  for (const index of [0, 1, 2]) {
    fireEvent.click(view.getAllByTestId('review-family-member-open')[index]);
    await act(async () => {});
  }
  await waitFor(() => expect(window.location.hash).toContain('sel='));
  return visited;
}

it('his path: three invariants visited, then Chats, Knowledge and Operations: the same page, with no request', async () => {
  const { view, server } = await openReview();
  await visitThree(view);
  const surface = view.getByTestId('review-surface');
  const tree = view.getByTestId('review-tree').innerHTML;
  const scroller = surface.querySelector<HTMLElement>('[data-review-scroller]')!;
  scroller.scrollTop = 240;
  const selected = window.location.hash;
  await act(async () => {});
  const reads = server.reviewReads();

  tab(view, 'Chats');
  expect(shownReview(view)).toBeNull();
  expect(window.location.hash).toBe('');
  tab(view, 'Knowledge');
  tab(view, 'Operations');
  await act(async () => {});

  expect(shownReview(view)).toBe(surface); // the same mounted page, never a new one
  expect(window.location.hash).toBe(selected); // its address, selection included, is back
  expect(view.getByTestId('review-tree').innerHTML).toBe(tree); // the revealed changes stay
  expect(scroller.scrollTop).toBe(240);
  expect(server.reviewReads()).toBe(reads);
});

it('each tab holds its own page: Knowledge after Operations is Knowledge, not the review', async () => {
  const { view } = await openReview();
  await visitThree(view);
  tab(view, 'Knowledge');
  const knowledge = view.container.querySelector('main[data-view="knowledge"]') ?? view.container;
  const page = knowledge.innerHTML;
  tab(view, 'Operations');
  expect(shownReview(view)).not.toBeNull();
  tab(view, 'Knowledge');
  expect(shownReview(view)).toBeNull();
  expect(knowledge.innerHTML).toBe(page);
  expect(view.getByRole('radio', { name: 'Knowledge' }).getAttribute('aria-checked')).toBe('true');
});

it('the URL wins: an address it names is opened, and the remembered review fills in only when it names none', async () => {
  const { view } = await openReview();
  const remembered = view.getByTestId('review-surface').dataset.reviewTarget;
  tab(view, 'Chats');
  // Named by the URL (a link, Back): that review opens, not the remembered one.
  const other = address('260928-MIK-L38');
  act(() => {
    window.history.pushState(null, '', other);
    window.dispatchEvent(new PopStateEvent('popstate'));
  });
  await waitFor(() =>
    expect(shownReview(view)?.dataset.reviewTarget).toBe(`${target.repo}/${target.master}/260928-MIK-L38`),
  );
  expect(window.location.hash).toBe(other);
  // Named by nothing: leaving and returning restores the review on screen when it was left.
  tab(view, 'Chats');
  tab(view, 'Operations');
  expect(shownReview(view)?.dataset.reviewTarget).not.toBe(remembered);
  expect(window.location.hash).toBe(other);
});

function holdTask(leaf: string | null) {
  const tasks: Record<string, TaskEntry> = {};
  if (leaf !== null)
    tasks[leaf] = {
      id: leaf,
      key: leaf,
      title: 'The leaf',
      children: [],
      counts: { planned: 0, started: 0, completed: 0, aborted: 0, works: 0, paused: 0, unknown: 0 },
      stage: 'started',
      stepsDone: 0,
      stepsTotal: 0,
      worktrees: [],
      reference: { repository: target.repo, path: `${target.master}/37_the-leaf.json` },
    };
  act(() => dashboardStore.setState({ tasks, generatedAt: '2026-10-26T12:00:00Z' }));
}

it.each([
  ['the leaf is still in the task list', target.leaf, true],
  ['the leaf is gone from the task list', null, false],
] as const)('a remembered review comes back when %s, and falls back to the task document otherwise', async (_case, leaf, restored) => {
  const { view } = await openReview();
  tab(view, 'Chats');
  holdTask(leaf);
  tab(view, 'Operations');
  await act(async () => {});
  if (restored) {
    expect(shownReview(view)).not.toBeNull();
    expect(window.location.hash).toBe(HASH);
  } else {
    expect(view.queryByTestId('review-surface')).toBeNull();
    expect(view.getByTestId('detail-panel')).toBeTruthy();
    expect(window.location.hash).toBe('');
  }
});

it('a hidden review answers no key pressed in the tab on screen', async () => {
  const { view } = await openReview();
  tab(view, 'Chats');
  fireEvent.keyDown(document.body, { key: '?' });
  expect(view.queryByRole('dialog', { name: 'Reviewer keys' })).toBeNull();
  tab(view, 'Operations');
  fireEvent.keyDown(document.body, { key: '?' });
  await waitFor(() => expect(view.getByRole('dialog', { name: 'Reviewer keys' })).toBeTruthy());
});

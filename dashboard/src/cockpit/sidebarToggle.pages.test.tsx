// MIK-R89 v2 rule 8 and evidence 12: one shared toggle hides and shows every sidebar of every page.
// Each page is walked the same way: hide the sidebar, the toggle stays focusable, the main column
// takes the width, the choice is stored per page and side, and a new mount (a reload) restores it.
// The review page's two sides are walked in reviewChatColumn.test.tsx (chat) and by the review
// surface's own tests (tree).
import { act, cleanup, fireEvent, render, waitFor, type RenderResult } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { dashboardStore } from '../data/store';
import { readerHash } from '../data/knowledgeReader';
import { GALLERY } from '../dev/fixtures';
import { sidebarKey, type SidebarPage, type SidebarSide } from '../grammar/SidebarToggle';
import { ChangeSetViewer } from '../panels/changeset/ChangeSetViewer';
import { FileViewer } from '../panels/file-viewer/FileViewer';
import { KnowledgeReader } from '../panels/knowledge-reader/KnowledgeReader';
import { NotesReaderViewer } from '../panels/notes-reader/NotesReaderViewer';
import { CockpitShell } from './Cockpit';

// The diff column is CodeMirror; the walk needs only the layout around it.
vi.mock('../panels/changeset/ChangeSetPane', () => ({
  ChangeSetPane: ({ diff }: { diff: { path: string } }) => <div data-testid="changeset-pane">{diff.path}</div>,
}));

const CHANGESET = {
  scope: 'wt-a',
  code: [{ path: 'a.ts', insertions: 1, deletions: 0, status: 'M' }],
  memory: [],
  counters: {
    code: { files: 1, insertions: 1, deletions: 0 },
    memory: { files: 0, insertions: 0, deletions: 0 },
  },
};

function answer(url: string): { status: number; body: unknown } {
  if (url.startsWith('/api/files/repos'))
    return { status: 200, body: { repos: [{ repo: 'repo-a', mainline: { label: 'main' }, worktrees: [] }] } };
  if (url.startsWith('/api/changeset/task')) return { status: 200, body: CHANGESET };
  if (url.startsWith('/api/knowledge/')) return { status: 200, body: { state: 'refused', detail: 'not served here' } };
  return { status: 404, body: null };
}

beforeEach(() => {
  vi.stubGlobal('CSS', { escape: (value: string) => value });
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      const { status, body } = answer(String(url));
      return new Response(JSON.stringify(body), { status });
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  dashboardStore.getState().reset();
  window.localStorage.clear();
  window.location.hash = '';
});

interface PageWalk {
  name: string;
  page: SidebarPage;
  side: SidebarSide;
  mount: () => RenderResult;
  // The sidebar element the toggle hides, once the page has drawn it.
  sidebar: (view: RenderResult) => HTMLElement;
  // The main column takes the hidden sidebar's width.
  mainTakesWidth: (view: RenderResult, sidebar: HTMLElement) => void;
}

// A resizable page: the sidebar panel and its handle leave the row, so the main panel is the one
// shown panel of its group and its flex share fills the row.
function panelGroupFilled(_view: RenderResult, sidebar: HTMLElement) {
  const group = sidebar.parentElement!;
  const shown = [...group.children].filter((child) => (child as HTMLElement).style.display !== 'none');
  expect(shown).toHaveLength(1);
  expect(shown[0].hasAttribute('data-panel')).toBe(true);
}

function operations(side: SidebarSide, columns: string): PageWalk {
  return {
    name: `Operations ${side}`,
    page: 'operations',
    side,
    mount: () => {
      dashboardStore.getState().applySnapshot(GALLERY.find((entry) => entry.name === 'engine-fleet')!.projection);
      return render(<CockpitShell initialView="operations" />);
    },
    sidebar: (view) => view.container.querySelector<HTMLElement>(side === 'left' ? '.rail--left' : '.rail--right')!,
    mainTakesWidth: (view) =>
      expect(view.container.querySelector<HTMLElement>('.shell__body')!.style.gridTemplateColumns).toBe(columns),
  };
}

function knowledge(side: SidebarSide): PageWalk {
  return {
    name: `Knowledge ${side}`,
    page: 'knowledge',
    side,
    mount: () => {
      window.location.hash = readerHash({ repo: 'repo-a', commit: 'published', view: 'path', path: '.' });
      return render(<KnowledgeReader />);
    },
    sidebar: (view) =>
      view.container.querySelector<HTMLElement>(side === 'left' ? '[data-reader-tree]' : '[data-reader-aside]')!,
    mainTakesWidth: panelGroupFilled,
  };
}

const PAGES: PageWalk[] = [
  operations('left', 'minmax(380px, 1fr) 300px'),
  operations('right', '340px minmax(380px, 1fr)'),
  knowledge('left'),
  knowledge('right'),
  {
    name: 'File Viewer',
    page: 'files',
    side: 'left',
    mount: () => render(<FileViewer />),
    sidebar: (view) => view.container.querySelector<HTMLElement>('[data-panel-group] > [data-panel]')!,
    mainTakesWidth: panelGroupFilled,
  },
  {
    name: 'change-set page',
    page: 'changeset',
    side: 'left',
    mount: () => render(<ChangeSetViewer repo="repo-a" scope="wt-a" onBack={() => {}} />),
    sidebar: (view) => view.container.querySelector<HTMLElement>('[data-panel-group] > [data-panel]')!,
    mainTakesWidth: panelGroupFilled,
  },
  {
    name: 'notes reader',
    page: 'notes',
    side: 'left',
    mount: () =>
      render(
        <NotesReaderViewer kind="notes" repo="repo-a" master="ops" folder onSelectNote={() => {}} onBack={() => {}} />,
      ),
    sidebar: (view) => view.container.querySelector<HTMLElement>('[data-panel-group] > [data-panel]')!,
    mainTakesWidth: panelGroupFilled,
  },
];

async function drawn(walk: PageWalk, view: RenderResult): Promise<{ toggle: HTMLElement; sidebar: HTMLElement }> {
  const toggle = await view.findByTestId(`sidebar-toggle-${walk.page}-${walk.side}`);
  await waitFor(() => expect(walk.sidebar(view)).toBeTruthy());
  return { toggle, sidebar: walk.sidebar(view) };
}

describe('every sidebar has the one shared toggle (MIK-R89 v2 rule 8)', () => {
  it.each(PAGES)('$name: hides, stays reachable, gives its width, is remembered', async (walk) => {
    const first = walk.mount();
    const { toggle, sidebar } = await drawn(walk, first);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(sidebar.style.display).not.toBe('none');

    fireEvent.click(toggle);
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(sidebar.style.display).toBe('none');
    expect(sidebar.getAttribute('aria-hidden')).toBe('true');
    // The toggle is not inside what it hides: it stays in reach at the main column's edge.
    expect(sidebar.contains(toggle)).toBe(false);
    act(() => toggle.focus());
    expect(document.activeElement).toBe(toggle);
    walk.mainTakesWidth(first, sidebar);
    expect(window.localStorage.getItem(sidebarKey(walk.page, walk.side))).toBe('0');
    first.unmount();
    if (walk.page === 'knowledge') window.location.hash = '';

    // A reload restores the hidden side, and the toggle shows it again.
    const second = walk.mount();
    const again = await drawn(walk, second);
    expect(again.toggle.getAttribute('aria-expanded')).toBe('false');
    expect(again.sidebar.style.display).toBe('none');
    fireEvent.click(again.toggle);
    expect(again.sidebar.style.display).not.toBe('none');
    expect(again.sidebar.getAttribute('aria-hidden')).not.toBe('true');
    expect(window.localStorage.getItem(sidebarKey(walk.page, walk.side))).toBe('1');
  });

  it('draws no toggle where a page has no sidebar: the notes reader without a rail', () => {
    const view = render(
      <NotesReaderViewer kind="decisions" repo="repo-a" master="ops" title="Decisions" decisions={[]} onSelectNote={() => {}} onBack={() => {}} />,
    );
    expect(view.getByTestId('notes-reader-viewer')).toBeTruthy();
    expect(view.queryByTestId('sidebar-toggle-notes-left')).toBeNull();
  });
});

import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { createRef } from 'react';
import { ReaderOutline } from './ReaderOutline';
import { ReaderToolbar } from './ReaderToolbar';
import { TruthView } from './TruthView';
import { PathView } from './PathViews';
import { ReaderNavContext } from './readerParts';
import type { PathViewAnswer, RecordViewAnswer, ReaderAddress } from '../../data/knowledgeReader';

const address: ReaderAddress = { repo: 'demo', commit: 'published', view: 'path', path: '.' };
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it('resolves a typed memory branch before navigating and keeps the prior selection on invalid input', async () => {
  const go = vi.fn();
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string) => {
      const url = new URL(input, 'http://localhost');
      const commit = url.searchParams.get('commit');
      const body = url.pathname.endsWith('/selections')
        ? { state: 'options', commits: [] }
        : commit === 'ar/older'
          ? { state: 'view', selection: { repo: 'demo', commit: 'a'.repeat(40) } }
          : {
              state: 'invalid-request',
              detail: 'Use a full or abbreviated commit name or a branch name.',
            };
      return { json: async () => body };
    }),
  );
  const view = render(
    <ReaderToolbar
      address={address}
      repos={['demo']}
      go={go}
      browsing={false}
      onBrowse={() => {}}
      records={{ answer: null, failure: null, load: vi.fn() }}
    />,
  );
  const input = view.getByRole('textbox', { name: 'Commit or branch' });
  fireEvent.change(input, { target: { value: 'ar/older' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  await waitFor(() => expect(go).toHaveBeenCalledWith({ ...address, commit: 'a'.repeat(40) }));
  go.mockClear();
  for (const invalid of ['missing', 'a\x00b']) {
    fireEvent.change(input, { target: { value: invalid } });
    fireEvent.click(view.getByRole('button', { name: 'Choose tree' }));
    expect((await view.findByRole('alert')).textContent).toContain('full or abbreviated');
    expect(go).not.toHaveBeenCalled();
    expect((view.getByRole('combobox', { name: 'Memory tree' }) as HTMLSelectElement).value).toBe(
      'published',
    );
  }
});

it('names truncated summary prose, loads its whole text and explains omitted deeper families', async () => {
  const answer: PathViewAnswer = {
    state: 'view',
    kind: 'directory',
    path: '.',
    selection: { repo: 'demo', commit: 'published', treeKey: 'old-tree' },
    testFile: false,
    prose: {
      state: 'present',
      path: 'onboarding/overview.md',
      text: '# Summary\nCut text',
      truncated: true,
      fullSize: 90000,
    },
    references: { state: 'present', path: 'onboarding/overview.json', items: [] },
    invariants: [],
    families: [],
    records: [],
    currentness: {},
    deeperEntries: 3,
  };
  const fetch = vi.fn(async (input: string) => {
    expect(input).toContain('/prose?');
    return {
      json: async () => ({
        state: 'present',
        selection: answer.selection,
        text: '# Summary\nWhole prose end',
      }),
    };
  });
  vi.stubGlobal('fetch', fetch);
  const nav = {
    repo: 'demo',
    commit: 'published',
    go: vi.fn(),
    openCode: vi.fn(),
    openReference: vi.fn(),
  };
  const view = render(
    <ReaderNavContext.Provider value={nav}>
      <PathView answer={answer} />
    </ReaderNavContext.Provider>,
  );
  expect(view.getByText(/truncated.*90000 bytes/).textContent).toContain('truncated');
  expect(
    view.getByText("Families of invariants in deeper folders are listed in those folders' views."),
  ).toBeTruthy();
  const whole = view.getByRole('button', { name: 'Load whole text' });
  fireEvent.click(whole);
  fireEvent.click(whole);
  expect(await view.findByText('Whole prose end')).toBeTruthy();
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(String(fetch.mock.calls[0]?.[0])).toContain('/prose?');
  expect(view.queryByRole('button', { name: 'Load whole text' })).toBeNull();
});

async function rejectChangedProse(commit: string) {
  const answer: PathViewAnswer = {
    state: 'view',
    kind: 'directory',
    path: '.',
    testFile: false,
    selection: { repo: 'demo', commit, treeKey: 'old-tree' },
    prose: {
      state: 'present',
      path: 'onboarding/overview.md',
      text: 'OLD PROSE [1]',
      truncated: true,
      fullSize: 90000,
    },
    references: {
      state: 'present',
      path: 'onboarding/overview.json',
      items: [
        {
          number: '1',
          note: 'OLD REFERENCE',
          targets: [{ kind: 'invariant', id: 'INV-OLD001' }],
        },
      ],
    },
    invariants: [],
    families: [],
    records: [],
    currentness: {},
  };
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({
      json: async () => ({
        state: 'present',
        selection: { ...answer.selection, treeKey: 'new-tree' },
        path: answer.prose.path,
        text: 'NEW PROSE [1]',
      }),
    })),
  );
  const nav = { repo: 'demo', commit, go: vi.fn(), openCode: vi.fn(), openReference: vi.fn() };
  const view = render(
    <ReaderNavContext.Provider value={nav}>
      <PathView answer={answer} />
    </ReaderNavContext.Provider>,
  );
  fireEvent.click(view.getByRole('button', { name: 'Load whole text' }));
  expect((await view.findByRole('alert')).textContent).toContain('Reload the whole view');
  expect(view.getByRole('button', { name: 'Reload whole view' })).toBeTruthy();
  expect(view.queryByText('NEW PROSE')).toBeNull();
  expect(view.getByText('OLD REFERENCE')).toBeTruthy();
  expect(view.getByText(/OLD PROSE/)).toBeTruthy();
}

it.each(['published', 'leaf:active'])('rejects changed prose at %s', rejectChangedProse);

async function keepRecordSnapshot(commit: string) {
  const event = {
    source: 'history' as const,
    commit: null,
    date: null,
    owner: 'leaf',
    attempt: 2,
    path: 'knowledge/history/leaf-attempt-2.json',
    disposition: 'changed',
    row: 'ROW-OLD001',
    document: {
      subject: 'FAM-ABC123',
      because: [{ id: 'MIK-R67', version: 'v2', packet: 'outside' }],
    },
  };
  const answer: RecordViewAnswer = {
    state: 'view',
    selection: { repo: 'demo', commit, treeKey: 'old-tree', memoryRevision: 'old-revision' },
    record: {
      id: 'FAM-ABC123',
      kind: 'family',
      path: 'family.json',
      document: { guarantee: 'OLD GUARANTEE' },
    },
    prose: { path: 'family.md', state: 'absent' },
    outgoing: [],
    incoming: [],
    timeline: { sources: {}, events: [event], continuation: 'kc2.older' },
  };
  let mismatch = false;
  let changed = false;
  const fetch = vi.fn(async (input: string) => {
    const url = new URL(input, 'http://localhost');
    const timeline = !url.searchParams.get('continuation')
      ? answer.timeline
      : mismatch
        ? {
            state: 'refused',
            code: 'continuation_binding_mismatch',
            detail: 'The memory tree changed.',
            sources: {},
            events: [],
          }
        : {
            sources: {},
            events: [{ source: 'record', commit: 'a'.repeat(40), date: null, change: 'added' }],
            continuation: 'kc2.oldest',
          };
    return {
      json: async () =>
        changed
          ? {
              ...answer,
              selection: {
                ...answer.selection,
                treeKey: 'new-tree',
                memoryRevision: 'new-revision',
              },
              record: { ...answer.record, document: { guarantee: 'NEW GUARANTEE' } },
              timeline: {
                sources: {},
                events: [{ ...event, document: { reason: 'NEW HISTORY' } }],
              },
            }
          : { ...answer, timeline },
    };
  });
  vi.stubGlobal('fetch', fetch);
  const nav = {
    repo: 'demo',
    commit,
    go: vi.fn(),
    openCode: vi.fn(),
    openReference: vi.fn(),
  };
  const view = render(
    <ReaderNavContext.Provider value={nav}>
      <TruthView answer={answer} />
    </ReaderNavContext.Provider>,
  );
  expect(view.getByText(/attempt 2.*knowledge\/history\/leaf-attempt-2.json/)).toBeTruthy();
  expect(
    view.getByText('requirement MIK-R67 (a task document; not part of this memory repository)'),
  ).toBeTruthy();
  expect(
    view.getByText('This row was written before the effect label became required.'),
  ).toBeTruthy();
  expect(view.getByText('effect not recorded')).toBeTruthy();
  fireEvent.click(view.getByRole('button', { name: 'Show older' }));
  await waitFor(() => expect(view.getAllByTestId('timeline-event')).toHaveLength(2));
  mismatch = true;
  fireEvent.click(view.getByRole('button', { name: 'Show older' }));
  expect((await view.findByRole('alert')).textContent).toContain('memory tree changed');
  fireEvent.click(view.getByRole('button', { name: 'Load first page' }));
  await waitFor(() => expect(view.getAllByTestId('timeline-event')).toHaveLength(1));
  fireEvent.click(view.getByRole('button', { name: 'Show older' }));
  await view.findByRole('alert');
  changed = true;
  fireEvent.click(view.getByRole('button', { name: 'Load first page' }));
  await waitFor(() =>
    expect(view.getByRole('alert').textContent).toContain('Reload the whole view'),
  );
  expect(view.getByRole('button', { name: 'Reload whole view' })).toBeTruthy();
  expect(view.queryByText('NEW HISTORY')).toBeNull();
  expect(view.queryByText('NEW GUARANTEE')).toBeNull();
  expect(view.getByTestId('record-lead').textContent).toBe('OLD GUARANTEE');
  expect(view.getAllByTestId('timeline-event')).toHaveLength(1);
}

it.each(['published', 'leaf:active'])('keeps the %s timeline snapshot', keepRecordSnapshot);

it.each(['prose', 'timeline'])('drops late %s side loads after navigation', async (kind) => {
  const selection = { repo: 'demo', commit: 'published', treeKey: 'same-tree' };
  const path: PathViewAnswer = {
    state: 'view',
    selection,
    kind: 'directory',
    path: '.',
    testFile: false,
    prose: { state: 'present', path: 'overview.md', text: 'INITIAL PROSE', truncated: true },
    references: { state: 'present', path: 'overview.json', items: [] },
    invariants: [],
    families: [],
    records: [],
    currentness: {},
  };
  const record: RecordViewAnswer = {
    state: 'view',
    selection,
    record: { id: 'FAM-ABC123', path: 'family.json', document: {} },
    prose: { state: 'absent', path: 'family.md' },
    outgoing: [],
    incoming: [],
    timeline: { sources: {}, events: [], continuation: 'kc2.older' },
  };
  let release!: (value: unknown) => void;
  vi.stubGlobal(
    'fetch',
    vi.fn(() => new Promise((resolve) => (release = resolve))),
  );
  const nav = {
    repo: 'demo',
    commit: 'published',
    go: vi.fn(),
    openCode: vi.fn(),
    openReference: vi.fn(),
  };
  const show = (place: string) => (
    <ReaderNavContext.Provider value={{ ...nav, commit: place }}>
      {kind === 'prose' ? (
        <PathView key={place} answer={path} />
      ) : (
        <TruthView key={place} answer={record} />
      )}
    </ReaderNavContext.Provider>
  );
  const view = render(show('published'));
  fireEvent.click(
    view.getByRole('button', { name: kind === 'prose' ? 'Load whole text' : 'Show older' }),
  );
  view.rerender(show('leaf:active'));
  await act(async () => {
    release({
      json: async () =>
        kind === 'prose'
          ? { ...path.prose, selection, text: 'LATE PROSE' }
          : {
              ...record,
              timeline: {
                sources: {},
                events: [{ source: 'record', commit: null, date: null, change: 'added' }],
              },
            },
    });
  });
  expect(view.queryByText('LATE PROSE')).toBeNull();
  expect(view.queryAllByTestId('timeline-event')).toHaveLength(0);
  expect(view.queryByRole('alert')).toBeNull();
  expect(
    view
      .getByRole('button', { name: kind === 'prose' ? 'Load whole text' : 'Show older' })
      .hasAttribute('disabled'),
  ).toBe(false);
});

// INV-KXT50F: 'On this page' lists the chapters the pane renders. Whole prose replaces the summary
// in place, so the chapters it adds must reach the outline without a navigation or a remount.
it('lists the chapters the whole text renders beside the chapters the summary showed', async () => {
  const answer: PathViewAnswer = {
    state: 'view',
    kind: 'directory',
    path: '.',
    selection: { repo: 'demo', commit: 'published', treeKey: 'old-tree' },
    testFile: false,
    prose: {
      state: 'present',
      path: 'onboarding/overview.md',
      text: '# Summary\n## Summary chapter\nCut text',
      truncated: true,
      fullSize: 90000,
    },
    references: { state: 'present', path: 'onboarding/overview.json', items: [] },
    invariants: [],
    families: [],
    records: [],
    currentness: {},
    deeperEntries: 0,
  };
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({
      json: async () => ({
        state: 'present',
        selection: answer.selection,
        text: '# Summary\n## Summary chapter\nWhole prose end\n## Whole chapter\nMore whole text',
      }),
    })),
  );
  const nav = {
    repo: 'demo',
    commit: 'published',
    go: vi.fn(),
    openCode: vi.fn(),
    openReference: vi.fn(),
  };
  const pane = createRef<HTMLDivElement>();
  const view = render(
    <ReaderNavContext.Provider value={nav}>
      <div ref={pane} data-testid="knowledge-view">
        <PathView answer={answer} />
      </div>
      <ReaderOutline pane={pane} ready="ready-1" />
    </ReaderNavContext.Provider>,
  );
  const outline = view.getByTestId('reader-outline');
  await waitFor(() => expect(outline.textContent).toContain('Summary chapter'));
  fireEvent.click(view.getByRole('button', { name: 'Load whole text' }));
  expect(await view.findByText('More whole text')).toBeTruthy();
  await waitFor(() => expect(outline.textContent).toContain('Whole chapter'));
  expect(outline.textContent).toContain('Summary chapter');
});

// The same promise for the other direction: a ready pane that renders no chapter must clear the
// previous page's list instead of keeping it. ReaderOutline keeps its own chapter state.
it('clears the previous page chapters when the next ready pane renders none', async () => {
  const pane = createRef<HTMLDivElement>();
  const page = (ready: string, hasChapter: boolean) => (
    <>
      <div ref={pane}>
        {hasChapter ? (
          <h2 id="previous-chapter">Previous chapter</h2>
        ) : (
          <p>Plain text without chapters</p>
        )}
      </div>
      <ReaderOutline pane={pane} ready={ready} />
    </>
  );
  const view = render(page('first-ready', true));
  const outline = view.getByTestId('reader-outline');
  await waitFor(() => expect(outline.querySelectorAll('button')).toHaveLength(1));
  view.rerender(page('next-ready', false));
  expect(view.queryByRole('heading')).toBeNull();
  await waitFor(() => expect(outline.querySelectorAll('button')).toHaveLength(0));
});

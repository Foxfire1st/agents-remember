// MIK-R29 on real data: the Knowledge reader's views over the REAL served answers of
// /api/knowledge/reader for a converted scratch copy of the real repositories (provenance: the
// `_provenance` key of knowledgeReader.captured.json). The memory was converted by L24's
// knowledge-convert; the decisions, incident, family routes, history rows, proof and census on top
// of it are SCRATCH-AUTHORED (their text says so), because converted memory holds none yet.
// `KnowledgeReader` is the real component; only `fetch` is stubbed.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { parseReaderHash, readerHash, type ReaderAddress } from '../../data/knowledgeReader';
import { KnowledgeReader } from './KnowledgeReader';

type Body = Record<string, unknown> & { state: string };
const captured = JSON.parse(
  readFileSync(
    path.join(path.dirname(new URL(import.meta.url).pathname), 'knowledgeReader.captured.json'),
    'utf8',
  ),
) as Record<string, Body>;

const REPO = 'agents-remember';
const WORKTREES = 'mcp/src/agents_remember/worktrees';
const INTEGRATE = `${WORKTREES}/modules/integrate.py`;
const LEDGER_TEST = 'mcp/tests/test_memory_ledger.py';

let requests: URL[] = [];
let overrides: Record<string, Body> = {};
// Tree listings held back until the test releases them (the stale-response test, review F13).
let heldTree: { commit: string; waiting: (() => void)[] } | null = null;

function bodyFor(url: URL): Body | null {
  const view = url.pathname.split('/').pop() ?? '';
  const key = `${view}:${url.searchParams.get('path') ?? url.searchParams.get('id') ?? ''}`;
  if (url.searchParams.get('continuation') && overrides[`${view}:next`]) {
    return overrides[`${view}:next`];
  }
  if (overrides[key]) return overrides[key];
  const pathBodies: Record<string, string> = {
    '.': 'path-root',
    [WORKTREES]: 'path-worktrees-dir',
    [INTEGRATE]: 'path-integrate-file',
    [LEDGER_TEST]: 'path-test-file',
  };
  const recordBodies: Record<string, string> = {
    'INV-N213W04A': 'record-invariant',
    'FAM-QWVGDSYX': 'record-family',
    'DEC-R29DEC': 'record-decision',
    'DEC-R29AAA': 'record-decision-superseded',
    'INC-R29NC1': 'record-incident',
  };
  const name = {
    selections: 'selections',
    records: 'records',
    census: 'census',
    code: 'code-integrate',
    'without-proof': 'without-proof-worktrees',
    subtree: 'subtree-worktrees',
    tree: { '': 'tree-root', [WORKTREES]: 'tree-worktrees' }[url.searchParams.get('path') ?? ''],
    path: pathBodies[url.searchParams.get('path') ?? ''],
    record: recordBodies[url.searchParams.get('id') ?? ''],
  }[view];
  return name ? captured[name] : null;
}

beforeEach(() => {
  requests = [];
  overrides = {};
  heldTree = null;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string) => {
      const url = new URL(address, 'http://localhost');
      requests.push(url);
      if (url.pathname === '/api/files/repos') {
        return {
          ok: true,
          status: 200,
          json: async () => ({ repos: [{ repo: REPO }] }),
        } as Response;
      }
      const body =
        bodyFor(url) ??
        (url.pathname.endsWith('/tree')
          ? {
              state: 'view',
              directory: url.searchParams.get('path'),
              code: { state: 'listed' },
              children: [],
            }
          : null);
      if (body === null) throw new Error(`unexpected reader request: ${address}`);
      if (
        heldTree &&
        url.pathname.endsWith('/tree') &&
        url.searchParams.get('commit') === heldTree.commit
      ) {
        const held = heldTree;
        await new Promise<void>((release) => held.waiting.push(release));
      }
      return { ok: true, status: 200, json: async () => body } as Response;
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.location.hash = '';
});

function open(address: Omit<ReaderAddress, 'repo' | 'commit'> & { commit?: string }) {
  window.location.hash = readerHash({ repo: REPO, commit: 'published', ...address });
  return render(<KnowledgeReader />);
}

it('opens a file with its prose, resolved references, entries, families and linked records', async () => {
  const view = open({ view: 'path', path: INTEGRATE });
  const pathView = await view.findByTestId('reader-path-view');

  // [n] markers link to their references; a reference with several targets lists them all.
  const markers = within(pathView).getAllByTestId('reference-marker');
  expect(markers.map((one) => one.dataset.reference)).toEqual(['1', '2', '3', '4', '5', '6']);
  const second = within(pathView)
    .getAllByTestId('reader-reference')
    .find((one) => one.dataset.reference === '2')!;
  expect(within(second).getAllByTestId('reference-target')).toHaveLength(3);
  fireEvent.click(markers[1]);
  await waitFor(() => expect(second.className).not.toBe(''));

  const invariant = within(pathView).getByTestId('reader-invariant');
  expect(invariant.dataset.invariant).toBe('INV-N213W04A');
  expect(within(invariant).getByTestId('invariant-state').dataset.state).toBe('current');
  const entry = within(invariant).getByTestId('reader-entry');
  expect(entry.dataset.entry).toBe('RLZ-97DY0D4C');
  expect(entry.dataset.state).toBe('current');

  const family = within(pathView).getByTestId('reader-family');
  expect(family.dataset.family).toBe('FAM-QWVGDSYX');
  expect(family.textContent).toContain(`via route ${WORKTREES}`);
  expect(within(family).getByTestId('family-elsewhere').textContent).toContain(
    'mcp/src/agents_remember/application/curator_source_manifest.py',
  );

  const decision = within(pathView).getByTestId('decision-card');
  expect(decision.dataset.decision).toBe('DEC-R29DEC');
  expect(within(decision).getByTestId('decision-status').dataset.state).toBe('active');
  expect(within(decision).getByTestId('reconsider-when').textContent).toContain(
    'families rarely span subtrees',
  );
  const linked = within(pathView).getAllByTestId('reader-linked-record');
  expect(linked.map((one) => one.dataset.record)).toEqual(['DEC-R29DEC', 'INC-R29NC1']);
  expect(view.getByTestId('reader-selection').dataset.indexState).toBe('complete');
});

it('opens a directory with its overview, routed families elsewhere and the route decisions', async () => {
  const view = open({ view: 'path', path: WORKTREES });
  const pathView = await view.findByTestId('reader-path-view');

  expect(pathView.dataset.kind).toBe('directory');
  expect(within(pathView).getByTestId('reader-prose').textContent).toContain('worktrees');
  const routed = within(pathView)
    .getAllByTestId('reader-family')
    .find((one) => one.dataset.family === 'FAM-QWVGDSYX')!;
  expect(routed.textContent).toContain(`via route ${WORKTREES}`);
  const decisions = within(pathView).getAllByTestId('decision-card');
  expect(
    decisions.map((one) => [
      one.dataset.decision,
      within(one).getByTestId('decision-status').dataset.state,
    ]),
  ).toEqual([
    ['DEC-R29AAA', 'superseded'],
    ['DEC-R29DEC', 'active'],
  ]);
  expect(within(decisions[0]).getByTestId('superseded-by').textContent).toContain('DEC-R29DEC');

  // Bounded (review F2): the children holding knowledge, and the paged list of every entry.
  const children = within(pathView).getAllByTestId('directory-child');
  expect(children.map((one) => one.dataset.path)).toEqual([
    `${WORKTREES}/integration`,
    `${WORKTREES}/ledger_projection.py`,
    `${WORKTREES}/modules`,
  ]);
  fireEvent.click(within(pathView).getByTestId('open-subtree'));
  await waitFor(() => expect(window.location.hash).toContain('view=subtree'));
  const subtree = await view.findByTestId('reader-subtree');
  expect(within(subtree).getAllByTestId('subtree-row')).toHaveLength(9);
  expect(within(subtree).queryByTestId('subtree-more')).toBeNull();
});

it('lands on the bounded root summary and follows a subtree page to the next', async () => {
  const view = open({ view: 'path', path: '.' });
  const root = await view.findByTestId('reader-path-view');
  expect(root.textContent).toContain('Repository summary');
  expect(
    within(root)
      .getAllByTestId('directory-child')
      .map((one) => one.dataset.path),
  ).toEqual(['dashboard', 'mcp', 'scripts', 'skills']);
  cleanup();

  const first = captured['subtree-worktrees'] as Body & { rows: unknown[]; page: object };
  overrides[`subtree:${WORKTREES}`] = {
    ...first,
    rows: first.rows.slice(0, 4),
    page: { ...first.page, rowsOnPage: 4, returned: 4, remaining: 5 },
    continuation: 'kc2.next',
  };
  const walk = open({ view: 'subtree', path: WORKTREES });
  const pane = await walk.findByTestId('reader-subtree');
  expect(within(pane).getAllByTestId('subtree-row')).toHaveLength(4);
  delete overrides[`subtree:${WORKTREES}`];
  overrides['subtree:next'] = { ...first, rows: first.rows.slice(4) };
  // A double click asks for the next page once, and adds it once (R2 note).
  const more = within(pane).getByTestId('subtree-more');
  fireEvent.click(more);
  fireEvent.click(more);
  await waitFor(() => expect(within(pane).getAllByTestId('subtree-row')).toHaveLength(9));
  expect(
    requests.filter((url) => url.searchParams.get('continuation') === 'kc2.next'),
  ).toHaveLength(1);
});

it('shows a test file its proofs by invariant with their facets', async () => {
  const view = open({ view: 'path', path: LEDGER_TEST });
  const pathView = await view.findByTestId('reader-path-view');
  expect(within(pathView).getByText('Proofs by invariant')).toBeTruthy();
  expect(within(pathView).getByTestId('reader-facet').textContent).toContain(
    'the ledger read answers from committed trailers',
  );
});

it('opens an invariant truth view with its states, links and a three-source timeline', async () => {
  const view = open({ view: 'record', id: 'INV-N213W04A' });
  const truth = await view.findByTestId('reader-truth-view');

  expect(within(truth).getByTestId('record-state').dataset.state).toBe('current');
  const fields = within(truth).getByTestId('record-fields');
  for (const name of [
    'statement',
    'applicability',
    'conditions',
    'exclusions',
    'status',
    'admission',
  ]) {
    expect(fields.querySelector(`[data-field="${name}"]`)).not.toBeNull();
  }
  expect(
    within(within(truth).getByTestId('invariant-proofs')).getByTestId('reader-entry').dataset.entry,
  ).toBe('PRF-R29PRF');
  expect(within(truth).getByTestId('invariant-linked').textContent).toContain('DEC-R29DEC');

  const events = within(truth).getAllByTestId('timeline-event');
  expect(new Set(events.map((one) => one.dataset.source))).toEqual(
    new Set(['record', 'history', 'entries']),
  );
  expect(events[0].dataset.source).toBe('record');
  expect(within(events[0]).getByTestId('meaning-diff').textContent).toContain('SCRATCH revision');
  // A new locator in the same file is a re-anchor, not a move (review F5).
  expect(events.some((one) => one.dataset.change === 're-anchored')).toBe(true);
});

it('opens a family, a decision and an incident with every field and their links', async () => {
  const family = open({ view: 'record', id: 'FAM-QWVGDSYX' });
  const familyView = await family.findByTestId('reader-truth-view');
  expect(within(familyView).getByTestId('family-routes').textContent).toContain(WORKTREES);
  expect(within(familyView).getByTestId('family-members').textContent).toContain('INV-103S55GN');
  expect(within(familyView).getByTestId('family-locations').textContent).toContain(INTEGRATE);
  cleanup();

  const decision = open({ view: 'record', id: 'DEC-R29DEC' });
  const card = await decision.findByTestId('decision-card');
  const alternatives = within(card).getByTestId('decision-alternatives').querySelectorAll('li');
  expect([...alternatives].map((one) => one.dataset.alternative)).toEqual(['chosen', 'rejected']);
  expect(card.textContent).toContain('supersedes');
  expect(card.textContent).toContain('DEC-R29AAA');
  cleanup();

  const incident = open({ view: 'record', id: 'INC-R29NC1' });
  const truth = await incident.findByTestId('reader-truth-view');
  const fields = within(truth).getByTestId('record-fields');
  for (const name of ['cause', 'cause_uncertainty', 'recovery', 'corrective_actions']) {
    expect(fields.querySelector(`[data-field="${name}"]`)).not.toBeNull();
  }
  const outgoing = within(truth).getByTestId('record-outgoing').querySelectorAll('li');
  expect([...outgoing].map((one) => one.dataset.relation)).toEqual(['violated', 'occurred_at']);
});

it('shows the census, the without-proof list and code opened at its symbol', async () => {
  overrides['census:'] = {
    ...captured.census,
    censuses: [
      {
        ...(captured.census.censuses as Record<string, unknown>[])[0],
        routes: [
          {
            route: WORKTREES,
            status: 'migrated',
            history: [
              { status: 'in_progress', provenance: { at: '2026-09-29T08:00:00+02:00' } },
              { status: 'migrated', provenance: { at: '2026-09-29T09:00:00+02:00' } },
            ],
          },
        ],
      },
    ],
  };
  const census = open({ view: 'census' });
  const report = await census.findByTestId('reader-census');
  expect(within(report).getAllByTestId('census-measure').length).toBeGreaterThan(3);
  expect(within(report).getByTestId('census-route').textContent).toContain(
    'in_progress @ 2026-09-29T08:00:00+02:00 → migrated',
  );
  cleanup();

  const list = open({ view: 'without-proof', path: WORKTREES });
  const rows = await list.findAllByTestId('without-proof-row');
  expect(rows.map((one) => within(one).getByTestId('record-link').dataset.record)).toEqual([
    'INV-453ZPCZ5',
    'INV-VXA7SZ0Z',
  ]);
  cleanup();

  const code = open({
    view: 'code',
    path: INTEGRATE,
    locator: JSON.stringify({ kind: 'symbol', name: 'validate_integrate_memory_contract' }),
  });
  const pane = await code.findByTestId('reader-code');
  expect(pane.dataset.locator).toBe('resolved');
  const lines = within(pane).getByTestId('reader-code-lines');
  expect(lines.querySelector('[data-line="193"]')?.textContent).toContain(
    'def validate_integrate_memory_contract',
  );
});

it('navigates by URL: explorer and links change the shareable hash, and the hash round-trips', async () => {
  const view = open({ view: 'path', path: INTEGRATE });
  await view.findByTestId('reader-path-view');
  const tree = await view.findByTestId('knowledge-tree');
  await waitFor(() => expect(within(tree).getAllByTestId('tree-node').length).toBeGreaterThan(3));
  expect(within(tree).getByText('mcp').parentElement?.textContent).toContain('115');

  fireEvent.click(within(view.getByTestId('reader-family')).getByTestId('record-link'));
  await waitFor(() => expect(window.location.hash).toContain('id=FAM-QWVGDSYX'));
  expect(parseReaderHash(window.location.hash)).toEqual({
    repo: REPO,
    commit: 'published',
    view: 'record',
    id: 'FAM-QWVGDSYX',
  });
  await view.findByTestId('family-routes');

  const address: ReaderAddress = {
    repo: REPO,
    commit: 'abc123',
    view: 'without-proof',
    path: 'mcp',
  };
  expect(parseReaderHash(readerHash(address))).toEqual(address);
  expect(parseReaderHash('#other')).toBeNull();
});

it('names a partial index, unavailable prose, unverifiable states and an unconverted tree', async () => {
  const file = captured['path-integrate-file'];
  overrides[`path:${INTEGRATE}`] = {
    ...file,
    selection: {
      ...(file.selection as Record<string, unknown>),
      indexState: 'partial',
      problems: [{ path: 'knowledge/invariants/INV-BR0KEN-x.json', detail: 'invalid JSON' }],
    },
    prose: {
      path: `onboarding/${INTEGRATE}.md`,
      state: 'unavailable',
      detail: 'git cat-file failed',
    },
    currentness: { unverifiableReason: 'no code tree was requested' },
  };
  const view = open({ view: 'path', path: INTEGRATE });
  const partial = await view.findByTestId('reader-partial');
  expect(partial.textContent).toContain('INV-BR0KEN-x.json');
  expect(view.getByTestId('reader-unavailable').textContent).toContain('git cat-file failed');
  expect(view.getByTestId('reader-unverifiable').textContent).toContain('no code tree');
  cleanup();

  overrides[`path:${WORKTREES}`] = {
    state: 'not-converted',
    selection: { repo: REPO, commit: 'f94bc4107' },
    detail: 'memory commit f94bc4107 holds no knowledge/layout.json',
  };
  const old = open({ view: 'path', path: WORKTREES, commit: 'f94bc4107' });
  expect((await old.findByTestId('reader-not-converted')).textContent).toContain('layout.json');
  expect(requests.some((url) => url.searchParams.get('commit') === 'f94bc4107')).toBe(true);
});

it('links [n] markers in prose text only, never inside code spans or fences', async () => {
  const file = captured['path-integrate-file'];
  overrides[`path:${INTEGRATE}`] = {
    ...file,
    prose: {
      path: `onboarding/${INTEGRATE}.md`,
      state: 'present',
      text: 'Reads `reports[0]` first [1], then [2].\n\n```python\ncandidates[3]\n```\n',
    },
  };
  const view = open({ view: 'path', path: INTEGRATE });
  const prose = await view.findByTestId('reader-prose');
  const markers = within(prose).getAllByTestId('reference-marker');
  expect(markers.map((one) => one.dataset.reference)).toEqual(['1', '2']);
  const code = [...prose.querySelectorAll('code')].map((one) => one.textContent);
  expect(code).toEqual(['reports[0]', 'candidates[3]\n']);
});

it('marks the located code lines, and names a timeline source that could not be read', async () => {
  const code = open({
    view: 'code',
    path: INTEGRATE,
    locator: JSON.stringify({ kind: 'symbol', name: 'validate_integrate_memory_contract' }),
  });
  const lines = await code.findByTestId('reader-code-lines');
  const located = [...lines.querySelectorAll('[data-located="true"]')].map((one) =>
    Number((one as HTMLElement).dataset.line),
  );
  expect([located[0], located[located.length - 1], located.length]).toEqual([193, 213, 21]);
  cleanup();

  const invariant = captured['record-invariant'] as Body & { timeline: Record<string, unknown> };
  overrides['record:INV-N213W04A'] = {
    ...invariant,
    timeline: {
      ...invariant.timeline,
      sources: {
        record: { state: 'read', events: 2 },
        history: { state: 'read', events: 1 },
        entries: { state: 'unavailable', detail: 'ReaderReadError: git log timed out' },
      },
    },
  };
  const truth = open({ view: 'record', id: 'INV-N213W04A' });
  const failed = await truth.findByTestId('timeline-source-unavailable');
  expect(failed.dataset.source).toBe('entries');
  expect(failed.textContent).toContain('git log timed out');
});

it('heads a superseded decision with its derived status', async () => {
  const view = open({ view: 'record', id: 'DEC-R29AAA' });
  const status = await view.findByTestId('record-status');
  expect(status.textContent).toContain('superseded');
});

it('names side reads that failed, pins a clean published view, and drops stale explorer answers', async () => {
  overrides['selections:'] = {
    ...captured.selections,
    commits: [],
    commitsState: { state: 'unavailable', detail: 'git log failed' },
  };
  const view = open({ view: 'path', path: INTEGRATE });
  expect((await view.findByTestId('reader-side-failure')).textContent).toContain(
    'memory commits unavailable: git log failed',
  );
  const pin = await view.findByTestId('reader-pin');
  expect(pin.textContent).toContain('1e3c8b1ef');

  // The listing asked for the pinned commit is held; the tree switches back before it answers.
  heldTree = { commit: '1e3c8b1ef3e35fd416dbc48ba02d3ef0252a3621', waiting: [] };
  fireEvent.click(pin);
  await waitFor(() => expect(window.location.hash).toContain('commit=1e3c8b1ef'));
  await waitFor(() =>
    expect(
      requests.some(
        (url) =>
          url.searchParams.get('commit') === heldTree?.commit && url.pathname.endsWith('/tree'),
      ),
    ).toBe(true),
  );
  overrides['tree:'] = { state: 'view', directory: '.', code: { state: 'listed' }, children: [] };
  fireEvent.change(view.getByTestId('reader-commit'), { target: { value: 'published' } });
  await waitFor(() => expect(window.location.hash).toContain('commit=published'));
  // The pinned commit's listings answer now, after the switch back: they must not land.
  const late = heldTree.waiting.length;
  heldTree.waiting.forEach((release) => release());
  await new Promise((settle) => setTimeout(settle, 20));
  expect(late).toBeGreaterThan(0);
  expect(within(view.getByTestId('knowledge-tree')).queryAllByTestId('tree-node')).toHaveLength(0);
});

it('names record links that could not be read instead of claiming there are none', async () => {
  overrides['record:INC-R29NC1'] = {
    ...captured['record-incident'],
    outgoing: [],
    outgoingState: { state: 'unavailable', detail: 'the record does not validate' },
  };
  const view = open({ view: 'record', id: 'INC-R29NC1' });
  const message = await view.findByTestId('record-outgoing-unavailable');
  expect(message.textContent).toContain('the record does not validate');
  expect(
    within(view.getByTestId('record-outgoing')).queryByText(/records no outgoing link/),
  ).toBeNull();
});

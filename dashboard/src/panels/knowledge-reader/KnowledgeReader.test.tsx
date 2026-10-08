// MIK-R29 on real data: the Knowledge reader's views over the REAL served answers of
// /api/knowledge/reader for a converted scratch copy of the real repositories (provenance: the
// `_provenance` key of knowledgeReader.captured.json). The memory was converted by L24's
// knowledge-convert; the decisions, incident, family routes, history rows, proof and census on top
// of it are SCRATCH-AUTHORED (their text says so), because converted memory holds none yet.
// `KnowledgeReader` is the real component; only `fetch` is stubbed.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { act, cleanup, fireEvent, render, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { EditorView } from '@codemirror/view';

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
      let body =
        bodyFor(url) ??
        (url.pathname.endsWith('/tree')
          ? {
              state: 'view',
              directory: url.searchParams.get('path'),
              code: { state: 'listed' },
              children: [],
            }
          : null);
      if (body && url.pathname.endsWith('/tree') && Array.isArray(body.children))
        body = {
          ...body,
          children: (body.children as Record<string, unknown>[]).map((child) => ({
            ...child,
            hasKnowledge: Boolean(child.onboarding || child.entries),
          })),
        };
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
  expect(
    (await view.findByTestId('reader-citation-pane')).querySelectorAll(
      '[data-testid=reference-target]',
    ),
  ).toHaveLength(3);

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
  expect(within(pathView).queryByTestId('reader-directory-children')).toBeNull();
  fireEvent.click(within(pathView).getByTestId('open-subtree'));
  await waitFor(() => expect(window.location.hash).toContain('view=subtree'));
  const subtree = await view.findByTestId('reader-subtree');
  expect(within(subtree).getAllByTestId('subtree-row')).toHaveLength(9);
  expect(within(subtree).queryByTestId('subtree-more')).toBeNull();
});

it('lands on the bounded root summary and follows a subtree page to the next', async () => {
  const view = open({ view: 'path', path: '.' });
  const root = await view.findByTestId('reader-path-view');
  expect(root.querySelector('h1')?.textContent).toBeTruthy();
  expect(within(root).queryByTestId('reader-directory-children')).toBeNull();
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
  expect(within(pathView).getByText(/Proofs by invariant/)).toBeTruthy();
  expect(within(pathView).getByTestId('reader-facet').textContent).toContain(
    'the ledger read answers from committed trailers',
  );
});

it('opens an invariant truth view with its states, links and a three-source timeline', async () => {
  const view = open({ view: 'record', id: 'INV-N213W04A' });
  const truth = await view.findByTestId('reader-truth-view');

  expect(within(truth).getByTestId('record-state').dataset.state).toBe('current');
  const fields = within(truth).getByTestId('record-fields');
  expect(within(truth).getByTestId('record-lead').textContent).toBeTruthy();
  for (const name of ['applicability', 'conditions', 'exclusions']) {
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
  expect(
    (
      await waitFor(() => {
        const line = lines.querySelector('[data-line="193"]');
        expect(line).not.toBeNull();
        return line;
      })
    )?.textContent,
  ).toContain('def validate_integrate_memory_contract');
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
  await waitFor(() =>
    expect(lines.querySelectorAll('[data-located="true"]').length).toBeGreaterThan(0),
  );
  const editor = EditorView.findFromDOM(lines.querySelector<HTMLElement>('.cm-editor')!)!;
  const located: number[] = [];
  for (const decorations of editor.state.facet(EditorView.decorations)) {
    if (typeof decorations === 'function') continue;
    decorations.between(0, editor.state.doc.length, (from, _to, decoration) => {
      if (decoration.spec.attributes?.['data-located'] === 'true') {
        const line = editor.state.doc.lineAt(from).number;
        expect(Number(decoration.spec.attributes['data-line'])).toBe(line);
        located.push(line);
      }
    });
  }
  expect(located).toEqual(Array.from({ length: 21 }, (_, index) => 193 + index));
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

it('shows a history row with its effect and decision, and a decision with the rows it caused', async () => {
  type Event = Record<string, unknown> & { source: string; document: Record<string, unknown> };
  const invariant = captured['record-invariant'] as Body & {
    timeline: Record<string, unknown> & { events: Event[] };
  };
  const historyLine = async (view: ReturnType<typeof open>) =>
    (await view.findAllByTestId('timeline-event')).find((one) => one.dataset.source === 'history')!;

  // A row with neither an effect nor a because reads as it always did.
  const plain = await historyLine(open({ view: 'record', id: 'INV-N213W04A' }));
  expect(within(plain).queryByTestId('history-effect')).toBeNull();
  expect(within(plain).queryByTestId('history-because')).toBeNull();
  expect(plain.lastElementChild?.textContent).toBe(
    "260928-MIK-L29 no_impact (open)SCRATCH: the reader reads this invariant's code and never writes it.",
  );
  cleanup();

  const requirement = {
    task: { repository: REPO, path: '260928_maintained-invariant-knowledge' },
    packet: 'requirements/MIK-R12-v2-curator-writer-for-all-knowledge-kinds.md',
    id: 'MIK-R12',
    version: 'v2',
  };
  overrides['record:INV-N213W04A'] = {
    ...invariant,
    timeline: {
      ...invariant.timeline,
      events: invariant.timeline.events.map((event) =>
        event.source === 'history'
          ? {
              ...event,
              disposition: 'changed',
              document: {
                ...event.document,
                effect: 'replace',
                because: [requirement, 'DEC-R29DEC'],
              },
            }
          : event,
      ),
    },
  };
  const changed = await historyLine(open({ view: 'record', id: 'INV-N213W04A' }));
  expect(changed.lastElementChild?.textContent).toContain(
    '260928-MIK-L29 changed · replace (open)',
  );
  const because = within(changed).getByTestId('history-because');
  expect(because.textContent).toContain('requirement MIK-R12@v2');
  const decision = within(because).getByTestId('record-link');
  expect(decision.dataset.record).toBe('DEC-R29DEC');
  fireEvent.click(decision);
  await waitFor(() => expect(parseReaderHash(window.location.hash)?.id).toBe('DEC-R29DEC'));
  cleanup();

  // The other direction: the decision's incoming link from that row names the row's subject and
  // leads to its page. A row served without its subject stays the plain row ID.
  const link = {
    source: 'ROW-R29AA2',
    sourceKind: 'history_row',
    relation: 'because',
    targetKind: 'record',
    target: 'DEC-R29DEC',
    detail: { id: 'DEC-R29DEC' },
    originPath: 'knowledge/history/260928-MIK-L29.json',
  };
  const subject = { id: 'INV-N213W04A', kind: 'invariant', title: 'SCRATCH subject' };
  overrides['record:DEC-R29DEC'] = {
    ...captured['record-decision'],
    incoming: [
      { ...link, sourceSubject: subject },
      { ...link, source: 'ROW-R29AA9' },
    ],
  };
  const page = open({ view: 'record', id: 'DEC-R29DEC' });
  const [named, unnamed] = [
    ...(await page.findByTestId('record-incoming')).querySelectorAll('li'),
  ] as HTMLElement[];
  expect(named.textContent).toContain('INV-N213W04A · SCRATCH subject row ROW-R29AA2 because');
  expect(within(unnamed).queryByTestId('record-link')).toBeNull();
  expect(unnamed.textContent).toContain('ROW-R29AA9 because (history_row');
  fireEvent.click(within(named).getByTestId('record-link'));
  await waitFor(() => expect(parseReaderHash(window.location.hash)?.id).toBe('INV-N213W04A'));
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
  await act(async () => { heldTree!.waiting.forEach((release) => release()); });
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

it('reads prose before records and references, skips empty sections, and leads a record with its meaning', async () => {
  const view = open({ view: 'path', path: INTEGRATE });
  const article = await view.findByTestId('reader-path-view');
  const order = [
    'reader-prose',
    'reader-invariants',
    'reader-families',
    'reader-linked-records',
    'reader-references-section',
  ];
  for (let index = 1; index < order.length; index++)
    expect(
      within(article)
        .getByTestId(order[index - 1])
        .compareDocumentPosition(within(article).getByTestId(order[index])) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  expect(within(article).queryByTestId('reader-directory-children')).toBeNull();
  cleanup();
  overrides[`path:${INTEGRATE}`] = {
    ...captured['path-integrate-file'],
    invariants: [],
    families: [],
    records: [],
    references: { state: 'present', items: [] },
  };
  const empty = open({ view: 'path', path: INTEGRATE });
  await empty.findByTestId('reader-path-view');
  for (const section of [
    'reader-invariants',
    'reader-families',
    'reader-linked-records',
    'reader-references-section',
  ])
    expect(empty.queryByTestId(section)).toBeNull();
  cleanup();
  const truth = open({ view: 'record', id: 'INV-N213W04A' });
  const lead = await truth.findByTestId('record-lead');
  expect(
    lead.compareDocumentPosition(truth.getByTestId('record-fields')) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(
    truth
      .getByTestId('record-timeline')
      .compareDocumentPosition(truth.getByTestId('record-origin')) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  const added = truth
    .getAllByTestId('timeline-event')
    .find((row) => row.dataset.source === 'record' && row.dataset.change === 'added');
  expect(added).toBeDefined();
  expect(added!.textContent).toContain('record added');
  expect(added!.querySelector('[data-testid=meaning-diff]')).toBeNull();
});

it('opens links at the top and restores the visit on browser Back', async () => {
  const view = open({ view: 'path', path: INTEGRATE });
  await view.findByTestId('reader-path-view');
  const pane = view.getByTestId('knowledge-view');
  pane.scrollTop = 1200;
  fireEvent.scroll(pane);
  fireEvent.click(within(view.getByTestId('reader-family')).getByTestId('record-link'));
  await view.findByTestId('reader-truth-view');
  expect(pane.scrollTop).toBe(0);
  fireEvent.click(view.getByRole('button', { name: 'Back' }));
  await view.findByTestId('reader-path-view');
  expect(pane.scrollTop).toBe(1200);
});

it('opens wide citations beside unchanged prose, and phone citations as a page with Browse navigation', async () => {
  const view = open({ view: 'path', path: INTEGRATE });
  const article = await view.findByTestId('reader-path-view');
  const pane = view.getByTestId('knowledge-view');
  const marker = within(article)
    .getAllByTestId('reference-marker')
    .find((row) => row.dataset.reference === '1')!;
  pane.scrollTop = 180;
  fireEvent.scroll(pane);
  fireEvent.click(marker);
  await view.findByTestId('reader-citation-pane');
  expect(pane.scrollTop).toBe(180);
  expect(parseReaderHash(window.location.hash)?.view).toBe('path');
  expect(view.getByTestId('reader-path-view')).toBe(article);
  cleanup();
  vi.stubGlobal('matchMedia', () => ({
    matches: true,
    addEventListener() {},
    removeEventListener() {},
  }));
  const phone = open({ view: 'path', path: INTEGRATE });
  await phone.findByTestId('reader-path-view');
  fireEvent.click(phone.getByRole('button', { name: 'Browse' }));
  expect(phone.getByTestId('knowledge-reader').querySelector('[data-browsing=true]')).toBeTruthy();
  fireEvent.click(phone.getByRole('button', { name: 'Document' }));
  expect(phone.getByTestId('knowledge-reader').querySelector('[data-browsing=true]')).toBeNull();
  fireEvent.click(
    phone.getAllByTestId('reference-marker').find((row) => row.dataset.reference === '1')!,
  );
  const targets = await phone.findByTestId('reader-citation-pane');
  fireEvent.click(within(targets).getAllByTestId('code-link')[0]);
  await phone.findByTestId('reader-code');
  expect(parseReaderHash(window.location.hash)?.view).toBe('code');
});

it('retains the last visible position when a phone chooser reports a hidden-pane zero', async () => {
  vi.stubGlobal('matchMedia', () => ({
    matches: true,
    addEventListener() {},
    removeEventListener() {},
  }));
  const view = open({ view: 'path', path: INTEGRATE });
  await view.findByTestId('reader-path-view');
  const pane = view.getByTestId('knowledge-view');
  pane.scrollTop = 690;
  fireEvent.scroll(pane);
  fireEvent.click(view.getAllByTestId('reference-marker')[0]);
  const picker = await view.findByTestId('reader-citation-pane');
  const panel = pane.closest<HTMLElement>('[data-reader-document]')!;
  panel.style.display = 'none'; // Chromium reads zero from this CSS-hidden panel.
  pane.scrollTop = 0;
  fireEvent.scroll(pane);
  fireEvent.click(within(picker).getAllByTestId('code-link')[0]);
  await view.findByTestId('reader-code');
  panel.style.display = '';
  fireEvent.click(view.getByRole('button', { name: 'Back' }));
  await view.findByTestId('reader-path-view');
  expect(pane.scrollTop).toBe(690);
});

it('names an unreadable reference list instead of presenting its empty result as zero', async () => {
  overrides[`path:${INTEGRATE}`] = {
    ...captured['path-integrate-file'],
    references: { state: 'unavailable', detail: 'references denied', items: [] },
  };
  const view = open({ view: 'path', path: INTEGRATE });
  const facts = await view.findByTestId('reader-facts');
  expect(facts.textContent).toContain('references unavailable');
  expect(facts.textContent).not.toContain('0 references');
  expect(view.getByTestId('reader-unavailable').textContent).toContain('references denied');
  expect(view.queryAllByTestId('reference-marker')).toHaveLength(0);
});

it('keeps unsupported text links in place, opens external links separately, and navigates mapped cards', async () => {
  const original = captured['path-integrate-file'];
  overrides[`path:${INTEGRATE}`] = {
    ...original,
    prose: { path: `onboarding/${INTEGRATE}.md`, state: 'present', text: `# Links\n\n[unmapped directory](../../../../agents-remember/mcp) [external page](https://example.com/) [mapped card](/onboarding/${LEDGER_TEST}.md)` },
  };
  const view = open({ view: 'path', path: INTEGRATE });
  const prose = await view.findByTestId('reader-prose');
  const pane = view.getByTestId('knowledge-view');
  pane.scrollTop = 1250;
  fireEvent.scroll(pane);
  const hash = window.location.hash;
  fireEvent.click(within(prose).getByTestId('prose-unresolved-link'));
  expect(window.location.hash).toBe(hash);
  expect(pane.scrollTop).toBe(1250);
  expect(within(prose).getByTestId('prose-unresolved-link').title).toContain('not an onboarding card or overview');
  const external = within(prose).getByRole('link', { name: 'external page' });
  expect(external.getAttribute('target')).toBe('_blank');
  expect(external.getAttribute('rel')).toContain('noopener');
  fireEvent.click(within(prose).getByRole('button', { name: 'mapped card' }));
  await waitFor(() => expect(parseReaderHash(window.location.hash)?.path).toBe(LEDGER_TEST));
});

it('preserves a reference Anchor note when its introductory sentence is already in prose', async () => {
  const original = captured['path-integrate-file'];
  overrides[`path:${INTEGRATE}`] = {
    ...original,
    prose: { path: `onboarding/${INTEGRATE}.md`, state: 'present', text: '# Reference\n\nShared sentence. [1]' },
    references: { state: 'present', items: [{ number: '1', note: 'Shared sentence.\n\nAnchor: exact retained anchor text', targets: [] }] },
  };
  const view = open({ view: 'path', path: INTEGRATE });
  const reference = await view.findByTestId('reader-reference');
  expect(reference.textContent).toContain('Anchor: exact retained anchor text');
  expect(reference.textContent).not.toContain('Shared sentence.');
});

it('shows the selected leaf code-source note and names empty record lists', async () => {
  const original = captured['path-integrate-file'];
  overrides[`path:${INTEGRATE}`] = {
    ...original,
    selection: { ...(original.selection as Record<string, unknown>), kind: 'leaf', codeNote: 'HEAD of the leaf code checkout; uncommitted code is not included.' },
  };
  const path = open({ view: 'path', path: INTEGRATE });
  expect((await path.findByTestId('reader-code-note')).textContent).toContain('uncommitted code is not included');
  cleanup();
  const record = open({ view: 'record', id: 'INV-N213W04A' });
  const fields = await record.findByTestId('record-fields');
  expect(fields.querySelector('[data-field=conditions]')?.textContent).toContain('(none)');
});

it('keeps paths and Records usable when a side count is unavailable', async () => {
  overrides['census:'] = { state: 'unavailable', detail: 'census denied' };
  overrides['without-proof:'] = { state: 'unavailable', detail: 'proof list denied' };
  const view = open({ view: 'path', path: '.' });
  const tree = await view.findByTestId('knowledge-tree');
  await waitFor(() => {
    expect(tree.querySelector('[data-record-row=census]')?.textContent).toContain('census denied');
    expect(tree.querySelector('[data-record-row=without-proof]')?.textContent).toContain('proof list denied');
  });
  expect(within(tree).getByRole('treeitem', { name: /Records/ })).toBeTruthy();
  await waitFor(() => expect(tree.querySelectorAll('[data-testid=tree-node]').length).toBeGreaterThan(0));
});

it('refreshes an already loaded collapsed branch when Show every path changes', async () => {
  overrides['tree:'] = { state: 'view', code: { state: 'listed' }, children: [{ name: 'dashboard', path: 'dashboard', kind: 'dir', inCode: true, onboarding: true, entries: 1 }] };
  overrides['tree:dashboard'] = { state: 'view', code: { state: 'listed' }, children: Array.from({ length: 7 }, (_, index) => ({ name: `file-${index}.ts`, path: `dashboard/file-${index}.ts`, kind: 'file', inCode: true, onboarding: index < 6, entries: 0 })) };
  overrides['path:dashboard'] = captured['path-root'];
  const view = open({ view: 'path', path: '.' });
  const tree = await view.findByTestId('knowledge-tree');
  const branch = await within(tree).findByRole('treeitem', { name: /dashboard/ });
  const count = () => tree.querySelectorAll('[data-path^="dashboard/file-"]').length;
  fireEvent.click(branch);
  await waitFor(() => expect(count()).toBe(6));
  fireEvent.click(branch);
  await waitFor(() => expect(count()).toBe(0));
  fireEvent.click(within(tree).getByRole('checkbox', { name: 'Show every path' }));
  fireEvent.click(await within(tree).findByRole('treeitem', { name: /dashboard/ }));
  await waitFor(() => expect(count()).toBe(7));
  const reads = requests.filter((url) => url.pathname.endsWith('/tree') && url.searchParams.get('path') === 'dashboard');
  expect(reads).toHaveLength(2);
});

it('shares one record-list acquisition through delay, known branches, rerenders and Back', async () => {
  const original = fetch;
  const pending: (() => void)[] = [];
  vi.stubGlobal('fetch', vi.fn(async (address: string) => {
    const url = new URL(address, 'http://localhost');
    if (!url.pathname.endsWith('/records')) return original(address);
    requests.push(url);
    await new Promise<void>((resolve) => pending.push(resolve));
    return { ok: true, status: 200, json: async () => captured.records } as Response;
  }));
  const view = open({ view: 'path', path: '.' });
  await view.findByTestId('reader-path-view');
  await waitFor(() => expect(pending.length).toBeGreaterThan(0));
  const tree = view.getByTestId('knowledge-tree');
  fireEvent.click(await within(tree).findByRole('treeitem', { name: /Records/ }));
  await act(async () => {});
  expect((view.getByLabelText('Record ID') as HTMLInputElement).disabled).toBe(false);
  expect(view.getByTestId('reader-path-view')).toBeTruthy();
  await waitFor(() => expect(tree.querySelectorAll('[data-testid=tree-node]').length).toBeGreaterThan(0));
  await act(async () => { pending.forEach((release) => release()); });
  fireEvent.click(await within(tree).findByRole('treeitem', { name: /Families/ }));
  expect(await within(tree).findByRole('treeitem', { name: /attribution-and-landing-pairing/ })).toBeTruthy();
  expect(within(tree).queryByRole('treeitem', { name: /^INV-/ })).toBeNull();
  const settledCalls = [...requests];
  view.rerender(<KnowledgeReader active={false} />);
  view.rerender(<KnowledgeReader active />);
  await act(async () => {});
  expect(requests).toEqual(settledCalls);
  const pane = view.getByTestId('knowledge-view');
  pane.scrollTop = 2100;
  fireEvent.scroll(pane);
  fireEvent.change(view.getByLabelText('Record ID'), { target: { value: 'FAM-QWVGDSYX' } });
  fireEvent.click(view.getByRole('button', { name: /^open$/ }));
  await view.findByTestId('reader-truth-view');
  fireEvent.click(view.getByRole('button', { name: /^Back$/ }));
  await view.findByTestId('reader-path-view');
  expect(pane.scrollTop).toBe(2100);
  fireEvent.click(await within(tree).findByRole('treeitem', { name: /Records/ }));
  fireEvent.click(await within(tree).findByRole('treeitem', { name: /Records/ }));
  await within(tree).findByRole('treeitem', { name: /Families/ });
  expect(requests.filter((url) => url.pathname.endsWith('/records'))).toHaveLength(1);
});

it('keeps a failed shared record list named without retrying it or gating paths and header', async () => {
  const original = fetch;
  vi.stubGlobal('fetch', vi.fn(async (address: string) => {
    const url = new URL(address, 'http://localhost');
    if (!url.pathname.endsWith('/records')) return original(address);
    requests.push(url);
    return { ok: false, status: 503, json: async () => ({ state: 'unavailable', detail: 'records denied', kinds: {} }) } as Response;
  }));
  const view = open({ view: 'path', path: '.' });
  expect((await view.findByTestId('reader-side-failure')).textContent).toContain('record list unavailable');
  expect((view.getByLabelText('Record ID') as HTMLInputElement).disabled).toBe(false);
  await view.findByTestId('reader-path-view');
  const tree = view.getByTestId('knowledge-tree');
  await waitFor(() => expect(tree.querySelectorAll('[data-testid=tree-node]').length).toBeGreaterThan(0));
  fireEvent.click(await within(tree).findByRole('treeitem', { name: /Records/ }));
  await waitFor(() => expect(within(tree).getByRole('alert').textContent).toContain('records denied'));
  expect(within(tree).queryByRole('treeitem', { name: /Families/ })).toBeNull();
  expect(view.getByTestId('reader-path-view')).toBeTruthy();
  view.rerender(<KnowledgeReader />);
  expect(requests.filter((url) => url.pathname.endsWith('/records'))).toHaveLength(1);
});

it('clears old record answers on commit and repository changes and ignores late selections', async () => {
  const original = fetch;
  const pending: { repo: string; commit: string; answer: (title: string) => void }[] = [];
  vi.stubGlobal('fetch', vi.fn(async (address: string) => {
    const url = new URL(address, 'http://localhost');
    if (url.pathname.endsWith('/repos')) {
      requests.push(url);
      return { ok: true, status: 200, json: async () => ({ repos: [{ repo: REPO }, { repo: 'other-repo' }] }) } as Response;
    }
    if (!url.pathname.endsWith('/records')) return original(address);
    requests.push(url);
    return new Promise<Response>((resolve) => pending.push({
      repo: url.searchParams.get('repo')!,
      commit: url.searchParams.get('commit')!,
      answer: (title) => resolve({ ok: true, status: 200, json: async () => ({
        ...captured.records, kinds: { family: [{ kind: 'family', id: 'FAM-QWVGDSYX', title }] },
      }) } as Response),
    }));
  }));
  const view = open({ view: 'path', path: '.' });
  await view.findByTestId('reader-path-view');
  await waitFor(() => expect(pending).toHaveLength(1));
  const first = pending[0];
  const commits = captured.selections.commits as { commit: string }[];
  fireEvent.change(view.getByTestId('reader-commit'), { target: { value: commits[0].commit } });
  await waitFor(() => expect(pending).toHaveLength(2));
  pending[1].answer('selection B');
  const lookup = () => view.container.querySelector<HTMLDataListElement>('#knowledge-record-ids')!;
  await waitFor(() => expect(lookup().textContent).toContain('selection B'));
  fireEvent.change(view.getByTestId('reader-commit'), { target: { value: commits[1].commit } });
  await waitFor(() => expect(pending).toHaveLength(3));
  expect(lookup().querySelectorAll('option')).toHaveLength(0);
  expect(view.queryByTestId('reader-side-failure')).toBeNull();
  await act(async () => { first.answer('late selection A'); });
  expect(lookup().querySelectorAll('option')).toHaveLength(0);
  fireEvent.change(view.getByLabelText('Repository'), { target: { value: 'other-repo' } });
  await waitFor(() => expect(pending).toHaveLength(4));
  pending[3].answer('selection D in other repository');
  await waitFor(() => expect(lookup().textContent).toContain('selection D'));
  await act(async () => { pending[2].answer('late selection C'); });
  expect(lookup().textContent).toContain('selection D');
  expect(lookup().textContent).not.toContain('late selection');
  expect(pending.map(({ repo, commit }) => [repo, commit])).toEqual([
    [REPO, 'published'], [REPO, commits[0].commit], [REPO, commits[1].commit],
    ['other-repo', 'published'],
  ]);
});

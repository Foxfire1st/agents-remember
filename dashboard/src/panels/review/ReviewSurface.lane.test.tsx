// MIK-R32 on real data: the unexplained-changes lane in the review workspace over Git trees.
//
// The bodies are the REAL served answers of the reviewer routes for the 260928-MIK-L32 worker's
// converted scratch leaf (provenance: laneReview.capture-provenance.json): seven changed files --
// three with no entry (a new module, CONTRIBUTING.md, a new binary file), an attributed file with a
// linked edit and an appended helper, an attributed file whose only change is its mode, a test whose
// edit a proof links, and a file whose converted entries were recorded at an older blob. The one
// synthetic body is the subject catalogue, answered empty so the surface opens the task-context review
// (the captured one) instead of a subject this check does not need. `ReviewSurface` is the real
// component and only `fetch` is stubbed.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ReviewResult } from '../../data/review';
import type { ReviewUnexplainedLane } from '../../data/reviewLane';
import { EXPLORER_LABELS, EXPLORER_PENDING } from './laneFocus';
import { ReviewSurface } from './ReviewSurface';

const captured = <T,>(name: string): T =>
  JSON.parse(
    readFileSync(path.join(path.dirname(new URL(import.meta.url).pathname), name), 'utf8'),
  ) as T;
const task = captured<ReviewResult>('laneReview.task.captured.json');
const trees = captured<unknown>('laneReview.trees.captured.json');
const lane = captured<{ lane: ReviewUnexplainedLane }>('laneReview.lane.captured.json');
const file = captured<unknown>('laneReview.file.captured.json');
const source = captured<unknown>('laneReview.source.captured.json');
const RETENTION = 'mcp/src/agents_remember/application/review_comparison_retention.py';
const EMPTY_CATALOGUE = { state: 'entries', entries: [], total_subjects: 0 };

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const requested: URL[] = [];

function answer(url: URL): unknown {
  if (url.pathname.endsWith('/entries')) return EMPTY_CATALOGUE;
  if (url.pathname.endsWith('/source-content')) return source;
  if (url.pathname.endsWith('/trees')) {
    if (url.searchParams.get('lane')) return lane;
    if (url.searchParams.get('file') === RETENTION) return file;
    return url.searchParams.get('file') ? null : trees;
  }
  return url.pathname.endsWith('/api/review/intent') ? task : null;
}

// `hold` keeps one read unanswered: the slow leaf-wide read (the gate's worklist), or the lane.
function serve(hold?: 'leaf-wide' | 'lane') {
  requested.length = 0;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string) => {
      const url = new URL(address, 'http://localhost');
      requested.push(url);
      const body = answer(url);
      const held = hold === 'leaf-wide' ? trees : hold === 'lane' ? lane : undefined;
      if (body === held) return new Promise<Response>(() => undefined);
      if (body === null) throw new Error(`Unexpected review request: ${address}`);
      return { ok: true, status: 200, json: async () => body } as Response;
    }),
  );
}

function open() {
  const payload = task.payload!;
  return render(
    <ReviewSurface
      repo={payload.candidate.repository_id}
      master={payload.candidate.master}
      leaf={payload.candidate.leaf_id}
      onBack={() => undefined}
    />,
  );
}

async function destinations(view: ReturnType<typeof open>) {
  const nav = await view.findByTestId('review-lane-destinations');
  await waitFor(() => expect(nav.dataset.laneState).toBe('measured'));
  return nav;
}

it('offers the two lane destinations after the families, with their file and hunk totals', async () => {
  serve();
  const view = open();
  const nav = await destinations(view);
  const [unexplained, unknown] = within(nav).getAllByTestId('review-lane-destination');
  expect(unexplained.textContent).toBe('Unexplained changes5 files · 3 hunks · 2 non-text');
  expect(unknown.textContent).toBe('Unknown attribution1 file · 1 hunk');
  // After the families, in the same tree panel.
  const family = view.getByTestId('review-family-tree');
  expect(family.compareDocumentPosition(nav) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  // One lane read, pinned to the comparison the payload names; no file is classified unasked.
  const reads = requested.filter((url) => url.searchParams.get('lane'));
  expect(reads.map((url) => url.searchParams.get('comparison'))).toEqual(['2']);
  expect(requested.some((url) => url.searchParams.get('file'))).toBe(false);
});

it('lists unexplained files first, then attributed files, and opens one on its unexplained hunk', async () => {
  serve();
  const view = open();
  const nav = await destinations(view);
  fireEvent.click(within(nav).getAllByTestId('review-lane-destination')[0]);
  const laneView = await view.findByTestId('review-lane');
  expect(
    within(nav).getAllByTestId('review-lane-destination')[0].getAttribute('aria-current'),
  ).toBe('true');
  const rows = within(laneView).getAllByTestId('review-lane-file');
  expect(rows.map((row) => [row.dataset.path, row.dataset.bucket, row.dataset.nonText])).toEqual([
    ['CONTRIBUTING.md', 'unexplained', undefined],
    ['docs/scratch-lane.bin', 'unexplained', 'binary'],
    ['mcp/src/agents_remember/application/review_lane_scratch.py', 'unexplained', undefined],
    [RETENTION, 'attributed', undefined],
    ['mcp/src/agents_remember/application/review_family_rosters.py', 'attributed', 'text'],
  ]);
  // The mode-only change in an attributed file is listed because the gate holds it unexplained.
  expect(rows[4].textContent).toContain('non-text (text, mode) · gate unexplained');
  // Opening the attributed file: its actual diff, focused on the one unexplained hunk.
  const retention = rows[3];
  fireEvent.click(within(retention).getByTestId('review-lane-file-open'));
  const focus = await within(retention).findByTestId('review-lane-file-focus');
  const hunks = await within(focus).findAllByTestId('review-lane-hunk');
  expect(
    hunks.map((hunk) => [hunk.dataset.hunkClass, hunk.dataset.before, hunk.dataset.after]),
  ).toEqual([['unexplained', 'none (after L571)', 'L572–577']]);
  expect(within(hunks[0]).getByTestId('diff-pane')).toBeTruthy();
  expect(within(focus).getByTestId('review-lane-file-facts').textContent).toContain(
    '2 hunk(s): 1 linked · 1 unexplained · 0 attribution unknown',
  );
  // The full file stays one control away, and the gate's own item for the file is shown beside.
  expect(within(focus).getByTestId('review-lane-full-file').textContent).toBe('Full file');
  expect((await within(focus).findByTestId('review-unexplained-file')).dataset.path).toBe(
    RETENTION,
  );
  const fileReads = requested.filter((url) => url.searchParams.get('file'));
  expect(fileReads.map((url) => url.searchParams.get('file'))).toEqual([RETENTION]);
});

it('lists the file of unknown attribution with the reason no range is supplied', async () => {
  serve();
  const view = open();
  const nav = await destinations(view);
  fireEvent.click(within(nav).getAllByTestId('review-lane-destination')[1]);
  const laneView = await view.findByTestId('review-lane');
  const rows = within(laneView).getAllByTestId('review-lane-file');
  expect(rows.map((row) => [row.dataset.path, row.dataset.bucket])).toEqual([
    ['mcp/src/agents_remember/application/review_source_admission.py', 'attribution_unknown'],
  ]);
  expect(rows[0].textContent).toContain('RLZ-CXH58B4W (recorded_blob_mismatch)');
  // The destination on screen is the tree's current node.
  expect(
    within(nav).getAllByTestId('review-lane-destination')[1].getAttribute('aria-current'),
  ).toBe('true');
});

it("says the gate's items are still being read, never that the gate raised none", async () => {
  serve('leaf-wide');
  const view = open();
  const nav = await destinations(view);
  fireEvent.click(within(nav).getAllByTestId('review-lane-destination')[0]);
  const laneView = await view.findByTestId('review-lane');
  const retention = within(laneView)
    .getAllByTestId('review-lane-file')
    .find((row) => row.dataset.path === RETENTION)!;
  fireEvent.click(within(retention).getByTestId('review-lane-file-open'));
  const focus = await within(retention).findByTestId('review-lane-file-focus');
  expect((await within(focus).findByTestId('review-lane-gate-state')).dataset.gateState).toBe(
    'reading',
  );
  expect(within(focus).queryByTestId('review-lane-gate-none')).toBeNull();
});

// The explorer's label of each changed path, as rendered.
function explorerLabels(view: ReturnType<typeof open>): Record<string, string> {
  const explorer = view.getByTestId('review-source-explorer');
  return Object.fromEntries(
    within(explorer)
      .getAllByTestId('review-inventory-entry')
      .map((row) => [
        within(row).getByTestId('review-inventory-open').dataset.path!,
        row.querySelector<HTMLElement>('[data-attribution]')!.dataset.attribution!,
      ]),
  );
}

it("labels the source explorer with the lane's bucket for every changed file", async () => {
  serve();
  const view = open();
  await destinations(view);
  // One classification: each path's explorer label is its lane bucket -- the file whose entries were
  // recorded at an older blob is of unknown attribution, the proof-linked test is attributed --
  // although the landed accounting's lists, still in the payload, call both unregistered.
  const buckets = Object.fromEntries(lane.lane.paths.map((one) => [one.path, one.bucket]));
  const labels = explorerLabels(view);
  expect(Object.keys(labels).sort()).toEqual(Object.keys(buckets).sort());
  for (const [pathName, bucket] of Object.entries(buckets))
    expect(labels[pathName]).toBe(EXPLORER_LABELS[bucket]);
  const ADMISSION = 'mcp/src/agents_remember/application/review_source_admission.py';
  const PROVED = 'mcp/tests/test_knowledge_review_attributed_source_content.py';
  expect([labels[ADMISSION], labels[PROVED]]).toEqual(['Attribution unknown', 'Mapped']);
  const landed = task.payload!.source.unattributed_changed_paths;
  expect(landed).toEqual(expect.arrayContaining([ADMISSION, PROVED]));
  // The same two files as the lane lists them.
  fireEvent.click(view.getAllByTestId('review-lane-destination')[1]);
  const unknown = await view.findByTestId('review-lane');
  expect(
    within(unknown)
      .getAllByTestId('review-lane-file')
      .map((row) => row.dataset.path),
  ).toEqual([ADMISSION]);
});

it('labels the explorer pending while the lane is read, never with a guessed bucket', async () => {
  serve('lane');
  const view = open();
  await view.findByTestId('review-lane-destinations');
  await waitFor(() => expect(view.getAllByTestId('review-inventory-entry').length).toBe(7));
  expect(new Set(Object.values(explorerLabels(view)))).toEqual(new Set([EXPLORER_PENDING]));
  // The technical details say the same: pending, no landed list in its place.
  const details = view.getByTestId('review-details');
  expect(within(details).getByTestId('review-unattributed').dataset.attributionState).toBe(
    'pending',
  );
  expect(within(details).getByTestId('review-remaining').textContent).toContain(
    `unknown_attribution_changed_paths: ${EXPLORER_PENDING}`,
  );
});

it("lists the lane's unexplained and unknown files in the technical details", async () => {
  serve();
  const view = open();
  await destinations(view);
  // Comparison 2 of the real scratch: the landed accounting lists the proof-linked test and the
  // file whose entries sit at an older blob as unregistered, and counts no unknown file.
  const ADMISSION = 'mcp/src/agents_remember/application/review_source_admission.py';
  const PROVED = 'mcp/tests/test_knowledge_review_attributed_source_content.py';
  expect(task.payload!.source.unattributed_changed_paths).toEqual(
    expect.arrayContaining([ADMISSION, PROVED]),
  );
  const details = view.getByTestId('review-details');
  const remaining = within(details).getByTestId('review-remaining');
  expect(remaining.dataset.attributionSource).toBe('lane');
  expect(remaining.textContent).toContain('unattributed_changed_paths: 3');
  expect(remaining.textContent).toContain('unknown_attribution_changed_paths: 1');
  // The lane's lists: the three unexplained files, and the one of unknown attribution.
  const unexplained = within(details).getByTestId('review-unattributed').textContent!;
  const byBucket = (bucket: string) =>
    lane.lane.paths.filter((one) => one.bucket === bucket).map((one) => one.path);
  for (const one of byBucket('unexplained')) expect(unexplained).toContain(one);
  expect(unexplained).not.toContain(PROVED);
  expect(unexplained).not.toContain(ADMISSION);
  expect(within(details).getByTestId('review-unknown-attribution').textContent).toBe(
    `changed paths of unknown attribution: ${ADMISSION}`,
  );
  expect(byBucket('attribution_unknown')).toEqual([ADMISSION]);
});

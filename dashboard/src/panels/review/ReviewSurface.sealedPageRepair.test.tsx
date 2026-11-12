// Sealed W1 findings only; actual L37 replies plus explicitly synthetic adverse wire transitions.
import { act, cleanup, fireEvent, render, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ReviewResult, ReviewEntryListResult, ReviewSourceInventory } from '../../data/review';
import type { ReviewTreesResult } from '../../data/reviewTrees';
import type { TaskChangeset, FileDiff } from '../../data/changeset';
import { ReviewSurface } from './ReviewSurface';
import { ChangeSetViewer } from '../changeset/ChangeSetViewer';
import { dashboardStore } from '../../data/store';
import { captured, catalogue, selectedMemberId, target, serve } from './l86.test-utils';
import { treeViewStore } from './triageViewPreference';
beforeEach(() => {
  localStorage.clear();
  treeViewStore.getState().setView('changed');
});
afterEach(() => {
  cleanup();
  dashboardStore.getState().reset();
  vi.unstubAllGlobals();
});
const family = captured<ReviewResult>('l86.family.captured.json');
const member = captured<ReviewResult>('l86.member.captured.json');
const trees = captured<ReviewTreesResult>('l86.trees.captured.json');
const lane = captured<{ state: string; comparison: { number: number }; lane: unknown }>(
  'l86.lane.captured.json',
);
const changes = captured<TaskChangeset>('l86.changeset.captured.json');
const diffs = captured<Record<string, FileDiff>>('l86.file-diffs.captured.json');
const response = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });
// The page opens with nothing selected (MIK-R89 v2 rule 2): these cases open the captured family by
// its selector, which is the subject the old auto-selection chose.
const familyId = catalogue.outline!.families.find((one) => one.id === 'FAM-2HBJREC2')!.selector_id;
const familyTarget = { ...target, selectorKind: 'family' as const, selectorId: familyId };
// The family's members' overview (MIK-R211 v4 rule 10), once the family's own read has answered.
const familyAnswered = (view: ReturnType<typeof render>) =>
  waitFor(() => {
    expect(view.getByTestId('review-family-overview').dataset.family).toBe(familyId);
    expect(view.getByTestId('review-subject-provenance')).toBeTruthy();
  });

// The attribution is the read's facts: a family's stand in its overview's slot after its member
// summaries (FamilyState, MIK-R211 v4 rule 10), an invariant's after its code.
async function heldAttribution(view: ReturnType<typeof render>, area: () => HTMLElement, release: () => void, asked: URL[]) {
  const remaining = () => within(area()).getByTestId('review-remaining');
  await waitFor(() => expect(remaining().dataset.attributionSource).toBe('lane'));
  expect(view.getAllByTestId('review-remaining')).toHaveLength(1);
  expect(remaining().textContent).toContain('Attribution pending');
  await waitFor(() => expect(asked.filter((url) => url.searchParams.has('lane'))).toHaveLength(1));
  await act(async () => release());
  await waitFor(() =>
    expect(remaining().textContent).toContain(
      'changed files without an invariant: 167',
    ),
  );
  expect(remaining().textContent).toContain(
    'changed files of unknown attribution: 2',
  );
  expect(asked.filter((url) => url.searchParams.has('lane'))).toHaveLength(1);
}

it('F05 main attribution uses the held destination counts and states pending without another read', async () => {
  const { release, asked } = serve(undefined, 'lane');
  const view = render(<ReviewSurface {...familyTarget} />);
  await familyAnswered(view);
  await heldAttribution(view, () => within(view.getByTestId('review-family-overview')).getByTestId('review-subject-slot'), release, asked);
});

it('F05 holds the same attribution in a selected invariant\'s facts after its code', async () => {
  const { release, asked } = serve(undefined, 'lane');
  const view = render(<ReviewSurface {...target} selectorKind="invariant" selectorId={selectedMemberId} />);
  await view.findByTestId('review-center-member');
  await heldAttribution(view, () => view.getByTestId('review-subject-after-code'), release, asked);
});

it('F08 every non-tree complete-file trigger gives a named refusal without selecting an unrelated range', async () => {
  const nonTree = structuredClone(family);
  nonTree.payload!.limitations = nonTree.payload!.limitations.filter(
    (text) => !text.startsWith('review:trees:'),
  );
  const unavailable: ReviewEntryListResult = {
    ...catalogue,
    outline: {
      state: 'unavailable',
      detail: 'Memory is not converted.',
      families: [],
      invariants: [],
    },
  };
  const asked: URL[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string) => {
      const url = new URL(address, 'http://localhost');
      asked.push(url);
      return response(url.pathname.endsWith('/entries') ? unavailable : nonTree);
    }),
  );
  const view = render(<ReviewSurface {...familyTarget} />);
  await familyAnswered(view);
  // The one "All changes" link is the tree's (MIK-R89 v2 rule 7); no card or pane repeats it.
  const triggers = view.getAllByRole('button', { name: /^All changes/ });
  expect(triggers).toHaveLength(1);
  expect(triggers[0].closest('[data-testid="review-tree"]')).not.toBeNull();
  for (const trigger of triggers) {
    trigger.focus();
    fireEvent.click(trigger);
    const refused = await view.findByTestId('review-changes-unavailable');
    expect(within(refused).getByTestId('review-refusal').dataset.reviewCode).toBe(
      'comparison_not_recorded',
    );
    expect(refused.textContent).toContain('no recorded tree comparison');
    const count = asked.length;
    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(view.queryByTestId('review-changes-unavailable')).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(trigger));
    expect(asked).toHaveLength(count);
  }
  expect(asked.some((url) => url.pathname.startsWith('/api/changeset/'))).toBe(false);
});

it('F04 an opened comparison retains its lane and inventory after a held subject answers another comparison', async () => {
  let releaseSubject: ((value: Response) => void) | undefined;
  let newComparison = false;
  const next = structuredClone(member);
  next.payload!.limitations = next.payload!.limitations.map((text) =>
    text === 'review:trees:5' ? 'review:trees:6' : text,
  );
  next.payload!.source.inventory.after_code_tree_id = 'e'.repeat(40);
  next.payload!.comparison!.after_snapshot_digest = 'e'.repeat(64);
  const nextLane = {
    ...lane,
    comparison: { ...lane.comparison, number: 6 },
    lane: {
      state: 'measured',
      detail: 'Synthetic newer comparison.',
      paths: [],
      unmeasured: [],
      unexplained_changes: {
        files: [],
        bucket_files: 0,
        attributed_files: 0,
        hunks: 0,
        non_text: 0,
      },
      unknown_attribution: {
        files: [],
        bucket_files: 0,
        attributed_files: 0,
        hunks: 0,
        non_text: 0,
      },
    },
  };
  const nextTrees = { ...trees, comparison: { ...trees.comparison!, number: 6 } };
  const asked: URL[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string) => {
      const url = new URL(address, 'http://localhost');
      asked.push(url);
      if (url.pathname.endsWith('/entries')) return response(catalogue);
      if (url.pathname.endsWith('/summary'))
        return response({ state: 'unavailable', refusal: { code: 'not-served', detail: 'Not part of this check.', next_action: 'n' } });
      if (url.pathname === '/api/review/intent') {
        if (url.searchParams.get('selectorKind') === 'invariant')
          return await new Promise<Response>((resolve) => {
            releaseSubject = resolve;
          });
        return response(family);
      }
      if (url.pathname === '/api/review/trees')
        return response(
          url.searchParams.has('lane')
            ? url.searchParams.get('comparison') === '6'
              ? nextLane
              : lane
            : newComparison
              ? nextTrees
              : trees,
        );
      if (url.pathname === '/api/changeset/task') return response(changes);
      if (url.pathname === '/api/changeset/file-diff')
        return response(
          diffs[`${url.searchParams.get('kind') ?? 'code'}:${url.searchParams.get('path')}`],
        );
      throw new Error(`Unexpected ${url}`);
    }),
  );
  const view = render(<ReviewSurface {...familyTarget} />);
  await familyAnswered(view);
  const tree = view.getByTestId('review-tree');
  // The tree's lane row counts the lane's bucket files (MIK-R86 v2 rule 4; W14).
  await waitFor(() => expect(within(tree).getByTestId('review-tree-lane').textContent).toContain('138 files'));
  fireEvent.click(tree.querySelector(`[data-subject-id="${selectedMemberId}"]`)!);
  await waitFor(() => expect(releaseSubject).toBeDefined());
  fireEvent.click(
    within(tree).getAllByTestId('review-tree-full-page').find((link) => link.dataset.destination === 'unexplained')!,
  );
  const page = await view.findByTestId('changeset-viewer');
  await waitFor(() => expect(within(page).getByText('changed code (167)')).toBeTruthy());
  newComparison = true;
  await act(async () => releaseSubject!(response(next)));
  await waitFor(() =>
    expect(
      asked.some(
        (url) => url.searchParams.get('comparison') === '6' && url.searchParams.has('lane'),
      ),
    ).toBe(true),
  );
  expect(within(page).getByText('changed code (167)')).toBeTruthy();
  expect(within(page).getByRole('button', { name: 'Changes without an invariant (167)' })).toBeTruthy();
  expect(
    asked
      .filter((url) => url.pathname.startsWith('/api/changeset/'))
      .every((url) => url.searchParams.get('comparison') === '5'),
  ).toBe(true);
  const count = asked.length;
  fireEvent.click(within(page).getByRole('button', { name: '← Back to intent review' }));
  await view.findByTestId('review-center-member');
  expect(asked).toHaveLength(count);
});

it('F10 All gives linked binary and mode-only files one non-text badge without changing all-linked text or cockpit rows', async () => {
  const data: TaskChangeset = {
    scope: 'synthetic-linked',
    code: [
      { path: 'linked.bin', status: 'M', insertions: null, deletions: null },
      { path: 'mode.py', status: 'M', insertions: 0, deletions: 0 },
      { path: 'linked.py', status: 'M', insertions: 1, deletions: 1 },
    ],
    memory: [],
    counters: {
      code: { files: 3, insertions: 1, deletions: 1 },
      memory: { files: 0, insertions: 0, deletions: 0 },
    },
  };
  const inventory: ReviewSourceInventory = {
    state: 'measured',
    entries: [
      { path: 'linked.bin', status: 'modified', content: 'binary', mode_change: false },
      { path: 'mode.py', status: 'modified', content: 'text', mode_change: true },
      { path: 'linked.py', status: 'modified', content: 'text', mode_change: false },
    ],
    listed_total: 3,
    partial: false,
    detail: 'Explicit synthetic linked non-text witness.',
    command: 'not executed',
    unrepresentable_paths: [],
  };
  const empty = { files: [], bucket_files: 0, attributed_files: 0, hunks: 0, non_text: 0 };
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string) =>
      response(
        address.includes('/file-diff')
          ? {
              scope: data.scope,
              kind: 'code',
              path: 'linked.bin',
              language: 'text',
              before: { content: '' },
              after: { content: '' },
            }
          : data,
      ),
    ),
  );
  const view = render(
    <ChangeSetViewer
      repo="synthetic"
      master="synthetic"
      leaf="synthetic"
      comparison={9}
      onBack={() => undefined}
      reviewContext={{
        page: { comparison: 9, filter: 'all' },
        inventory,
        lane: {
          phase: 'ready',
          value: {
            state: 'measured',
            detail: 'Linked changes absent from destinations.',
            paths: [],
            unmeasured: [],
            unexplained_changes: empty,
            unknown_attribution: empty,
          },
        },
      }}
    />,
  );
  await view.findByText('linked.py');
  expect(view.getAllByText('non-text')).toHaveLength(2);
  view.rerender(
    <ChangeSetViewer
      repo="synthetic"
      master="synthetic"
      leaf="synthetic"
      mode="committed"
      onBack={() => undefined}
    />,
  );
  await waitFor(() => expect(view.queryByText('non-text')).toBeNull());
});

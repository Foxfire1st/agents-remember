// Real Surface with immutable native answers; catalogue/auxiliary reads isolate origin presentation.
import { cleanup, render, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ReviewOrigin, ReviewResult } from '../../data/review';
import { resetCardCachesForTests } from '../../data/reviewTrees';
import familyRaw from './subjectReview.successorFamily.captured.json';
import invariantRaw from './subjectReview.successorInvariant.captured.json';
import handoffRaw from './l57.f04-native.family.captured.json';
import trees from './l86.trees.captured.json';
import lane from './l86.lane.captured.json';
import cards from './l86.member-cards.captured.json';
import { ReviewSurface } from './ReviewSurface';
import { syntheticCatalogue } from './outline.test-utils';

let catalogue = syntheticCatalogue([]);
vi.mock('../../data/useReviewCatalogue', async (original) => ({
  ...await original<typeof import('../../data/useReviewCatalogue')>(),
  useReviewCatalogue: () => ({ loading: false, ...catalogue, refresh: () => undefined }),
}));
afterEach(() => { cleanup(); resetCardCachesForTests(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

function mount(body: ReviewResult, width: number) {
  catalogue = syntheticCatalogue([body]);
  vi.stubGlobal('innerWidth', width);
  const media = window.matchMedia;
  vi.stubGlobal('matchMedia', (query: string) => ({ ...media(query), matches: width <= 640 }));
  vi.stubGlobal('fetch', vi.fn(async (address: string) => {
    const url = new URL(address, 'http://localhost');
    let answer: unknown = body;
    if (url.pathname.endsWith('/trees')) answer = url.searchParams.has('invariants') ? cards : url.searchParams.has('lane') ? lane : trees;
    else if (url.pathname.endsWith('/source-content')) answer = { state: 'refused', refusal: { code: 'not-found', detail: 'Outside origin presentation', next_action: 'Read source separately' } };
    else if (url.pathname !== '/api/review/intent') throw new Error(`Unexpected origin test route ${url}`);
    return new Response(JSON.stringify(answer), { status: 200 });
  }));
  const payload = body.payload!;
  const selection = payload.knowledge.revision_selection!;
  return render(<ReviewSurface repo={payload.candidate.repository_id} master={payload.candidate.master}
    leaf={payload.candidate.leaf_id} history="recorded" selectorKind={selection.record_kind}
    selectorId={selection.record_id} onBack={() => undefined} />);
}
// The record's origin is the leaf that introduced it, which no revision updates: Technical details
// name it "introduced by" in their closed provenance fold, never as a side's writer (MIK-R89 v2
// rules 1 and 6, amended 9dde2b09). Every supplied value stays reachable there.
function assertOrigin(area: HTMLElement, testid: string, origin: ReviewOrigin) {
  const block = within(area).getByTestId(testid);
  expect(block.closest('details')?.dataset.disclosure).toBe('provenance');
  expect(block.closest('details')?.open).toBe(false);
  expect(block.textContent).toContain(`introduced by ${origin.leaf ?? `task ${origin.task}`}`);
  expect(block.textContent).not.toMatch(/(before|after) origin/);
  for (const value of [origin.task, origin.leaf, origin.wave, origin.handoffEntry, origin.handoff?.path, ...origin.handoff?.evidence ?? []]) {
    if (value) expect(block.textContent).toContain(value);
  }
}

for (const width of [1280, 390]) {
  for (const [kind, raw] of [['family', familyRaw], ['invariant', invariantRaw], ['family hand-off', handoffRaw]] as const) {
    it(`keeps the supplied native ${kind} origins in the closed Technical details at ${width}px`, async () => {
      const body = raw as unknown as ReviewResult;
      const view = mount(body, width);
      await waitFor(() => view.getByTestId(kind.startsWith('family') ? 'review-center-family' : 'review-center-member'));
      const area = view.getByTestId('review-subject-provenance');
      const { before_origin: before, after_origin: after } = body.payload!.knowledge;
      expect(before).toBeDefined();
      expect(after).toBeDefined();
      if (JSON.stringify(before) === JSON.stringify(after)) assertOrigin(area, 'review-origin', after!);
      else for (const [side, origin] of [['before', before!], ['after', after!]] as const) assertOrigin(area, `review-origin-${side}`, origin);
      // Nothing of the origin stands in the subject's reading before its content.
      expect(within(view.getByTestId('review-subject-slot')).queryByText(/introduced by|origin:/)).toBeNull();
    });
  }
}

it('keeps distinct family task, leaf/wave and hand-off entries on their own sides (synthetic origin control)', async () => {
  const body = structuredClone(familyRaw) as unknown as ReviewResult;
  body.payload!.knowledge.before_origin = { task: 'before-task', leaf: 'before-leaf', handoffEntry: 'before-entry' };
  body.payload!.knowledge.after_origin = { task: 'after-task', wave: 'after-wave', handoffEntry: 'after-entry', handoff: { path: 'after-handoff.json', evidence: ['after-evidence'] } };
  const view = mount(body, 390);
  await waitFor(() => view.getByTestId('review-center-family'));
  const area = view.getByTestId('review-subject-provenance');
  for (const side of ['before', 'after'] as const) assertOrigin(area, `review-origin-${side}`, body.payload!.knowledge[`${side}_origin`]!);
  expect(within(area).getByTestId('review-origin-before').textContent).not.toContain('after-task');
  expect(within(area).getByTestId('review-origin-after').textContent).not.toContain('before-task');
});

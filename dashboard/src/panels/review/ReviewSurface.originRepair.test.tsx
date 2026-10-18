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
function assertOrigin(area: HTMLElement, side: string, origin: ReviewOrigin) {
  const block = within(area).getByTestId(`review-origin-${side}`);
  expect(block.closest('details')).toBeNull();
  expect(block.hidden).toBe(false);
  for (const value of [origin.task, origin.leaf, origin.wave, origin.handoffEntry, origin.handoff?.path, ...origin.handoff?.evidence ?? []]) {
    if (value) expect(block.textContent).toContain(value);
  }
}

for (const width of [1280, 390]) {
  for (const [kind, raw] of [['family', familyRaw], ['invariant', invariantRaw], ['family hand-off', handoffRaw]] as const) {
    it(`shows both supplied native ${kind} origins without disclosure at ${width}px`, async () => {
      const body = raw as unknown as ReviewResult;
      const view = mount(body, width);
      const area = await waitFor(() => view.getByTestId(kind.startsWith('family') ? 'review-center-family' : 'review-center-column'));
      for (const side of ['before', 'after'] as const) {
        const origin = body.payload!.knowledge[`${side}_origin`];
        expect(origin).toBeDefined();
        assertOrigin(area, side, origin!);
      }
    });
  }
}

it('keeps distinct family task, leaf/wave and hand-off entries on their own sides (synthetic origin control)', async () => {
  const body = structuredClone(familyRaw) as unknown as ReviewResult;
  body.payload!.knowledge.before_origin = { task: 'before-task', leaf: 'before-leaf', handoffEntry: 'before-entry' };
  body.payload!.knowledge.after_origin = { task: 'after-task', wave: 'after-wave', handoffEntry: 'after-entry', handoff: { path: 'after-handoff.json', evidence: ['after-evidence'] } };
  const view = mount(body, 390);
  const area = await waitFor(() => view.getByTestId('review-center-family'));
  for (const side of ['before', 'after'] as const) assertOrigin(area, side, body.payload!.knowledge[`${side}_origin`]!);
  expect(within(area).getByTestId('review-origin-before').textContent).not.toContain('after-task');
  expect(within(area).getByTestId('review-origin-after').textContent).not.toContain('before-task');
});

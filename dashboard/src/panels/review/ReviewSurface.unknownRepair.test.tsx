// Synthetic unknown facts over native answers: real Surface presentation, not classifier evidence.
import { cleanup, render, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ReviewResult } from '../../data/review';
import { resetCardCachesForTests } from '../../data/reviewTrees';
import familyRaw from './subjectReview.successorFamily.captured.json';
import invariantRaw from './subjectReview.successorInvariant.captured.json';
import trees from './l86.trees.captured.json';
import lane from './l86.lane.captured.json';
import cards from './l86.member-cards.captured.json';
import { ReviewSurface } from './ReviewSurface';
import { syntheticCatalogue } from './outline.test-utils';

const CURATION = 'No entry covers these lines yet; curation attaches them to an invariant or records that none applies.';
let catalogue = syntheticCatalogue([]);
vi.mock('../../data/useReviewCatalogue', async (original) => ({
  ...await original<typeof import('../../data/useReviewCatalogue')>(),
  useReviewCatalogue: () => ({ loading: false, ...catalogue, refresh: () => undefined }),
}));
afterEach(() => { cleanup(); resetCardCachesForTests(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

for (const [kind, raw] of [['family', familyRaw], ['invariant', invariantRaw]] as const) {
  it(`shows the exact curation explanation in the unknown tree tooltip and beside the selected ${kind} reason`, async () => {
    const body = structuredClone(raw) as unknown as ReviewResult;
    const payload = body.payload!;
    const entry = payload.family_context!.entries[0];
    const reasons = ['synthetic memory side could not be read'];
    entry.change_kinds!.guarantee = 'unknown';
    entry.change_kinds!.guarantee_detail = reasons[0];
    for (const member of entry.change_kinds!.members) {
      member.primary = 'unknown';
      member.unknown_reasons = reasons;
    }
    catalogue = syntheticCatalogue([body]);
    for (const row of catalogue.outline.families) row.change = 'unknown';
    for (const row of catalogue.outline.invariants) row.change = 'unknown';
    vi.stubGlobal('fetch', vi.fn(async (address: string) => {
      const url = new URL(address, 'http://localhost');
      let answer: unknown = body;
      if (url.pathname.endsWith('/trees')) answer = url.searchParams.has('invariants') ? cards : url.searchParams.has('lane') ? lane : trees;
      else if (url.pathname.endsWith('/source-content')) answer = { state: 'refused', refusal: { code: 'not-found', detail: 'Outside unknown presentation', next_action: 'Read source separately' } };
      else if (url.pathname !== '/api/review/intent') throw new Error(`Unexpected unknown test route ${url}`);
      return new Response(JSON.stringify(answer), { status: 200 });
    }));
    const selected = payload.knowledge.revision_selection!;
    const view = render(<ReviewSurface repo={payload.candidate.repository_id} master={payload.candidate.master}
      leaf={payload.candidate.leaf_id} history="recorded" selectorKind={selected.record_kind}
      selectorId={selected.record_id} onBack={() => undefined} />);
    // The change facts follow the subject's content and code, open (MIK-R89 v2 rule 1, amended 9dde2b09).
    const area = await waitFor(() => view.getByTestId(kind === 'family' ? 'review-center-family-details' : 'review-center-member-details'));
    const badges = view.getAllByTestId('review-outline-badge').filter((badge) => badge.dataset.changeKind === 'unknown');
    expect(badges.length).toBeGreaterThan(0);
    for (const badge of badges) expect(badge.title).toContain(CURATION);
    const visibleReasons = within(area).getAllByTestId('review-change-why');
    expect(visibleReasons.length).toBeGreaterThan(0);
    for (const reason of visibleReasons) {
      expect(reason.textContent).toContain(reasons[0]);
      expect(reason.textContent).toContain(CURATION);
      expect(reason.closest('details')).toBeNull();
      expect(reason.hidden).toBe(false);
    }
    expect(view.queryByTestId('review-changeset')).toBeNull();
  });
}

// Current raw L37 tree captures exercise statement and guarantee word diffs.
// The same-revision adversarial case explicitly changes only revision metadata
// in memory; it is not published or claimed as an unmodified capture.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import type { ReviewResult } from '../../data/review';
import type { ReviewTreesResult } from '../../data/reviewTrees';
import { ReviewSurface } from './ReviewSurface';

const captured = <T,>(name: string): T =>
  JSON.parse(
    readFileSync(path.join(path.dirname(new URL(import.meta.url).pathname), name), 'utf8'),
  ) as T;
const family = captured<ReviewResult>('subjectReview.successorFamily.captured.json');
const invariant = captured<ReviewResult>('subjectReview.successorInvariant.captured.json');
const entries = captured<unknown>('l86.entries.captured.json');
const cards = captured<ReviewTreesResult>('l86.member-cards.captured.json');
const leafTrees = captured<ReviewTreesResult>('l86.trees.captured.json');
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function sameRevisionGuarantee(result: ReviewResult): ReviewResult {
  const body = structuredClone(result);
  const entry = body.payload!.family_context!.entries[0];
  entry.after.guarantee!.revision_id = entry.before.guarantee!.revision_id;
  entry.after.guarantee!.display_version = entry.before.guarantee!.display_version;
  return body;
}

function serve(sameRevision = false) {
  const bodies = new Map(
    [family, invariant].map((result) => [
      result.payload!.knowledge.revision_selection!.record_id,
      sameRevision ? sameRevisionGuarantee(result) : result,
    ]),
  );
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string) => {
      const url = new URL(address, 'http://localhost');
      const body = url.pathname.endsWith('/trees')
        ? url.searchParams.get('invariants')
          ? cards
          : leafTrees
        : url.pathname.endsWith('/entries')
          ? entries
          : url.pathname.endsWith('/source-content')
            ? {
                state: 'refused',
                refusal: { code: 'not-found', detail: 'n/a', next_action: 'n/a' },
              }
            : bodies.get(url.searchParams.get('selectorId') ?? '');
      if (body === undefined) throw new Error(`Unexpected review request: ${address}`);
      return { ok: true, status: 200, json: async () => body } as Response;
    }),
  );
}

function open(result: ReviewResult) {
  const payload = result.payload!;
  return render(
    <ReviewSurface
      repo={payload.candidate.repository_id}
      master={payload.candidate.master}
      leaf={payload.candidate.leaf_id}
      selectorKind={payload.knowledge.revision_selection!.record_kind}
      selectorId={payload.knowledge.revision_selection!.record_id}
      onBack={() => undefined}
    />,
  );
}

it("word-diffs a tree review's member statement and family guarantee in the center", async () => {
  serve();
  const view = open(invariant);
  const center = await view.findByTestId('review-center-member');
  const passages = await within(center).findAllByTestId('intent-diff');
  // The member's own statement is its content; the guarantee of the family it was selected under
  // follows its code (MIK-R89 v2 rule 1, amended 9dde2b09).
  expect(passages.map((node) => node.dataset.field)).toEqual(['statement', 'applicability', 'conditions.1']);
  const details = view.getByTestId('review-center-member-details');
  expect(within(details).getAllByTestId('intent-diff').map((node) => node.dataset.field)).toEqual(['guarantee']);
  const statement = passages[0];
  expect(within(center).getByTestId('review-center-statement-label').textContent).toBe(
    'Changed statement, applicability, conditions, exclusions · revision r2 → r4',
  );
  const spoken = within(statement).getByTestId('intent-diff-inline').textContent!;
  expect(spoken.replace(/\s+/g, ' ')).toContain(
    'removed: reachable added: authored knowledge write',
  );
  const guarantee = within(details).getByTestId('review-center-guarantee-changed');
  expect(guarantee.dataset.wordDiff).toBe('true');
  expect(within(guarantee).getByTestId('review-center-guarantee-label').textContent).toBe(
    'Changed guarantee · revision r1 → r3',
  );
  // No code editor draws the prose (the center's changed code card keeps its own diff).
  expect(within(guarantee).queryByTestId('diff-pane')).toBeNull();
  const intent = within(center).getByTestId('review-center-member-changed');
  expect(within(intent).queryByTestId('diff-pane')).toBeNull();
  cleanup();
  // A family opens its members' overview (MIK-R211 v4 rule 10): its slot draws the family read's
  // guarantee change badge, steps and judgment but no word diff; the family guarantee is word-diffed
  // in the detail of the member opened from it.
  const familyView = open(family);
  const overview = await familyView.findByTestId('review-family-overview');
  expect(within(overview).queryAllByTestId('intent-diff')).toHaveLength(0);
  fireEvent.click(
    within(overview)
      .getAllByTestId('review-member-summary')
      .find((row) => row.dataset.subject === invariant.payload!.knowledge.revision_selection!.record_id)!
      .querySelector('button')!,
  );
  const memberDetails = await familyView.findByTestId('review-center-member-details');
  await waitFor(() =>
    expect(
      within(memberDetails).getByTestId('review-center-guarantee-changed').dataset.wordDiff,
    ).toBe('true'),
  );
});

// Review R1 F2: one guarantee revision whose two texts differ is never called unchanged on a tree
// review -- not in the center, not in the family navigator, not in the center's details.
it('never calls one guarantee revision unchanged when its texts differ, anywhere on a tree review', async () => {
  serve(true);
  const view = open(invariant);
  await view.findByTestId('review-center-member');
  const center = view.getByTestId('review-center-member-details');
  expect(within(center).getByTestId('review-center-guarantee-label').textContent).toBe(
    'Changed guarantee · revision r1 → r1 · the same revision on both sides; its text differs',
  );
  expect(view.queryByTestId('review-family-guarantee')).toBeNull();
  expect(
    within(center).getByTestId('review-center-facts').querySelector('[data-fact="guarantee"]')!
      .textContent,
  ).toBe('guarantee: same_revision_text_changed');
});

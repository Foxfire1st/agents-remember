// MIK-R35 wired into the real review surface: the REAL served bodies of the converted scratch leaf
// (gitTrees.*.captured.json, a tree comparison `review:trees:1`), with the family guarantee and the
// touched member's statement given a successor revision. `ReviewSurface` is the real component and
// only `fetch` is stubbed. The same bodies without the tree comparison's token stand for a dataset
// review, which keeps the landed rendering.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { cleanup, render, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import type { ReviewResult } from '../../data/review';
import type { ReviewTreesResult } from '../../data/reviewTrees';
import { ReviewSurface } from './ReviewSurface';

const captured = <T,>(name: string): T =>
  JSON.parse(
    readFileSync(path.join(path.dirname(new URL(import.meta.url).pathname), name), 'utf8'),
  ) as T;
const family = captured<ReviewResult>('gitTrees.family.captured.json');
const invariant = captured<ReviewResult>('gitTrees.invariant.captured.json');
const entries = captured<unknown>('gitTrees.entries.captured.json');
const cards = captured<ReviewTreesResult>('gitTrees.cards.captured.json');
const leafTrees = captured<ReviewTreesResult>('../../data/reviewTrees.captured.json');
const NEXT_MEMBER = 'f0f0f0f0-0000-5000-8000-000000000002';
const NEXT_FAMILY = 'f0f0f0f0-0000-5000-8000-000000000003';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

// The body with a successor family revision (a clause added to the guarantee) and, for the member
// review, a successor revision of the selected member ("is refused" -> "is rejected and reported").
// `sameRevision`: the guarantee's text changes under its one revision (MIK-R21 allows it).
function successor(result: ReviewResult, tree: boolean, sameRevision = false): ReviewResult {
  const body = structuredClone(result);
  const payload = body.payload!;
  if (!tree) payload.limitations = payload.limitations.filter((t) => !t.startsWith('review:'));
  const entry = payload.family_context!.entries[0];
  const guarantee = entry.after.guarantee!;
  entry.after.guarantee = {
    ...guarantee,
    ...(sameRevision ? {} : { revision_id: NEXT_FAMILY, display_version: 'r3' }),
    joint_guarantee: guarantee.joint_guarantee.replace(
      'is refused by name.',
      'is refused by name, and the refusal says what each snapshot answered.',
    ),
  };
  const selection = payload.knowledge.revision_selection!;
  if (selection.record_kind !== 'invariant') return body;
  const statement = payload.knowledge.after_statement.text!.replace(
    'is refused',
    'is rejected and reported',
  );
  entry.after.members = entry.after.members.map((member) =>
    member.invariant_revision_id === selection.after_revision_id
      ? { ...member, invariant_revision_id: NEXT_MEMBER, display_version: 'r3', statement }
      : member,
  );
  payload.knowledge.revision_selection = { ...selection, after_revision_id: NEXT_MEMBER };
  payload.knowledge.after_statement = { ...payload.knowledge.after_statement, text: statement };
  return body;
}

function serve(tree: boolean, sameRevision = false) {
  const bodies = new Map(
    [family, invariant].map((result) => [
      result.payload!.knowledge.revision_selection!.record_id,
      successor(result, tree, sameRevision),
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
  serve(true);
  const view = open(invariant);
  const center = await view.findByTestId('review-center-member');
  const passages = await within(center).findAllByTestId('intent-diff');
  expect(passages.map((node) => node.dataset.field)).toEqual(['guarantee', 'statement']);
  const statement = passages[1];
  expect(within(center).getByTestId('review-center-statement-label').textContent).toBe(
    'Changed statement · revision r2 → r3',
  );
  const spoken = within(statement).getByTestId('intent-diff-inline').textContent!;
  expect(spoken.replace(/\s+/g, ' ')).toContain(
    'is removed: refused added: rejected and reported with source_content_unresolved',
  );
  const guarantee = within(center).getByTestId('review-center-guarantee-changed');
  expect(guarantee.dataset.wordDiff).toBe('true');
  expect(within(guarantee).getByTestId('review-center-guarantee-label').textContent).toBe(
    'Changed guarantee · revision r2 → r3',
  );
  // No code editor draws the prose (the center's changed code card keeps its own diff).
  expect(within(guarantee).queryByTestId('diff-pane')).toBeNull();
  const intent = within(center).getByTestId('review-center-member-changed');
  expect(within(intent).queryByTestId('diff-pane')).toBeNull();
  cleanup();
  const familyView = open(family);
  const familyCenter = await familyView.findByTestId('review-center-family');
  await waitFor(() =>
    expect(
      within(familyCenter).getByTestId('review-center-guarantee-changed').dataset.wordDiff,
    ).toBe('true'),
  );
});

it('keeps the landed statement and guarantee rendering for a dataset review', async () => {
  serve(false);
  const view = open(invariant);
  const center = await view.findByTestId('review-center-member');
  await within(center).findByTestId('review-center-member-changed');
  expect(within(center).queryByTestId('intent-diff')).toBeNull();
  const guarantee = within(center).getByTestId('review-center-guarantee-changed');
  expect(guarantee.dataset.wordDiff).toBeUndefined();
  expect(within(guarantee).getByTestId('diff-pane')).toBeTruthy();
});

// Review R1 F2: one guarantee revision whose two texts differ is never called unchanged on a tree
// review -- not in the center, not in the family navigator, not in the center's details.
it('never calls one guarantee revision unchanged when its texts differ, anywhere on a tree review', async () => {
  serve(true, true);
  const view = open(invariant);
  const center = await view.findByTestId('review-center-member');
  expect(within(center).getByTestId('review-center-guarantee-label').textContent).toBe(
    'Changed guarantee · revision r2 → r2 · the same revision on both sides; its text differs',
  );
  const rail = view
    .getAllByTestId('review-family-guarantee')
    .map((node) => node.firstElementChild!.textContent);
  expect(rail).toEqual([
    'Joint guarantee · before · same revision, text differs',
    'Joint guarantee · after · same revision, text differs',
  ]);
  expect(
    within(center).getByTestId('review-center-facts').querySelector('[data-fact="guarantee"]')!
      .textContent,
  ).toBe('guarantee: same_revision_text_changed');
  cleanup();
  serve(false, true);
  const dataset = open(invariant);
  await dataset.findByTestId('review-center-member');
  expect(
    dataset
      .getAllByTestId('review-family-guarantee')
      .map((node) => node.firstElementChild!.textContent),
  ).toEqual(['Joint guarantee · unchanged']);
});

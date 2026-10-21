// Current L37 raw tree-route answers: family/member reading, and the reading column's cards (MIK-R89
// v2 rule 2, W1, W2: each subject's changed locations as one card per entry under its own heading,
// its unchanged and not comparable locations as two closed groups).
// Provenance: subjectReview.capture-provenance.json and l86.capture-provenance.json.
// The full-tree fixture is historical, used only for isolated worklist chrome;
// these tests do not qualify the separately reported current full-tree refusal.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ReviewResult } from '../../data/review';
import type { ReviewTreesResult } from '../../data/reviewTrees';
import { ReviewSurface } from './ReviewSurface';
import { resetCardCachesForTests } from '../../data/reviewTrees';

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
  resetCardCachesForTests();
  cleanup();
  vi.unstubAllGlobals();
});

const requested: URL[] = [];

const lane = captured<ReviewTreesResult>('l86.lane.captured.json');

function serve(leafWide: ReviewTreesResult = leafTrees) {
  requested.length = 0;
  const familyId = family.payload!.knowledge.revision_selection!.record_id;
  const invariantId = invariant.payload!.knowledge.revision_selection!.record_id;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string) => {
      const url = new URL(address, 'http://localhost');
      const body = url.pathname.endsWith('/trees')
        ? url.searchParams.get('invariants')
          ? cards
          : url.searchParams.get('lane')
            ? lane
            : leafWide
        : url.pathname.endsWith('/entries')
          ? entries
          : url.pathname.endsWith('/source-content')
            ? {
                state: 'refused',
                refusal: {
                  code: 'not-found',
                  detail: 'Source bytes are not part of this check.',
                  next_action: 'Open the mounted source endpoint.',
                },
              }
            : url.searchParams.get('selectorId') === familyId
              ? family
              : url.searchParams.get('selectorId') === invariantId
                ? invariant
                : null;
      requested.push(url);
      if (body === null) throw new Error(`Unexpected review request: ${address}`);
      return { ok: true, status: 200, json: async () => body } as Response;
    }),
  );
}

it('renders a converted leaf family review from its trees and opens the touched member', async () => {
  serve();
  const payload = family.payload!;
  expect(payload.limitations).toContain('review:trees:5');
  const view = render(
    <ReviewSurface
      repo={payload.candidate.repository_id}
      master={payload.candidate.master}
      leaf={payload.candidate.leaf_id}
      selectorKind="family"
      selectorId={payload.knowledge.revision_selection!.record_id}
      onBack={() => undefined}
    />,
  );
  await view.findByTestId('review-center-family');
  expect(view.getByTestId('review-scope-record').textContent).toContain(
    `${payload.source.inventory.listed_total} changed files`,
  );
  expect(view.queryByTestId('review-inventory-entry')).toBeNull();
  // The family and its whole recorded roster, read from the memory trees' indexes.
  const entry = payload.family_context!.entries[0];
  expect(entry.display_label).toBe('knowledge-write-and-publication-foundation');
  // The family's members follow its content and its members' code (MIK-R89 v2 rule 1, 9dde2b09).
  const members = within(view.getByTestId('review-center-family-details')).getAllByTestId(
    'review-center-open-member',
  );
  expect(members).toHaveLength(
    new Set(
      [...entry.before.members, ...entry.after.members].map((row) => row.invariant_revision_id),
    ).size,
  );
  expect(entry.before.members).toHaveLength(3);
  // Opening the touched member reads its own review and shows both statements.
  const touched = invariant.payload!.knowledge.revision_selection!;
  fireEvent.click(
    view
      .getAllByTestId('review-center-open-member')
      .find((node) => node.dataset.revision === touched.after_revision_id)!,
  );
  const center = await view.findByTestId('review-center-member');
  const statement = within(center).getByTestId('review-center-member-changed');
  // Both revision ids are Technical details' (rule 6, 9dde2b09).
  const revisions = view.getByTestId('review-center-member-revisions');
  expect(revisions.closest('details')?.open).toBe(false);
  expect(revisions.textContent).toContain(touched.before_revision_id);
  expect(revisions.textContent).toContain(touched.after_revision_id);
  expect(
    within(statement)
      .getAllByTestId('intent-diff')
      .some((node) => node.dataset.field === 'statement'),
  ).toBe(true);
  expect(center.dataset.family).toBe(entry.family_id);
  // The member's own locations stand under its heading in the column, one card per changed entry;
  // its not comparable entries are lines of its closed group (W1, rule 5).
  const section = view
    .getAllByTestId('review-subject-section')
    .find((node) => node.dataset.subject === touched.record_id)!;
  const cards = within(section).getAllByTestId('review-expression-card');
  expect(cards.map((card) => card.dataset.entries).sort()).toEqual(['PRF-TMK4N3', 'RLZ-6HAWT71R']);
  const notComparable = within(section)
    .getAllByTestId('review-location-group')
    .find((group) => group.dataset.group === 'not-comparable')!;
  fireEvent.click(within(notComparable).getByTestId('review-location-group-toggle'));
  expect(
    within(notComparable)
      .getAllByTestId('review-location-line')
      .some((line) => line.dataset.entry === 'RLZ-Y766ENWA'),
  ).toBe(true);
});

it('shows the current family as focused cards with changed and unchanged expressions', async () => {
  serve();
  const payload = family.payload!;
  const view = render(
    <ReviewSurface
      repo={payload.candidate.repository_id}
      master={payload.candidate.master}
      leaf={payload.candidate.leaf_id}
      selectorKind="family"
      selectorId={payload.knowledge.revision_selection!.record_id}
      onBack={() => undefined}
    />,
  );
  const center = await view.findByTestId('review-center-family');
  await waitFor(() => expect(view.getAllByTestId('review-expression-card').length).toBeGreaterThan(0));
  // The cards read (read 5) names the review's own comparison and the opening's shown subjects,
  // the family's two changed members among them, sorted and distinct (W4).
  const read = requested.find((url) => url.searchParams.get('invariants'))!;
  expect(read.searchParams.get('comparison')).toBe('5');
  const asked = read.searchParams.get('invariants')!.split(',');
  expect(asked).toEqual([...new Set(asked)].sort());
  expect(asked).toEqual(expect.arrayContaining(['8b1a17ca-96ba-5ede-a09f-abfb11ada0a5', 'c88cc287-0469-556c-9f42-b742b2377478']));
  // The leaf-wide read is pinned to the same comparison (review F11).
  const leafWide = requested.find(
    (url) =>
      url.pathname.endsWith('/trees') &&
      !url.searchParams.get('invariants') &&
      !url.searchParams.get('lane'),
  )!;
  expect(leafWide.searchParams.get('comparison')).toBe('5');
  expect(leafWide.searchParams.get('history')).toBeNull();
  // No accordion of whole files: one card per changed entry, under its subject's heading.
  expect(view.queryByTestId('review-expression-diffs')).toBeNull();
  const all = view.getAllByTestId('review-expression-card');
  expect(all.map((card) => card.dataset.entries).sort()).toEqual(['PRF-TMK4N3', 'RLZ-6HAWT71R', 'RLZ-K64BDQNF']);
  const proof = all.find((card) => card.dataset.cardKind === 'proof')!;
  expect(proof.dataset.entries).toBe('PRF-TMK4N3');
  expect(within(proof).getByTestId('review-block-role').textContent).toBe('proof');
  const changed = all.find((card) => card.dataset.cardKind !== 'proof')!;
  expect(within(changed).getByTestId('review-card-excerpt').dataset.excerpt).toBe('diff');
  const rationale = within(changed).getByTestId('review-card-rationales');
  const excerpt = within(changed).getByTestId('review-card-excerpt');
  expect(
    rationale.compareDocumentPosition(excerpt) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  // Unchanged locations are lines of a closed group, never cards (rule 5).
  const groups = view.getAllByTestId('review-location-group').filter((group) => group.dataset.group === 'unchanged');
  expect(groups.length).toBeGreaterThan(0);
  // Worklist records have their own view; the subject shows only its own items, after its code.
  expect(center).toBeTruthy();
  const block = within(view.getByTestId('review-center-family-details')).getByTestId('review-subject-worklist');
  expect(block.textContent).toContain('Worklist items about this record');
  expect(requested.some((url) => url.searchParams.has('lane'))).toBe(true);
  fireEvent.click(view.getByTestId('review-tree-worklist'));
  const knowledge = view.getByTestId('review-worklist-view');
  expect(
    within(knowledge)
      .getAllByTestId('review-worklist-item')
      .map((row) => row.dataset.kind)
      .sort(),
  ).toEqual(leafTrees.worklist!.items.map((item) => item.kind).sort());
  expect(knowledge.querySelector('pre')).toBeNull();
});

it('takes no planning mark from a leaf-wide read of another comparison (review R2-3)', async () => {
  // The leaf-wide body answers comparison 2 while the payload was composed over comparison 1: its
  // worklist describes other trees, so the cards must not carry its planned/unplanned marks.
  serve({ ...leafTrees, comparison: { ...leafTrees.comparison!, number: 2 } });
  const payload = family.payload!;
  const view = render(
    <ReviewSurface
      repo={payload.candidate.repository_id}
      master={payload.candidate.master}
      leaf={payload.candidate.leaf_id}
      selectorKind="family"
      selectorId={payload.knowledge.revision_selection!.record_id}
      onBack={() => undefined}
    />,
  );
  await view.findByTestId('review-center-family');
  const panel = await within(view.getByTestId('review-center-family-details')).findByTestId('review-subject-worklist');
  expect(panel.textContent).toContain('The available answer belongs to comparison 2.');
  await waitFor(() => expect(view.getAllByTestId('review-expression-card').length).toBeGreaterThan(0));
  const voices = view.getAllByTestId('review-card-voice');
  expect(voices.length).toBeGreaterThan(0);
  for (const voice of voices) expect(voice.textContent).not.toMatch(/· (planned|unplanned)/);
});

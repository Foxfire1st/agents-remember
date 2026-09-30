// MIK-R25 rule 6 and MIK-R31 on real data: the review workspace over a converted leaf's Git trees.
//
// The bodies are the REAL served answers of the reviewer routes for the 260928-MIK-L31 worker's
// scratch copy of the real repositories after conversion (provenance: gitTrees.capture-provenance
// .json): the leaf edits `_not_listed` in review_source_admission.py, re-anchors the realization it
// touches, adds a (scratch-authored) proof and declares two expected effects. Each memory side was
// read through the derived index of its tree, never a dataset copy. `ReviewSurface` is the real
// component and only `fetch` is stubbed. The workspace and its navigation behave as over datasets;
// the central reading path shows the selected family's focused expression cards (MIK-R31).
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
const family = captured<ReviewResult>('gitTrees.family.captured.json');
const invariant = captured<ReviewResult>('gitTrees.invariant.captured.json');
const entries = captured<unknown>('gitTrees.entries.captured.json');
const cards = captured<ReviewTreesResult>('gitTrees.cards.captured.json');
const leafTrees = captured<ReviewTreesResult>('../../data/reviewTrees.captured.json');
const CHANGED = 'mcp/src/agents_remember/application/review_source_admission.py';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const requested: URL[] = [];

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
  expect(payload.limitations).toContain('review:trees:1');
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
  // The complete source inventory: the one changed file.
  const inventory = view.getAllByTestId('review-inventory-entry').map((row) => row.textContent);
  expect(inventory.some((text) => text?.includes(CHANGED))).toBe(true);
  // The family and its whole recorded roster, read from the memory trees' indexes.
  const entry = payload.family_context!.entries[0];
  expect(entry.display_label).toBe('Comparison-bound unchanged realization context');
  const rendered = view
    .getAllByTestId('review-family')
    .find((node) => node.dataset.family === entry.family_id)!;
  const members = within(rendered).getAllByTestId('review-family-member');
  expect(members).toHaveLength(entry.before.members.length);
  expect(entry.before.members).toHaveLength(7);
  // Opening the touched member reads its own review and shows both statements.
  const touched = invariant.payload!.knowledge.revision_selection!;
  fireEvent.click(
    view
      .getAllByTestId('review-family-member-open')
      .find((node) => node.dataset.revision === touched.after_revision_id)!,
  );
  const center = await view.findByTestId('review-center-member');
  await waitFor(() =>
    expect(center.textContent).toContain(
      invariant.payload!.knowledge.after_statement.text!.slice(0, 60),
    ),
  );
  expect(center.dataset.family).toBe(entry.family_id);
  // The member keeps its family's cards, its own entries first (the seed of MIK-R01's order).
  const seeded = await within(center).findAllByTestId('review-expression-card');
  expect(seeded[0].dataset.entries).toBe('RLZ-CXH58B4W');
  expect(seeded[1].dataset.entries).toBe('PRF-7Q3M5K');
});

it('shows the family around _not_listed as focused cards: one changed range, the rest unchanged', async () => {
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
  await waitFor(() =>
    expect(within(center).getByTestId('review-expression-cards').dataset.cardsState).toBe('ready'),
  );
  const section = within(center).getByTestId('review-expression-cards');
  // The cards read names the review's own comparison and every member of the family.
  const read = requested.find((url) => url.searchParams.get('invariants'))!;
  expect(read.searchParams.get('comparison')).toBe('1');
  // The leaf-wide read is pinned to the same comparison (review F11).
  const leafWide = requested.find(
    (url) => url.pathname.endsWith('/trees') && !url.searchParams.get('invariants'),
  )!;
  expect(leafWide.searchParams.get('comparison')).toBe('1');
  expect(leafWide.searchParams.get('history')).toBeNull();
  expect(read.searchParams.get('invariants')!.split(',')).toHaveLength(7);
  // No accordion of whole files: the center holds cards, one per (path, range).
  expect(within(center).queryByTestId('review-expression-diffs')).toBeNull();
  const all = within(section).getAllByTestId('review-expression-card');
  expect(all).toHaveLength(11);
  expect([section.dataset.changedCount, section.dataset.unchangedCount]).toEqual(['1', '10']);
  // The packet's conforming example: four cards in the edited file, distinct regions, each with its
  // role and its own rationale; the one changed range is a diff, the other three are unchanged. They
  // come in the family order (member by ID: INV-2TQGXFAX, -9ECAFVTQ, -XTXZESS6, -YBS0CVBK).
  const inFile = all.filter((card) => card.dataset.path === CHANGED);
  expect(inFile.map((card) => [card.dataset.entries, card.dataset.cardChange])).toEqual([
    ['RLZ-CXH58B4W', 'changed'],
    ['RLZ-D43E5CF2', 'unchanged'],
    ['RLZ-9EC7B6PN', 'unchanged'],
    ['RLZ-NM6160PK', 'unchanged'],
  ]);
  const changed = inFile[0];
  expect(changed.dataset.beforeRange).toBe('L194–213');
  expect(within(changed).getByTestId('review-card-rationale').textContent).toContain(
    'Builds the source_content_unresolved refusal',
  );
  expect(changed.textContent).toContain('Code expression · primary-authority');
  const rationale = within(changed).getByTestId('review-card-rationales');
  const excerpt = within(changed).getByTestId('review-card-excerpt');
  expect(excerpt.dataset.excerpt).toBe('diff');
  expect(
    rationale.compareDocumentPosition(excerpt) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(within(inFile[1]).getByTestId('review-card-excerpt').dataset.excerpt).toBe('unchanged');
  expect(
    new Set(inFile.map((card) => within(card).getByTestId('review-card-rationale').textContent))
      .size,
  ).toBe(4);
  // The proof entry's card names its facet in place of a role and a rationale.
  const proof = all.find((card) => card.dataset.cardKind === 'proof')!;
  expect(proof.textContent).toContain('Test proof · facet');
  expect(within(proof).getByTestId('review-card-rationale').textContent).toContain(
    'no content is read for it',
  );
  // MIK-R11's marks from the worklist: the touched member is planned.
  expect(within(changed).getByTestId('review-card-voice').textContent).toContain(
    'touched invariant · planned',
  );
  // The leaf's knowledge changes and worklist remain reachable in the same reading path.
  const knowledge = within(center).getByTestId('review-leaf-knowledge');
  expect(
    within(knowledge)
      .getAllByTestId('review-worklist-item')
      .map((row) => row.dataset.kind),
  ).toContain('planned_untouched');
});

it('leaves a dataset review exactly as it was: no tree read, the landed file view', async () => {
  // A dataset (unconverted) review declares no `review:trees:<n>`, so the surface asks the tree view
  // nothing and renders the landed expression view (MIK-R31 Transition: unconverted is unchanged).
  const dataset = captured<ReviewResult>('familyReview.complete.captured.json');
  expect(dataset.payload!.limitations.some((token) => token.startsWith('review:trees:'))).toBe(
    false,
  );
  const urls: URL[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string) => {
      const url = new URL(address, 'http://localhost');
      urls.push(url);
      const body = url.pathname.endsWith('/entries')
        ? { state: 'entries', entries: [], total_subjects: 0 }
        : dataset;
      return { ok: true, status: 200, json: async () => body } as Response;
    }),
  );
  const payload = dataset.payload!;
  const view = render(
    <ReviewSurface
      repo={payload.candidate.repository_id}
      master={payload.candidate.master}
      leaf={payload.candidate.leaf_id}
      selectorKind="invariant"
      selectorId={payload.knowledge.revision_selection!.record_id}
      onBack={() => undefined}
    />,
  );
  await view.findByTestId('review-expression-diffs');
  expect(view.queryByTestId('review-expression-cards')).toBeNull();
  expect(view.queryByTestId('review-leaf-knowledge')).toBeNull();
  expect(urls.some((url) => url.pathname.endsWith('/trees'))).toBe(false);
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
  const center = await view.findByTestId('review-center-family');
  const panel = await within(center).findByTestId('review-leaf-knowledge');
  expect(panel.textContent).toContain(
    'this view reads comparison 2, the review shows comparison 1',
  );
  await waitFor(() =>
    expect(within(center).getByTestId('review-expression-cards').dataset.cardsState).toBe('ready'),
  );
  const voices = within(center).getAllByTestId('review-card-voice');
  expect(voices.length).toBeGreaterThan(0);
  for (const voice of voices) expect(voice.textContent).not.toMatch(/· (planned|unplanned)/);
});

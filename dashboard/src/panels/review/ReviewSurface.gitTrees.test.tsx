// MIK-R25 rule 6 on real data: the landed review workspace over a converted leaf's Git trees.
//
// The three bodies are the REAL served answers of the reviewer routes for the 260928-MIK-L25 worker's
// scratch copy of the real repositories after conversion (provenance: gitTrees.capture-provenance
// .json): the leaf edits `_not_listed` in review_source_admission.py and re-anchors the realization
// it touches. Each memory side was read through the derived index of its tree, never a dataset copy.
// `ReviewSurface` is the real component and only `fetch` is stubbed. The workspace and its
// navigation behave exactly as over datasets: only the data source changed.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ReviewResult } from '../../data/review';
import { ReviewSurface } from './ReviewSurface';

const captured = <T,>(name: string): T =>
  JSON.parse(
    readFileSync(path.join(path.dirname(new URL(import.meta.url).pathname), name), 'utf8'),
  ) as T;
const family = captured<ReviewResult>('gitTrees.family.captured.json');
const invariant = captured<ReviewResult>('gitTrees.invariant.captured.json');
const entries = captured<unknown>('gitTrees.entries.captured.json');
const CHANGED = 'mcp/src/agents_remember/application/review_source_admission.py';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function serve() {
  const familyId = family.payload!.knowledge.revision_selection!.record_id;
  const invariantId = invariant.payload!.knowledge.revision_selection!.record_id;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string) => {
      const url = new URL(address, 'http://localhost');
      const body = url.pathname.endsWith('/entries')
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
      if (body === null) throw new Error(`Unexpected review request: ${address}`);
      return { ok: true, status: 200, json: async () => body } as Response;
    }),
  );
}

it('renders a converted leaf family review from its trees and opens the touched member', async () => {
  serve();
  const payload = family.payload!;
  expect(payload.limitations).toContain('review:trees:2');
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
});

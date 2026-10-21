// Genuine current tree-route bodies; see subjectReview.capture-provenance.json.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { cleanup, fireEvent, render, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ReviewPayload, ReviewResult } from '../../data/review';
import { ReviewSurface } from './ReviewSurface';
import { resetCardCachesForTests } from '../../data/reviewTrees';

const captured = (name: string): ReviewResult =>
  JSON.parse(
    readFileSync(path.join(path.dirname(new URL(import.meta.url).pathname), name), 'utf8'),
  ) as ReviewResult;
const successorFamily = captured('subjectReview.successorFamily.captured.json');
const successorInvariant = captured('subjectReview.successorInvariant.captured.json');
const noFamily = captured('subjectReview.noFamily.captured.json');
const response = (body: unknown): Response =>
  ({ ok: true, status: 200, json: async () => body }) as Response;

afterEach(() => {
  resetCardCachesForTests();
  cleanup();
  vi.unstubAllGlobals();
});

function serve(family: ReviewResult | null, invariant: ReviewResult) {
  const requests: URL[] = [];
  const subject = invariant.payload!.knowledge.revision_selection!;
  const familySubject = family?.payload?.knowledge.revision_selection;
  const entries = [
    {
      selector_kind: 'invariant',
      selector_id: subject.record_id,
      label: 'Recorded invariant',
      presence: 'both',
    },
  ];
  if (familySubject)
    entries.push({
      selector_kind: 'family',
      selector_id: familySubject.record_id,
      label: 'Recorded family',
      presence: 'both',
    });
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string) => {
      const url = new URL(address, 'http://localhost');
      requests.push(url);
      if (url.pathname.endsWith('/source-content'))
        return response({
          state: 'refused',
          refusal: {
            code: 'not-found',
            detail: 'Captured source bytes are not part of this subject-read check.',
            next_action: 'Inspect the mounted source endpoint.',
          },
        });
      if (url.pathname.endsWith('/entries'))
        return response({
          state: 'entries',
          entries,
          total_subjects: entries.length,
          family_total: family ? 1 : 0,
          invariant_total: 1,
        });
      if (
        url.searchParams.get('selectorKind') === 'invariant' &&
        url.searchParams.get('selectorId') === subject.record_id
      )
        return response(invariant);
      if (
        family &&
        url.searchParams.get('selectorKind') === 'family' &&
        url.searchParams.get('selectorId') === familySubject?.record_id
      )
        return response(family);
      throw new Error(`Unexpected review request: ${address}`);
    }),
  );
  return requests;
}

function mount(payload: ReviewPayload) {
  const candidate = payload.candidate;
  const selected = payload.knowledge.revision_selection!;
  return render(
    <ReviewSurface
      repo={candidate.repository_id}
      master={candidate.master}
      leaf={candidate.leaf_id}
      selectorKind={selected.record_kind}
      selectorId={selected.record_id}
      onBack={() => undefined}
    />,
  );
}

it('compares an authored successor through the family member route using both authoritative revision IDs and statements', async () => {
  const requests = serve(successorFamily, successorInvariant);
  const payload = successorInvariant.payload!;
  const selected = payload.knowledge.revision_selection!;
  expect(selected.state).toBe('compared');
  expect(selected.before_revision_id).not.toBe(selected.after_revision_id);
  const view = mount(successorFamily.payload!);
  await view.findByTestId('review-center-family');
  fireEvent.click(
    view
      .getAllByTestId('review-center-open-member')
      .find((node) => node.dataset.revision === selected.after_revision_id)!,
  );
  const changed = await view.findByTestId('review-center-member-changed');
  const statement = within(changed)
    .getAllByTestId('intent-diff')
    .find((node) => node.dataset.field === 'statement')!;
  expect(statement).toBeDefined();
  // Reconstruct each actual displayed side, excluding the opposite edit and screen-reader labels.
  for (const side of ['before', 'after'] as const) {
    const displayed = within(statement)
      .getByTestId('intent-diff-inline')
      .cloneNode(true) as HTMLElement;
    displayed.querySelectorAll(side === 'before' ? 'ins' : 'del').forEach((node) => node.remove());
    displayed
      .querySelectorAll('del > span:not([aria-hidden]), ins > span:not([aria-hidden])')
      .forEach((node) => node.remove());
    const text = displayed
      .textContent!.replace(/·/g, ' ')
      .replace(/→/g, '\t')
      .replace(/↵\n/g, '\n');
    expect(text).toBe(payload.knowledge[`${side}_statement`].text);
  }
  // The two revision ids are Technical details' (MIK-R89 v2 rule 6, amended 9dde2b09).
  const revisions = view.getByTestId('review-center-member-revisions');
  expect(revisions.textContent).toContain(selected.before_revision_id);
  expect(revisions.textContent).toContain(selected.after_revision_id);
  expect(view.queryByTestId('review-center-member-one-sided')).toBeNull();
  expect(view.getByTestId('review-center-member').dataset.family).toBe(
    payload.family_context!.entries[0].family_id,
  );
  expect(requests.some((url) => url.searchParams.get('selectorId') === selected.record_id)).toBe(
    true,
  );
  const judgment = view.getByTestId('review-center-judgment');
  expect(judgment.dataset.judgmentState).toBe(payload.judgment.state);
  expect(
    within(judgment)
      .getAllByTestId('review-judgment-row')
      .map((row) => row.textContent),
  ).toEqual(payload.judgment.rows.map((row) => expect.stringContaining(row.reason!)));
});

it('keeps confirmed no-family intent and judgment in pane order', async () => {
  serve(null, noFamily);
  expect(noFamily.payload!.family_context!.state).toBe('no_family_recorded');
  const view = mount(noFamily.payload!);
  const center = await view.findByTestId('review-center-member');
  // A measured zero is the normal case: nothing names it (MIK-R89 v2 rule 1, amended 9dde2b09).
  expect(within(center).queryByTestId('review-center-member-family')).toBeNull();
  expect(view.getByTestId('review-subject-meta').textContent).not.toContain('family');
  const statement = within(center).getByTestId('review-center-member-unchanged');
  expect(statement.textContent).toContain(noFamily.payload!.knowledge.after_statement.text);
  // The subject's code locations are the reading column's blocks under its heading, for every
  // subject alike (MIK-R89 v2 rule 2); the slot keeps the statement, then the judgment.
  expect(within(center).queryByTestId('review-expression-cards')).toBeNull();
  const judgment = within(center).getByTestId('review-center-judgment');
  expect(
    statement.compareDocumentPosition(judgment) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(judgment.dataset.judgmentState).toBe('none');
  expect(judgment.textContent).toContain(noFamily.payload!.judgment.detail);
});

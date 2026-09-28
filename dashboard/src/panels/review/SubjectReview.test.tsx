// Real review-route bodies (family, invariant) and exact owner-produced responses (single-head cases)
// from the frozen reviewer counterexamples. See the adjacent receipt.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ReviewPayload, ReviewResult } from '../../data/review';
import { ReviewSurface } from './ReviewSurface';

const captured = (name: string): ReviewResult =>
  JSON.parse(
    readFileSync(path.join(path.dirname(new URL(import.meta.url).pathname), name), 'utf8'),
  ) as ReviewResult;
const assessedFamily = captured('subjectReview.family.captured.json');
const assessedInvariant = captured('subjectReview.invariant.captured.json');
const successorFamily = captured('subjectReview.successorFamily.captured.json');
const successorInvariant = captured('subjectReview.successorInvariant.captured.json');
const noFamily = captured('subjectReview.noFamily.captured.json');
const response = (body: unknown): Response =>
  ({ ok: true, status: 200, json: async () => body }) as Response;

afterEach(() => {
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

it('reads the clicked member owner before displaying its concern, keeps family context, and preserves ambiguous revisions', async () => {
  const requests = serve(assessedFamily, assessedInvariant);
  const family = assessedFamily.payload!.family_context!.entries[0];
  const subject = assessedInvariant.payload!.knowledge.revision_selection!;
  const member = family.before.members.find((row) => row.invariant_id === subject.record_id)!;
  const assessment = assessedInvariant.payload!.evidence.assessments[0];
  expect(assessedFamily.payload!.evidence.assessments).toHaveLength(0);
  const view = mount(assessedFamily.payload!);
  await view.findByTestId('review-center-family');
  const population = view.getAllByTestId('review-inventory-entry').map((row) => row.textContent);
  const memberButton = view
    .getAllByTestId('review-family-member-open')
    .find((node) => node.dataset.revision === member.invariant_revision_id)!;
  fireEvent.click(memberButton);
  const center = await view.findByTestId('review-center-member');
  await waitFor(() =>
    expect(within(center).getByTestId('review-center-assessment').textContent).toContain(
      assessment.finding,
    ),
  );
  expect(
    requests.some(
      (url) =>
        url.searchParams.get('selectorKind') === 'invariant' &&
        url.searchParams.get('selectorId') === subject.record_id,
    ),
  ).toBe(true);
  const judgment = within(center).getByTestId('review-center-assessment');
  expect(judgment.textContent).toContain('concern_found');
  expect(judgment.dataset.binding).toBe(assessment.binding_state);
  expect(judgment.querySelector('[data-applicability]')?.getAttribute('data-applicability')).toBe(
    'direct',
  );
  expect(within(center).queryByTestId('review-center-unassessed')).toBeNull();
  expect(within(center).getByTestId('review-center-member-ambiguous')).toBeTruthy();
  expect(within(center).queryByTestId('review-center-member-changed')).toBeNull();
  expect(within(center).getByTestId('review-center-observation').textContent).toContain(
    'not a subject judgment',
  );
  expect(center.dataset.family).toBe(family.family_id);
  const retainedFamily = view
    .getAllByTestId('review-family')
    .find((node) => node.dataset.family === family.family_id)!;
  expect(within(retainedFamily).getAllByTestId('review-family-member')).toHaveLength(
    family.before.members.length,
  );
  expect(view.getAllByTestId('review-inventory-entry').map((row) => row.textContent)).toEqual(
    population,
  );
});

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
      .getAllByTestId('review-family-member-open')
      .find((node) => node.dataset.revision === selected.after_revision_id)!,
  );
  const changed = await view.findByTestId('review-center-member-changed');
  await waitFor(() =>
    expect(changed.textContent).toContain(payload.knowledge.before_statement.text),
  );
  expect(changed.textContent).toContain(payload.knowledge.after_statement.text);
  expect(changed.textContent).toContain(selected.before_revision_id);
  expect(changed.textContent).toContain(selected.after_revision_id);
  expect(view.queryByTestId('review-center-member-one-sided')).toBeNull();
  expect(view.getByTestId('review-center-member').dataset.family).toBe(
    payload.family_context!.entries[0].family_id,
  );
  expect(requests.some((url) => url.searchParams.get('selectorId') === selected.record_id)).toBe(
    true,
  );
  expect(view.getByTestId('review-center-unassessed').textContent).toContain(
    'no authored judgment was returned',
  );
});

it('keeps confirmed no-family intent and its unassessed evidence in the same central path', async () => {
  serve(null, noFamily);
  expect(noFamily.payload!.family_context!.state).toBe('no_family_recorded');
  const view = mount(noFamily.payload!);
  const center = await view.findByTestId('review-center-member');
  expect(within(center).getByTestId('review-center-member-family').textContent).toBe(
    'No recorded family',
  );
  const statement = within(center).getByTestId('review-center-member-changed');
  const expressions = within(center).getByTestId('review-expression-diffs');
  const evidence = within(center).getByTestId('review-center-evidence');
  expect(
    statement.compareDocumentPosition(expressions) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(
    expressions.compareDocumentPosition(evidence) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(within(center).getByTestId('review-center-unassessed')).toBeTruthy();
});

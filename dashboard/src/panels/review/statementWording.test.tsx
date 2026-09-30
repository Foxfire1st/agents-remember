// MIK-R31 rule 3 (the 13:40 decision): "wording unchanged" only when every authored text field of the
// two revisions is identical; shown once, with the revisions as compact metadata and IDs in details.
// The knowledge pane and member rows start from the REAL served invariant review of the converted
// scratch leaf (gitTrees.invariant.captured.json); the second revision is derived from it by changing
// exactly the field each case is about.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { cleanup, render } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import type { ReviewFamilyMember, ReviewKnowledgePane, ReviewResult } from '../../data/review';
import { SelectedStatement } from './SubjectReview';
import { wordingComparison } from './statementWording';

const payload = (
  JSON.parse(
    readFileSync(
      path.join(
        path.dirname(new URL(import.meta.url).pathname),
        'gitTrees.invariant.captured.json',
      ),
      'utf8',
    ),
  ) as ReviewResult
).payload!;
const selection = payload.knowledge.revision_selection!;
const subject = { kind: 'invariant' as const, id: selection.record_id };
const row = payload.family_context!.entries[0].before.members.find(
  (member) => member.invariant_revision_id === selection.before_revision_id,
)!;
const NEXT = 'f0f0f0f0-0000-5000-8000-000000000002';

afterEach(cleanup);

function successor(change: Partial<ReviewFamilyMember> = {}) {
  const after = { ...row, invariant_revision_id: NEXT, display_version: 'r3', ...change };
  const knowledge: ReviewKnowledgePane = {
    ...payload.knowledge,
    revision_selection: { ...selection, after_revision_id: NEXT },
    after_statement: { ...payload.knowledge.after_statement, text: after.statement },
    after_conditions: after.essential_conditions,
  };
  return render(
    <SelectedStatement
      knowledge={knowledge}
      subject={subject}
      layout="split"
      members={{ before: [{ ...row, display_version: 'r2' }], after: [after] }}
    />,
  );
}

describe('wording unchanged', () => {
  it('renders two revisions with identical authored text once, with the revisions as metadata', () => {
    const view = successor();
    const shown = view.getByTestId('review-center-member-wording-unchanged');
    expect(view.getByTestId('review-center-statement-label').textContent).toBe(
      'Wording unchanged · revision r2 → r3',
    );
    expect(view.getAllByTestId('review-center-statement-prose')).toHaveLength(1);
    // Once in the reading path; the raw records stay behind the closed details disclosure.
    const visible = [...shown.childNodes].filter((node) => node.nodeName !== 'DETAILS');
    expect(
      visible
        .map((node) => node.textContent)
        .join('')
        .split(row.statement!),
    ).toHaveLength(2);
    expect(view.queryByTestId('diff-pane')).toBeNull();
    expect(view.getByTestId('review-center-member-revisions').textContent).toContain(NEXT);
  });

  it('never labels a condition-only revision unchanged: the changed condition is named', () => {
    const conditions = [...row.essential_conditions, 'A new condition the successor adds.'];
    const view = successor({ essential_conditions: conditions });
    expect(view.queryByTestId('review-center-member-wording-unchanged')).toBeNull();
    expect(view.getByTestId('review-center-member-changed')).toBeTruthy();
    expect(view.getByTestId('review-center-statement-label').textContent).toBe(
      'Changed conditions · revision r2 → r3',
    );
    const fields = view.getByTestId('review-center-changed-fields');
    expect(fields.textContent).toContain('A new condition the successor adds.');
    expect(view.getAllByTestId('review-center-statement-prose')).toHaveLength(1);
  });

  it('decides from every field and never promotes an uncarried field to unchanged', () => {
    const base = { statement: 's', applicability: 'a', conditions: ['c'], exclusions: [] };
    expect(wordingComparison(base, { ...base }, false)).toEqual({ kind: 'wording_unchanged' });
    expect(wordingComparison(base, { ...base, exclusions: ['x'] }, false)).toEqual({
      kind: 'changed',
      fields: ['exclusions'],
    });
    expect(wordingComparison(base, { ...base, applicability: undefined }, false)).toEqual({
      kind: 'not_comparable',
      fields: ['applicability'],
    });
    expect(wordingComparison(base, base, true)).toEqual({ kind: 'same_revision' });
  });

  it("keeps each side's own state line when one statement side is not present (F5)", () => {
    const knowledge: ReviewKnowledgePane = {
      ...payload.knowledge,
      revision_selection: { ...selection, after_revision_id: NEXT },
      after_statement: {
        state: 'unresolved',
        language: 'text',
        detail: 'the after revision could not be read',
      },
    };
    const view = render(
      <SelectedStatement knowledge={knowledge} subject={subject} layout="split" />,
    );
    expect(view.queryByTestId('review-center-statement-prose')).toBeNull();
    expect(view.getByTestId('review-after-state').dataset.sideState).toBe('unresolved');
    expect(view.getByTestId('review-before-state').dataset.sideState).toBe('present');
  });

  it('renders an added statement as labelled prose, not a split code editor', () => {
    const knowledge: ReviewKnowledgePane = {
      ...payload.knowledge,
      revision_selection: { ...selection, state: 'added', before_revision_id: undefined },
      before_statement: { state: 'absent', language: 'text', detail: 'no revision before' },
    };
    const view = render(
      <SelectedStatement knowledge={knowledge} subject={subject} layout="split" />,
    );
    expect(view.getByTestId('review-center-statement-label').textContent).toContain(
      'Added statement',
    );
    expect(view.getByTestId('review-center-statement-prose').textContent).toBe(
      payload.knowledge.after_statement.text,
    );
    expect(view.queryByTestId('diff-pane')).toBeNull();
  });
});

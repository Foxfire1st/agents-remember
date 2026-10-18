// L57-CODE-F13: component branch controls, not producer/classifier acceptance.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { cleanup, render, within } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import type { ReviewFamilyContextEntry, ReviewFamilyGuarantee, ReviewResult } from '../../data/review';
import { guaranteeComparison } from '../../data/review';
import { GuaranteeComparisonBlock } from './FamilyGuarantees';
import { TreeComparisonScope } from './IntentWordDiff';

const sameRevision = (JSON.parse(readFileSync(
  path.join(path.dirname(new URL(import.meta.url).pathname), 'familyReview.identical.captured.json'), 'utf8',
)) as ReviewResult).payload!.family_context!.entries[0];

// Deliberately synthetic, independently authored operands: a family revision can change
// metadata (here proposed -> accepted) without changing its only authored prose field.
// The current raw IDENTICAL capture now selects ONE revision, so it cannot defend this
// still-shipped branch. Do not relabel or rewrite that capture to manufacture a witness.
const familyId = 'synthetic-family-metadata-only';
const wording = 'A published review retains the identities of both recorded operands.';
const before: ReviewFamilyGuarantee = {
  family_id: familyId, revision_id: 'synthetic-family-before', display_version: 'r1',
  joint_guarantee: wording, state_at_origin: 'proposed',
  provenance: { control: 'synthetic metadata-only revision' }, payload_digest: 'synthetic-before',
};
const after: ReviewFamilyGuarantee = {
  family_id: familyId, revision_id: 'synthetic-family-after', display_version: 'r2',
  joint_guarantee: wording, state_at_origin: 'accepted', acceptance_ref: 'synthetic-acceptance',
  provenance: { control: 'synthetic metadata-only revision' }, payload_digest: 'synthetic-after',
};
const distinctRevisions: ReviewFamilyContextEntry = {
  family_id: familyId, state: 'recorded', candidates: [],
  detail: 'Synthetic component control: two selected revisions with unchanged guarantee wording.',
  selection: {
    record_kind: 'family', record_id: familyId, state: 'compared',
    before_revision_id: before.revision_id, after_revision_id: after.revision_id,
    before_heads: [before.revision_id], after_heads: [after.revision_id],
    before_retained: [before.revision_id], after_retained: [after.revision_id],
    statement: 'Synthetic metadata-only family revision comparison.',
  },
  before: {
    side: 'before', family_id: familyId, state: 'recorded',
    family_revision_id: before.revision_id, recorded_revision_ids: [before.revision_id],
    guarantee: before, members: [], members_total: 0, detail: 'Synthetic before operand.',
  },
  after: {
    side: 'after', family_id: familyId, state: 'recorded',
    family_revision_id: after.revision_id, recorded_revision_ids: [before.revision_id, after.revision_id],
    guarantee: after, members: [], members_total: 0, detail: 'Synthetic after operand.',
  },
};

afterEach(cleanup);

it.each([false, true])('keeps distinct family revisions with equal wording distinct (tree=%s)', (tree) => {
  const entry = distinctRevisions;
  const before = entry.before.guarantee!;
  const after = entry.after.guarantee!;
  expect(before.revision_id).not.toBe(after.revision_id);
  expect(entry.selection.before_revision_id).toBe(before.revision_id);
  expect(entry.selection.after_revision_id).toBe(after.revision_id);
  expect(before.joint_guarantee).toBe(after.joint_guarantee);
  expect(guaranteeComparison(entry).kind).toBe('identical_text');
  const view = render(<TreeComparisonScope tree={tree}>
    <GuaranteeComparisonBlock entry={entry} layout="inline" />
  </TreeComparisonScope>);
  const block = view.getByTestId('review-center-guarantee-identical-text');
  expect(within(block).getByText('Wording unchanged · revision r1 → r2').closest('details')).toBeNull();
  expect(within(block).getAllByText(after.joint_guarantee, { exact: true })).toHaveLength(1);
  expect(within(block).getByTestId('review-center-guarantee').dataset.revision).toBe(after.revision_id);
  const records = within(block).getByText(
    `before revision ${before.revision_id} · after revision ${after.revision_id}`,
  ).closest('details')!;
  expect(records.open).toBe(false);
  expect(view.queryByTestId('review-center-guarantee-unchanged')).toBeNull();
  expect(view.queryByTestId('review-center-guarantee-changed')).toBeNull();
  expect(view.queryByTestId('intent-diff')).toBeNull();
});

it('retains the current raw same-revision control as unchanged revision, not identical-text', () => {
  const before = sameRevision.before.guarantee!;
  const after = sameRevision.after.guarantee!;
  expect(before.revision_id).toBe(after.revision_id);
  expect(before.joint_guarantee).toBe(after.joint_guarantee);
  expect(guaranteeComparison(sameRevision).kind).toBe('unchanged_revision');
  const view = render(<GuaranteeComparisonBlock entry={sameRevision} layout="inline" />);
  const block = view.getByTestId('review-center-guarantee-unchanged');
  expect(block.textContent).toContain('Guarantee unchanged · same recorded revision');
  expect(within(block).getAllByText(after.joint_guarantee, { exact: true })).toHaveLength(1);
  expect(view.queryByTestId('review-center-guarantee-identical-text')).toBeNull();
});

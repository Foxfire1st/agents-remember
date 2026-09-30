// MIK-R34 (the rulings round, Q3): where an unknown membership's `Attribution unknown` state is shown.
// The payload is the REAL review of INV-Z66EMHMH on comparison 3 of the MIK-L34 scratch leaf, and the
// target is the real before-side occurrence of its FAM-4V4GSQCS membership (markerUnknown.* provenance);
// the one derived body drops the review's family entries, as a review that composed no family has none.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { cleanup, render } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import type { ReviewPayload, ReviewResult } from '../../data/review';
import type { ReviewFileClassification } from '../../data/reviewLane';
import { fileMarks } from './hunkMarkers';
import { type IntentMarkerScopeValue, IntentMarkerScope } from './intentMarkerScope';
import { MemberFamilyLabel, MemberTargetNote } from './MarkerTargetState';

const captured = <T,>(name: string): T =>
  JSON.parse(
    readFileSync(path.join(path.dirname(new URL(import.meta.url).pathname), name), 'utf8'),
  ) as T;
const payload = captured<ReviewResult>('markerUnknown.memberUnknown.captured.json').payload!;
const file = captured<{ file_classification: ReviewFileClassification }>(
  'markerUnknown.file.captured.json',
).file_classification;
const marks = fileMarks(file);
if (marks.kind !== 'hunks') throw new Error('notes.py carries per-hunk marks');
const [named] = marks.hunks[0].realizations.find(
  (entry) => entry.invariant === 'INV-Z66EMHMH',
)!.occurrences;
const target = named.target;
const subject = { kind: 'invariant', id: target.invariantKey };

afterEach(cleanup);

function inScope(node: React.ReactNode) {
  const scope: IntentMarkerScopeValue = {
    comparison: 3,
    listed: () => true,
    partial: false,
    classify: () => null,
    peek: () => undefined,
    origin: {
      path: file.path,
      pane: 'file',
      hunk: marks.kind === 'hunks' ? marks.hunks[0].key : '',
    },
    target,
    returning: null,
    follow: vi.fn(),
    back: vi.fn(),
    settle: vi.fn(),
  };
  return render(<IntentMarkerScope.Provider value={scope}>{node}</IntentMarkerScope.Provider>);
}

it("marks the member's row when the named family is in the tree, and only that row", () => {
  expect([target.state, target.family]).toEqual(['membership_unknown', 'FAM-4V4GSQCS']);
  const row = inScope(
    <>
      <MemberTargetNote familyId={target.familyKey!} memberRevisionId={target.memberRevisionKey!} />
      <MemberTargetNote familyId={target.familyKey!} memberRevisionId="another-revision" />
    </>,
  );
  expect(row.getAllByTestId('review-member-target-state')).toHaveLength(1);
  // The invariant view keeps the review's own label: the row carries the state.
  cleanup();
  const label = inScope(
    <MemberFamilyLabel subject={subject} payload={payload} entry={undefined} />,
  );
  expect(label.queryByTestId('review-center-target-state')).toBeNull();
});

it('shows the state on the invariant view when the named family is not in the review', () => {
  const composedNone: ReviewPayload = {
    ...payload,
    family_context: { ...payload.family_context!, entries: [] },
  };
  const view = inScope(
    <MemberFamilyLabel subject={subject} payload={composedNone} entry={undefined} />,
  );
  expect(view.getByTestId('review-center-target-state').textContent).toContain(
    'Attribution unknownINV-Z66EMHMH in FAM-4V4GSQCS: not every family record of the after knowledge',
  );
  // Another subject on screen: the marker's target is not this view's selection.
  cleanup();
  const other = inScope(
    <MemberFamilyLabel
      subject={{ kind: 'invariant', id: 'another' }}
      payload={composedNone}
      entry={undefined}
    />,
  );
  expect(other.queryByTestId('review-center-target-state')).toBeNull();
});

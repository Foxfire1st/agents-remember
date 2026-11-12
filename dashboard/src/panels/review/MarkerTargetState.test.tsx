// MIK-R34 (the rulings round, Q3): where an unknown membership's `Attribution unknown` state is shown.
// The payload is the REAL review of INV-Z66EMHMH on comparison 3 of the MIK-L34 scratch leaf, and the
// target is the real before-side occurrence of its FAM-4V4GSQCS membership (markerUnknown.* provenance);
// its roster is unreadable, so a named entry alone is not a member row. The explicit synthetic
// component controls below exercise alternate renderer inputs, not producer/classification evidence.
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

it('marks only the exact row props and keeps the invariant fallback when its roster is unreadable', () => {
  expect([target.state, target.family]).toEqual(['membership_unknown', 'FAM-4V4GSQCS']);
  const row = inScope(
    <>
      <MemberTargetNote familyId={target.familyKey!} memberRevisionId={target.memberRevisionKey!} />
      <MemberTargetNote familyId={target.familyKey!} memberRevisionId="another-revision" />
    </>,
  );
  expect(row.getAllByTestId('review-member-target-state')).toHaveLength(1);
  // Synthetic row props above do not make the actual native unreadable roster acquire that row.
  cleanup();
  const label = inScope(
    <MemberFamilyLabel subject={subject} payload={payload} entry={undefined} />,
  );
  expect(label.getByTestId('review-center-target-state').textContent).toContain(target.reason);
});

it('suppresses a duplicate center state only when the exact member is present', () => {
  // Synthetic component pairing: the healthy native comparison-2 roster supplies the row topology.
  // This tests hasTargetMember's positive branch, not a same-comparison unknown-membership producer.
  const withRoster = captured<ReviewResult>('markerReturn.invariant.captured.json').payload!;
  const view = inScope(<MemberFamilyLabel subject={subject} payload={withRoster} entry={undefined} />);
  expect(view.queryByTestId('review-center-target-state')).toBeNull();
});

it('shows the state on the invariant view when the named family is not in the review', () => {
  const composedNone: ReviewPayload = {
    ...payload,
    // Explicit renderer-only composition with a real native unavailable/empty context.
    family_context: captured<ReviewResult>('markerUnknown.noFamily.captured.json')
      .payload!.family_context,
  };
  const view = inScope(
    <MemberFamilyLabel subject={subject} payload={composedNone} entry={undefined} />,
  );
  expect(view.getByTestId('review-center-target-state').textContent).toContain(
    target.reason,
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

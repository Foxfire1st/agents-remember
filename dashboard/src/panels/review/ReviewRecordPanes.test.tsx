// Synthetic current-shape payload; only candidate/surface identities come from the capture.
import { cleanup, render } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import type { ReviewPayload } from '../../data/review';
import captured from './subjectReview.invariant.captured.json';
import { ReviewMainFacts, ReviewRecordIdentities } from './ReviewRecordPanes';

function syntheticPayload(): ReviewPayload {
  return {
    surface_version: captured.payload.surface_version,
    candidate: captured.payload.candidate,
    knowledge: {
      invariant_ids: [],
      family_ids: [],
      before_statement: { state: 'absent', language: 'text', detail: 'Synthetic absent side' },
      after_statement: { state: 'absent', language: 'text', detail: 'Synthetic absent side' },
      before_conditions: [],
      after_conditions: [],
      revision_groups: [],
      field_changes: [
        { item_id: 'INV-SYNTHETIC', item_kind: 'invariant', field: 'status', after_value: '' },
        {
          item_id: 'INV-SYNTHETIC',
          item_kind: 'invariant',
          field: 'source',
          before_value: '{"path":"old.py","lines":[1,2]}',
          after_value: '{"path":"new.py","lines":[]}',
        },
        {
          item_id: 'INV-SYNTHETIC',
          item_kind: 'invariant',
          field: 'statement',
          after_value: 'Authored in the subject pane',
        },
      ],
      unresolved: [
        {
          field: 'revision',
          recorded_reference: 'INV-SYNTHETIC',
          detail: 'Revision could not be read',
        },
      ],
      selection_state: 'task_context',
    },
    source: {
      inventory: {
        state: 'measured',
        entries: [],
        listed_total: 0,
        partial: false,
        detail: 'Synthetic zero-path comparison',
        command: 'synthetic read',
        unrepresentable_paths: [],
      },
      locations: [],
      remaining: [{ name: 'unattributed', reason: 'source count unavailable' }],
      attributed_changed_paths: [],
      unattributed_changed_paths: [],
      unresolved: [{ field: 'source', detail: 'Source anchor could not be read' }],
    },
    judgment: { state: 'none', detail: 'Synthetic no rows', rows: [], proofs: [] },
    staleness: { state: 'stale', statement: 'The synthetic candidate moved', moved: ['code'] },
    submission: {
      state: 'disabled_stale',
      reason: 'This review is read-only.',
      next_action: 'Use the existing publication authority.',
      proposed_dispositions: ['changed'],
      none_is_approval: false,
    },
    limitations: [],
  };
}

afterEach(cleanup);

it('renders current-shape main facts, unresolved reads and read-only state without retired branches', () => {
  const view = render(<ReviewMainFacts payload={syntheticPayload()} />);
  expect(view.getByTestId('review-field-changes').textContent).toContain(
    'status: (absent) → (recorded empty)',
  );
  expect(
    view.getByText('source: {"path":"old.py","lines":[1,2]} → {"path":"new.py","lines":[]}')
      .textContent,
  ).toBe('source: {"path":"old.py","lines":[1,2]} → {"path":"new.py","lines":[]}');
  expect(view.getByTestId('review-unresolved').textContent).toContain('Revision could not be read');
  expect(view.getByTestId('review-unresolved').textContent).toContain(
    'Source anchor could not be read',
  );
  expect(view.getByTestId('review-remaining').textContent).toContain(
    'not measured (source count unavailable)',
  );
  expect(view.getByTestId('review-stale').textContent).toContain('candidate moved');
  expect(view.getByText(/This review is read-only/).closest('details')).toBeNull();
  expect(view.container.textContent).not.toMatch(/assessment|evidence claim|detection/i);
  expect(view.queryByText('Authored in the subject pane')).toBeNull();
  // The one "All changes" link is the tree's (MIK-R89 v2 rule 7).
  expect(view.queryByRole('button', { name: 'All changes' })).toBeNull();
});

it('names attribution counts even before a lane read exists and explains an unmeasured change set', () => {
  const payload = syntheticPayload();
  payload.source.inventory.state = 'unavailable';
  payload.source.remaining = [{ name: 'unattributed_changed_paths', reason: 'Git could not measure' }];
  const view = render(<ReviewMainFacts payload={payload} />);
  expect(view.getByTestId('review-remaining').textContent).toBe(
    'changed files without an invariant: the change set could not be measured');
});

// Finding 2: a changed field's name and each remaining count are read as words, never as the
// answer's tokens (`payload_digest`, `changed_paths_outside_selection`).
it('names changed fields and remaining counts in words', () => {
  const payload = syntheticPayload();
  payload.knowledge.field_changes = [
    { item_id: 'one', item_kind: 'invariant', field: 'payload_digest', before_value: 'a1', after_value: 'b2' },
  ];
  payload.source.remaining = [
    { name: 'locations_remaining', value: 0 },
    { name: 'changed_paths_outside_selection', value: 175 },
    { name: 'records_present_outside_selection', value: 6 },
    { name: 'references_unresolved', value: 0 },
  ];
  const view = render(<ReviewMainFacts payload={payload} />);
  expect(view.getByTestId('review-field-changes').textContent).toBe('payload digest: a1 → b2');
  expect(view.getByTestId('review-remaining').textContent).toBe(
    'comparison items not on this page: 0 · changed files no link of this subject reaches: 175 · ' +
      'changed records outside this subject: 6 · unresolved references: 0',
  );
});

it('keeps only current judgment/proof identifiers and provenance in the record identities', () => {
  const payload = syntheticPayload();
  payload.judgment = {
    state: 'rows',
    detail: 'Synthetic recorded judgment',
    rows: [
      {
        attempt: 1,
        row_id: 'ROW-SYNTHETIC',
        subject: 'INV-SYNTHETIC',
        disposition: 'changed',
        reason: 'Authored judgment reason',
        because: ['INV-OTHER'],
        covered_entries: 1,
        history_path: 'knowledge/history/synthetic/attempt-1.json',
        closed: true,
      },
    ],
    proofs: [
      {
        id: 'PRF-SYNTHETIC',
        invariant: 'INV-SYNTHETIC',
        path: 'tests/synthetic.py',
        facet: 'Authored proof facet',
        state: 'current',
        detail: 'Authored proof detail',
      },
    ],
  };
  // The record identities sit in Technical details (reading/TechnicalDetails.tsx); the page's paging
  // controls and the unanswered subject's state are the details' and the slot's own.
  const view = render(<ReviewRecordIdentities payload={payload} />);
  const details = view.container;
  expect(details.textContent).toContain('ROW-SYNTHETIC');
  expect(details.textContent).toContain('PRF-SYNTHETIC');
  expect(details.textContent).toContain('knowledge/history/synthetic/attempt-1.json');
  expect(details.textContent).not.toMatch(/Authored|assessment|evidence claim|detection/i);
});

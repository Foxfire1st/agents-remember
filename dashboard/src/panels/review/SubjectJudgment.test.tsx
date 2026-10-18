// Explicit synthetic component rows, not a producer capture or a classification witness.
import { cleanup, render } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import type { ReviewPayload } from '../../data/review';
import { SubjectJudgment, SelectedStatement } from './SubjectReview';

const subject = { kind: 'invariant' as const, id: 'INV-TARGET' };
function payload(): ReviewPayload {
  return {
    surface_version: 'synthetic', candidate: { repository_id: 'repo', master: 'master', leaf_id: 'leaf' },
    knowledge: {
      invariant_ids: [subject.id], family_ids: [],
      before_statement: { state: 'unresolved', language: 'text', detail: 'No readable before' },
      after_statement: { state: 'unresolved', language: 'text', detail: 'No readable after' },
      before_conditions: [], after_conditions: [], revision_groups: [], field_changes: [], unresolved: [],
      selection_state: 'subject_selected', revision_selection: { record_kind: 'invariant', record_id: subject.id,
        state: 'unresolved', before_heads: [], after_heads: [], before_retained: [], after_retained: [], statement: 'Synthetic selection' },
    },
    source: { inventory: { state: 'measured', listed_total: 0, entries: [], detail: 'Synthetic empty inventory',
      partial: false, command: 'synthetic', unrepresentable_paths: [] }, locations: [], remaining: [],
      attributed_changed_paths: [], unattributed_changed_paths: [], unresolved: [] },
    staleness: { state: 'current', statement: 'Synthetic', moved: [] },
    submission: { state: 'unavailable', reason: 'Read only', next_action: 'Writer', proposed_dispositions: [], none_is_approval: true },
    limitations: [],
    judgment: {
      state: 'rows', detail: 'Recorded rows',
      rows: [{ attempt: 1, row_id: 'ROW-FIRST0', subject: 'INV-TARGET', disposition: 'changed',
        effect: 'clarify', reason: 'Keep the sentence exact.', because: ['INV-OTHER0'],
        covered_entries: 2, history_path: 'knowledge/history/leaf/attempt-1.json', closed: true }],
      proofs: [{ id: 'PRF-FIRST0', invariant: 'INV-TARGET', path: 'tests/test_rule.py',
        symbol: 'test_rule', facet: 'The rule holds.', state: 'current', detail: 'Exact anchor at after tree' }],
    },
  };
}
afterEach(cleanup);

it('shows judgment rows and proofs without hiding their text in disclosures', () => {
  const view = render(<SubjectJudgment payload={payload()} subject={subject} />);
  expect(view.getByRole('heading', { name: 'Judgment and proofs' })).toBeTruthy();
  expect(view.getByText('Keep the sentence exact.').closest('details')).toBeNull();
  expect(view.getByText('The rule holds.').closest('details')).toBeNull();
  expect(view.container.textContent).toContain('attempt 1');
  expect(view.container.textContent).toContain('2 covered entries');
  expect(view.container.textContent).not.toMatch(/assessment|evidence claim|detection/i);
});

it('keeps proofs visible when judgment history is unavailable', () => {
  const body = payload();
  Object.assign(body, { judgment: { ...Reflect.get(body, 'judgment'), state: 'unavailable', rows: [],
    detail: 'Could not read knowledge/history/leaf/attempt-1.json' } });
  const view = render(<SubjectJudgment payload={body} subject={subject} />);
  expect(view.getByText('Could not read knowledge/history/leaf/attempt-1.json')).toBeTruthy();
  expect(view.getByText('The rule holds.')).toBeTruthy();
});

it('reports a missing current-build field as a failed read, not no judgment', () => {
  const body = payload();
  Reflect.deleteProperty(body, 'judgment');
  const view = render(<SubjectJudgment payload={body} subject={subject} />);
  expect(view.getByRole('alert').textContent).toContain('did not supply judgment');
});

it('names the missing effect of an older changed family row', () => {
  const body = payload();
  const selection = body.knowledge.revision_selection!;
  selection.record_kind = 'family';
  selection.record_id = 'FAM-FIRST0';
  const row = Reflect.get(body, 'judgment').rows[0];
  row.subject = 'FAM-FIRST0';
  delete row.effect;
  delete row.covered_entries;
  row.examined_members = 3;
  const view = render(<SubjectJudgment payload={body} subject={{ kind: 'family', id: 'FAM-FIRST0' }} />);
  expect(view.container.textContent).toContain('effect not recorded');
  expect(view.container.textContent).toContain('3 examined members');
});

it('shows each statement sides recorded origin, including its hand-off entry', () => {
  const body = payload();
  const knowledge = { ...body.knowledge,
    before_origin: { task: 'old-task', wave: 'wave-one', handoffEntry: 'ENTRY-BEFORE' },
    after_origin: { task: 'new-task', leaf: 'leaf-two', handoffEntry: 'ENTRY-AFTER' } };
  const view = render(<SelectedStatement knowledge={knowledge} subject={subject} layout="inline" />);
  for (const text of ['old-task', 'wave-one', 'ENTRY-BEFORE', 'new-task', 'leaf-two', 'ENTRY-AFTER'])
    expect(view.container.textContent).toContain(text);
});

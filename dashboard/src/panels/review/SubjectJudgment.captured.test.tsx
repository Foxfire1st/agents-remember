// L57-CODE-F15: executable playback of seven unmodified native HTTP bodies.
// Source epochs/input-tree faults are recorded in l57.capture-provenance.json:
// current-final-01 is real L37 data; proof-04 scenarios fault scratch INPUT trees.
// These are retained native component controls, NOT fresh producer or browser acceptance.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { cleanup, render, within } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import type { ReviewPayload, ReviewResult } from '../../data/review';
import type { ReviewSubject } from './ReviewNavigation';
import { SubjectJudgment } from './SubjectReview';

const writer = { kind: 'invariant', id: '8b1a17ca-96ba-5ede-a09f-abfb11ada0a5' } as const;
const proofPath = 'mcp/tests/test_knowledge_cutover.py';

function mountCapture(file: string, subject: ReviewSubject) {
  const body = JSON.parse(readFileSync(
    path.join(path.dirname(new URL(import.meta.url).pathname), file), 'utf8',
  )) as ReviewResult;
  expect(body.state).toBe('review');
  expect(body.payload).toBeDefined();
  const payload = body.payload!;
  // Required current typed contract: never inject judgment, infer it from other panes,
  // default absent rows/proofs, or change the returned subject to fit the component.
  expect(payload.knowledge.revision_selection).toMatchObject({
    record_kind: subject.kind, record_id: subject.id,
  });
  expect(payload.judgment).toBeDefined();
  expect(Array.isArray(payload.judgment.rows)).toBe(true);
  expect(Array.isArray(payload.judgment.proofs)).toBe(true);
  const view = render(<SubjectJudgment payload={payload} subject={subject} />);
  const section = view.getByTestId('review-center-judgment');
  const rows = within(section).queryAllByTestId('review-judgment-row');
  const proofs = within(section).queryAllByTestId('review-judgment-proof');
  expect(section.dataset.judgmentState).toBe(payload.judgment.state);
  expect(within(section).getByText(payload.judgment.detail).closest('details')).toBeNull();
  expect(rows).toHaveLength(payload.judgment.rows.length);
  expect(proofs).toHaveLength(payload.judgment.proofs.length);
  for (const [index, row] of payload.judgment.rows.entries()) {
    const drawn = within(rows[index]);
    expect(drawn.getByText(row.reason).closest('details')).toBeNull();
    expect(rows[index].textContent).toContain(`attempt ${row.attempt} · ${row.disposition}`);
    expect(rows[index].textContent).toContain(row.closed ? 'closed' : 'open');
    expect(drawn.getByText(`${row.subject} · ${row.row_id} · ${row.history_path}`).closest('details')!.open).toBe(false);
    if (row.covered_entries !== undefined)
      expect(rows[index].textContent).toContain(`${row.covered_entries} covered ${row.covered_entries === 1 ? 'entry' : 'entries'}`);
    if (row.examined_members !== undefined)
      expect(rows[index].textContent).toContain(`${row.examined_members} examined members`);
  }
  for (const [index, proof] of payload.judgment.proofs.entries()) {
    const drawn = within(proofs[index]);
    expect(proofs[index].dataset.proofState).toBe(proof.state);
    expect(proofs[index].textContent).toContain(`${proof.path} · ${proof.symbol}`);
    expect(drawn.getByText(proof.facet).closest('details')).toBeNull();
    expect(drawn.getByText(proof.detail).closest('details')).toBeNull();
    expect(drawn.getByText(`${proof.id} · ${proof.invariant}`).closest('details')!.open).toBe(false);
  }
  return { ...view, payload, section, rows, proofs };
}

function assertWriterHistory(payload: ReviewPayload) {
  expect(payload.judgment.state).toBe('rows');
  expect(payload.judgment.rows).toMatchObject([
    { attempt: 1, row_id: 'ROW-A2NA0E', disposition: 'changed', effect: 'replace', covered_entries: 6, closed: true },
    { attempt: 2, row_id: 'ROW-KY2EJG', disposition: 'changed', effect: 'weaken', covered_entries: 6, closed: true },
  ]);
}

afterEach(cleanup);

it('native family history keeps assigned and changed attempts with its current member proof', () => {
  const view = mountCapture('l57.family.captured.json', {
    kind: 'family', id: 'b6bbe31f-6ff4-50cc-aa80-c1dc91809539',
  });
  expect(view.payload.judgment.state).toBe('rows');
  expect(view.payload.judgment.rows).toMatchObject([
    { attempt: 1, row_id: 'ROW-FVQW6H', disposition: 'assigned', examined_members: 11, closed: true },
    { attempt: 2, row_id: 'ROW-YJ7CG3', disposition: 'changed', examined_members: 11, closed: true },
  ]);
  // The older family changed row actually omits effect: disclose that, never invent one.
  expect(view.payload.judgment.rows[1]).not.toHaveProperty('effect');
  expect(view.rows[0].textContent).not.toContain('effect not recorded');
  expect(view.rows[1].textContent).toContain('changed · effect not recorded · closed');
  expect(view.payload.judgment.proofs).toMatchObject([
    { id: 'PRF-JR10RF', invariant: 'INV-T9M21FB4', path: proofPath, state: 'current' },
  ]);
  expect(view.proofs[0].dataset.proofState).toBe('current');
  expect(view.queryByText('No proofs were returned for this subject.')).toBeNull();
  expect(view.queryByRole('alert')).toBeNull();
});

it('native invariant history preserves weaken then clarify and structured because references', () => {
  const view = mountCapture('l57.invariant.captured.json', {
    kind: 'invariant', id: '104c2f90-c046-578f-950a-2a66c9ac93d0',
  });
  expect(view.payload.judgment.state).toBe('rows');
  expect(view.payload.judgment.rows).toMatchObject([
    { attempt: 1, row_id: 'ROW-VT6B4T', disposition: 'changed', effect: 'weaken', covered_entries: 1 },
    { attempt: 2, row_id: 'ROW-DQN7HY', disposition: 'changed', effect: 'clarify', covered_entries: 1 },
  ]);
  expect(view.payload.judgment.rows[0].because).toEqual([
    { task: { repository: 'agents-remember', path: '260928_maintained-invariant-knowledge' },
      packet: 'requirements/MIK-R25-v1-reviewer-on-git-trees.md', id: 'MIK-R25', version: 'v1' },
    'DEC-YZA7E4',
  ]);
  for (const [index, effect] of ['weaken', 'clarify'].entries()) {
    expect(view.rows[index].textContent).toContain(`changed · ${effect} · closed`);
    const because = within(view.rows[index]).getByText(
      'Because: MIK-R25@v1 · agents-remember:260928_maintained-invariant-knowledge · requirements/MIK-R25-v1-reviewer-on-git-trees.md · DEC-YZA7E4',
    );
    expect(because.closest('details')).toBeNull();
  }
  expect(view.payload.judgment.proofs).toEqual([]);
  expect(view.getByText('No proofs were returned for this subject.')).toBeTruthy();
  expect(view.queryByRole('alert')).toBeNull();
});

it('native missing-path input keeps judgments and renders the proof as stale, not unverifiable', () => {
  const view = mountCapture('l57.missing-proof.captured.json', writer);
  assertWriterHistory(view.payload);
  expect(view.payload.judgment.proofs).toMatchObject([
    { id: 'PRF-TMK4N3', path: proofPath, state: 'stale',
      detail: "the path 'mcp/tests/test_knowledge_cutover.py' is absent at the code tree" },
  ]);
  expect(view.proofs[0].dataset.proofState).toBe('stale');
  expect(view.proofs[0].textContent).toContain("the path 'mcp/tests/test_knowledge_cutover.py' is absent at the code tree");
  expect(view.section.textContent).not.toContain('unverifiable');
  expect(view.queryByText('No proofs were returned for this subject.')).toBeNull();
  expect(view.queryByRole('alert')).toBeNull();
});

it('native no-impact history remains authored judgment even when the selected revision is unchanged', () => {
  const view = mountCapture('l57.no-impact.captured.json', {
    kind: 'invariant', id: '0b171ed1-ce31-5c55-88ab-335127bf9d37',
  });
  const selection = view.payload.knowledge.revision_selection!;
  expect(selection.before_revision_id).toBe('aae21ba6-5ce4-5a76-94fb-79c1bae2cae0');
  expect(selection.after_revision_id).toBe(selection.before_revision_id);
  expect(view.payload.judgment.state).toBe('rows');
  expect(view.payload.judgment.rows).toMatchObject([
    { attempt: 1, row_id: 'ROW-013JP6', disposition: 'no_impact', covered_entries: 2, closed: true },
    { attempt: 2, row_id: 'ROW-3K13YB', disposition: 'no_impact', covered_entries: 2, closed: true },
  ]);
  for (const row of view.rows) expect(row.textContent).toContain('no_impact · closed');
  expect(view.section.textContent).not.toContain('effect not recorded');
  expect(view.section.textContent).not.toContain('This leaf recorded no judgment');
  expect(view.payload.judgment.proofs).toEqual([]);
  expect(view.getByText('No proofs were returned for this subject.')).toBeTruthy();
  expect(view.queryByRole('alert')).toBeNull();
});

it('native measured no-judgment read stays none with neither rows nor proofs', () => {
  const view = mountCapture('l57.none.captured.json', {
    kind: 'invariant', id: '7651e8f5-37aa-5834-9df3-c406a55d78cf',
  });
  expect(view.payload.judgment).toEqual({
    state: 'none', rows: [], proofs: [], detail: 'This leaf recorded no judgment about this record',
  });
  expect(view.section.dataset.judgmentState).toBe('none');
  expect(view.getByText('This leaf recorded no judgment about this record').closest('details')).toBeNull();
  expect(view.rows).toHaveLength(0);
  expect(view.proofs).toHaveLength(0);
  expect(view.getByText('No proofs were returned for this subject.')).toBeTruthy();
  expect(view.queryByRole('alert')).toBeNull();
});

it('native changed-range input keeps judgments and names the stale proof content mismatch', () => {
  const view = mountCapture('l57.stale-proof.captured.json', writer);
  assertWriterHistory(view.payload);
  expect(view.payload.judgment.proofs).toMatchObject([
    { id: 'PRF-TMK4N3', path: proofPath, state: 'stale',
      detail: 'the range content differs from the recorded content' },
  ]);
  expect(view.proofs[0].dataset.proofState).toBe('stale');
  expect(within(view.proofs[0]).getByText('the range content differs from the recorded content').closest('details')).toBeNull();
  expect(view.section.textContent).not.toContain('absent at the code tree');
  expect(view.queryByText('No proofs were returned for this subject.')).toBeNull();
  expect(view.queryByRole('alert')).toBeNull();
});

it('native unreadable-history input alerts unavailable while its independently current proof remains visible', () => {
  const view = mountCapture('l57.unavailable.captured.json', writer);
  expect(view.payload.judgment.state).toBe('unavailable');
  expect(view.payload.judgment.rows).toEqual([]);
  expect(view.payload.judgment.detail).toBe(
    'the after memory index is incomplete (1 file(s) failed: knowledge/history/260928-MIK-L37-attempt-2.json); history it did not read cannot be reported as absent',
  );
  expect(view.section.dataset.judgmentState).toBe('unavailable');
  expect(view.getByRole('alert').textContent).toBe(view.payload.judgment.detail);
  expect(view.payload.judgment.proofs).toMatchObject([
    { id: 'PRF-TMK4N3', path: proofPath, state: 'current',
      detail: 'The proof entry is current at the after code tree.' },
  ]);
  expect(view.proofs[0].dataset.proofState).toBe('current');
  expect(view.rows).toHaveLength(0);
  expect(view.queryByText('No proofs were returned for this subject.')).toBeNull();
  expect(view.section.textContent).not.toContain('This leaf recorded no judgment');
});

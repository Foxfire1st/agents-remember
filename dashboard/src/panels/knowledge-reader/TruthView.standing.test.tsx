// The reader heads a record with its derived state and its writer (role and model).
// The record answer is the captured invariant with the fields the server now adds (see
// mcp/tests/test_knowledge_read_record_standing.py for the server side): a converted record names
// both, an unconverted one reads proposed, written by unknown, and shows no old status word.
import { fireEvent, within } from '@testing-library/react';
import { expect, it } from 'vitest';
import { INTEGRATE, captured, open, overrides, type Body } from './readerTestSupport';

const ID = 'INV-N213W04A';
const withRecord = (fields: Record<string, unknown>): Body => {
  const answer = captured['record-invariant'];
  return { ...answer, record: { ...(answer.record as Record<string, unknown>), ...fields } };
};

it('heads a converted record with its state and "written by role · model"', async () => {
  overrides[`record:${ID}`] = withRecord({
    status: '',
    recordState: 'reviewed',
    writer: { role: 'curator', harness: 'claude', model: 'claude-opus-5-5' },
  });
  const truth = await open({ view: 'record', id: ID }).findByTestId('reader-truth-view');
  expect(within(truth).getByTestId('record-revision-state').dataset.state).toBe('reviewed');
  expect(within(truth).getByTestId('record-writer').textContent).toBe(
    ' written by curator · claude-opus-5-5',
  );
  // The state badge and the writer are separate words in the heading.
  expect(within(truth).getByRole('heading', { level: 2 }).textContent).toContain(
    'reviewed written by curator',
  );
  // The currentness badge keeps its own place beside it.
  expect(within(truth).getByTestId('record-state').dataset.state).toBe('current');
});

it('heads an unconverted record as proposed, written by unknown, with no old status word', async () => {
  overrides[`record:${ID}`] = withRecord({
    status: '',
    recordState: 'proposed',
    writer: { role: 'unknown', harness: 'unknown', model: 'unknown' },
  });
  const truth = await open({ view: 'record', id: ID }).findByTestId('reader-truth-view');
  expect(within(truth).getByTestId('record-revision-state').dataset.state).toBe('proposed');
  expect(within(truth).getByTestId('record-writer').textContent).toBe(' written by unknown · unknown');
  expect(within(truth).queryByTestId('record-status')).toBeNull();
  expect(within(truth).getByRole('heading', { level: 2 }).textContent).not.toMatch(/accepted/);
});

// The reader's record lists: a family's members, a path's invariants and families, and the record
// list of the sidebar tree. Each row names its record's state and writer beside its link.
const REVIEWED = { recordState: 'reviewed', writer: { role: 'curator', harness: 'claude', model: 'claude-opus-5-5' } };
const UNCONVERTED = { status: '', recordState: 'proposed', writer: { role: 'unknown', harness: 'unknown', model: 'unknown' } };
const withStanding = <T extends Record<string, unknown>>(rows: T[], first = REVIEWED) =>
  rows.map((row, index) => ({ ...row, ...(index === 0 ? first : UNCONVERTED) }));

it("names each family member's state and writer", async () => {
  const answer = captured['record-family'] as Body & { family: { members: Record<string, unknown>[] } };
  overrides['record:FAM-QWVGDSYX'] = { ...answer, family: { ...answer.family, members: withStanding(answer.family.members) } };
  const truth = await open({ view: 'record', id: 'FAM-QWVGDSYX' }).findByTestId('reader-truth-view');
  const members = within(truth).getAllByTestId('record-standing');
  expect(members.map((one) => one.textContent)).toEqual([
    ' · reviewed · written by curator · claude-opus-5-5',
    ' · proposed · written by unknown · unknown',
  ]);
  expect(members[0].dataset.recordState).toBe('reviewed');
});

it("names the state and writer of a path's invariants and families", async () => {
  const answer = captured['path-integrate-file'] as Body & {
    invariants: Record<string, unknown>[];
    families: Record<string, unknown>[];
  };
  overrides[`path:${INTEGRATE}`] = {
    ...answer,
    invariants: withStanding(answer.invariants),
    families: withStanding(answer.families, UNCONVERTED),
  };
  const view = open({ view: 'path', path: INTEGRATE });
  const invariant = (await view.findAllByTestId('reader-invariant'))[0];
  expect(within(invariant).getByTestId('record-standing').textContent).toBe(
    ' · reviewed · written by curator · claude-opus-5-5',
  );
  const family = view.getAllByTestId('reader-family')[0];
  expect(within(family).getByTestId('record-standing').textContent).toBe(
    ' · proposed · written by unknown · unknown',
  );
});

it("names each record's state and writer in the sidebar's record list", async () => {
  const answer = captured.records as Body & { kinds: Record<string, Record<string, unknown>[]> };
  overrides['records:'] = { ...answer, kinds: { ...answer.kinds, family: withStanding(answer.kinds.family) } };
  const view = open({ view: 'path', path: '.' });
  await view.findByTestId('reader-path-view');
  const tree = view.getByTestId('knowledge-tree');
  fireEvent.click(await within(tree).findByRole('treeitem', { name: /Records/ }));
  fireEvent.click(await within(tree).findByRole('treeitem', { name: /Families/ }));
  const first = await within(tree).findByRole('treeitem', { name: /generated-artifacts-are-disposable-projections/ });
  // The state and writer are the row's own wrapping second line, not the suffix cut to the row.
  expect(within(first).getByTestId('tree-row-detail').textContent).toBe(
    'reviewed · written by curator · claude-opus-5-5',
  );
  const second = within(tree).getByRole('treeitem', { name: new RegExp(String(answer.kinds.family[1].title)) });
  expect(within(second).getByTestId('tree-row-detail').textContent).toBe(
    'proposed · written by unknown · unknown',
  );
  // A kind's row keeps its count in the suffix and has no second line.
  const kind = within(tree).getByRole('treeitem', { name: /Families/ });
  expect(within(kind).queryByTestId('tree-row-detail')).toBeNull();
});

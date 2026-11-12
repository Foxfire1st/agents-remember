// `j`/`k` move by block identity over the model's blocks and the lane's hunks (MIK-R89 v2 rules 11
// and 13; evidence 15), and the status of the last move stays only beside the stops it was said for
// (MIK-R39 rule 11; INV-CK3PTB). Every render is recorded, so the test sees the very render in which
// the stops change.
import { cleanup, fireEvent, render } from '@testing-library/react';
import { useRef, useState } from 'react';
import { afterEach, describe, expect, it } from 'vitest';

import { blockKey, hunkIdentityOf } from './blockIdentity';
import { changeSetModel } from './changeSetOrder';
import { l104Inputs, l104Selector, l181Answer, l181Inputs } from './changeSetOrder.test-utils';
import {
  blockStops,
  laneStops,
  nextStop,
  outcomeMessage,
  useBlockTraversal,
  type BlockStop,
  type TraversalStop,
} from './changeTraversal';
import type { ReviewSelection } from './reviewSelection';

afterEach(cleanup);

const NO_LATER = 'No later block; the selection stays.';
const model = changeSetModel(l181Inputs(), 'priority', 'changed', { selection: null });
const hunk = hunkIdentityOf('dashboard/src/lane.ts', { before: { start: 3, count: 1 }, after: { start: 3, count: 2 } }, l181Answer.comparison!);
const stops = [...blockStops(model), ...laneStops([hunk])];
const blocks = stops.filter((stop): stop is BlockStop => stop.kind === 'block');
const keyOf = (selection: ReviewSelection) => (selection.kind === 'block' ? blockKey(selection.block) : '');
const step = (selection: ReviewSelection | null, direction: 1 | -1) => {
  const outcome = nextStop(stops, selection, direction);
  return outcome.kind === 'select' ? { kind: 'block' as const, block: outcome.stop.identity } : outcome;
};

describe('the stops', () => {
  it('walk the changed blocks in display order, then the lane hunks', () => {
    expect(blocks.slice(0, model.blocks.length).map((stop) => stop.key)).toEqual(model.blocks.map((node) => node.key));
    expect(blocks.slice(model.blocks.length).map((stop) => stop.key)).toEqual([blockKey(hunk)]);
    expect(stops.at(-1)?.key).toBe(blockKey(hunk));
    // The groups' locations are no stops.
    const grouped = new Set(model.subjects[0].unchanged.map((node) => node.key));
    expect(stops.some((stop) => grouped.has(stop.key))).toBe(false);
  });

  it('start at the first block from no selection and move by identity', () => {
    const first = step(null, 1) as ReviewSelection;
    expect(keyOf(first)).toBe(model.blocks[0].key);
    expect(keyOf(step(null, -1) as ReviewSelection)).toBe(model.blocks[0].key);
    const second = step(first, 1) as ReviewSelection;
    expect(keyOf(second)).toBe(model.blocks[1].key);
    expect(keyOf(step(second, -1) as ReviewSelection)).toBe(model.blocks[0].key);
  });

  it('enter a selected subject at its first block, and end with the selection kept', () => {
    const subject: ReviewSelection = { kind: 'subject', subject: { kind: 'invariant', id: model.subjects[0].row.selectorId } };
    expect(keyOf(step(subject, 1) as ReviewSelection)).toBe(model.blocks[0].key);
    expect(step(subject, -1)).toEqual({ kind: 'end', direction: -1 });
    const last: ReviewSelection = { kind: 'block', block: hunk };
    const outcome = nextStop(stops, last, 1);
    expect(outcome).toEqual({ kind: 'end', direction: 1 });
    expect(outcomeMessage(outcome)).toBe(NO_LATER);
  });
});

// From a selection with no stop of its own, `j`/`k` go to the blocks after and before the place the
// page shows it at (rule 11), never to the first block of the page; nothing selected still starts
// there.
describe('from a selection that holds no stop', () => {
  const all = changeSetModel(l181Inputs(), 'priority', 'all', { selection: null });
  const order = [...blockStops(all), ...laneStops([hunk])];
  const from = (selection: ReviewSelection, direction: 1 | -1) => {
    const outcome = nextStop(order, selection, direction);
    return outcome.kind === 'select' ? outcome.stop.key : outcome;
  };
  const changed = all.subjects[0].changed;

  it('moves from a subject without changed blocks to the blocks around its place', () => {
    // All: INV-1ZKP4R with its blocks, the synthetic INV-0SYNTH and INV-ZSYNTH, then the lane.
    const synthetic: ReviewSelection = { kind: 'subject', subject: { kind: 'invariant', id: 'synthetic-unchanged-0' } };
    expect(from(synthetic, -1)).toBe(all.blocks.at(-1)!.key);
    expect(from(synthetic, 1)).toBe(blockKey(hunk));
  });

  it('moves from a folded location to the blocks around its subject\'s groups', () => {
    const folded: ReviewSelection = { kind: 'block', block: all.subjects[0].unchanged[0].identity };
    expect(from(folded, -1)).toBe(all.blocks.at(-1)!.key);
    expect(from(folded, 1)).toBe(blockKey(hunk));
  });

  it('moves from an entry that its region\'s card draws as from that card', () => {
    // Captured: RLZ-ZA95M4 shares RLZ-EC5NRM's region, whose one card is the first stop.
    const shared: ReviewSelection = { kind: 'block', block: changed.find((node) => node.entry.id === 'RLZ-ZA95M4')!.identity };
    expect(order.map((stop) => stop.kind === 'block' && stop.identity.kind === 'entry' && stop.identity.entry)).not.toContain('RLZ-ZA95M4');
    expect(from(shared, 1)).toBe(all.blocks[1].key);
    expect(all.blocks[1].entry.id).toBe('RLZ-78JFRR');
    expect(from(shared, -1)).toEqual({ kind: 'end', direction: -1 });
  });

  it('moves from a family heading into its members, and before it to the block above', () => {
    // Synthetic: a family over two captured L104 subjects, which Priority places after the others.
    const outline = l104Inputs().outline!;
    const family = {
      selector_id: 'family-synthetic',
      id: 'FAM-SYNTH',
      title: 'a synthetic family',
      change: 'unchanged' as const,
      members: ['INV-SE4ECQ', 'INV-EWGTN0'].map((id) => ({ selector_id: l104Selector(id), id, membership: 'both' as const })),
    };
    const nested = changeSetModel(l104Inputs({ outline: { ...outline, families: [family] } }), 'priority', 'changed', { selection: null });
    expect(nested.groups.map((group) => group.family?.row.id ?? '-')).toEqual(['-', 'FAM-SYNTH']);
    const stops = blockStops(nested);
    const heading: ReviewSelection = { kind: 'subject', subject: { kind: 'family', id: 'family-synthetic' } };
    // The family's first block is its first member's; the block above it is the last of the subject above.
    const entry = nested.blocks.findIndex((node) => node.subject === nested.groups[1].subjects[0].row.selectorId);
    expect(nested.blocks[entry - 1].subject).toBe(nested.groups[0].subjects.at(-1)!.row.selectorId);
    const outcome = (direction: 1 | -1) => {
      const next = nextStop(stops, heading, direction);
      return next.kind === 'select' ? next.stop.key : next;
    };
    expect(outcome(1)).toBe(nested.blocks[entry].key);
    expect(outcome(-1)).toBe(nested.blocks[entry - 1].key);
  });

  it('moves from a lane file not yet read to the hunks around it, and from the lane to its first hunk', () => {
    const comparison = l181Answer.comparison!;
    const span = { before: { start: 1, count: 1 }, after: { start: 1, count: 1 } };
    const [first, third] = ['dashboard/src/a.ts', 'dashboard/src/c.ts'].map((path) => hunkIdentityOf(path, span, comparison));
    const lane = [...blockStops(all), ...laneStops([first, third], ['dashboard/src/a.ts', 'dashboard/src/b.ts', 'dashboard/src/c.ts'])];
    const step = (selection: ReviewSelection, direction: 1 | -1) => {
      const outcome = nextStop(lane, selection, direction);
      return outcome.kind === 'select' ? outcome.stop.key : outcome;
    };
    const unread: ReviewSelection = { kind: 'lane', path: 'dashboard/src/b.ts' };
    expect(step(unread, 1)).toBe(blockKey(third));
    expect(step(unread, -1)).toBe(blockKey(first));
    expect(step({ kind: 'lane' }, 1)).toBe(blockKey(first));
    expect(step({ kind: 'lane' }, -1)).toBe(all.blocks.at(-1)!.key);
  });

  it('keeps the selection at an end, and starts at the first block only from no selection', () => {
    const last: ReviewSelection = { kind: 'subject', subject: { kind: 'invariant', id: 'synthetic-unchanged-z' } };
    const lastStops = blockStops(all);
    expect(nextStop(lastStops, last, 1)).toEqual({ kind: 'end', direction: 1 });
    expect(nextStop(lastStops, null, 1)).toMatchObject({ kind: 'select', stop: { key: all.blocks[0].key } });
    expect(nextStop(lastStops, null, -1)).toMatchObject({ kind: 'select', stop: { key: all.blocks[0].key } });
  });
});

// One stop list without a later block: every move forward from its stop ends at once.
function Probe({ rows, rendered }: { rows: BlockStop[]; rendered: string[] }) {
  const zone = useRef<HTMLElement>(null);
  const [selection, setSelection] = useState<ReviewSelection | null>({ kind: 'block', block: rows[0].identity });
  const { move, status } = useBlockTraversal({ zone, stops: rows, selection, onSelect: setSelection, enabled: false });
  rendered.push(`${rows[0].key.slice(0, 12)}: ${status}`);
  return (
    <section ref={zone}>
      <button type="button" onClick={() => move(1)}>
        next
      </button>
    </section>
  );
}

it('renders a status only beside the stops it was said for, and never again once other stops were shown', () => {
  const rendered: string[] = [];
  const first = [blocks.at(-1)!];
  const second = [{ ...blocks[0], key: `other-${blocks[0].key}` }];
  const label = (rows: TraversalStop[]) => rows[0].key.slice(0, 12);
  const view = render(<Probe rows={first} rendered={rendered} />);
  fireEvent.click(view.getByText('next'));
  expect(rendered.at(-1)).toBe(`${label(first)}: ${NO_LATER}`);
  view.rerender(<Probe rows={second} rendered={rendered} />);
  const beside = rendered.filter((one) => one.startsWith(label(second)));
  expect(new Set(beside)).toEqual(new Set([`${label(second)}: `]));
  const before = rendered.length;
  view.rerender(<Probe rows={first} rendered={rendered} />);
  expect(new Set(rendered.slice(before))).toEqual(new Set([`${label(first)}: `]));
  fireEvent.click(view.getByText('next'));
  expect(rendered.at(-1)).toBe(`${label(first)}: ${NO_LATER}`);
});

function Zone({ selections }: { selections: (ReviewSelection | null)[] }) {
  const zone = useRef<HTMLElement>(null);
  const [selection, setSelection] = useState<ReviewSelection | null>(null);
  const { status } = useBlockTraversal({
    zone,
    stops,
    selection,
    onSelect: (next) => {
      selections.push(next);
      setSelection(next);
    },
    enabled: true,
  });
  return (
    <section ref={zone} data-kbzone="review">
      <button type="button">row</button>
      <input aria-label="filter" />
      <p>{status}</p>
    </section>
  );
}

it('binds j and k on the review zone, moves by block identity, and stays inert in a text field', () => {
  const selections: (ReviewSelection | null)[] = [];
  const view = render(<Zone selections={selections} />);
  const row = view.getByRole('button', { name: 'row' });
  fireEvent.keyDown(row, { key: 'j', code: 'KeyJ' });
  fireEvent.keyDown(row, { key: 'j', code: 'KeyJ' });
  fireEvent.keyDown(row, { key: 'k', code: 'KeyK' });
  expect(selections.map((selection) => keyOf(selection!))).toEqual([
    model.blocks[0].key,
    model.blocks[1].key,
    model.blocks[0].key,
  ]);
  fireEvent.keyDown(view.getByRole('textbox', { name: 'filter' }), { key: 'j', code: 'KeyJ' });
  expect(selections).toHaveLength(3);
  fireEvent.keyDown(row, { key: 'k', code: 'KeyK' });
  expect(view.getByText('No earlier block; the selection stays.')).toBeTruthy();
});

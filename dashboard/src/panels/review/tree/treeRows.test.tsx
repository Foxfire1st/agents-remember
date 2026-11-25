// MIK-R211 v1 rule 5: the sidebar renders as before L183. Every row is one line — a caret, the
// shortened name and the reason or "changed" chip whole at the right — and a graded location's grade
// chip sits in that same trailing cell, so a graded row keeps the row's three grid cells and changes
// no other row's columns.
import { render } from '@testing-library/react';
import { expect, it } from 'vitest';

import {
  changeSetModel,
  ROLE_LABEL,
  type ChangeSetInputs,
  type FamilyHeading,
  type LocationNode,
  type SubjectNode,
} from '../changeSetOrder';
import { l181Inputs } from '../changeSetOrder.test-utils';
import type { ExplainerCategory, ExplainerGrade } from '../explainer/explainerApi';
import { FamilyRow, LocationRow, SubjectRow } from './TreeRows';
import * as styles from './treeStyles';

const category: ExplainerCategory = {
  name: 'confidence',
  meaning: 'How far the explainer trusts the block',
  scale: [{ grade: 'high' }, { grade: 'low' }],
};

// L181's captured card read, in the tree's changed rows at Priority order.
const changed = (inputs: ChangeSetInputs): LocationNode[] =>
  changeSetModel(inputs, 'priority', 'changed', { selection: null }).groups.flatMap((group) =>
    group.subjects.flatMap((subject) => subject.changed),
  );

// A grade names one changed location of the capture; a judgment is drawn on every row it names.
const [first] = changed(l181Inputs());
const firstEntry = first.card.entries[0].id;
const grade: ExplainerGrade = {
  reference: firstEntry,
  category: category.name,
  grade: 'high',
  reason: 'the guard runs before the write',
  stale: false,
};
const gradedInputs = l181Inputs({ categories: [category], grades: [grade] });
const graded = changed(gradedInputs).find((node) => node.card.entries.some((entry) => entry.id === firstEntry))!;
const ungraded = changed(l181Inputs())[0];

const renderRow = (node: LocationNode) =>
  render(<LocationRow node={node} nested={false} current={false} onSelect={() => undefined} />);

const reasonText = (node: LocationNode) => node.reason || ROLE_LABEL[node.role];

it('draws a graded location row as three cells, the chip whole in the trailing one', () => {
  expect(graded.judgment).toBeTruthy();
  const view = renderRow(graded);
  const row = view.getByTestId('review-tree-location');
  expect(row.className.split(' ')).toEqual(expect.arrayContaining(styles.row.split(' ')));
  // The caret, the name and one trailing cell: the chip is no cell of its own (before L183's grid).
  expect([...row.children].map((cell) => cell.className)).toEqual([
    styles.caret,
    styles.name,
    styles.reasonCell,
  ]);
  const tail = row.lastElementChild as HTMLElement;
  const chip = view.getByTestId('review-judgment-chip');
  expect(chip.parentElement).toBe(tail);
  expect(chip.textContent).toBe(graded.judgment!.chipLabel);
  // The chip keeps its size (nowrap, no shrink) and the reason follows it, ending at the row's right.
  expect(chip.className.split(' ')).toEqual(expect.arrayContaining(['flex-sh_0', 'white-space_nowrap']));
  expect(chip.nextElementSibling?.className).toBe(styles.reason);
  expect(tail.textContent).toBe(`${graded.judgment!.chipLabel}${reasonText(graded)}`);
  expect(row.textContent?.endsWith(tail.textContent!)).toBe(true);
});

it('draws an ungraded location row as before: three cells, no chip and no empty placeholder', () => {
  expect(ungraded.judgment).toBeUndefined();
  const view = renderRow(ungraded);
  const row = view.getByTestId('review-tree-location');
  expect([...row.children].map((cell) => cell.className)).toEqual([
    styles.caret,
    styles.name,
    styles.reason,
  ]);
  expect(view.queryByTestId('review-judgment-chip')).toBeNull();
  expect(row.textContent?.endsWith(reasonText(ungraded))).toBe(true);
});

it('keeps every changed location at three cells, graded or not', () => {
  const nodes = changed(gradedInputs);
  const view = render(
    <>
      {nodes.map((node) => (
        <LocationRow key={node.key} node={node} nested={false} current={false} onSelect={() => undefined} />
      ))}
    </>,
  );
  const rows = view.getAllByTestId('review-tree-location');
  expect(rows.length).toBeGreaterThan(1);
  expect(rows.map((row) => row.children.length)).toEqual(rows.map(() => 3));
  expect(view.getAllByTestId('review-judgment-chip').length).toBeGreaterThan(0);
  // One chip, one trailing cell: a grade draws on the rows it names and moves no other row's cells.
  expect(rows.filter((row) => row.querySelector('[data-testid="review-judgment-chip"]')).length).toBe(1);
});

// A family or subject row ends with its record's state in the candidate tree, in the
// row's own muted reason look; the writer (role and model) is its title and part of the row's name.
const subject = (standing?: SubjectNode['row']['standing']) =>
  ({
    key: '-/s1',
    row: { kind: 'invariant', id: 'INV-7K3F9Q', selectorId: 's1', title: 'INV-7K3F9Q holds', badge: 'changed', standing },
    first: true,
    effects: [],
    cards: 'unread',
    locationsKnown: false,
    changed: [],
    reason: 'changed',
  }) as unknown as SubjectNode;

it('ends a subject row with its state, the writer in its title and accessible name', () => {
  const view = render(
    <SubjectRow
      node={subject({ record_state: 'reviewed', writer: { role: 'worker', model: 'model-b' } })}
      nested={false}
      current={false}
      onSelect={() => undefined}
    />,
  );
  const row = view.getByTestId('review-family-member-open');
  expect([...row.children].map((cell) => cell.className)).toEqual([styles.caret, styles.name, styles.reasonCell]);
  const tag = view.getByTestId('review-record-state');
  expect(tag.className).toBe(styles.reason);
  expect(tag.textContent).toBe('reviewed');
  expect(tag.title).toBe('reviewed · written by worker · model-b');
  expect(row.getAttribute('aria-label')).toContain('reviewed · written by worker · model-b');
});

it('draws a subject row with no state as before, and an unconverted one as proposed with no writer', () => {
  const plain = render(<SubjectRow node={subject()} nested={false} current={false} onSelect={() => undefined} />);
  const row = plain.getByTestId('review-family-member-open');
  expect(row.lastElementChild?.className).not.toBe(styles.reasonCell);
  expect(plain.queryByTestId('review-record-state')).toBeNull();
  plain.unmount();
  const old = render(
    <SubjectRow node={subject({ record_state: 'proposed', writer: null })} nested={false} current={false} onSelect={() => undefined} />,
  );
  const tag = old.getByTestId('review-record-state');
  expect([tag.textContent, tag.title]).toEqual(['proposed', 'proposed']);
});

it('ends a family row with its state beside its reason', () => {
  const family = {
    row: { kind: 'family', id: 'FAM-STATEW', selectorId: 'f1', title: 'State and writer', badge: 'unchanged', standing: { record_state: 'consolidated', writer: { role: 'consolidator', model: 'model-c' } } },
    reason: '',
  } as FamilyHeading;
  const view = render(<FamilyRow family={family} current={false} onSelect={() => undefined} />);
  const tag = view.getByTestId('review-record-state');
  expect(tag.textContent).toBe('consolidated');
  expect(tag.title).toBe('consolidated · written by consolidator · model-c');
  expect(view.getByTestId('review-family-open').getAttribute('aria-label')).toContain('written by consolidator · model-c');
});

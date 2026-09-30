// MIK-R33 on real data: the family tree over the REAL served answers of the reviewer route for the
// 260928-MIK-L33 worker's converted scratch copy of the real repositories (provenance:
// triageReal.capture-provenance.json). The scratch leaf's code change is this leaf's own diff; its
// knowledge edits are SCRATCH-AUTHORED (a revised invariant, a member joining a family, a revised
// guarantee, a re-anchored entry), and the changed files' entries were re-recorded at the candidate
// blob as a curator does. Each memory side was read through the derived index of its tree.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { cleanup, fireEvent, render, within } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, beforeEach, expect, it } from 'vitest';
import type { ReviewFamilyContext, ReviewResult } from '../../data/review';
import { FamilyTree, type FamilySelection } from './FamilyTree';
import { treeOrderStore } from './triageOrderPreference';

const contextOf = (name: string): ReviewFamilyContext =>
  (
    JSON.parse(
      readFileSync(path.join(path.dirname(new URL(import.meta.url).pathname), name), 'utf8'),
    ) as ReviewResult
  ).payload!.family_context!;
const family = contextOf('triageReal.family.captured.json');
const shared = contextOf('triageReal.shared.captured.json');
const J = { key: 'j', code: 'KeyJ' };

function Harness({ context }: { context: ReviewFamilyContext }) {
  const [selection, setSelection] = useState<FamilySelection | null>(null);
  const [query, setQuery] = useState('');
  return (
    <div data-kbzone="review">
      <FamilyTree
        context={context}
        selection={selection}
        onSelect={setSelection}
        onRosterNext={() => undefined}
        query={query}
        onQuery={setQuery}
        embedded
        tree
      />
    </div>
  );
}

// The invariant a member node shows, through the occurrence identity the server described.
const invariants = new Map(
  [family, shared].flatMap((context) =>
    context.entries.flatMap((entry) =>
      (entry.change_kinds?.members ?? []).map((one) => [one.member_id, one.invariant]),
    ),
  ),
);
const recordOf = (node: Element | null) =>
  invariants.get((node as HTMLElement | null)?.dataset.occurrence ?? '');
const stopsOf = (node: HTMLElement) =>
  within(node)
    .getAllByTestId('review-family-member-open')
    .map((one) => `${recordOf(one)} ${one.dataset.changePrimary}`);

beforeEach(() => {
  localStorage.clear();
  treeOrderStore.getState().setOrder('triage');
});
afterEach(cleanup);

it('badges the real family, lists its changes first and keeps all 24 members', () => {
  const view = render(<Harness context={family} />);
  const node = view.getByTestId('review-family');
  const rows = stopsOf(node);
  // 24 members; the revised one has a row per revision (r3 before, r4 after), kept together.
  expect(rows).toHaveLength(25);
  expect(new Set(rows).size).toBe(24);
  expect(rows.slice(0, 11)).toEqual([
    'INV-2E8MG43K intent',
    'INV-ZS9ZS878 intent',
    'INV-ZS9ZS878 intent',
    'INV-21SDZECS implementation',
    'INV-555EHWM8 implementation',
    'INV-BR5MTSTY implementation',
    'INV-H8EM1VJR implementation',
    'INV-KKJYVB40 implementation',
    'INV-VPX81HXV implementation',
    'INV-2TQGXFAX membership',
    'INV-49H32B3F unchanged',
  ]);
  expect(rows.slice(10).every((row) => row.endsWith(' unchanged'))).toBe(true);
  // A stale entry the curator repaired in the unchanged SubjectReview.tsx has moved.
  const repaired = within(node)
    .getAllByTestId('review-family-member-open')
    .find((one) => recordOf(one) === 'INV-21SDZECS')!;
  expect(within(repaired).getByTestId('review-change-badge').title).toContain(
    'realization RLZ-4BD3B78H re-anchored (stale at base)',
  );
  // An entry only carried to its current blob (same content) did not move.
  const carried = within(node)
    .getAllByTestId('review-family-member-open')
    .find((one) => recordOf(one) === 'INV-ZY0YMXMQ')!;
  expect(carried.dataset.changePrimary).toBe('unchanged');
  // The reworded invariant keeps its revision: intent, noted, never unchanged.
  const reworded = within(node)
    .getAllByTestId('review-family-member-open')
    .find((one) => recordOf(one) === 'INV-2E8MG43K')!;
  expect(within(reworded).getByTestId('review-change-mark').textContent).toBe(
    'same revision; text differs',
  );
  // Implementation established, yet two of its entries in changed files stay unresolved: the
  // unknown mark and its reasons stand (ICR-R32 rule 1). The entry that established nothing
  // (RLZ-YRVX77Q4) is named first; the re-anchored RLZ-1PT6BEVC follows.
  const unresolved = within(node)
    .getAllByTestId('review-family-member-open')
    .find((one) => recordOf(one) === 'INV-VPX81HXV')!;
  expect(within(unresolved).getByTestId('review-change-badge').dataset.changeMarks).toBe('unknown');
  expect(within(unresolved).getByTestId('review-change-why').textContent).toMatch(
    /^change kind unknown: RLZ-YRVX77Q4 supplies no range on the before side of dashboard\/src\/panels\/review\/FamilyTree\.tsx: it is recorded at blob \w{10}, but the before blob is \w{10} \(recorded_blob_mismatch[\s\S]*\(and 1 more\)$/,
  );
  // The revised invariant's code also changed: intent with an implementation mark.
  const revised = within(node)
    .getAllByTestId('review-family-member-open')
    .find((one) => recordOf(one) === 'INV-ZS9ZS878')!;
  expect(within(revised).getByTestId('review-change-badge').dataset.changeMarks).toBe(
    'implementation',
  );
  expect(view.getByTestId('review-family-breakdown').textContent).toBe(
    'Members by change: 2 intent · 6 impl · 1 membership · 0 unknown of 24',
  );
});

it('does not treat an incomplete roster page as unreturned members, so j never stops there', () => {
  const view = render(<Harness context={family} />);
  // The real roster page is incomplete (its other items remain), yet every member is returned.
  expect(family.entries[0].after.page?.complete).toBe(false);
  expect(view.getAllByTestId('review-family-roster-next').length).toBeGreaterThan(0);
  expect(view.getByTestId('review-family').dataset.membersUnreturned).toBeUndefined();
  view.getByTestId('review-family-open').focus();
  const visited: (string | undefined)[] = [];
  for (let step = 0; step < 10; step += 1) {
    fireEvent.keyDown(document.activeElement!, J);
    visited.push(recordOf(view.container.querySelector('[aria-current="true"]')));
  }
  expect(visited).toEqual([
    'INV-2E8MG43K',
    'INV-ZS9ZS878',
    'INV-21SDZECS',
    'INV-555EHWM8',
    'INV-BR5MTSTY',
    'INV-H8EM1VJR',
    'INV-KKJYVB40',
    'INV-VPX81HXV',
    'INV-2TQGXFAX',
    'INV-2TQGXFAX',
  ]);
  expect(view.getByTestId('review-change-status').textContent).toMatch(/No later change/);
});

it('shows the shared member where it joined and the revised guarantee on its own row', () => {
  const view = render(<Harness context={shared} />);
  const rows = view.getAllByTestId('review-family').map((node) => ({
    guarantee: within(node).getByTestId('review-guarantee-change').dataset.changeKind,
    member: within(node)
      .getAllByTestId('review-family-member-open')
      .find((one) => recordOf(one) === 'INV-2TQGXFAX')?.dataset.changePrimary,
    breakdown: within(node).getByTestId('review-family-breakdown').textContent,
  }));
  expect(rows).toEqual([
    {
      guarantee: 'unchanged',
      member: 'membership',
      breakdown: 'Members by change: 2 intent · 6 impl · 1 membership · 0 unknown of 24',
    },
    {
      guarantee: 'intent',
      member: 'unchanged',
      breakdown: 'Members by change: 0 intent · 0 impl · 0 membership · 0 unknown of 7',
    },
  ]);
});

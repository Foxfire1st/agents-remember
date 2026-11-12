// Immutable MIK-L33 capture facts remain visible in the main page. R86 moves them out of the tree.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { cleanup, render, within } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import type { ReviewFamilyContext, ReviewResult, ReviewOutline } from '../../data/review';
import type { ReviewFamilyContextEntry, ReviewFamilyMember } from '../../data/reviewFamily';
import {
  FamilyBreakdown,
  GuaranteeChangeBadge,
  MemberChangeBadge,
  useMemberChange,
} from './ChangeBadges';
import { outlineGroups, familyTriage, memberChanges } from './changeTriage';

const contextOf = (name: string): ReviewFamilyContext =>
  (
    JSON.parse(
      readFileSync(path.join(path.dirname(new URL(import.meta.url).pathname), name), 'utf8'),
    ) as ReviewResult
  ).payload!.family_context!;
const family = contextOf('triageReal.family.captured.json');
const shared = contextOf('triageReal.shared.captured.json');
afterEach(cleanup);
function MainMember({
  entry,
  member,
}: {
  entry: ReviewFamilyContextEntry;
  member: ReviewFamilyMember;
}) {
  const state = useMemberChange(
    memberChanges(entry),
    member.member_id,
    entry.family_id,
    member.invariant_revision_id,
  );
  return (
    <section data-testid={member.display_label}>
      <MemberChangeBadge state={state} />
    </section>
  );
}
function MainFacts({ context }: { context: ReviewFamilyContext }) {
  return (
    <>
      {context.entries.map((entry) => {
        const members = new Map(
          [...entry.before.members, ...entry.after.members].map((member) => [
            member.member_id,
            member,
          ]),
        );
        return (
          <section key={entry.family_id} data-testid="main-family-facts">
            <GuaranteeChangeBadge kinds={entry.change_kinds} />
            <FamilyBreakdown triage={familyTriage(entry)} />
            {[...members.values()].map((member) => (
              <MainMember key={member.member_id} entry={entry} member={member} />
            ))}
          </section>
        );
      })}
    </>
  );
}
it('keeps every delivered fact and every reason in the main page outside disclosures', () => {
  const view = render(<MainFacts context={family} />);
  expect(view.getAllByTestId('review-change-badge')).toHaveLength(24);
  expect(view.getByTestId('review-family-breakdown').textContent).toBe(
    'Members by change: 2 intent · 6 impl · 1 membership · 0 unknown of 24',
  );
  const repaired = view.getByTestId('INV-21SDZECS');
  expect(within(repaired).getByTestId('review-change-badge').title).toContain(
    'realization RLZ-4BD3B78H re-anchored (stale at base)',
  );
  expect(
    within(view.getByTestId('INV-2E8MG43K')).getByTestId('review-change-mark').textContent,
  ).toBe('same revision; text differs');
  const unresolved = view.getByTestId('INV-VPX81HXV');
  expect(within(unresolved).getByTestId('review-change-badge').dataset.changeMarks).toBe('unknown');
  const reason = within(unresolved).getByTestId('review-change-why').textContent;
  expect(reason).toContain('RLZ-YRVX77Q4');
  expect(reason).toContain('RLZ-1PT6BEVC');
  expect(reason).not.toContain('and 1 more');
  expect(
    within(view.getByTestId('INV-ZS9ZS878')).getByTestId('review-change-badge').dataset.changeMarks,
  ).toBe('implementation');
  expect(view.container.querySelector('details')).toBeNull();
});
it('preserves each family guarantee and occurrence membership on the main page', () => {
  const view = render(<MainFacts context={shared} />);
  const rows = view.getAllByTestId('main-family-facts').map((node) => ({
    guarantee: within(node).getByTestId('review-guarantee-change').dataset.changeKind,
    member: within(within(node).getByTestId('INV-2TQGXFAX')).getByTestId('review-change-badge')
      .dataset.changeKind,
  }));
  expect(rows).toEqual([
    { guarantee: 'unchanged', member: 'membership' },
    { guarantee: 'intent', member: 'unchanged' },
  ]);
});

it('uses walked implementation facts for outline badges with intent and membership precedence', () => {
  const [entry] = family.entries;
  const members = new Map(
    [...entry.before.members, ...entry.after.members].map((member) => [
      member.invariant_id!,
      member,
    ]),
  );
  const before = new Set(entry.before.members.map((member) => member.invariant_id));
  const after = new Set(entry.after.members.map((member) => member.invariant_id));
  // Synthetic outline index fields over immutable captured member facts, not a served outline.
  const outline: ReviewOutline = {
    state: 'outlined',
    detail: 'Synthetic outline for captured implementation overlay.',
    families: [
      {
        selector_id: entry.family_id,
        id: 'FAM-captured',
        title: 'Captured family',
        change: 'unchanged',
        members: [...members].map(([id, row]) => ({
          selector_id: id,
          id: row.display_label!,
          membership: !before.has(id) ? 'joined' : !after.has(id) ? 'left' : 'both',
        })),
      },
    ],
    invariants: [...members].map(([id, row]) => ({
      selector_id: id,
      id: row.display_label!,
      summary: 'Captured member',
      change:
        memberChanges(entry).get(row.member_id)!.primary === 'intent' ? 'changed' : 'unchanged',
    })),
  };
  const [group] = outlineGroups(outline, [], family.entries, 'priority');
  expect(group.members).toHaveLength(24);
  expect(group.members.filter((row) => row.badge === 'changed')).toHaveLength(2);
  expect(group.members.filter((row) => row.badge === 'code changed')).toHaveLength(6);
  expect(group.members.filter((row) => row.badge === 'joined')).toHaveLength(1);
  expect(group.family!.badge).toBe('9 changed');
  expect(group.members.find((row) => row.id === 'INV-ZS9ZS878')!.badge).toBe('changed');
});

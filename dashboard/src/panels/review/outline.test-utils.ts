// Explicit synthetic outline adapters for immutable captures predating R86. Captures keep their bytes.
import type {
  ReviewEntry,
  ReviewOutline,
  ReviewOutlineChange,
  ReviewResult,
  ReviewFamilyContextEntry,
  ReviewFamilyMember,
} from '../../data/review';
import { memberChanges } from './changeTriage';
const identity = (member: ReviewFamilyMember) =>
  member.invariant_id ?? member.invariant_revision_id;
function memberChange(
  entries: ReviewFamilyContextEntry[],
  member: ReviewFamilyMember,
): ReviewOutlineChange {
  const id = identity(member);
  const before = entries
    .flatMap((entry) => entry.before.members)
    .filter((row) => identity(row) === id);
  const after = entries
    .flatMap((entry) => entry.after.members)
    .filter((row) => identity(row) === id);
  if (!before.length) return 'added';
  if (!after.length) return 'removed';
  const intent = entries.some((entry) =>
    entry.change_kinds?.members.some(
      (fact) => fact.member_id === member.member_id && fact.primary === 'intent',
    ),
  );
  if (intent) return 'changed';
  const revisions = (rows: ReviewFamilyMember[]) =>
    [...new Set(rows.map((row) => row.invariant_revision_id))].sort().join(' ');
  return revisions(before) === revisions(after) ? 'unchanged' : 'changed';
}
export function syntheticCatalogue(results: ReviewResult[]): {
  entries: ReviewEntry[];
  outline: ReviewOutline;
} {
  const families = new Map(
    results.flatMap((result) =>
      (result.payload?.family_context?.entries ?? []).map(
        (entry) => [entry.family_id, entry] as const,
      ),
    ),
  );
  const invariants = new Map<string, ReviewOutline['invariants'][number]>();
  const outline: ReviewOutline = {
    state: 'outlined',
    detail: 'Synthetic index outline over immutable older captures.',
    families: [],
    invariants: [],
  };
  for (const entry of families.values()) {
    const members = new Map(
      [...entry.after.members, ...entry.before.members].map((member) => [identity(member), member]),
    );
    const ordered = [...members].sort(
      (a, b) =>
        (memberChanges(entry).get(a[1].member_id)?.authored_position ?? 0) -
        (memberChanges(entry).get(b[1].member_id)?.authored_position ?? 0),
    );
    const before = new Set(entry.before.members.map(identity));
    const after = new Set(entry.after.members.map(identity));
    outline.families.push({
      selector_id: entry.family_id,
      id: entry.family_id,
      title: entry.display_label ?? entry.family_id,
      change:
        entry.before.guarantee?.joint_guarantee === entry.after.guarantee?.joint_guarantee
          ? 'unchanged'
          : 'changed',
      members: ordered.map(([id, row]) => ({
        selector_id: id,
        id: row.display_label ?? id,
        membership: !before.has(id) ? 'joined' : !after.has(id) ? 'left' : 'both',
      })),
    });
    for (const [id, member] of members)
      invariants.set(id, {
        selector_id: id,
        id: member.display_label ?? id,
        summary: member.statement ?? 'Content not on this roster page',
        change: memberChange([...families.values()], member),
      });
  }
  outline.invariants = [...invariants.values()];
  const entries: ReviewEntry[] = [
    ...outline.families.map((row): ReviewEntry => ({
      selector_kind: 'family',
      selector_id: row.selector_id,
      label: row.title,
      presence: 'both',
    })),
    ...outline.invariants.map((row): ReviewEntry => ({
      selector_kind: 'invariant',
      selector_id: row.selector_id,
      label: `${row.id} ${row.summary}`,
      presence: 'both',
    })),
  ];
  return { outline, entries };
}

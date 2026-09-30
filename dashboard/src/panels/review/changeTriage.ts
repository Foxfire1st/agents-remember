// The family tree's triage (MIK-R33, adopting ICR-R32): the change facts the server delivered with
// the roster, turned into order, counts and labels. Pure functions over the delivered facts; nothing
// here decides a fact. A family entry without `change_kinds` (a dataset review) is left exactly as
// the landed tree shows it.

import type {
  ReviewChangeKind,
  ReviewChangeMark,
  ReviewFamilyChanges,
  ReviewFamilyContextEntry,
  ReviewFamilyMember,
  ReviewMemberChange,
} from '../../data/reviewFamily';
import type { TreeOrder } from './triageOrderPreference';

// Precedence and sort weight (ICR-R32 rule 2): intent > implementation > membership > unknown >
// unchanged.
export const CHANGE_WEIGHT: Record<ReviewChangeKind, number> = {
  intent: 4,
  implementation: 3,
  membership: 2,
  unknown: 1,
  unchanged: 0,
};

export const CHANGE_LABEL: Record<ReviewChangeKind | ReviewChangeMark, string> = {
  intent: 'intent',
  implementation: 'impl',
  membership: 'membership',
  unknown: 'unknown',
  unchanged: 'unchanged',
  text_differs: 'same revision; text differs',
  test: 'test',
};

// What each kind says about an occurrence, for its accessible description.
export const CHANGE_MEANING: Record<ReviewChangeKind, string> = {
  intent:
    'the invariant revision differs between the sides, it was added or removed, or its text differs',
  implementation: 'the revision is unchanged; an entry changed or a changed hunk meets its range',
  membership: "this family's record lists it on one side only",
  unknown: 'a fact could not be established, so this is not known to be unchanged',
  unchanged: 'revision, membership and realization facts are all established as unchanged',
};

export interface FamilyTriage {
  guarantee: ReviewFamilyChanges['guarantee'];
  counts: Record<ReviewChangeKind, number>;
  // Distinct member occurrences this tree holds (every page walked so far).
  returned: number;
  // The deduplicated union of the family's members on either side, when it could be read.
  total?: number;
  // Some of the family's members are not returned yet.
  partial: boolean;
  // The breakdown covers only the returned members: some are unreturned, or the total is unknown.
  scoped: boolean;
  // The family's sort weight: its highest member or guarantee weight, and at least `unknown` while
  // members are unreturned or the total is unknown.
  weight: number;
}

const GUARANTEE_WEIGHT: Record<ReviewFamilyChanges['guarantee'], number> = {
  intent: CHANGE_WEIGHT.intent,
  unknown: CHANGE_WEIGHT.unknown,
  unchanged: CHANGE_WEIGHT.unchanged,
};

export function hasChangeFacts(entries: readonly ReviewFamilyContextEntry[]): boolean {
  return entries.some((entry) => entry.change_kinds !== undefined);
}

export function memberChanges(entry: ReviewFamilyContextEntry): Map<string, ReviewMemberChange> {
  return new Map((entry.change_kinds?.members ?? []).map((member) => [member.member_id, member]));
}

// The kind a returned member occurrence shows. A returned member the facts do not describe reads
// `unknown`, never `unchanged`.
export function occurrenceKind(
  facts: Map<string, ReviewMemberChange>,
  member: ReviewFamilyMember,
): ReviewChangeKind {
  return facts.get(member.member_id)?.primary ?? 'unknown';
}

function returnedMembers(entry: ReviewFamilyContextEntry): Map<string, ReviewFamilyMember> {
  const returned = new Map<string, ReviewFamilyMember>();
  for (const side of [entry.before, entry.after]) {
    for (const member of side.members)
      if (!returned.has(member.member_id)) returned.set(member.member_id, member);
  }
  return returned;
}

export function familyTriage(entry: ReviewFamilyContextEntry): FamilyTriage | undefined {
  const kinds = entry.change_kinds;
  if (kinds === undefined) return undefined;
  const facts = memberChanges(entry);
  const counts: Record<ReviewChangeKind, number> = {
    intent: 0,
    implementation: 0,
    membership: 0,
    unknown: 0,
    unchanged: 0,
  };
  const returned = returnedMembers(entry);
  for (const member of returned.values()) counts[occurrenceKind(facts, member)] += 1;
  const total = kinds.members_total;
  // Unreturned members: fewer returned than the family's total. A roster page can be incomplete
  // with every member returned (its other items remain); only without a total does an incomplete
  // page have to count as unreturned members.
  const partial =
    total !== undefined
      ? returned.size < total
      : [entry.before, entry.after].some((side) => side.page !== undefined && !side.page.complete);
  // A total the server could not read never lets the returned members pass for the whole family.
  const scoped = partial || total === undefined;
  const weights = [
    GUARANTEE_WEIGHT[kinds.guarantee],
    ...Object.entries(counts)
      .filter(([, count]) => count > 0)
      .map(([kind]) => CHANGE_WEIGHT[kind as ReviewChangeKind]),
    scoped ? CHANGE_WEIGHT.unknown : CHANGE_WEIGHT.unchanged,
  ];
  return {
    guarantee: kinds.guarantee,
    counts,
    returned: returned.size,
    total,
    partial,
    scoped,
    weight: Math.max(...weights),
  };
}

// The family row's breakdown (ICR-R32 rules 3 and 5). It is not the entry's `+N −N` intent count.
export function breakdownText(triage: FamilyTriage): string {
  const { counts } = triage;
  const head = `${counts.intent} intent · ${counts.implementation} impl · ${counts.membership} membership · ${counts.unknown} unknown`;
  if (!triage.scoped) return `${head} of ${triage.total ?? triage.returned}`;
  const total = triage.total === undefined ? 'total unknown' : `${triage.total} total`;
  return `${head} of ${triage.returned} returned (${total})`;
}

// Families by their weight, then authored order (the context's own order); unchanged families stay.
export function orderFamilies(
  entries: readonly ReviewFamilyContextEntry[],
  order: TreeOrder,
): ReviewFamilyContextEntry[] {
  if (order === 'authored' || !hasChangeFacts(entries)) return [...entries];
  return entries
    .map((entry, index) => ({ entry, index, weight: familyTriage(entry)?.weight ?? 0 }))
    .sort((a, b) => b.weight - a.weight || a.index - b.index)
    .map(({ entry }) => entry);
}

// Member rows by weight (triage order only), then authored order: the family record's `members`
// position the server delivered, then the landed row order. The rows of one occurrence (both
// revisions of a revised member) stay together. Unchanged siblings are never removed.
export function orderMemberRows<Row extends { member: ReviewFamilyMember }>(
  entry: ReviewFamilyContextEntry,
  rows: readonly Row[],
  order: TreeOrder,
): Row[] {
  if (entry.change_kinds === undefined) return [...rows];
  const facts = memberChanges(entry);
  const first = new Map<string, number>();
  rows.forEach((row, index) => {
    if (!first.has(row.member.member_id)) first.set(row.member.member_id, index);
  });
  const key = (row: Row, index: number): number[] => {
    const change = facts.get(row.member.member_id);
    const weight = order === 'triage' ? -CHANGE_WEIGHT[occurrenceKind(facts, row.member)] : 0;
    const position = change?.authored_position ?? Number.MAX_SAFE_INTEGER;
    return [weight, position, first.get(row.member.member_id) ?? index, index];
  };
  return rows
    .map((row, index) => ({ row, key: key(row, index) }))
    .sort((a, b) => compareKeys(a.key, b.key))
    .map(({ row }) => row);
}

function compareKeys(a: number[], b: number[]): number {
  for (let index = 0; index < a.length; index += 1) {
    const difference = (a[index] ?? 0) - (b[index] ?? 0);
    if (difference !== 0) return difference;
  }
  return 0;
}

// Union two deliveries of one family's facts (an admitted roster continuation): every occurrence
// either delivery described is kept, and the newer family-level facts apply.
export function mergeChangeKinds(
  previous: ReviewFamilyChanges | undefined,
  next: ReviewFamilyChanges | undefined,
): ReviewFamilyChanges | undefined {
  if (previous === undefined || next === undefined) return next ?? previous;
  const members = new Map(previous.members.map((member) => [member.member_id, member]));
  for (const member of next.members) members.set(member.member_id, member);
  return { ...next, members: [...members.values()] };
}

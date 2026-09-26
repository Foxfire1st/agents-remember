// Group realization claims by recorded address; observed identities remain per-side facts.
import { FAMILY_SIDES } from '../../data/review';
import type {
  ReviewFamilyContextEntry,
  ReviewFamilyMember,
  ReviewFamilyMemberSource,
  ReviewFamilySideName,
} from '../../data/review';

const RESOLVED_ADDRESSES: ReadonlySet<string> = new Set(['exact_recorded_blob']);
const UNMEASURED_ADDRESSES: ReadonlySet<string> = new Set([
  'recorded_object_unavailable',
  'not_requested',
]);

export interface FamilyMembershipRow {
  side: ReviewFamilySideName;
  member: ReviewFamilyMember;
}

export function carriedMembership(entry: ReviewFamilyContextEntry): FamilyMembershipRow[] {
  return [
    ...entry.before.members.map((member) => ({ side: 'before' as const, member })),
    ...entry.after.members.map((member) => ({ side: 'after' as const, member })),
  ];
}

export interface FamilyExcerptOccurrence {
  side: ReviewFamilySideName;
  revision: string;
  label?: string;
}

export interface FamilyExcerptSideReading {
  side: ReviewFamilySideName;
  resolutions: string[];
  observed: string[];
}

export interface FamilyExpressionExcerpt {
  key: string;
  path: string;
  recorded?: string;
  readingsBySide: FamilyExcerptSideReading[];
  roles: string[];
  details: string[];
  occurrences: FamilyExcerptOccurrence[];
  revisions: string[];
  rows: number;
}

export interface FamilyExpressionCollection {
  excerpts: FamilyExpressionExcerpt[];
  rows: number;
  membershipRows: number;
  bySide: { side: ReviewFamilySideName; rows: number }[];
  membershipRowsWithChanged: number;
  membershipRowsWithoutChanged: number;
  distinctRevisions: number;
  resolved: number;
  unmeasured: number;
}

function excerptKey(claim: ReviewFamilyMemberSource): string {
  return `${claim.path ?? ''}\u0000${claim.recorded_source_identity ?? ''}`;
}

function claimClass(claim: ReviewFamilyMemberSource): 'changed' | 'resolved' | 'unmeasured' {
  if (claim.path === undefined || claim.resolution === undefined) return 'unmeasured';
  if (RESOLVED_ADDRESSES.has(claim.resolution)) return 'resolved';
  if (UNMEASURED_ADDRESSES.has(claim.resolution)) return 'unmeasured';
  return 'changed';
}

interface ExcerptTally {
  byKey: Map<string, FamilyExpressionExcerpt>;
  occurrences: Map<string, Set<string>>;
  rows: number;
  resolved: number;
  unmeasured: number;
  membershipRowsWithChanged: number;
  bySide: { side: ReviewFamilySideName; rows: number }[];
}

function absorbClaim(
  key: string,
  claim: ReviewFamilyMemberSource,
  row: { side: ReviewFamilySideName; member: ReviewFamilyMember },
  tally: ExcerptTally,
): void {
  let excerpt = tally.byKey.get(key);
  if (excerpt === undefined) {
    excerpt = {
      key,
      path: claim.path ?? '',
      recorded: claim.recorded_source_identity,
      readingsBySide: [],
      roles: [],
      details: [],
      occurrences: [],
      revisions: [],
      rows: 0,
    };
    tally.byKey.set(key, excerpt);
    tally.occurrences.set(key, new Set());
  }
  excerpt.rows += 1;
  if (!excerpt.roles.includes(claim.role)) excerpt.roles.push(claim.role);
  if (!excerpt.details.includes(claim.detail)) excerpt.details.push(claim.detail);
  const revision = row.member.invariant_revision_id;
  if (!excerpt.revisions.includes(revision)) excerpt.revisions.push(revision);
  const occurrenceKey = `${row.side}\u0000${revision}`;
  if (!tally.occurrences.get(key)?.has(occurrenceKey)) {
    tally.occurrences.get(key)?.add(occurrenceKey);
    excerpt.occurrences.push({ side: row.side, revision, label: row.member.display_label });
  }
}

function tallyChangedExcerpts(membership: FamilyMembershipRow[]): ExcerptTally {
  const tally: ExcerptTally = {
    byKey: new Map(),
    occurrences: new Map(),
    rows: 0,
    resolved: 0,
    unmeasured: 0,
    membershipRowsWithChanged: 0,
    bySide: FAMILY_SIDES.map((side) => ({ side, rows: 0 })),
  };
  for (const row of membership) {
    let changedHere = 0;
    for (const claim of row.member.sources) {
      const kind = claimClass(claim);
      if (kind === 'resolved') tally.resolved += 1;
      if (kind === 'unmeasured') tally.unmeasured += 1;
      if (kind !== 'changed') continue;
      tally.rows += 1;
      changedHere += 1;
      const sideTally = tally.bySide.find((entry) => entry.side === row.side);
      if (sideTally !== undefined) sideTally.rows += 1;
      absorbClaim(excerptKey(claim), claim, row, tally);
    }
    if (changedHere > 0) tally.membershipRowsWithChanged += 1;
  }
  return tally;
}

function recordSideReadings(
  membership: FamilyMembershipRow[],
  byKey: Map<string, FamilyExpressionExcerpt>,
): void {
  for (const { side, member } of membership) {
    for (const claim of member.sources) {
      if (claim.path === undefined || claim.resolution === undefined) continue;
      const excerpt = byKey.get(excerptKey(claim));
      if (excerpt === undefined) continue;
      let reading = excerpt.readingsBySide.find((entry) => entry.side === side);
      if (reading === undefined) {
        reading = { side, resolutions: [], observed: [] };
        excerpt.readingsBySide.push(reading);
      }
      if (!reading.resolutions.includes(claim.resolution))
        reading.resolutions.push(claim.resolution);
      const observed = claim.observed_source_identity;
      if (observed !== undefined && !reading.observed.includes(observed))
        reading.observed.push(observed);
    }
  }
}

function orderExcerpts(byKey: Map<string, FamilyExpressionExcerpt>): FamilyExpressionExcerpt[] {
  const sideRank = (side: ReviewFamilySideName) => FAMILY_SIDES.indexOf(side);
  for (const excerpt of byKey.values()) {
    excerpt.readingsBySide.sort((left, right) => sideRank(left.side) - sideRank(right.side));
    for (const reading of excerpt.readingsBySide) reading.observed.sort();
    excerpt.roles.sort();
    excerpt.occurrences.sort(
      (left, right) =>
        sideRank(left.side) - sideRank(right.side) || left.revision.localeCompare(right.revision),
    );
    excerpt.revisions.sort();
  }
  return [...byKey.values()].sort((left, right) => left.key.localeCompare(right.key));
}

export function familyExpressionExcerpts(
  membership: FamilyMembershipRow[],
): FamilyExpressionCollection {
  const tally = tallyChangedExcerpts(membership);
  recordSideReadings(membership, tally.byKey);
  const revisions = new Set(membership.map((row) => row.member.invariant_revision_id));
  return {
    excerpts: orderExcerpts(tally.byKey),
    rows: tally.rows,
    membershipRows: membership.length,
    bySide: tally.bySide,
    membershipRowsWithChanged: tally.membershipRowsWithChanged,
    membershipRowsWithoutChanged: membership.length - tally.membershipRowsWithChanged,
    distinctRevisions: revisions.size,
    resolved: tally.resolved,
    unmeasured: tally.unmeasured,
  };
}

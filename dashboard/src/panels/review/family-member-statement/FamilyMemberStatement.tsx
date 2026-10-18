import type { ReviewFamilyContextEntry, ReviewFamilyMember } from '../../../data/review';
import { muted, prose } from '../familyCenterStyles';
import { IntentTextPassage } from '../IntentWordDiff';

interface MemberStatementPair {
  before: ReviewFamilyMember;
  after: ReviewFamilyMember;
}

// Index the roster once rather than rescanning both sides for every displayed row.
export function memberStatementPairs(
  entry: ReviewFamilyContextEntry,
): Map<string, MemberStatementPair> {
  const unique = (members: ReviewFamilyMember[]) => {
    const found = new Map<string, ReviewFamilyMember | null>();
    for (const member of members)
      if (member.invariant_id)
        found.set(member.invariant_id, found.has(member.invariant_id) ? null : member);
    return found;
  };
  const before = unique(entry.before.members);
  const after = unique(entry.after.members);
  const pairs = new Map<string, MemberStatementPair>();
  for (const [id, left] of before) {
    const right = after.get(id);
    if (
      left?.state === 'recorded' &&
      right?.state === 'recorded' &&
      left.statement !== undefined &&
      right.statement !== undefined &&
      left.statement !== right.statement
    )
      pairs.set(id, { before: left, after: right });
  }
  return pairs;
}

// Only an unambiguous recorded pair is compared. Both revision links stay in the
// roster; the first row carries their one passage, the second repeats no text.
export function FamilyMemberStatement({
  pair,
  member,
}: {
  pair?: MemberStatementPair;
  member: ReviewFamilyMember;
}) {
  if (pair) {
    const { before, after } = pair;
    if (member.invariant_revision_id === before.invariant_revision_id)
      return (
        <IntentTextPassage
          before={before.statement!}
          after={after.statement!}
          field="statement"
          offerLayout
        />
      );
    if (member.invariant_revision_id === after.invariant_revision_id) return null;
  }
  return member.state === 'recorded' ? (
    <div className={prose} data-review-content="statement">
      {member.statement}
    </div>
  ) : (
    <div className={muted}>statement not carried on this page ({member.state})</div>
  );
}

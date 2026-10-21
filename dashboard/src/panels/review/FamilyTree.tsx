// The family roster helpers the reading column's family review uses (MIK-R39): the selection of a
// family position, the walked tree, member rows of both sides, and the roster lines and continuation.
// The review page's navigation tree is tree/ReviewTree.tsx (MIK-R86 v2), drawn from changeSetOrder.ts.
import { css } from '../../../styled-system/css';
import type {
  ReviewFamilyContextEntry,
  ReviewFamilyMember,
  ReviewFamilyRevisionContext,
  ReviewFamilyRosterPage,
  ReviewFamilySideName,
} from '../../data/review';
import { FAMILY_SIDES } from '../../data/review';
import { rowWording, wordingComparison } from './statementWording';
import { tokenWords } from './stateWords';

export interface FamilySelection {
  familyId: string;
  memberRevisionId?: string;
}

// The tree the reader has walked (MIK-R39), drawn in place of the selected subject's context alone.
// `kept` names, by family, the families that are not in the selected subject's context and the
// subject each was last read for.
export interface WalkedTree {
  entries: ReviewFamilyContextEntry[];
  kept: ReadonlyMap<string, string>;
  // Said beside the tree: the walk started again because the comparison changed.
  notice?: string | null;
  // Said above the tree when the selected subject's own answer composed no family context.
  subjectState?: string | null;
}

const muted = css({ color: 'muted', fontSize: '0.75rem', margin: '0.25rem 0' });
const node = css({
  display: 'flex',
  width: '100%',
  minWidth: 0,
  alignItems: 'center',
  gap: '0.35rem',
  background: 'transparent',
  border: 'none',
  color: 'ink',
  cursor: 'pointer',
  font: 'inherit',
  lineHeight: '1.5',
  padding: '0 0.3rem',
  textAlign: 'left',
  whiteSpace: 'nowrap',
  _hover: { background: 'color-mix(in oklab, var(--amber) 8%, transparent)' },
  _focusVisible: { outline: '1px solid var(--amber)' },
});
interface MemberRow {
  invariantRevisionId: string;
  sides: ReviewFamilySideName[];
  member: ReviewFamilyMember;
  // For a revision listed on both sides: whether the two sides' carried texts are the same, differ,
  // or cannot be compared (a side's content is not on this page). A tree comparison says so rather
  // than calling one revision "unchanged" (review R1 F2).
  wording?: 'same' | 'differs' | 'unknown';
}

function sidesWording(first: ReviewFamilyMember, second: ReviewFamilyMember): MemberRow['wording'] {
  if (first.state !== 'recorded' || second.state !== 'recorded') return 'unknown';
  const same = wordingComparison(rowWording(first), rowWording(second), false);
  return same.kind === 'wording_unchanged' ? 'same' : 'differs';
}

export function memberRows(entry: ReviewFamilyContextEntry): MemberRow[] {
  const byRevision = new Map<string, MemberRow>();
  for (const side of FAMILY_SIDES) {
    for (const member of entry[side].members) {
      const existing = byRevision.get(member.invariant_revision_id);
      if (existing === undefined) {
        byRevision.set(member.invariant_revision_id, {
          invariantRevisionId: member.invariant_revision_id,
          sides: [side],
          member,
        });
        continue;
      }
      existing.sides.push(side);
      existing.wording = sidesWording(existing.member, member);
      if (member.state === 'recorded') existing.member = member;
    }
  }
  return [...byRevision.values()];
}

export function RosterLine({
  side,
  testid = 'review-family-roster',
}: {
  side: ReviewFamilyRevisionContext;
  testid?: string;
}) {
  const page = side.page;
  if (page === undefined) return null;
  const counts = page.counts;
  return (
    <p
      className={muted}
      data-testid={testid}
      data-roster-complete={page.complete ? 'true' : 'false'}
    >
      {side.side} roster ({tokenWords(page.state)}): the read has returned {counts.primary_items_returned} of{' '}
      {counts.primary_items_total} item(s) of this family revision&apos;s recorded selection,{' '}
      {counts.primary_items_remaining} remaining. The selected family revision records{' '}
      {side.members_total} membership row(s); loaded context contains {side.members.length} of them
      {completionNote(page)}
      {counts.unresolved_anchor_total
        ? ` · ${counts.unresolved_anchor_total} anchor(s) not at their recorded blob`
        : ''}{' '}
      · {page.scope.join(' · ') || 'no scope recorded'}
    </p>
  );
}

function completionNote(page: ReviewFamilyRosterPage): string {
  if (!page.complete) return '';
  return page.state === 'first_page'
    ? ' — the page is the whole selection'
    : ' — this page completes the walk; loaded context can retain earlier pages';
}

function carriedOf(side: ReviewFamilyRevisionContext): string {
  return `${side.side}: ${side.members.length} of the ${side.members_total} recorded membership row(s) it measured`;
}

export function emptyRosterSentence(entry: ReviewFamilyContextEntry): string {
  const recorded = FAMILY_SIDES.map((side) => entry[side]).filter(
    (side) => side.state === 'recorded',
  );
  if (recorded.length === 0) return `no member row is carried here: ${entry.detail}`;
  const measured = recorded.reduce((total, side) => total + side.members_total, 0);
  if (measured === 0) {
    return 'the read measured zero memberships for the selected family revision: it records 0 recorded membership row(s).';
  }
  const counts = recorded.map(carriedOf).join('; ');
  const bounded = recorded.some((side) => side.page !== undefined && !side.page.complete);
  return bounded
    ? `this page carried no member row — ${counts}. The continuation beside each bounded roster reaches the rows this page did not carry.`
    : `this page carried no member row — ${counts}.`;
}

export function RosterNext({
  entry,
  onRosterNext,
  testid = 'review-family-roster-next',
}: {
  entry: ReviewFamilyContextEntry;
  onRosterNext: (familyId: string, side: ReviewFamilySideName, continuation: string) => void;
  testid?: string;
}) {
  return (
    <>
      {FAMILY_SIDES.map((side) => {
        const page = entry[side].page;
        if (page === undefined || page.complete || page.continuation === undefined) return null;
        return (
          <button
            key={side}
            type="button"
            className={node}
            data-testid={testid}
            data-family={entry.family_id}
            data-family-label={familyLabel(entry)}
            data-side={side}
            data-continuation={page.continuation}
            onClick={() => onRosterNext(entry.family_id, side, page.continuation as string)}
          >
            continue the {side} roster walk of {familyLabel(entry)} at the cursor this page
            published
          </button>
        );
      })}
    </>
  );
}

function familyLabel(entry: ReviewFamilyContextEntry): string {
  return entry.display_label ?? entry.family_id;
}

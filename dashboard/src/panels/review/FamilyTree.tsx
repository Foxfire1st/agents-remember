import { Fragment } from 'react';

import { css, cx } from '../../../styled-system/css';
import type {
  ReviewFamilyContext,
  ReviewFamilyContextEntry,
  ReviewFamilyGuarantee,
  ReviewFamilyMember,
  ReviewFamilyRevisionContext,
  ReviewFamilyRosterPage,
  ReviewFamilySideName,
} from '../../data/review';
import { FAMILY_SIDES } from '../../data/review';

export interface FamilySelection {
  familyId: string;
  memberRevisionId?: string;
}

const shell = css({
  background: 'bgPanel',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '3px',
  padding: '1rem',
  minWidth: '0',
});

const sectionLabel = css({
  color: 'cyan',
  fontSize: '0.72rem',
  letterSpacing: '0.1em',
  textTransform: 'uppercase',
  margin: '0',
});

const searchInput = css({
  width: '100%',
  background: 'bg',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '2px',
  color: 'ink',
  font: 'inherit',
  padding: '0.25rem 0.35rem',
  _focusVisible: { outline: '1px solid var(--amber)', outlineOffset: '1px' },
});

const muted = css({ color: 'muted', fontSize: '0.78rem', margin: '0.2rem 0' });

const statements = css({ margin: '0.4rem 0', paddingLeft: '0.4rem', listStyle: 'none' });

const AMBER_WASH = (percent: number) =>
  `color-mix(in oklab, var(--amber) ${percent}%, transparent)`;

const node = css({
  display: 'block',
  width: '100%',
  background: 'transparent',
  border: 'none',
  borderLeftWidth: '2px',
  borderLeftStyle: 'solid',
  borderLeftColor: 'transparent',
  color: 'ink',
  cursor: 'pointer',
  font: 'inherit',
  padding: '0.65rem 0.5rem',
  textAlign: 'left',
  borderRadius: '2px',
  _focusVisible: { outline: '1px solid var(--amber)', outlineOffset: '1px' },
  _hover: { background: AMBER_WASH(8) },
});

const current = css({
  background: AMBER_WASH(16),
  borderLeftColor: 'amber',
});

const guaranteeText = css({
  whiteSpace: 'pre-wrap',
  margin: '0.15rem 0',
  fontSize: '0.84rem',
  borderLeftWidth: '1px',
  borderLeftStyle: 'solid',
  borderLeftColor: 'grid',
  paddingLeft: '0.5rem',
});

const memberText = css({
  display: 'block',
  whiteSpace: 'pre-wrap',
  margin: '0.15rem 0 0.35rem',
  fontSize: '0.82rem',
  color: 'ink',
});

const sideTag = css({
  color: 'amber',
  fontSize: '0.7rem',
  letterSpacing: '0.06em',
  textTransform: 'uppercase',
});

const familyBlock = css({
  marginBottom: '0.7rem',
  borderTopWidth: '1px',
  borderTopStyle: 'solid',
  borderTopColor: 'grid',
  paddingTop: '0.5rem',
});

interface MemberRow {
  invariantRevisionId: string;
  sides: ReviewFamilySideName[];
  member: ReviewFamilyMember;
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
      if (member.state === 'recorded') existing.member = member;
    }
  }
  return [...byRevision.values()];
}

function SideStates({ entry }: { entry: ReviewFamilyContextEntry }) {
  return (
    <>
      {FAMILY_SIDES.map((side) => {
        const context = entry[side];
        if (context.state === 'recorded') return null;
        return (
          <p
            key={side}
            className={muted}
            data-testid="review-family-side"
            data-side={side}
            data-side-state={context.state}
          >
            {side}: {context.state} — {context.detail}
          </p>
        );
      })}
    </>
  );
}

function FamilyHistory({ entry }: { entry: ReviewFamilyContextEntry }) {
  return (
    <>
      {FAMILY_SIDES.map((side) => {
        const recorded = entry[side].recorded_revision_ids;
        if (recorded.length === 0) return null;
        const selected = entry[side].family_revision_id;
        return (
          <p key={side} className={muted} data-testid="review-family-history" data-side={side}>
            {recorded.length} recorded revision(s) of this family in the {side} snapshot
            {selected === undefined
              ? '; this review selected none of them'
              : `; this review selected ${selected}`}
          </p>
        );
      })}
    </>
  );
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
      {side.side} roster ({page.state}): this page carried {counts.primary_items_returned} of{' '}
      {counts.primary_items_total} item(s) of this family revision&apos;s recorded selection,{' '}
      {counts.primary_items_remaining} remaining. The selected family revision records{' '}
      {side.members_total} membership row(s) and this page carried {side.members.length} of them
      {completionNote(page)}
      {counts.unresolved_anchor_total
        ? ` · ${counts.unresolved_anchor_total} unresolved anchor(s) in this selection`
        : ''}{' '}
      · {page.scope.join(' · ') || 'no scope recorded'}
    </p>
  );
}

function completionNote(page: ReviewFamilyRosterPage): string {
  if (!page.complete) return '';
  return page.state === 'first_page'
    ? ' — the page is the whole selection'
    : ' — this page completes the walk; the pages before it carried the rows this one did not';
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

function RosterLines({ entry }: { entry: ReviewFamilyContextEntry }) {
  return (
    <>
      {FAMILY_SIDES.map((side) => (
        <Fragment key={side}>
          <RosterLine side={entry[side]} />
        </Fragment>
      ))}
    </>
  );
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

function guaranteesOf(
  entry: ReviewFamilyContextEntry,
): { guarantee: ReviewFamilyGuarantee; side: string; note: string }[] {
  const before = entry.before.guarantee;
  const after = entry.after.guarantee;
  if (before === undefined && after === undefined) return [];
  if (before !== undefined && after !== undefined) {
    if (before.revision_id === after.revision_id) {
      return [
        {
          guarantee: before,
          side: 'both',
          note: 'Joint guarantee · unchanged',
        },
      ];
    }
    return [
      { guarantee: before, side: 'before', note: 'Joint guarantee · before' },
      { guarantee: after, side: 'after', note: 'Joint guarantee · after' },
    ];
  }
  const one = (before ?? after) as ReviewFamilyGuarantee;
  const side = before !== undefined ? 'before' : 'after';
  return [{ guarantee: one, side, note: `Joint guarantee · ${side} only` }];
}

function FamilyGuarantees({ entry }: { entry: ReviewFamilyContextEntry }) {
  const guarantees = guaranteesOf(entry);
  if (!guarantees.length) {
    return (
      <p className={muted} data-testid="review-family-guarantee">
        no family revision was selected for this family, so no guarantee of it may be shown:{' '}
        {entry.detail}
      </p>
    );
  }
  return (
    <>
      {guarantees.map((recorded) => (
        <p
          key={`${recorded.side}:${recorded.guarantee.revision_id}`}
          className={guaranteeText}
          data-testid="review-family-guarantee"
          data-guarantee-side={recorded.side}
          data-guarantee-revision={recorded.guarantee.revision_id}
        >
          <span
            className={css({
              display: 'block',
              color: 'cyan',
              fontSize: '0.72rem',
              marginBottom: '0.3rem',
            })}
          >
            {recorded.note}
          </span>
          {recorded.guarantee.joint_guarantee}
        </p>
      ))}
    </>
  );
}

function FamilyCandidates({ entry }: { entry: ReviewFamilyContextEntry }) {
  if (!entry.candidates.length) return null;
  return (
    <p className={muted} data-testid="review-family-candidates">
      {entry.candidates.length} recorded head(s) this context declined to choose between:{' '}
      {entry.candidates.map((candidate) => candidate.revision_id).join(', ')}
    </p>
  );
}

function memberLabel(member: ReviewFamilyMember): string {
  const identity = member.display_label ?? member.display_version ?? member.invariant_revision_id;
  return member.display_label && member.display_version
    ? `${identity} · ${member.display_version}`
    : identity;
}

function memberSidesNote(row: MemberRow): string {
  return row.sides.length === 2
    ? 'recorded on both snapshots'
    : `recorded on the ${row.sides[0]} snapshot only`;
}

function MemberNode({
  entry,
  row,
  selected,
  onSelect,
}: {
  entry: ReviewFamilyContextEntry;
  row: MemberRow;
  selected: FamilySelection | null;
  onSelect: (selection: FamilySelection) => void;
}) {
  const member = row.member;
  const isCurrent =
    selected?.familyId === entry.family_id &&
    selected?.memberRevisionId === member.invariant_revision_id;
  return (
    <li
      data-testid="review-family-member"
      data-member={member.member_id}
      data-sides={row.sides.join('+')}
    >
      <button
        type="button"
        className={cx(node, isCurrent ? current : undefined)}
        data-tree-node="member"
        data-testid="review-family-member-open"
        data-revision={member.invariant_revision_id}
        aria-current={isCurrent ? 'true' : undefined}
        onClick={() =>
          onSelect({ familyId: entry.family_id, memberRevisionId: member.invariant_revision_id })
        }
        onKeyDown={treeArrow}
      >
        {member.state === 'recorded' ? (
          <span className={memberText}>{member.statement}</span>
        ) : (
          <span data-testid="review-family-member-state">
            {memberLabel(member)} · statement not carried on this page
          </span>
        )}
        <span className={muted}>{memberSidesNote(row)}</span>
        <span className={sideTag}>
          {row.sides.length === 2 ? ' · unchanged revision' : ` · ${row.sides[0]} only`}
        </span>
      </button>
      {member.other_family_revision_ids.length ? (
        <details>
          <summary className={muted}>
            Shared member · {member.other_family_revision_ids.length} other family revisions
          </summary>
          <p className={muted} data-testid="review-family-member-shared">
            this same member revision is recorded under {member.other_family_revision_ids.length}{' '}
            other family revision(s): {member.other_family_revision_ids.join(', ')}
          </p>
        </details>
      ) : null}
    </li>
  );
}

function MemberRoster({
  entry,
  selected,
  onSelect,
}: {
  entry: ReviewFamilyContextEntry;
  selected: FamilySelection | null;
  onSelect: (selection: FamilySelection) => void;
}) {
  const rows = memberRows(entry);
  if (rows.length) {
    return (
      <ul className={statements}>
        {rows.map((row) => (
          <MemberNode
            key={row.invariantRevisionId}
            entry={entry}
            row={row}
            selected={selected}
            onSelect={onSelect}
          />
        ))}
      </ul>
    );
  }
  return (
    <p className={muted} data-testid="review-family-empty-roster">
      {emptyRosterSentence(entry)}
    </p>
  );
}

function FamilyNode({
  entry,
  selected,
  onSelect,
  onRosterNext,
}: {
  entry: ReviewFamilyContextEntry;
  selected: FamilySelection | null;
  onSelect: (selection: FamilySelection) => void;
  onRosterNext: (familyId: string, side: ReviewFamilySideName, continuation: string) => void;
}) {
  const familyCurrent =
    selected?.familyId === entry.family_id && selected.memberRevisionId === undefined;
  return (
    <li
      className={familyBlock}
      data-testid="review-family"
      data-family={entry.family_id}
      data-family-state={entry.state}
    >
      <button
        type="button"
        className={cx(node, familyCurrent ? current : undefined)}
        data-tree-node="family"
        data-testid="review-family-open"
        data-family={entry.family_id}
        aria-current={familyCurrent ? 'true' : undefined}
        onClick={() => onSelect({ familyId: entry.family_id })}
        onKeyDown={treeArrow}
      >
        ▾ {familyLabel(entry)}
      </button>
      <FamilyGuarantees entry={entry} />
      <details>
        <summary>{memberRows(entry).length} member revisions · roster details</summary>
        {entry.display_label !== undefined && entry.label_side !== undefined ? (
          <p className={muted} data-testid="review-family-label-side">
            the label “{entry.display_label}” is the {entry.label_side} snapshot&apos;s recorded
            label
          </p>
        ) : null}
        <FamilyCandidates entry={entry} />
        <SideStates entry={entry} />
        <FamilyHistory entry={entry} />
        <RosterLines entry={entry} />
      </details>
      <MemberRoster entry={entry} selected={selected} onSelect={onSelect} />
      <RosterNext entry={entry} onRosterNext={onRosterNext} />
    </li>
  );
}

function filterScope(
  query: string,
  shownFamilies: number,
  totalFamilies: number,
  shownMembers: number,
  totalMembers: number,
): string {
  if (query === '') {
    return `${totalFamilies} families · ${totalMembers} member revisions shown · full sibling context`;
  }
  return `Filter “${query}”: ${shownFamilies}/${totalFamilies} families · ${shownMembers}/${totalMembers} member revisions. Matching families retain all siblings.`;
}

export function familyMatches(entry: ReviewFamilyContextEntry, needle: string): boolean {
  const haystack = [
    entry.family_id,
    entry.display_label ?? '',
    entry.before.guarantee?.joint_guarantee ?? '',
    entry.after.guarantee?.joint_guarantee ?? '',
    ...entry.candidates.map((candidate) => candidate.joint_guarantee),
    ...memberRows(entry).flatMap((row) => [
      row.invariantRevisionId,
      row.member.invariant_revision_id,
      row.member.display_label ?? '',
      row.member.statement ?? '',
    ]),
  ]
    .join('\n')
    .toLowerCase();
  return haystack.includes(needle);
}

function treeArrow(event: React.KeyboardEvent<HTMLButtonElement>): void {
  const delta = event.key === 'ArrowDown' ? 1 : event.key === 'ArrowUp' ? -1 : 0;
  if (delta === 0) return;
  const nodes = Array.from(
    event.currentTarget
      .closest('[data-testid=review-family-list]')
      ?.querySelectorAll<HTMLButtonElement>('[data-tree-node]') ?? [],
  );
  const index = nodes.indexOf(event.currentTarget);
  if (index === -1) return;
  event.preventDefault();
  nodes[(index + delta + nodes.length) % nodes.length]?.focus();
}

function FamilyList({
  shown,
  selected,
  onSelect,
  onRosterNext,
}: {
  shown: ReviewFamilyContextEntry[];
  selected: FamilySelection | null;
  onSelect: (selection: FamilySelection) => void;
  onRosterNext: (familyId: string, side: ReviewFamilySideName, continuation: string) => void;
}) {
  return (
    <ul className={statements} data-testid="review-family-list">
      {shown.map((entry) => (
        <FamilyNode
          key={entry.family_id}
          entry={entry}
          selected={selected}
          onSelect={onSelect}
          onRosterNext={onRosterNext}
        />
      ))}
    </ul>
  );
}

export function FamilyTree({
  context,
  selection,
  onSelect,
  onRosterNext,
  query,
  onQuery,
  embedded = false,
}: {
  embedded?: boolean;
  context: ReviewFamilyContext;
  selection: FamilySelection | null;
  onSelect: (selection: FamilySelection) => void;
  onRosterNext: (familyId: string, side: ReviewFamilySideName, continuation: string) => void;
  query: string;
  onQuery: (next: string) => void;
}) {
  const needle = query.trim().toLowerCase();
  const allMembers = context.entries.reduce((total, entry) => total + memberRows(entry).length, 0);
  const shown = context.entries.filter((entry) => needle === '' || familyMatches(entry, needle));
  const shownMembers = shown.reduce((total, entry) => total + memberRows(entry).length, 0);
  const composed = context.entries.length > 0;

  return (
    <section
      className={embedded ? css({ minWidth: 0 }) : shell}
      data-testid="review-family-tree"
      data-family-state={context.state}
    >
      <label className={sectionLabel} htmlFor="review-family-filter">
        Find family, guarantee or member
      </label>
      <input
        id="review-family-filter"
        className={searchInput}
        data-testid="review-family-filter"
        type="search"
        placeholder="filter families, guarantees and members"
        value={query}
        onChange={(event) => onQuery(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Escape') onQuery('');
        }}
      />
      <p className={muted} data-testid="review-family-filter-scope">
        {filterScope(query, shown.length, context.entries.length, shownMembers, allMembers)}
      </p>
      <FamilyContextDetails context={context} composed={composed} />
      {shown.length ? (
        <FamilyList
          shown={shown}
          selected={selection}
          onSelect={onSelect}
          onRosterNext={onRosterNext}
        />
      ) : (
        <p className={muted} data-testid="review-family-none-shown">
          {composed
            ? `no family context matches “${query}”. The ${context.entries.length} composed context(s) are unchanged by this filter; clear it to see them.`
            : 'this review composed no family context, so there is nothing here to filter.'}
        </p>
      )}
    </section>
  );
}

function FamilyContextDetails({
  context,
  composed,
}: {
  context: ReviewFamilyContext;
  composed: boolean;
}) {
  return (
    <details>
      <summary>Family context details</summary>
      <p className={muted} data-testid="review-family-context" data-context-state={context.state}>
        {context.state}: {context.detail}
      </p>
      {composed ? (
        <p className={muted} data-testid="review-family-counts">
          {context.families_returned} of {context.families_total} recorded family context(s)
          composed
          {context.families_remaining
            ? ` · ${context.families_remaining} remaining`
            : ' · none remaining'}{' '}
          · {context.membership_rows_total} membership row(s) ·{' '}
          {context.unique_member_revision_total} distinct member revision(s)
        </p>
      ) : null}
      {context.limitations.map((limitation) => (
        <p key={limitation} className={muted} data-testid="review-family-limitation">
          limitation: {limitation}
        </p>
      ))}
    </details>
  );
}

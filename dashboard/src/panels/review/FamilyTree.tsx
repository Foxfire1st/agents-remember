import { useId, useRef } from 'react';
import type { ReviewTreesRead } from '../../data/reviewTrees';
import {
  CodeFactsScrollAnchor,
  codeFacts,
  codeChangesCount,
  useCodeFactsNotice,
} from './codeFacts';
import { css, cx } from '../../../styled-system/css';
import type {
  ReviewEntry,
  ReviewFailure,
  ReviewOutline,
  ReviewFamilyContext,
  ReviewFamilyContextEntry,
  ReviewFamilyMember,
  ReviewFamilyRevisionContext,
  ReviewFamilyRosterPage,
  ReviewFamilySideName,
} from '../../data/review';
import { FAMILY_SIDES } from '../../data/review';
import type { ReviewSubject } from './ReviewNavigation';
import { OutlineChangeBadge, TriageControls } from './ChangeBadges';
import { MeaningBadge, meaningBadgeText } from './MeaningEffects';
import { outlineGroups, realChange, type OutlineRow } from './changeTriage';
import { useChangeTraversal } from './changeTraversal';
import { rowWording, wordingComparison } from './statementWording';
import { useTreeOrder } from './triageOrderPreference';
import { useTreeView, treeViewStore } from './triageViewPreference';

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
const current = css({
  background: 'color-mix(in oklab, var(--amber) 16%, transparent)',
  color: 'amber',
});
const titleStyle = css({
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
  minWidth: 0,
  flex: 1,
});
const list = css({ listStyle: 'none', margin: 0, padding: 0, minWidth: 0 });
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
      {side.side} roster ({page.state}): the read has returned {counts.primary_items_returned} of{' '}
      {counts.primary_items_total} item(s) of this family revision&apos;s recorded selection,{' '}
      {counts.primary_items_remaining} remaining. The selected family revision records{' '}
      {side.members_total} membership row(s); loaded context contains {side.members.length} of them
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

// The recorded-effects badge text (MIK-R43): the step labels in attempt order on a
// row whose change badge is added, removed or changed. The state `none` shows no
// badge; the badge joins the row's accessible name at the call site.
function rowMeaningBadge(row: OutlineRow): string | undefined {
  if (row.meaning?.state !== 'steps') return undefined;
  if (row.badge !== 'added' && row.badge !== 'removed' && row.badge !== 'changed') return undefined;
  return meaningBadgeText(row.meaning.labels);
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

// The row's badge facts for its attributes: the aggregate guard and the accessible
// name carrying the change badge and the recorded-effects badge.
function nodeBadges(row: OutlineRow, meaning: string | undefined) {
  const aggregate = /^\d+ changed$/.test(row.badge ?? '');
  return {
    primary: aggregate ? 'unchanged' : row.badge,
    label: [row.title, row.badge, meaning].filter(Boolean).join(' '),
  };
}

function OutlineNode({
  row,
  selected,
  keptFor,
  onSelect,
}: {
  row: OutlineRow;
  selected: boolean;
  keptFor?: string;
  onSelect: () => void;
}) {
  const description = useId();
  const family = row.kind === 'family';
  const familyId = family ? row.selectorId : row.familyId;
  const meaning = rowMeaningBadge(row);
  const badges = nodeBadges(row, meaning);
  const descriptionId = keptFor ? description : undefined;
  return (
    <>
      <button
        type="button"
        className={cx(node, selected && current)}
        data-testid={family ? 'review-family-open' : 'review-family-member-open'}
        data-tree-node={family ? 'family' : 'member'}
        data-family={familyId}
        data-revision={row.revisionId}
        data-subject-kind={row.kind}
        data-subject-id={row.selectorId}
        data-occurrence={JSON.stringify([row.kind, row.familyId, row.selectorId])}
        data-change-primary={badges.primary}
        data-meaning={meaning}
        aria-label={badges.label}
        aria-current={selected || undefined}
        aria-describedby={descriptionId}
        title={row.title}
        onClick={onSelect}
        onKeyDown={treeArrow}
      >
        <span className={titleStyle}>{row.title}</span>
        <OutlineChangeBadge badge={row.badge} />
        {meaning && row.meaning ? <MeaningBadge meaning={row.meaning} /> : null}
        <KeptTag keptFor={keptFor} description={description} />
      </button>
    </>
  );
}
function KeptTag({ keptFor, description }: { keptFor?: string; description: string }) {
  if (!keptFor) return null;
  return (
    <>
      <span data-testid="review-family-kept" title={`Kept; last read for ${keptFor}`}>
        kept
      </span>
      <span id={description} className={css({ srOnly: true })}>
        Kept; last read for {keptFor}.
      </span>
    </>
  );
}

interface FamilyTreeProps {
  walked?: WalkedTree;
  context: ReviewFamilyContext;
  selection: FamilySelection | null;
  query: string;
  onQuery: (next: string) => void;
  outline?: ReviewOutline;
  treeRead?: ReviewTreesRead | null;
  reviewComparison?: number;
  catalogueEntries?: ReviewEntry[];
  catalogueLoading?: boolean;
  catalogueProblem?: ReviewFailure;
  subject?: ReviewSubject;
  onSubjectSelect: (subject: ReviewSubject, context?: FamilySelection) => void;
  traversalEnabled?: boolean;
}
function rowSelected(
  row: OutlineRow,
  subject: ReviewSubject | undefined,
  selection: FamilySelection | null,
): boolean {
  if (subject) {
    if (subject.kind !== row.kind || subject.id !== row.selectorId) return false;
    return selectedFamily(row, selection);
  }
  if (!selection) return false;
  if (row.kind === 'family')
    return selection.familyId === row.selectorId && !selection.memberRevisionId;
  return selection.familyId === row.familyId && selection.memberRevisionId === row.revisionId;
}
function selectedFamily(row: OutlineRow, selection: FamilySelection | null): boolean {
  if (row.kind === 'family' || !selection || !row.familyId) return true;
  return selection.familyId === row.familyId;
}
function familyVisible(row: OutlineRow, props: FamilyTreeProps, all: boolean): boolean {
  if (all || realChange(row.badge) || rowSelected(row, props.subject, props.selection)) return true;
  if (props.walked?.kept.has(row.selectorId)) return true;
  return (props.walked?.entries ?? props.context.entries).some(
    (entry) => entry.family_id === row.selectorId,
  );
}
function shownGroup(
  group: import('./changeTriage').OutlineGroup,
  props: FamilyTreeProps,
  all: boolean,
) {
  const familyShown = group.family && familyVisible(group.family, props, all);
  const members = group.members.filter(
    (row) => all || realChange(row.badge) || rowSelected(row, props.subject, props.selection),
  );
  const needle = props.query.trim().toLowerCase();
  const familyMatch =
    group.family && `${group.family.id} ${group.family.title}`.toLowerCase().includes(needle);
  const matches =
    needle === '' || familyMatch
      ? members
      : members.filter((row) => `${row.id} ${row.title}`.toLowerCase().includes(needle));
  const show =
    needle === '' || familyMatch ? familyShown || matches.length > 0 : matches.length > 0;
  return {
    family: familyShown || matches.length > 0 ? group.family : undefined,
    members: matches,
    show,
  };
}
function treeSelection(row: OutlineRow): FamilySelection | undefined {
  if (row.kind === 'family') return { familyId: row.selectorId };
  if (!row.familyId) return undefined;
  return { familyId: row.familyId, memberRevisionId: row.revisionId };
}
function TreeRows({
  groups,
  props,
}: {
  groups: import('./changeTriage').OutlineGroup[];
  props: FamilyTreeProps;
}) {
  const select = (row: OutlineRow) => {
    const context = treeSelection(row);
    props.onSubjectSelect({ kind: row.kind, id: row.selectorId }, context);
  };
  const renderRow = (row: OutlineRow) => (
    <OutlineNode
      key={row.selectorId}
      row={row}
      selected={rowSelected(row, props.subject, props.selection)}
      keptFor={row.kind === 'family' ? props.walked?.kept.get(row.selectorId) : undefined}
      onSelect={() => select(row)}
    />
  );
  return (
    <>
      {groups.map((group, index) => (
        <li
          key={group.family?.selectorId ?? `unlisted:${index}`}
          data-testid={group.family ? 'review-family' : undefined}
          data-family={group.family?.selectorId}
          data-family-kept={keptAttribute(props.walked, group.family)}
        >
          {group.family ? (
            <>{renderRow(group.family)}</>
          ) : (
            <p className={muted}>Without a family</p>
          )}
          <ul className={cx(list, group.family && css({ paddingLeft: '0.8rem' }))}>
            {group.members.map((row) => (
              <li key={row.selectorId} data-testid="review-family-member">
                {renderRow(row)}
              </li>
            ))}
          </ul>
        </li>
      ))}
    </>
  );
}
function keptAttribute(
  walked: WalkedTree | undefined,
  row: OutlineRow | undefined,
): string | undefined {
  return row && walked?.kept.has(row.selectorId) ? 'true' : undefined;
}
function TreeFilter({
  query,
  onQuery,
  view,
  available,
}: Pick<FamilyTreeProps, 'query' | 'onQuery'> & { view: 'changed' | 'all'; available: boolean }) {
  return (
    <div className={css({ display: 'flex', gap: '0.4rem', alignItems: 'center', minWidth: 0 })}>
      <input
        aria-label="Filter families and invariants"
        data-testid="review-family-filter"
        type="search"
        placeholder="Find family or invariant"
        value={query}
        className={css({
          flex: 1,
          minWidth: 0,
          background: 'bg',
          color: 'ink',
          border: '1px solid var(--grid)',
          font: 'inherit',
        })}
        onChange={(event) => onQuery(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Escape') onQuery('');
        }}
      />
      <button
        type="button"
        data-testid="review-tree-view"
        aria-pressed={view === 'all'}
        disabled={!available}
        onClick={() => treeViewStore.getState().setView(view === 'all' ? 'changed' : 'all')}
      >
        View: {view === 'all' ? 'All' : 'Changed'}
      </button>
    </div>
  );
}
function groupHasChange(group: import('./changeTriage').OutlineGroup): boolean {
  return realChange(group.family?.badge) || group.members.some((row) => realChange(row.badge));
}
function treeRowsKey(groups: import('./changeTriage').OutlineGroup[]): string {
  return groups
    .flatMap((group) => [...(group.family ? [group.family] : []), ...group.members])
    .map((row) => `${row.familyId ?? ''}:${row.selectorId}:${row.badge ?? ''}`)
    .join(' ');
}
function useTreeRows(props: FamilyTreeProps) {
  const order = useTreeOrder();
  const preference = useTreeView();
  const admitted = !props.catalogueLoading && !props.catalogueProblem;
  const available = admitted && props.outline?.state === 'outlined';
  const view = props.catalogueLoading || available ? preference : 'all';
  const groups = admittedGroups(props, order);
  const shown = groups
    .map((group) => shownGroup(group, props, view === 'all'))
    .filter((group) => group.show);
  const noChange = available && view === 'changed' && !groups.some(groupHasChange);
  return { order, available, view, shown, noChange };
}
export function FamilyTree(props: FamilyTreeProps) {
  const { order, available, view, shown, noChange } = useTreeRows(props);
  const root = useRef<HTMLElement>(null);
  const traversal = useChangeTraversal(
    root,
    available && props.traversalEnabled !== false,
    `${order}:${view}:${treeRowsKey(shown)}`,
  );
  const codeCount = codeChangesCount(props.outline, props.treeRead, props.reviewComparison);
  const codeNotice = useCodeFactsNotice(codeCount, props.reviewComparison);
  const status = [props.walked?.notice, catalogueStatus(props), traversal.status]
    .filter(Boolean)
    .join(' ');
  return (
    <section ref={root} data-testid="review-family-tree" className={css({ minWidth: 0 })}>
      <TreeFilter query={props.query} onQuery={props.onQuery} view={view} available={available} />
      <TriageControls order={order} onMove={traversal.move} status={status} />
      <p className={muted} aria-live="polite" data-testid="review-code-facts-status">
        {codeNotice}
      </p>
      {noChange ? <p className={muted}>This leaf changed no recorded meaning.</p> : null}
      <ul
        className={list}
        data-testid="review-family-list"
        data-tree-order={order}
        data-tree-view={view}
      >
        <CodeFactsScrollAnchor root={root} count={codeCount} comparison={props.reviewComparison}>
          <TreeRows groups={shown} props={props} />
        </CodeFactsScrollAnchor>
      </ul>
      {props.query && shown.length === 0 ? (
        <p className={muted}>No rows match “{props.query}”.</p>
      ) : null}
    </section>
  );
}

function catalogueStatus(props: FamilyTreeProps): string | undefined {
  if (props.catalogueLoading) return 'Reading families and invariants for this comparison…';
  if (props.catalogueProblem) return 'The catalogue for this comparison is unavailable.';
  return undefined;
}

function admittedGroups(
  props: FamilyTreeProps,
  order: import('./triageOrderPreference').TreeOrder,
) {
  if (props.catalogueLoading || props.catalogueProblem) return [];
  return outlineGroups(
    props.outline,
    props.catalogueEntries ?? [],
    props.walked?.entries ?? props.context.entries,
    order,
    props.treeRead === undefined ? undefined : codeFacts(props.treeRead, props.reviewComparison),
  );
}

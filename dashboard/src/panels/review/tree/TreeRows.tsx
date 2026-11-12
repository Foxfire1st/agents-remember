// The tree's rows (MIK-R86 v2 rules 1-4; mockup app.js:202-227). Every row is one line and three
// cells: a caret, a name cut with an ellipsis, and one trailing cell — its reason, and, on a graded
// location, the explainer's grade chip beside it (MIK-R211 v1 rule 5). A family heading may also
// carry `kept`. Nothing else.
// The accessible name keeps the title and the change badge (INV-W09GQM), the tooltip the whole title
// (INV-PKFP2R).
import { useId } from 'react';

import { css, cx } from '../../../../styled-system/css';
import { blockKey } from '../blockIdentity';
import { OutlineChangeBadge } from '../ChangeBadges';
import {
  EFFECT_RANK,
  ROLE_LABEL,
  type FamilyHeading,
  type FoldedGroup,
  type LocationJudgment,
  type LocationNode,
  type SubjectNode,
} from '../changeSetOrder';
import { meaningBadgeText } from '../MeaningEffects';
import type { ReviewSelection } from '../reviewSelection';
import { treeArrow } from './treeKeys';
import * as styles from './treeStyles';

export type TreeGroupName = FoldedGroup;

const fileName = (path: string) => path.split('/').pop() ?? path;

// The kept tag's description belongs to the family row's button, which is what a reader focuses
// (the tag itself is not focusable).
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

// A subject's or family's reason: the change badge in its own words, or the recorded effect with the
// location count, or (Memory currentness) its currentness.
function Reason({ text, badge }: { text: string; badge?: string }) {
  if (!text) return <span className={styles.reason} />;
  if (badge !== undefined && text === badge) return <OutlineChangeBadge badge={badge as never} />;
  const [effect, ...rest] = text.split(' · ');
  const labels = effect.split(' → ');
  const rank = Math.min(...labels.map((label) => EFFECT_RANK[label] ?? 3));
  if (!rest.length) return <span className={styles.reason}>{text}</span>;
  return (
    <span className={styles.reason}>
      <span className={rank === 0 ? styles.hot : styles.effect}>{effect}</span> · {rest.join(' · ')}
    </span>
  );
}

// A reason that ends in line counts ("enforce · +12 −3" from changeSetOrder.ts `lineCounts`, a lane
// file's "+16 −4" from LaneSection.tsx's file facts) draws "+a" in mint and "−r" in alarm red
// (MIK-R89 v2 rule 1 as amended by 719ade09; mockup app.js:213 and :227). The signs and numbers stay
// in the text, so colour is not their only carrier. Any other reason is drawn as it is.
const COUNTS = /^(.*?)(\+\d+) (−\d+)$/;

export function CountsReason({ text }: { text: string }) {
  const counts = COUNTS.exec(text);
  if (!counts) return <span className={styles.reason}>{text}</span>;
  const [, lead, added, removed] = counts;
  return (
    <span className={styles.reason}>
      {lead}
      <span>
        <span className={styles.ins}>{added}</span> <span className={styles.del}>{removed}</span>
      </span>
    </span>
  );
}

export function FamilyRow({
  family,
  current,
  onSelect,
}: {
  family: FamilyHeading;
  current: boolean;
  onSelect: () => void;
}) {
  const { row } = family;
  const aggregate = /^\d+ changed$/.test(row.badge ?? '');
  const description = useId();
  return (
    <button
      type="button"
      className={styles.row}
      data-testid="review-family-open"
      data-tree-node="family"
      data-row-key={`f:${row.selectorId}`}
      data-family={row.selectorId}
      data-subject-kind="family"
      data-subject-id={row.selectorId}
      data-change-primary={aggregate ? 'unchanged' : row.badge}
      aria-current={current || undefined}
      aria-label={[row.title, row.badge, family.kept ? 'kept' : ''].filter(Boolean).join(' ')}
      aria-describedby={family.kept ? description : undefined}
      title={row.title}
      onClick={onSelect}
      onKeyDown={treeArrow}
    >
      <span className={styles.caret} aria-hidden="true">▾</span>
      <span className={styles.name}>{row.title}</span>
      <span className={css({ display: 'flex', gap: '0.35rem', alignItems: 'baseline' })}>
        <Reason text={family.reason} badge={row.badge} />
        <KeptTag keptFor={family.kept} description={description} />
      </span>
    </button>
  );
}

function subjectCaret(node: SubjectNode): string {
  if (!node.first || !(node.counts?.changed ?? node.changed.length)) return '';
  return node.locationsKnown ? '▾' : '▸';
}

// D576: the latest move changed this invariant and its detail has not been read again since.
const STALE_TITLE = 'Changed by the latest move; read again when opened';

export function StaleMark({ stale }: { stale?: boolean }) {
  if (!stale) return null;
  return (
    <span className={styles.stale} data-testid="review-stale-mark" title={STALE_TITLE}>
      ●<span className={css({ srOnly: true })}> changed by the move</span>
    </span>
  );
}

export function SubjectRow({
  node,
  nested,
  current,
  stale,
  onSelect,
}: {
  node: SubjectNode;
  nested: boolean;
  current: boolean;
  stale?: boolean;
  onSelect: () => void;
}) {
  const { row } = node;
  const meaning = row.meaning?.state === 'steps' ? meaningBadgeText(row.meaning.labels) : undefined;
  const summary = row.title.startsWith(row.id) ? row.title.slice(row.id.length).trim() : row.title;
  return (
    <button
      type="button"
      className={cx(styles.row, nested && styles.child)}
      data-testid="review-family-member-open"
      data-tree-node="member"
      data-row-key={`s:${node.key}`}
      data-family={row.familyId}
      data-subject-kind={row.kind}
      data-subject-id={row.selectorId}
      data-change-primary={row.badge}
      data-meaning={meaning}
      data-stale={stale ? 'true' : undefined}
      aria-current={current || undefined}
      aria-label={[row.title, row.badge, meaning, node.reason === row.badge ? '' : node.reason, stale ? 'changed by the move' : '']
        .filter(Boolean)
        .join(' ')}
      title={row.title}
      onClick={onSelect}
      onKeyDown={treeArrow}
    >
      <span className={styles.caret} aria-hidden="true">{subjectCaret(node)}</span>
      <span className={styles.name}>
        <StaleMark stale={stale} />
        <span className={styles.id}>{row.id}</span>
        {summary}
      </span>
      <Reason text={node.reason} badge={row.badge} />
    </button>
  );
}

const judgmentChip = css({
  fontFamily: 'mono',
  fontSize: '0.66rem',
  paddingInline: '0.32rem',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'purple',
  borderRadius: '2px',
  color: 'purple',
  whiteSpace: 'nowrap',
  flexShrink: 0,
  '&[data-stale="true"]': {
    borderStyle: 'dashed',
    borderColor: 'amber',
    color: 'amber',
  },
});

export function JudgmentChip({ judgment }: { judgment?: LocationJudgment }) {
  if (!judgment) return null;
  return (
    <span
      className={judgmentChip}
      data-testid="review-judgment-chip"
      data-stale={judgment.stale ? 'true' : undefined}
      data-category={judgment.activeCategory ?? undefined}
      data-grade={judgment.activeGrade ?? undefined}
      title={judgment.hoverText}
    >
      {judgment.chipLabel}
    </span>
  );
}

// A location row's trailing cell: its reason, and — when the block is graded — the explainer's grade
// chip beside it. One cell either way, so a graded row keeps the row's three grid cells and changes no
// other row's columns (MIK-R211 v1 rule 5).
function LocationTail({ judgment, text }: { judgment?: LocationJudgment; text: string }) {
  const reason = <CountsReason text={text} />;
  if (!judgment) return reason;
  return (
    <span className={styles.reasonCell}>
      <JudgmentChip judgment={judgment} />
      {reason}
    </span>
  );
}

function UngradedHeading({ count, nested }: { count?: number; nested: boolean }) {
  if (!count || count <= 0) return null;
  return (
    <div
      className={cx(styles.row, styles.groupRow, nested ? styles.grandchild : styles.child)}
      data-testid="review-tree-ungraded-group"
      data-count={count}
    >
      <span className={styles.caret} aria-hidden="true">▸</span>
      <span className={styles.name}>{`Ungraded (${count})`}</span>
      <span className={styles.reason} />
    </div>
  );
}

function locationTooltip(node: LocationNode): string {
  const base = `${node.path} ${node.range}${node.symbol ? ` · ${node.symbol}` : ''}`;
  return node.judgment ? `${base}\n${node.judgment.hoverText}` : base;
}

function locationAriaLabel(node: LocationNode, label: string): string {
  const roleWord = node.role === 'unclassified' ? 'unclassified' : node.role;
  return `${label} ${node.path} ${node.range} ${roleWord} ${node.reason}`;
}

export function LocationRow({
  node,
  nested,
  current,
  onSelect,
}: {
  node: LocationNode;
  nested: boolean;
  current: boolean;
  onSelect: () => void;
}) {
  const label = node.symbol ?? fileName(node.path);
  const detail = node.symbol ? fileName(node.path) : node.range;
  const rowSpace = nested ? styles.grandchild : styles.child;
  return (
    <>
      <UngradedHeading count={node.ungradedGroupCount} nested={nested} />
      <button
        type="button"
        className={cx(styles.row, rowSpace)}
        data-testid="review-tree-location"
        data-tree-node="location"
        data-row-key={`b:${node.key}`}
        data-entry={node.entry.id}
        data-entries={node.card.entries.map((entry) => entry.id).join(',')}
        data-change={node.change}
        data-graded={node.judgment ? 'true' : undefined}
        data-stale-grade={node.judgment?.stale ? 'true' : undefined}
        aria-current={current || undefined}
        aria-label={locationAriaLabel(node, label)}
        title={locationTooltip(node)}
        onClick={onSelect}
        onKeyDown={treeArrow}
      >
        <span className={styles.caret} aria-hidden="true" />
        <span className={styles.name}>
          {node.role === 'proof' ? <span className={styles.proof} aria-hidden="true">✓</span> : null}
          {label}
          <span className={styles.dim}>{detail}</span>
        </span>
        <LocationTail judgment={node.judgment} text={node.reason || ROLE_LABEL[node.role]} />
      </button>
    </>
  );
}

// The two closed group rows of a subject (MIK-R89 v2 rule 5): each opens its group in the reading
// column and scrolls the column there. It expands nothing in the tree. Drawn only above 0 (W13), and
// while filtering only for a group that holds a match (changeSetOrder.ts `filterTree`).
export function GroupRows({
  node,
  groups,
  nested,
  onOpenGroup,
}: {
  node: SubjectNode;
  groups: readonly TreeGroupName[];
  nested: boolean;
  onOpenGroup: (node: SubjectNode, group: TreeGroupName) => void;
}) {
  const rows: [TreeGroupName, number, string][] = [
    ['unchanged', node.counts?.unchanged ?? node.unchanged.length, 'unchanged'],
    ['undetermined', node.counts?.not_comparable ?? node.notComparable.length, 'not comparable'],
  ];
  return (
    <>
      {rows
        .filter(([group, count]) => count > 0 && groups.includes(group))
        .map(([group, count, words]) => (
          <li key={group}>
            <button
              type="button"
              className={cx(styles.row, styles.groupRow, nested ? styles.grandchild : styles.child)}
              data-testid="review-tree-group"
              data-tree-node="group"
              data-group={group}
              aria-label={`Open the ${count} ${words} locations of ${node.row.id} in the reading column`}
              onClick={() => onOpenGroup(node, group)}
              onKeyDown={treeArrow}
            >
              <span className={styles.caret} aria-hidden="true">▸</span>
              <span className={styles.name}>
                {count} {words}
              </span>
              <span className={styles.reason} />
            </button>
          </li>
        ))}
    </>
  );
}

// Which row the selection names (aria-current). A block names the row of the location that draws it
// (changeSetOrder.ts `draws`); a subject its occurrence under the family it names, or its first; a
// family its heading.
export function isCurrentLocation(selection: ReviewSelection | null, node: LocationNode): boolean {
  return selection?.kind === 'block' && selection.block.kind === 'entry' && node.draws.includes(blockKey(selection.block));
}

export function isCurrentSubject(selection: ReviewSelection | null, node: SubjectNode): boolean {
  if (selection?.kind !== 'subject' || selection.subject.kind !== 'invariant') return false;
  if (selection.subject.id !== node.row.selectorId) return false;
  return selection.family === undefined ? node.first : selection.family === node.row.familyId;
}

export function isCurrentFamily(selection: ReviewSelection | null, family: FamilyHeading): boolean {
  return (
    selection?.kind === 'subject' &&
    selection.subject.kind === 'family' &&
    selection.subject.id === family.row.selectorId
  );
}

// The change-kind badges, family breakdown and triage controls of the family tree (MIK-R33, adopting
// ICR-R32). Every value rendered here is the server's fact or a count of them (changeTriage.ts);
// styling uses the existing dashboard tokens, and the words carry the meaning without colour.

import { useId } from 'react';

import { css, cx } from '../../../styled-system/css';
import { ariaKeyshortcuts, bindingFor, useEffectiveKeymap } from '../../data/keymap/preferences';
import type {
  ReviewChangeKind,
  ReviewChangeMark,
  ReviewFamilyChanges,
  ReviewMemberChange,
} from '../../data/reviewFamily';
import { CHANGE_LABEL, CHANGE_MEANING, type FamilyTriage, breakdownText } from './changeTriage';
import type { TraversalDirection } from './changeTraversal';
import type { MarkTarget } from './hunkMarkers';
import { ATTRIBUTION_UNKNOWN, useMemberTarget } from './MarkerTargetState';
import { type TreeOrder, treeOrderStore } from './triageOrderPreference';

const pill = css({
  display: 'inline-block',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderRadius: '2px',
  padding: '0 0.35rem',
  fontSize: '0.68rem',
  letterSpacing: '0.04em',
  lineHeight: '1.5',
  whiteSpace: 'nowrap',
});

const KIND_STYLE: Record<ReviewChangeKind, string> = {
  intent: css({ color: 'alarm', borderColor: 'alarm' }),
  implementation: css({ color: 'cyan', borderColor: 'cyan' }),
  membership: css({ color: 'purple', borderColor: 'purple' }),
  unknown: css({ color: 'gold', borderColor: 'gold', borderStyle: 'dashed' }),
  unchanged: css({ color: 'muted', borderColor: 'grid' }),
};

const markStyle = css({ color: 'muted', fontSize: '0.66rem', whiteSpace: 'nowrap' });
// The followed marker's tag on the membership line (MIK-R34's per-member state, stated once).
const targetTag = css({
  border: '1px dashed currentColor',
  borderRadius: '2px',
  padding: '0 0.35rem',
  whiteSpace: 'nowrap',
});
const badgeRow = css({
  display: 'flex',
  flexWrap: 'wrap',
  alignItems: 'center',
  gap: '0.3rem',
  marginTop: '0.25rem',
});
const muted = css({ color: 'muted', fontSize: '0.75rem', margin: '0.15rem 0' });
// Why an unknown is unknown, in view beside its badge (not only in a tooltip).
const why = css({
  display: 'block',
  color: 'gold',
  fontSize: '0.7rem',
  lineHeight: '1.4',
  marginTop: '0.2rem',
  overflowWrap: 'anywhere',
});
// The traversal controls and their status stay in view while the tree scrolls, so an end or a
// partial family is announced where the reader is looking (the rail, or the page when stacked).
// Sticky below the workspace's other sticky control, a followed marker's way back, while it is open
// in the stacked layout (`--review-sticky-top`, set by ReviewWorkspace); else at the top.
const triageBar = css({
  position: 'sticky',
  top: 'var(--review-sticky-top, 0)',
  zIndex: 1,
  background: 'bgPanel',
  borderBottomWidth: '1px',
  borderBottomStyle: 'solid',
  borderBottomColor: 'grid',
  // Padding on both edges keeps the children's margins inside the bar's own background.
  paddingBlock: '0.2rem',
  // Stuck under a scroll container's padding (the stacked phone layout), the bar's panel-coloured
  // shadow covers that strip; in flow it fills the bar's own top margin, so it covers nothing.
  marginTop: '0.75rem',
  boxShadow: '0 -0.75rem 0 var(--bg-panel)',
});
const controls = css({
  display: 'flex',
  flexWrap: 'wrap',
  gap: '0.4rem',
  alignItems: 'center',
  margin: '0.4rem 0',
});

function titleOf(kind: ReviewChangeKind, evidence: readonly string[]): string {
  return [`${CHANGE_LABEL[kind]}: ${CHANGE_MEANING[kind]}`, ...evidence].join('\n');
}

function Marks({ marks }: { marks: readonly ReviewChangeMark[] }) {
  return (
    <>
      {marks.map((mark) => (
        <span key={mark} className={markStyle} data-testid="review-change-mark" data-mark={mark}>
          {mark === 'text_differs' ? CHANGE_LABEL[mark] : `+${CHANGE_LABEL[mark]}`}
        </span>
      ))}
    </>
  );
}

// "first reason (and N more)", every reason in the tooltip.
function firstOf(reasons: readonly string[]): string {
  const more = reasons.length - 1;
  return `${reasons[0]}${more > 0 ? ` (and ${more} more)` : ''}`;
}

// Why an unknown is unknown, as the server named it (the lane's per-entry reason, or the side that
// could not be read), labelled with what is unknown: the change kind, the membership, the guarantee.
// A member node names only its subject and is described by these lines (review R3-1).
function UnknownReason({
  label,
  reasons,
  id,
}: {
  label: string;
  reasons: readonly string[];
  id?: string;
}) {
  if (!reasons.length) return null;
  return (
    <span id={id} className={why} data-testid="review-change-why" title={reasons.join('\n')}>
      {label}: {firstOf(reasons)}
    </span>
  );
}

// A returned member the delivered facts do not describe: unknown, and saying so.
const UNDESCRIBED_CHANGE: ReviewMemberChange = {
  member_id: '',
  intent: 'unknown',
  implementation: 'unknown',
  membership: 'unknown',
  proof: false,
  text_differs: false,
  range_unresolved: false,
  primary: 'unknown',
  marks: [],
  evidence: [],
  unknown_reasons: ['no change facts were delivered for this member'],
  membership_reasons: [],
};

// One member row's change statement: its facts, and -- when a followed intent marker targets this
// row with an unknown membership (MIK-R34) -- that target, which becomes a tag on the membership line
// instead of a second statement. `describedBy` orders the node's accessible description whatever the
// drawn order: the change-kind fact (the badge, then why the kind is unknown), then the membership.
export interface MemberChangeState {
  change: ReviewMemberChange;
  target: MarkTarget | null;
  ids: { kind: string; membership: string; reasons: string };
  describedBy: string;
}

export function useMemberChange(
  facts: Map<string, ReviewMemberChange> | undefined,
  memberId: string,
  familyId: string,
  memberRevisionId: string,
): MemberChangeState | undefined {
  const base = useId();
  const target = useMemberTarget(familyId, memberRevisionId);
  if (!facts) return undefined;
  const change = facts.get(memberId) ?? UNDESCRIBED_CHANGE;
  const ids = {
    kind: `${base}-kind`,
    membership: `${base}-membership`,
    reasons: `${base}-reasons`,
  };
  const membership =
    target !== null || (change.membership === 'unknown' && change.membership_reasons.length > 0);
  const reasons = change.unknown_reasons.length > 0;
  return {
    change,
    target,
    ids,
    describedBy: [ids.kind, reasons && ids.reasons, membership && ids.membership]
      .filter(Boolean)
      .join(' '),
  };
}

// The membership line: why the membership is unknown, carrying the followed marker's `Attribution
// unknown` tag when a marker opened this row (drawn before the change-kind reason then). Should the
// tree comparison know a membership the marker's classification could not confirm, the line names the
// marker's reason as its own, and the badge keeps the comparison's fact: both are named, neither
// replaced.
function membershipLine(
  change: ReviewMemberChange,
  target: MarkTarget | null,
): { text: string; title?: string } | null {
  if (change.membership === 'unknown' && change.membership_reasons.length > 0)
    return {
      text: `membership unknown: ${firstOf(change.membership_reasons)}`,
      title: change.membership_reasons.join('\n'),
    };
  if (!target) return null;
  const reason = target.reason ?? 'membership not established';
  return { text: `the marker's classification: ${reason}`, title: reason };
}

function MembershipReason({ state }: { state: MemberChangeState }) {
  const { change, target, ids } = state;
  const line = membershipLine(change, target);
  if (!line) return null;
  return (
    <span
      id={ids.membership}
      className={why}
      data-testid="review-change-membership-why"
      data-member-state={target ? 'membership_unknown' : undefined}
      title={line.title}
    >
      {target ? (
        <>
          <span className={targetTag} data-testid="review-member-target-tag">
            {ATTRIBUTION_UNKNOWN}
          </span>{' '}
          opened from an intent marker ·{' '}
        </>
      ) : null}
      {line.text}
    </span>
  );
}

// One member occurrence's primary badge, its secondary marks, and why any unknown is unknown --
// nothing without change facts (a dataset review). A returned member the facts do not describe reads
// `unknown`.
export function MemberChangeBadge({ state }: { state: MemberChangeState | undefined }) {
  if (!state) return null;
  const { change, target, ids } = state;
  const kind = change.primary;
  const reasons = (
    <UnknownReason id={ids.reasons} label="change kind unknown" reasons={change.unknown_reasons} />
  );
  return (
    <>
      <span
        id={ids.kind}
        className={badgeRow}
        data-testid="review-change-badge"
        data-change-kind={kind}
        data-change-marks={change.marks.join(' ')}
        title={titleOf(kind, [...change.evidence, ...change.unknown_reasons])}
      >
        <span className={cx(pill, KIND_STYLE[kind])}>{CHANGE_LABEL[kind]}</span>
        <Marks marks={change.marks} />
      </span>
      {target ? (
        <>
          <MembershipReason state={state} />
          {reasons}
        </>
      ) : (
        <>
          {reasons}
          <MembershipReason state={state} />
        </>
      )}
    </>
  );
}

// The family's own row badge: the fact of its independently authored guarantee.
export function GuaranteeChangeBadge({ kinds }: { kinds: ReviewFamilyChanges | undefined }) {
  if (!kinds) return null;
  const kind: ReviewChangeKind = kinds.guarantee;
  return (
    <>
      <span
        className={badgeRow}
        data-testid="review-guarantee-change"
        data-change-kind={kind}
        title={[kinds.guarantee_detail, kinds.detail].filter(Boolean).join('\n')}
      >
        <span className={markStyle}>guarantee</span>
        <span className={cx(pill, KIND_STYLE[kind])}>{CHANGE_LABEL[kind]}</span>
      </span>
      <UnknownReason
        label="guarantee unknown"
        reasons={kind === 'unknown' ? [kinds.guarantee_detail] : []}
      />
    </>
  );
}

// The family row's breakdown of its member occurrences; never labelled as the entry's intent count.
export function FamilyBreakdown({ triage }: { triage: FamilyTriage | undefined }) {
  if (!triage) return null;
  return (
    <p
      className={muted}
      data-testid="review-family-breakdown"
      data-partial={triage.partial ? 'true' : 'false'}
    >
      Members by change: {breakdownText(triage)}
    </p>
  );
}

// The order choice, the visible next/previous controls and the polite traversal status. The key
// labels are the keymap owner's effective bindings, so a rebinding shows here too.
export function TriageControls({
  order,
  onMove,
  status,
}: {
  order: TreeOrder;
  onMove: (direction: TraversalDirection) => void;
  status: string;
}) {
  const keymap = useEffectiveKeymap();
  const next = bindingFor(keymap, 'review.nextChange');
  const previous = bindingFor(keymap, 'review.previousChange');
  return (
    <div className={triageBar} data-testid="review-triage-bar">
      <div className={controls} data-testid="review-triage-controls">
        <button
          type="button"
          data-testid="review-tree-order"
          data-order={order}
          title="Switch between changes first and the families' authored order"
          onClick={() =>
            treeOrderStore.getState().setOrder(order === 'authored' ? 'triage' : 'authored')
          }
        >
          {order === 'authored' ? 'Order: authored' : 'Order: changes first'}
        </button>
        <button
          type="button"
          data-testid="review-previous-change"
          aria-keyshortcuts={previous ? ariaKeyshortcuts(previous.chord) : undefined}
          onClick={() => onMove(-1)}
        >
          ↑ Previous change{previous ? ` (${previous.label})` : ''}
        </button>
        <button
          type="button"
          data-testid="review-next-change"
          aria-keyshortcuts={next ? ariaKeyshortcuts(next.chord) : undefined}
          onClick={() => onMove(1)}
        >
          ↓ Next change{next ? ` (${next.label})` : ''}
        </button>
      </div>
      <p className={muted} role="status" aria-live="polite" data-testid="review-change-status">
        {status}
      </p>
    </div>
  );
}

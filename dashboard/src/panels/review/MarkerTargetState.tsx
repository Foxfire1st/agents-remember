// The selection a followed intent marker opened when its membership is unknown (MIK-R34 rule 2, with
// ICR-R24@v3's per-member `Attribution unknown` state): `Attribution unknown` and its reason, shown at
// the target -- on the member's row when the family is named and in the tree, otherwise on the
// invariant view -- and never the `No recorded family` that a confirmed absence reads.
//
// The review's own family context is kept beside it, never replaced silently: where the review states
// a measured zero the marker's classification could not confirm, both are named.
import { useContext, useId } from 'react';

import { css } from '../../../styled-system/css';
import type {
  ReviewFamilyContext,
  ReviewFamilyContextEntry,
  ReviewPayload,
} from '../../data/review';
import type { MarkTarget } from './hunkMarkers';
import { IntentMarkerScope } from './intentMarkerScope';

export const ATTRIBUTION_UNKNOWN = 'Attribution unknown';

const tag = css({
  border: '1px dashed currentColor',
  borderRadius: '2px',
  padding: '0 0.35rem',
  marginRight: '0.4rem',
  fontSize: '0.7rem',
  whiteSpace: 'nowrap',
});
const rowNote = css({ display: 'block', color: 'amber', fontSize: '0.74rem', marginTop: '0.3rem' });
const centerNote = css({ color: 'amber' });
const shell = css({
  background: 'bgPanel',
  border: '1px dashed var(--amber)',
  borderRadius: '3px',
  padding: '1rem',
  minWidth: 0,
});
const title = css({
  color: 'amber',
  fontSize: '0.75rem',
  letterSpacing: '0.1em',
  textTransform: 'uppercase',
  margin: '0 0 0.6rem',
});
const muted = css({ color: 'muted', fontSize: '0.75rem', margin: '0.35rem 0' });

interface Subject {
  kind: string;
  id: string;
}

function unknownTarget(target: MarkTarget | null | undefined): MarkTarget | null {
  return target?.state === 'membership_unknown' ? target : null;
}

// The followed marker's unknown membership when it targets this member row of the named family.
export function useMemberTarget(familyId: string, memberRevisionId: string): MarkTarget | null {
  const target = unknownTarget(useContext(IntentMarkerScope)?.target);
  if (
    !target?.familyKey ||
    target.familyKey !== familyId ||
    target.memberRevisionKey !== memberRevisionId
  )
    return null;
  return target;
}

// On the member's row of the named family: the tree's own node for the target. A tree comparison's
// row states it once, through its change badge (MIK-R33, `ChangeBadges.tsx`); this note serves a row
// without change facts.
export function MemberTargetNote({
  id,
  familyId,
  memberRevisionId,
}: {
  // The row describes itself by this note (its accessible description, review MIK-L33 R3-1).
  id?: string;
  familyId: string;
  memberRevisionId: string;
}) {
  const target = useMemberTarget(familyId, memberRevisionId);
  if (!target) return null;
  return (
    <span
      id={id}
      className={rowNote}
      data-testid="review-member-target-state"
      data-member-state="membership_unknown"
    >
      <span className={tag}>{ATTRIBUTION_UNKNOWN}</span> {target.reason}
    </span>
  );
}

const familyIdsOf = (context?: ReviewFamilyContext) =>
  (context?.entries ?? []).map((entry) => entry.family_id);

// The unknown membership to show on the invariant view: the target invariant is the subject, and its
// membership has no row in this tree (no family is named, or the named family is not in the review's
// family context).
export function useInvariantTargetState(
  subject: Subject | undefined,
  context: ReviewFamilyContext | undefined,
): MarkTarget | null {
  const target = unknownTarget(useContext(IntentMarkerScope)?.target);
  if (!target || subject?.kind !== 'invariant' || subject.id !== target.invariantKey) return null;
  return target.familyKey && familyIdsOf(context).includes(target.familyKey) ? null : target;
}

function reasonText(target: MarkTarget): string {
  return `${target.invariant}${target.family ? ` in ${target.family}` : ''}: ${target.reason}`;
}

// The rail's statement of the target's unknown membership: in place of the review's `No recorded
// family` or `Attribution unknown` statement when the review composed no tree (whose own words stay one
// click away), or above the tree it composed. It is what a follow focuses: its accessible name is the
// state, and its description the reason (review R1 F3).
export function InvariantTargetState({
  target,
  context,
}: {
  target: MarkTarget;
  context?: ReviewFamilyContext;
}) {
  const id = useId();
  return (
    <section
      className={shell}
      data-testid="review-rail-target-state"
      data-member-state="membership_unknown"
      data-family-state={context?.state}
      data-target-state-focus
      tabIndex={-1}
      aria-labelledby={`${id}-state`}
      aria-describedby={`${id}-reason`}
    >
      <h2 className={title} id={`${id}-state`}>
        {ATTRIBUTION_UNKNOWN}
      </h2>
      <p className={muted} id={`${id}-reason`}>
        {reasonText(target)}.
      </p>
      <p className={muted}>
        Opened from an intent marker: this membership is not established, which is not a confirmed
        absence of a family.
      </p>
      {context ? (
        <details>
          <summary>The review&apos;s family context</summary>
          <p className={muted}>
            {context.state}: {context.detail}
          </p>
        </details>
      ) : null}
    </section>
  );
}

// The member review's family line, as the review states it (moved here from FamilyReviewCenter).
function memberContextLabel(
  entry: ReviewFamilyContextEntry | undefined,
  payload: ReviewPayload,
): string {
  if (entry) return `${entry.display_label ?? entry.family_id} · Member review`;
  return payload.family_context?.state === 'no_family_recorded'
    ? 'No recorded family'
    : 'Family context unavailable';
}

// The invariant view's family line: the review's own label, or -- for the target of an unknown
// membership -- `Attribution unknown` with its reason, followed by what the review's context reads.
export function MemberFamilyLabel({
  subject,
  payload,
  entry,
}: {
  subject: Subject | undefined;
  payload: ReviewPayload;
  entry: ReviewFamilyContextEntry | undefined;
}) {
  const label = memberContextLabel(entry, payload);
  const target = useInvariantTargetState(subject, payload.family_context);
  if (!target) return <>{label}</>;
  return (
    <span
      className={centerNote}
      data-testid="review-center-target-state"
      data-member-state="membership_unknown"
    >
      <span className={tag}>{ATTRIBUTION_UNKNOWN}</span>
      {reasonText(target)}. The review&apos;s family context reads: {label}.
    </span>
  );
}

// "Technical details · raw records of comparison #n" (MIK-R89 v2 rule 6; mockup `technical`): one
// closed provenance disclosure at the bottom of the reading column. It holds the raw records that
// once opened the page (the movement JSON, UX F5), the comparison's identities, the task and subject
// identifiers, the selected subject's provenance (who introduced its record, its revision ids, the
// hand-off files and entries, the evidence sentences and the family context's full sentences,
// amendment 9dde2b09), the selected answer's record identities and one entry-identifier list for the
// cards on the page (MIK-R86 rule 19's provenance class). Nothing a reader needs is only here: the
// chip's popover carries the states, each subject's meta line names an abnormal family read by a
// chip, and its slot carries its facts.
import { css } from '../../../../styled-system/css';
import type { ReviewFailure, ReviewOrigin, ReviewPayload, ReviewSelectorKind } from '../../../data/review';
import type { ReviewDeliveredComparison, ReviewTreeComparison, ReviewTreeEntry } from '../../../data/reviewTrees';
import { movementRecord } from '../ReviewMovement';
import { ReviewRecordIdentities } from '../ReviewRecordPanes';
import { DeliveredFacts, ScopeRecords, type SubjectReadState } from '../ReviewScopeHeader';
import { familyStateWords } from '../stateWords';

const details = css({
  marginBlock: '2.2rem 1rem',
  borderTopWidth: '1px',
  borderTopStyle: 'solid',
  borderTopColor: 'grid',
  paddingTop: '0.6rem',
  '& pre': {
    background: 'bgPanel',
    borderWidth: '1px',
    borderStyle: 'solid',
    borderColor: 'grid',
    borderRadius: '3px',
    padding: '0.7rem',
    fontSize: '0.72rem',
    lineHeight: '1.5',
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
    color: 'muted',
  },
  '& h4': { fontSize: '0.72rem', color: 'cyan', margin: '0.9rem 0 0.3rem', fontWeight: 600 },
});

export interface TechnicalDetailsProps {
  repo: string;
  master: string;
  leaf: string;
  comparison: ReviewTreeComparison | null;
  // The leaf's latest comparison: the task context's movement and limitation records are its own, so
  // under an older comparison they are labelled with this number, not with the one on screen.
  latest?: number;
  // The pair the leaf delivered, when its enclosure records one (MIK-R195): whether the comparison
  // on screen is that pair, and the delivered identities when it is not.
  delivered?: ReviewDeliveredComparison | null;
  // The last admitted payload of the task context, and whether the selected subject's own read is
  // pending or unanswered (its identities then are not shown as that subject's).
  frame: ReviewPayload | null;
  shown: ReviewPayload | null;
  status: SubjectReadState;
  subject?: { kind: ReviewSelectorKind; id: string };
  instead: ReviewFailure | null;
  entries: readonly ReviewTreeEntry[];
  paging?: React.ReactNode;
}

function subjectLabel(subject: TechnicalDetailsProps['subject'], instead: ReviewFailure | null): string {
  if (instead === null && subject) return `${subject.kind} ${subject.id}`;
  return 'whole task (no subject selected)';
}

function EntryIdentifiers({ entries }: { entries: readonly ReviewTreeEntry[] }) {
  return (
    <section data-testid="review-entry-identifiers">
      <h4>Entry identifiers</h4>
      <ul>
        {entries.map((entry) => (
          <li key={entry.id} data-entry={entry.id}>
            {entry.kind} {entry.id} · {entry.invariant} · {entry.invariant_key}
            <ul>
              {(['before', 'after'] as const).map((side) => (
                <li key={side} data-side={side}>
                  {side}
                  {entry[side].blob ? ` · blob ${entry[side].blob}` : ''}
                  {entry[side].content ? ` · content ${entry[side].content}` : ''}
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
    </section>
  );
}

// The record's `origin`: the leaf (or task and wave) that introduced it, which no revision updates. It
// is named "introduced by", never as the writer of a revision (9dde2b09).
function introducedBy(origin: ReviewOrigin): string {
  const parts = [`introduced by ${origin.leaf ?? `task ${origin.task}`}`];
  if (origin.leaf) parts.push(`task ${origin.task}`);
  if (origin.wave) parts.push(`wave ${origin.wave}`);
  if (origin.handoffEntry) parts.push(`hand-off entry ${origin.handoffEntry}`);
  return parts.join(' · ');
}

function IntroducedBy({ side, origin }: { side?: 'before' | 'after'; origin: ReviewOrigin }) {
  return (
    <div data-testid={side ? `review-origin-${side}` : 'review-origin'}>
      <p>
        {side ? `${side} side: ` : ''}
        {introducedBy(origin)}
      </p>
      {origin.handoff?.path ? <p>Hand-off: {origin.handoff.path}</p> : null}
      {origin.handoff?.evidence?.map((text, index) => <p key={index}>{text}</p>)}
    </div>
  );
}

// One origin when both sides carry the same, else each side's own.
function Origins({ payload }: { payload: ReviewPayload }) {
  const { before_origin: before, after_origin: after } = payload.knowledge;
  if (before && after && JSON.stringify(before) === JSON.stringify(after)) return <IntroducedBy origin={after} />;
  return (
    <>
      {before ? <IntroducedBy side="before" origin={before} /> : null}
      {after ? <IntroducedBy side="after" origin={after} /> : null}
    </>
  );
}

// The selected subject's provenance: its record's origin, its revision ids, and its family context's
// own sentences, family by family. An abnormal family read is named outside, by the subject's meta
// line chip, so its full sentence stands here as provenance (9dde2b09).
function SubjectProvenance({ payload }: { payload: ReviewPayload }) {
  const selection = payload.knowledge.revision_selection;
  const context = payload.family_context;
  return (
    <section data-testid="review-subject-provenance">
      <h4>Selected subject&apos;s provenance</h4>
      <Origins payload={payload} />
      {selection ? (
        <p data-testid="review-center-member-revisions">
          {selection.record_kind} revision {selection.before_revision_id ?? 'none'} → {selection.after_revision_id ?? 'none'}
        </p>
      ) : null}
      {context ? (
        <p data-testid="review-family-context-detail" data-family-state={context.state}>
          {context.families_returned} of {context.families_total} family contexts read · {familyStateWords(context.state)}:{' '}
          {context.detail}
          {context.limitations.length ? ` · ${context.limitations.join('; ')}` : ''}
        </p>
      ) : (
        <p data-testid="review-family-context-detail">Family context was not supplied.</p>
      )}
      {context?.entries.map((entry) => (
        <div key={entry.family_id} data-family={entry.family_id}>
          <p data-testid="review-center-family-state" data-family-state={entry.state}>
            {entry.display_label ?? entry.family_id} · {entry.state}: {entry.detail}
          </p>
          <p data-testid="review-center-family-selection">
            {entry.selection.state}: {entry.selection.statement}
          </p>
          <p>
            family revision {entry.before.family_revision_id ?? 'none'} → {entry.after.family_revision_id ?? 'none'}
          </p>
        </div>
      ))}
    </section>
  );
}

// Whose movement and limitation records these are: the task context's, of the latest comparison.
function recordsOf(comparison: ReviewTreeComparison | null, latest: number | undefined): string | null {
  if (latest === undefined || comparison === null || latest === comparison.number) return null;
  return `comparison #${latest} (the task context's; #${comparison.number} is shown read-only)`;
}

// The raw record: the comparison on screen, then the task context's movement and limitations, named
// as the latest comparison's when an older one is on screen.
function rawRecord(comparison: ReviewTreeComparison | null, frame: ReviewPayload | null, foreign: string | null) {
  return {
    comparison: comparison ?? 'not read',
    ...(foreign ? { movement_and_limitations_of: foreign } : {}),
    review_comparison: frame?.comparison ?? 'not supplied',
    ...movementRecord(frame),
    limitations: frame?.limitations ?? [],
  };
}

export function TechnicalDetails(props: TechnicalDetailsProps) {
  const { comparison, frame, shown, status } = props;
  const foreign = recordsOf(comparison, props.latest);
  const record = rawRecord(comparison, frame, foreign);
  // The paging controls are the reader's controls, so they stand above the closed disclosure, which
  // holds identifiers and provenance only (INV-P4Z520).
  return (
    <>
      {props.paging}
      <details className={details} data-disclosure="provenance" data-testid="review-details">
        <summary>
          Technical details · raw records of comparison #{comparison?.number ?? '…'}
          {foreign ? ` · movement and limitations of #${props.latest}` : ''}
        </summary>
        <pre data-testid="review-movement-facts">{JSON.stringify(record, null, 2)}</pre>
        <h4>Task and subject identifiers</h4>
        <p data-testid="review-subject">
          {props.repo} · {props.master} · {props.leaf} · {subjectLabel(props.subject, props.instead)}
        </p>
        {frame ? (
          <ScopeRecords payload={frame} repo={props.repo} master={props.master} leaf={props.leaf} status={status} />
        ) : null}
        <DeliveredFacts delivered={props.delivered} />
        {shown && status === null && props.subject && props.instead === null ? <SubjectProvenance payload={shown} /> : null}
        {shown ? <ReviewRecordIdentities payload={shown} /> : null}
        <EntryIdentifiers entries={props.entries} />
      </details>
    </>
  );
}

// Technical record identities stay compact. Authored content and reasons are visible in the page.
import type { ReviewPayload, ReviewUnresolvedReference } from '../../data/review';
import type { LaneRead, ReviewUnexplainedLane } from '../../data/reviewLane';
import { laneAttributionFacts, laneCountText } from './laneFocus';

const paragraph = (text: string, testid?: string) => <p data-testid={testid}>{text}</p>;
const unresolved = (entries: ReviewUnresolvedReference[]) =>
  entries.length ? (
    <ul data-testid="review-unresolved" data-review-content="reason">
      {entries.map((entry, index) => (
        <li key={index}>
          unresolved {entry.field}
          {entry.recorded_reference ? ` (${entry.recorded_reference})` : ''}: {entry.detail}
        </li>
      ))}
    </ul>
  ) : null;
const authoredFields = new Set([
  'statement',
  'applicability',
  'conditions',
  'exclusions',
  'essential_conditions',
  'joint_guarantee',
]);

// A changed field's value as the technical details name it (ICR-L6): an unrecorded side is the
// named absence, a recorded empty string is named as recorded — neither ever prints as blank.
const fieldValue = (value?: string) =>
  value === undefined ? '(absent)' : value === '' ? '(recorded empty)' : value;

function RecordFacts({ payload }: { payload: ReviewPayload }) {
  const { knowledge, source, evidence } = payload;
  const fields = knowledge.field_changes.filter((change) => !authoredFields.has(change.field));
  const references = [
    ...knowledge.unresolved,
    ...source.unresolved,
    ...evidence.unresolved,
    ...evidence.evidence_links.flatMap((one) => one.unresolved),
  ];
  return (
    <>
      {' '}
      {fields.length ? (
        <ul data-testid="review-field-changes" data-review-content="changed-field">
          {fields.map((change) => (
            <li key={`${change.item_id}:${change.field}`}>
              {change.field}: {fieldValue(change.before_value)} → {fieldValue(change.after_value)}
            </li>
          ))}
        </ul>
      ) : null}
      {knowledge.signals.length ? (
        <div data-review-content="signal">
          <h3>Detection signals</h3>
          <ul>
            {knowledge.signals.map((signal) => (
              <li key={signal.signal_id}>
                {signal.condition} · {signal.scope_limitations.join('; ')}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {references.length ? (
        <div>
          <h3>Unresolved references</h3>
          {unresolved(references)}
        </div>
      ) : null}
    </>
  );
}
export function ReviewMainFacts({
  payload,
  laneRead,
  onAllChanges,
}: {
  payload: ReviewPayload;
  laneRead?: LaneRead<ReviewUnexplainedLane> | null;
  onAllChanges?: () => void;
}) {
  const { source, submission, staleness } = payload;
  const lane = laneRead ? laneAttributionFacts(laneRead) : null;
  return (
    <section data-testid="review-main-facts">
      <RecordFacts payload={payload} />
      {source.remaining.length ? (
        <p
          data-review-content="counts"
          data-testid="review-remaining"
          data-attribution-source={lane ? 'lane' : 'landed'}
        >
          {source.remaining
            .map(
              (count) =>
                (lane && laneCountText(count.name, lane)) ??
                `${count.name}: ${count.value ?? `not measured (${count.reason ?? 'no reason recorded'})`}`,
            )
            .join(' · ')}
        </p>
      ) : null}
      {staleness.state !== 'current'
        ? paragraph(
            staleness.statement,
            staleness.state === 'stale' ? 'review-stale' : 'review-staleness-unmeasured',
          )
        : null}
      <div data-review-content="submission-state">
        <p data-submission-state={submission.state}>
          assessment submission:{' '}
          {submission.state === 'disabled_stale' ? 'DISABLED' : 'not offered'} — {submission.reason}
        </p>
        <p>next: {submission.next_action}</p>
      </div>
      {onAllChanges ? (
        <button type="button" onClick={onAllChanges}>
          All changes
        </button>
      ) : null}
    </section>
  );
}

export function ReviewTechnicalDetails({
  payload,
  unanswered,
  paging,
}: {
  payload: ReviewPayload | null;
  unanswered: { label: string; unavailable: boolean };
  paging: React.ReactNode;
  laneRead?: LaneRead<ReviewUnexplainedLane> | null;
}) {
  return (
    <section>
      {payload === null ? (
        <p
          data-review-content="read-state"
          data-testid={
            unanswered.unavailable ? 'review-details-unavailable' : 'review-details-pending'
          }
        >
          {unanswered.unavailable
            ? `No records are shown: ${unanswered.label} could not be read.`
            : `Reading the records of ${unanswered.label}…`}
        </p>
      ) : null}
      {paging}
      <details
        data-disclosure="provenance"
        data-testid="review-details"
        style={{ marginTop: '1rem' }}
      >
        <summary>Technical details · records, paging and submission contract</summary>
        {payload ? (
          <>
            <p data-testid="review-selection">
              {payload.comparison
                ? `comparison: ${payload.comparison.reference} · policy ${payload.comparison.policy_version}`
                : 'No comparison identifier'}
            </p>
            <p data-testid="review-revision-groups">
              retained revisions:{' '}
              {payload.knowledge.revision_groups
                .map((group) => `${group.side}:${group.record_id}=${group.selected_revision_count}`)
                .join(' · ') || 'none selected'}
            </p>
            <p data-testid="review-expansion">
              {payload.source.expansion_reference} {payload.source.expansion_command}
            </p>
            <p>
              Evidence records:{' '}
              {payload.evidence.evidence_links.map((link) => link.claim_id).join(', ') || 'none'};
              observation records:{' '}
              {payload.evidence.observations.map((one) => one.observation_id).join(', ') || 'none'};
              assessment records:{' '}
              {[...payload.knowledge.assessments, ...payload.evidence.assessments]
                .map((one) => one.assessment_id)
                .join(', ') || 'none'}
            </p>
            <p>
              Dispositions the existing authority accepts:{' '}
              {payload.submission.proposed_dispositions.join(', ')}. None is publication approval.
            </p>
          </>
        ) : null}
      </details>
    </section>
  );
}

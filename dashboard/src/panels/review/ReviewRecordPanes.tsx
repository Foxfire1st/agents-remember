// Technical record identities stay compact. Authored content and reasons are visible in the page.
import type { ReviewPayload, ReviewUnresolvedReference } from '../../data/review';
import type { LaneRead, ReviewUnexplainedLane } from '../../data/reviewLane';
import { laneAttributionFacts, laneCountText } from './laneFocus';
import { remainingWords, tokenWords } from './stateWords';

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
  const { knowledge, source } = payload;
  const fields = knowledge.field_changes.filter((change) => !authoredFields.has(change.field));
  const references = [...knowledge.unresolved, ...source.unresolved];
  return (
    <>
      {' '}
      {fields.length ? (
        <ul data-testid="review-field-changes" data-review-content="changed-field">
          {fields.map((change) => (
            <li key={`${change.item_id}:${change.field}`}>
              {tokenWords(change.field)}: {fieldValue(change.before_value)} → {fieldValue(change.after_value)}
            </li>
          ))}
        </ul>
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
// The selected subject's main-page facts (its slot in the reading column): changed fields, unresolved
// references, the remaining counts, the staleness statement and the submission contract. The one
// "All changes" link is the tree's (MIK-R89 v2 rule 7); this pane has none.
export function ReviewMainFacts({
  payload,
  laneRead,
}: {
  payload: ReviewPayload;
  laneRead?: LaneRead<ReviewUnexplainedLane> | null;
}) {
  const { source, submission, staleness } = payload;
  const lane = laneRead ? laneAttributionFacts(laneRead) : { state: 'unknown' as const };
  return (
    <section data-testid="review-main-facts">
      <RecordFacts payload={payload} />
      {source.remaining.length ? (
        <p
          data-review-content="counts"
          data-testid="review-remaining"
          data-attribution-source="lane"
        >
          {source.remaining
            .map(
              (count) =>
                laneCountText(count.name, lane) ??
                `${remainingWords(count.name)}: ${count.value ?? `not measured (${count.reason ?? 'no reason recorded'})`}`,
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
        <p data-submission-state={submission.state}>Read-only review — {submission.reason}</p>
        <p>next: {submission.next_action}</p>
      </div>
    </section>
  );
}

function JudgmentIdentifiers({ judgment }: { judgment: ReviewPayload['judgment'] | undefined }) {
  if (!judgment) return <p>Judgment identifiers not supplied.</p>;
  return (
    <p>
      Judgment rows:{' '}
      {judgment.rows.map((row) => `${row.row_id} · ${row.history_path}`).join(', ') ||
        'none returned'}
      ; proof identifiers: {judgment.proofs.map((proof) => proof.id).join(', ') || 'none returned'}
    </p>
  );
}

// The selected answer's record identities, for Technical details (MIK-R89 v2 rule 6): the comparison
// reference and policy, the retained revisions, the expansion command, the judgment identifiers and
// the dispositions the existing authority accepts.
export function ReviewRecordIdentities({ payload }: { payload: ReviewPayload }) {
  return (
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
      <JudgmentIdentifiers judgment={payload.judgment} />
      <p>
        Dispositions the existing authority accepts:{' '}
        {payload.submission.proposed_dispositions.join(', ')}. None is publication approval.
      </p>
    </>
  );
}

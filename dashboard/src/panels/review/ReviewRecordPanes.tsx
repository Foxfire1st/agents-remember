// The review's record panes: the knowledge, source, evidence and submission records of ONE admitted
// payload, behind the workspace's "Technical details" disclosure.
//
// WHY THIS IS ITS OWN MODULE. These renderers are pure display of one payload's record fields, with
// no read, selection or paging logic; they grew the surface past the repository's file-size rail. The
// surface owns which payload they are handed, and hands them one only when that payload answers the
// subject on screen: while another subject is pending or unavailable the disclosure stays mounted (so its open state
// survives) and states that the records are being read, rather than keeping the previous subject's
// statements and assessments under the new subject's header.

import type {
  ReviewApplicabilitySummary,
  ReviewAssessmentDisplay,
  ReviewAuthoredEffect,
  ReviewContextRecord,
  ReviewDisplayedApplicability,
  ReviewKnowledgePane,
  ReviewPayload,
  ReviewSignal,
  ReviewUnresolvedReference,
} from '../../data/review';
import { KnowledgeStatements } from './KnowledgeStatements';
const TAKEOVER = 'changeset-viewer';

const pane = (title: string, children: React.ReactNode) => (
  <section
    style={{ marginBottom: '1.25rem', minWidth: 0, overflowWrap: 'anywhere' }}
    data-pane={title}
  >
    <h3 style={{ margin: '0 0 0.4rem' }}>{title}</h3>
    {children}
  </section>
);

const muted = (text: string, testid?: string) => (
  <p style={{ color: 'var(--muted)', margin: '0.2rem 0' }} data-testid={testid}>
    {text}
  </p>
);

const attribution = (author?: string, inputs: string[] = []) =>
  author === undefined
    ? `author: unresolved reference${inputs.length ? ` · inputs: ${inputs.join(', ')}` : ''}`
    : `author: ${author}${inputs.length ? ` · inputs: ${inputs.join(', ')}` : ''}`;

const applicabilityNote = (entry: { applicability?: ReviewDisplayedApplicability }) => {
  const label = entry.applicability;
  if (label === undefined) {
    return null;
  }
  const subject =
    label.subject_kind !== undefined && label.subject_id !== undefined
      ? ` (${label.subject_kind} ${label.subject_id})`
      : '';
  return (
    <div style={{ color: 'var(--muted)' }} data-applicability={label.state}>
      applicability: {label.state}
      {subject} · {label.detail}
    </div>
  );
};

const contextList = (records?: ReviewContextRecord[]) =>
  records?.length ? (
    <ul style={{ margin: '0.2rem 0', paddingLeft: '1.1rem' }} data-testid="review-context">
      {records.map((entry) => (
        <li
          key={`${entry.records}:${entry.record_id}`}
          data-context-of={`${entry.subject_kind}:${entry.subject_id}`}
        >
          context {entry.records} {entry.record_id} · {entry.label} · of {entry.subject_kind}{' '}
          {entry.subject_id} · via {entry.relationship}
          <div style={{ color: 'var(--muted)' }}>
            {attribution(entry.author_ref)} · references: {entry.references.join(', ')}
          </div>
        </li>
      ))}
    </ul>
  ) : null;

const applicabilityCounts = (summaries?: ReviewApplicabilitySummary[]) =>
  summaries?.length ? (
    <ul style={{ margin: '0.2rem 0', paddingLeft: '1.1rem' }} data-testid="review-applicability">
      {summaries.map((row) => (
        <li key={row.records}>
          supplied {row.records}: {row.supplied} · direct {row.direct} · historical {row.historical}{' '}
          · context {row.context} · candidate {row.candidate} · unresolved {row.unresolved} · not
          displayed {row.unrelated} — {row.detail}
        </li>
      ))}
    </ul>
  ) : null;

const unresolvedList = (entries: ReviewUnresolvedReference[]) =>
  entries.length ? (
    <ul
      style={{ margin: '0.2rem 0 0.6rem', paddingLeft: '1.1rem' }}
      data-testid="review-unresolved"
    >
      {entries.map((entry, index) => (
        <li key={`${entry.field}:${entry.recorded_reference ?? index}`}>
          unresolved {entry.field}
          {entry.recorded_reference ? ` (${entry.recorded_reference})` : ''}: {entry.detail}
        </li>
      ))}
    </ul>
  ) : null;

const fieldValue = (value?: string) =>
  value === undefined ? '(absent)' : value === '' ? '(recorded empty)' : value;

function assessmentBlock(entry: ReviewAssessmentDisplay) {
  return (
    <li
      key={entry.assessment_id}
      data-testid="review-assessment"
      data-binding={entry.binding_state}
    >
      <strong>{entry.disposition}</strong> · {entry.finding}
      <div style={{ color: 'var(--muted)' }}>{entry.rationale}</div>
      <div style={{ color: 'var(--muted)' }}>
        {attribution(entry.author_ref, entry.examined_inputs)} · binding: {entry.binding_state}
        {entry.role_ref ? ` · role: ${entry.role_ref}` : ''}
      </div>
      {applicabilityNote(entry)}
    </li>
  );
}

function authoredEffect(effect: ReviewAuthoredEffect) {
  return (
    <li key={`${effect.record_kind}:${effect.record_id}`} data-testid="review-authored-effect">
      <strong>{effect.record_kind}</strong>
      {effect.label ? ` · ${effect.label}` : ''} · {effect.record_id}
      {effect.rationale ? <div>{effect.rationale}</div> : null}
      <div style={{ color: 'var(--muted)' }}>
        {attribution(effect.author_ref, effect.examined_inputs)}
      </div>
      {applicabilityNote(effect)}
      {unresolvedList(effect.unresolved)}
    </li>
  );
}

function signalBlock(signal: ReviewSignal) {
  return (
    <li key={signal.signal_id} data-testid="review-signal">
      <strong>{signal.condition}</strong> · input set: {signal.input_set} · {signal.signal_id}
      <div style={{ color: 'var(--muted)' }}>
        extractor: {signal.extractor_version} · policy: {signal.policy_version}
      </div>
      {signal.relationship_paths.length ? (
        <div style={{ color: 'var(--muted)' }}>paths: {signal.relationship_paths.join(', ')}</div>
      ) : null}
      {signal.scope_limitations.length ? (
        <div style={{ color: 'var(--muted)' }}>
          scope limitations: {signal.scope_limitations.join(', ')}
        </div>
      ) : null}
      {applicabilityNote(signal)}
    </li>
  );
}

function KnowledgeFacts({ knowledge }: { knowledge: ReviewKnowledgePane }) {
  const conditions = knowledge.before_conditions.length || knowledge.after_conditions.length;
  return (
    <>
      {conditions ? (
        <div style={{ color: 'var(--muted)' }} data-testid="review-conditions">
          before conditions: {knowledge.before_conditions.join('; ') || 'none recorded'} · after
          conditions: {knowledge.after_conditions.join('; ') || 'none recorded'}
        </div>
      ) : null}
      <p style={{ margin: '0.4rem 0' }} data-testid="review-revision-groups">
        retained revisions —{' '}
        {knowledge.revision_groups
          .map((group) => `${group.side}:${group.record_id}=${group.selected_revision_count}`)
          .join(' · ') || 'none selected'}
      </p>
      <ul style={{ margin: '0.2rem 0', paddingLeft: '1.1rem' }} data-testid="review-field-changes">
        {knowledge.field_changes.map((change) => (
          <li key={`${change.item_id}:${change.field}`}>
            {change.field}: {fieldValue(change.before_value)} → {fieldValue(change.after_value)}
          </li>
        ))}
      </ul>
    </>
  );
}

function AuthoredRecords({ knowledge }: { knowledge: ReviewKnowledgePane }) {
  return (
    <>
      <h4 style={{ margin: '0.6rem 0 0.2rem' }}>Authored effects and preservation claims</h4>
      {knowledge.authored_effects.length ? (
        <ul style={{ margin: '0.2rem 0', paddingLeft: '1.1rem' }}>
          {knowledge.authored_effects.map(authoredEffect)}
        </ul>
      ) : (
        muted('No authored effect, preservation claim or unresolved question is recorded here.')
      )}
      <h4 style={{ margin: '0.6rem 0 0.2rem' }}>Detection signals (facts, not findings)</h4>
      {knowledge.signals.length ? (
        <ul style={{ margin: '0.2rem 0', paddingLeft: '1.1rem' }}>
          {knowledge.signals.map(signalBlock)}
        </ul>
      ) : (
        muted('No detection signal was supplied to this rendering.')
      )}
    </>
  );
}

function KnowledgePane({ payload }: { payload: ReviewPayload }) {
  const { knowledge } = payload;
  return pane(
    'Knowledge',
    <>
      <div style={{ color: 'var(--muted)' }} data-testid="review-selection">
        {payload.comparison
          ? `comparison: ${payload.comparison.reference} · policy ${payload.comparison.policy_version}`
          : `no knowledge comparison was made · ${knowledge.selection_detail ?? 'no subject selected'}`}
      </div>
      <KnowledgeStatements before={knowledge.before_statement} after={knowledge.after_statement} />
      <KnowledgeFacts knowledge={knowledge} />
      <AuthoredRecords knowledge={knowledge} />
      {knowledge.assessments.length ? (
        <ul style={{ margin: '0.2rem 0', paddingLeft: '1.1rem' }}>
          {knowledge.assessments.map(assessmentBlock)}
        </ul>
      ) : (
        muted('UNASSESSED — no assessment is recorded against this subject.', 'review-unassessed')
      )}
      {contextList(knowledge.context)}
      {applicabilityCounts(knowledge.applicability)}
      {unresolvedList(knowledge.unresolved)}
    </>,
  );
}

function SourcePane({ payload }: { payload: ReviewPayload }) {
  const { source } = payload;
  return pane(
    'Source',
    <>
      <p
        style={{ color: 'var(--muted)', margin: '0.2rem 0' }}
        data-testid="review-source-explorer-pointer"
      >
        the complete source change explorer ({source.inventory.listed_total} measured listed
        path(s), state {source.inventory.state}
        {source.inventory.partial ? ', partial' : ''}) is the population section of the workspace
        above; every listed path is openable there.
      </p>
      <ul style={{ margin: '0.2rem 0', paddingLeft: '1.1rem' }} data-testid="review-locations">
        {source.locations.map((location) => (
          <li
            key={`${location.claim_id}:${location.path}`}
            data-change-state={location.change_state}
          >
            {location.path} · role: {location.role ?? 'unclassified (no role recorded)'}
            {location.before_only ? ' · before-only' : ''} · {location.change_state} ·{' '}
            {location.resolution}
            {location.rationale ? (
              <div style={{ color: 'var(--muted)' }}>{location.rationale}</div>
            ) : null}
          </li>
        ))}
      </ul>
      <p style={{ margin: '0.4rem 0' }} data-testid="review-remaining">
        {source.remaining
          .map((count) =>
            count.value === undefined
              ? `${count.name}: not measured (${count.reason ?? 'no reason recorded'})`
              : `${count.name}: ${count.value}`,
          )
          .join(' · ')}
      </p>
      {source.unattributed_changed_paths.length ? (
        <p style={{ margin: '0.2rem 0' }} data-testid="review-unattributed">
          changed paths with no registered attribution:{' '}
          {source.unattributed_changed_paths.join(', ')}
        </p>
      ) : null}
      {source.expansion_reference ? (
        <p style={{ color: 'var(--muted)', margin: '0.2rem 0' }} data-testid="review-expansion">
          full selected-candidate diff: {source.expansion_reference}
          {source.expansion_command ? ` — ${source.expansion_command}` : ''}
        </p>
      ) : (
        muted('The comparison published no source expansion for this selection.')
      )}
      {unresolvedList(source.unresolved)}
    </>,
  );
}

function EvidencePane({ payload }: { payload: ReviewPayload }) {
  const { evidence } = payload;
  return pane(
    'Evidence and assessment',
    <>
      {evidence.evidence_state === 'recorded' ? (
        <>
          <ul style={{ margin: '0.2rem 0', paddingLeft: '1.1rem' }} data-testid="review-evidence">
            {evidence.evidence_links.map((link) => (
              <li key={link.claim_id}>
                evidence claim {link.claim_id}
                {link.assessment_refs.length
                  ? ` · assessments: ${link.assessment_refs.join(', ')}`
                  : ''}
                {applicabilityNote(link)}
                {unresolvedList(link.unresolved)}
              </li>
            ))}
          </ul>
          <ul
            style={{ margin: '0.2rem 0', paddingLeft: '1.1rem' }}
            data-testid="review-observations"
          >
            {evidence.observations.map((observation) => (
              <li key={observation.observation_id}>
                observation {observation.observation_id} · result: {observation.execution_result}
                <div style={{ color: 'var(--muted)' }}>
                  candidate: {observation.tested_candidate ?? 'not recorded'} · command:{' '}
                  {observation.command_identity ?? 'not recorded'} · artifact:{' '}
                  {observation.result_artifact_ref ?? 'not recorded'} (
                  {observation.result_artifact_digest ?? 'no digest'}) · environment:{' '}
                  {observation.environment_identity ?? 'not recorded'}
                </div>
                {applicabilityNote(observation)}
              </li>
            ))}
          </ul>
        </>
      ) : (
        muted('No recorded evidence links', 'review-no-evidence')
      )}
      {evidence.source_inspection_available
        ? muted('Source-based inspection remains available in the Source pane.')
        : null}
      {evidence.assessments.length ? (
        <ul style={{ margin: '0.2rem 0', paddingLeft: '1.1rem' }} data-testid="review-assessments">
          {evidence.assessments.map(assessmentBlock)}
        </ul>
      ) : (
        muted('UNASSESSED — no assessment is recorded against this subject.', 'review-unassessed')
      )}
      {contextList(evidence.context)}
      {applicabilityCounts(evidence.applicability)}
      {unresolvedList(evidence.unresolved)}
    </>,
  );
}

function SubmissionBlock({ payload }: { payload: ReviewPayload }) {
  const { submission, staleness } = payload;
  return (
    <section style={{ marginBottom: '1.25rem' }} data-testid="review-submission">
      {staleness.state === 'stale' ? (
        <p style={{ margin: '0.2rem 0' }} data-testid="review-stale">
          {staleness.statement} — previous input: {staleness.previous_comparison_ref}
        </p>
      ) : null}
      {staleness.state === 'not-measured' ? (
        <p style={{ margin: '0.2rem 0' }} data-testid="review-staleness-unmeasured">
          {staleness.statement}
        </p>
      ) : null}
      <p
        style={{ color: 'var(--muted)', margin: '0.2rem 0' }}
        data-submission-state={submission.state}
      >
        assessment submission: {submission.state === 'disabled_stale' ? 'DISABLED' : 'not offered'}{' '}
        — {submission.reason}
      </p>
      <p style={{ color: 'var(--muted)', margin: '0.2rem 0' }}>next: {submission.next_action}</p>
      <p style={{ color: 'var(--muted)', margin: '0.2rem 0' }}>
        dispositions the existing authority accepts: {submission.proposed_dispositions.join(', ')} —
        none is publication approval.
      </p>
    </section>
  );
}

// The disclosure itself. `payload` is the answer for the subject on screen, or `null` while that
// subject is being read or could not be read (`unanswered`); `paging` is the surface's page controls
// for the same payload.
export function ReviewTechnicalDetails({
  payload,
  unanswered,
  paging,
}: {
  payload: ReviewPayload | null;
  unanswered: { label: string; unavailable: boolean };
  paging: React.ReactNode;
}) {
  return (
    <details data-testid="review-details" style={{ marginTop: '1rem' }}>
      <summary style={{ cursor: 'pointer', color: 'var(--muted)' }}>
        Technical details · records, paging and submission contract
      </summary>
      {payload === null ? (
        <p
          style={{ color: 'var(--muted)', margin: '0.6rem 0' }}
          data-testid={
            unanswered.unavailable ? 'review-details-unavailable' : 'review-details-pending'
          }
        >
          {unanswered.unavailable
            ? `No records are shown: ${unanswered.label} could not be read.`
            : `Reading the records of ${unanswered.label}…`}
        </p>
      ) : (
        <div
          className={TAKEOVER}
          style={{
            display: 'grid',
            gridTemplateColumns: 'minmax(0, 1fr)',
            gap: '1rem',
            marginTop: '0.6rem',
          }}
        >
          <SubmissionBlock payload={payload} />
          {paging}
          <KnowledgePane payload={payload} />
          <SourcePane payload={payload} />
          <EvidencePane payload={payload} />
        </div>
      )}
    </details>
  );
}

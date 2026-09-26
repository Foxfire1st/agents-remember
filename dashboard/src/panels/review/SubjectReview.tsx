// Statements and judgments belong to the server-selected subject, not to a local roster row.
import { css } from '../../../styled-system/css';
import type {
  ReviewAssessmentDisplay,
  ReviewDisplayedApplicability,
  ReviewKnowledgePane,
  ReviewObservation,
  ReviewPayload,
  ReviewRevisionSelection,
} from '../../data/review';
import type { ReviewSubject } from './ReviewNavigation';
import type { DiffLayout } from './SourceExplorer';
import { KnowledgeStatements } from './KnowledgeStatements';

const muted = css({ color: 'muted', fontSize: '0.78rem', margin: '0.35rem 0' });
const prose = css({ fontSize: '0.9rem', whiteSpace: 'pre-wrap', lineHeight: '1.8' });
const sectionLabel = css({
  color: 'cyan',
  fontSize: '0.75rem',
  textTransform: 'uppercase',
  letterSpacing: '0.1em',
});
const rows = css({ paddingLeft: '1.1rem', margin: '0.4rem 0' });

export function selectedRevision(
  knowledge: ReviewKnowledgePane,
  subject: ReviewSubject,
): ReviewRevisionSelection | null {
  const selection = knowledge.revision_selection;
  return selection?.record_kind === subject.kind && selection.record_id === subject.id
    ? selection
    : null;
}

function statementState(selection: ReviewRevisionSelection): string {
  if (selection.state === 'compared')
    return selection.before_revision_id === selection.after_revision_id ? 'unchanged' : 'changed';
  if (selection.state === 'added' || selection.state === 'removed') return 'one-sided';
  return selection.state;
}

function statementLabel(selection: ReviewRevisionSelection): string {
  if (selection.state === 'ambiguous')
    return 'Ambiguous revision selection · no before/after pair was chosen';
  if (selection.state === 'unresolved') return 'Revision comparison unresolved';
  if (selection.state === 'added') return 'Statement recorded on the after side only';
  if (selection.state === 'removed') return 'Statement recorded on the before side only';
  return selection.before_revision_id === selection.after_revision_id
    ? 'Statement unchanged · same recorded revision'
    : 'Statement revision changed · before/after comparison';
}

export function SelectedStatement({
  knowledge,
  subject,
  layout,
}: {
  knowledge: ReviewKnowledgePane;
  subject: ReviewSubject;
  layout: DiffLayout;
}) {
  const selection = selectedRevision(knowledge, subject);
  if (!selection)
    return (
      <p className={muted} data-testid="review-center-member-unavailable">
        The selected subject's revision comparison was not supplied. No statement comparison is
        claimed.
      </p>
    );
  const state = statementState(selection);
  const unresolved = state === 'ambiguous' || state === 'unresolved';
  return (
    <div data-testid={`review-center-member-${state}`} data-revision-state={selection.state}>
      <p className={muted}>{statementLabel(selection)}</p>
      {unresolved ? (
        <p className={muted} data-testid="review-center-statement-unavailable">
          Before: {knowledge.before_statement.state} · after: {knowledge.after_statement.state}. No
          statement diff is available.
        </p>
      ) : state === 'unchanged' && knowledge.before_statement.state === 'present' ? (
        <p className={prose}>{knowledge.before_statement.text}</p>
      ) : (
        <KnowledgeStatements
          before={knowledge.before_statement}
          after={knowledge.after_statement}
          mode={layout}
        />
      )}
      {selection.state === 'ambiguous' ? (
        <p className={muted}>
          Retained revisions remain available as context. Manual revision pairing is not available
          in this reviewer.
        </p>
      ) : null}
      <details>
        <summary>Statement revision details</summary>
        <p className={muted} data-testid="review-center-member-revisions">
          Before {selection.before_revision_id ?? 'no revision selected'} → after{' '}
          {selection.after_revision_id ?? 'no revision selected'}
        </p>
        <details>
          <summary>Raw selection record</summary>
          <pre className={css({ whiteSpace: 'pre-wrap', fontSize: '0.75rem' })}>
            {JSON.stringify(
              { selection, before: knowledge.before_statement, after: knowledge.after_statement },
              null,
              2,
            )}
          </pre>
        </details>
      </details>
    </div>
  );
}

function Applicability({ value }: { value?: ReviewDisplayedApplicability }) {
  if (!value) return <span> · applicability not supplied</span>;
  return (
    <span data-applicability={value.state}>
      {' '}
      · {value.state}
      {value.subject_id ? ` · ${value.subject_kind} ${value.subject_id}` : ''}
    </span>
  );
}

function Assessment({ record }: { record: ReviewAssessmentDisplay }) {
  return (
    <li data-testid="review-center-assessment" data-binding={record.binding_state}>
      <strong>{record.disposition}</strong> · {record.finding}
      <p className={muted}>{record.rationale}</p>
      <p className={muted}>
        Currentness: {record.binding_state}
        <Applicability value={record.applicability} />
      </p>
      <details>
        <summary>Assessment record</summary>
        <p>
          {record.assessment_id} · {record.author_ref} · {record.role_ref}
        </p>
        <p>{record.examined_inputs.join(' · ')}</p>
      </details>
    </li>
  );
}

function Observation({ record }: { record: ReviewObservation }) {
  const candidate = record.applicability?.state === 'candidate';
  return (
    <li data-testid="review-center-observation">
      <strong>
        {candidate ? 'Candidate execution' : 'Recorded observation'}: {record.execution_result}
      </strong>
      {candidate ? (
        <p className={muted}>Execution input for the candidate; this is not a subject judgment.</p>
      ) : null}
      <p className={muted}>
        <Applicability value={record.applicability} />
      </p>
      <details>
        <summary>Execution record</summary>
        <p>
          {record.observation_id} · {record.command_identity}
        </p>
        <p>
          {record.tested_candidate} · {record.result_artifact_ref} · {record.result_artifact_digest}
        </p>
      </details>
    </li>
  );
}

function AssessmentAbsence({ payload }: { payload: ReviewPayload }) {
  const channel = payload.evidence.channels?.find((value) => value.records === 'assessments');
  const unread = channel && !['recorded', 'none_recorded'].includes(channel.state);
  if (unread)
    return (
      <div className={muted} data-testid="review-center-assessment-unavailable">
        Assessment {channel.state} · {channel.detail}
        <p>{channel.next_action}</p>
      </div>
    );
  return (
    <p className={muted} data-testid="review-center-unassessed">
      Not assessed · no authored judgment was returned for this subject.
    </p>
  );
}

export function SubjectEvidence({
  payload,
  subject,
}: {
  payload: ReviewPayload;
  subject: ReviewSubject;
}) {
  const selection = selectedRevision(payload.knowledge, subject);
  if (!selection)
    return (
      <p className={muted} data-testid="review-center-evidence-unavailable">
        This payload does not contain the selected subject's evidence read. No absence of an
        assessment is claimed.
      </p>
    );
  const evidence = payload.evidence;
  return (
    <section data-testid="review-center-evidence" data-evidence-state={evidence.evidence_state}>
      <h3 className={sectionLabel}>
        {subject.kind === 'family'
          ? 'Execution evidence and family impact'
          : 'Execution evidence and authored assessment'}
      </h3>
      <p className={muted}>
        Evidence: {evidence.evidence_state} · assessment: {evidence.assessment_state}
      </p>
      {evidence.observations.length ? (
        <ul className={rows}>
          {evidence.observations.map((record) => (
            <Observation key={record.observation_id} record={record} />
          ))}
        </ul>
      ) : (
        <p className={muted} data-testid="review-center-no-observation">
          No recorded observation was returned for this subject.
        </p>
      )}
      {evidence.assessments.length ? (
        <ul className={rows}>
          {evidence.assessments.map((record) => (
            <Assessment key={record.assessment_id} record={record} />
          ))}
        </ul>
      ) : (
        <AssessmentAbsence payload={payload} />
      )}
      {evidence.evidence_links.length ? (
        <details>
          <summary>{evidence.evidence_links.length} recorded evidence claims</summary>
          <ul className={rows}>
            {evidence.evidence_links.map((link) => (
              <li key={link.claim_id}>
                {link.claim_id}
                <Applicability value={link.applicability} />
                {link.unresolved.map((item) => (
                  <p key={item.field}>{item.detail}</p>
                ))}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      {evidence.context?.length ? (
        <p className={muted} data-testid="review-center-unjoined">
          {evidence.context.length} context records concern other subjects; their judgments remain
          with those subjects.
        </p>
      ) : null}
    </section>
  );
}

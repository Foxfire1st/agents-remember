// Statements and judgments belong to the server-selected subject, not to a local roster row.
import { css } from '../../../styled-system/css';
import type {
  ReviewAssessmentDisplay,
  ReviewDisplayedApplicability,
  ReviewFamilyMember,
  ReviewKnowledgePane,
  ReviewObservation,
  ReviewPayload,
  ReviewRevisionSelection,
} from '../../data/review';
import type { ReviewSubject } from './ReviewNavigation';
import type { DiffLayout } from './SourceExplorer';
import { KnowledgeStatements } from './KnowledgeStatements';
import {
  revisionMeta,
  wordingComparison,
  type AuthoredWording,
  type WordingComparison,
  type WordingField,
} from './statementWording';

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

function statementState(selection: ReviewRevisionSelection, wording: WordingComparison): string {
  if (selection.state === 'compared') {
    if (wording.kind === 'same_revision') return 'unchanged';
    return wording.kind === 'wording_unchanged' ? 'wording-unchanged' : 'changed';
  }
  if (selection.state === 'added' || selection.state === 'removed') return 'one-sided';
  return selection.state;
}

const FIELD_NAMES: Record<WordingField, string> = {
  statement: 'statement',
  applicability: 'applicability',
  conditions: 'conditions',
  exclusions: 'exclusions',
};

function statementLabel(
  selection: ReviewRevisionSelection,
  wording: WordingComparison,
  revisions: string,
): string {
  if (selection.state === 'ambiguous')
    return 'Ambiguous revision selection · no before/after pair was chosen';
  if (selection.state === 'unresolved') return 'Revision comparison unresolved';
  if (selection.state === 'added') return 'Added statement · recorded on the after side only';
  if (selection.state === 'removed') return 'Removed statement · recorded on the before side only';
  if (wording.kind === 'same_revision') return 'Statement unchanged · same recorded revision';
  if (wording.kind === 'wording_unchanged') return `Wording unchanged · ${revisions}`;
  if (wording.kind === 'not_comparable')
    return `Statement revised · ${revisions} · ${wording.fields.join(', ')} not carried here`;
  return `Changed ${wording.fields.map((field) => FIELD_NAMES[field]).join(', ')} · ${revisions}`;
}

// The two revisions' authored text, from the member rows of the selected revisions when the page
// carried them (every field), else from the knowledge pane (statement and conditions) plus the
// comparison's own field changes for the rest. A field neither carries stays undefined.
function authoredSides(
  knowledge: ReviewKnowledgePane,
  selection: ReviewRevisionSelection,
  members: ReviewFamilyMember[],
): { before: AuthoredWording; after: AuthoredWording; labels: [string, string] } {
  const row = (revision?: string) =>
    members.find(
      (member) => member.invariant_revision_id === revision && member.state === 'recorded',
    );
  const beforeRow = row(selection.before_revision_id);
  const afterRow = row(selection.after_revision_id);
  const reported = (field: string) =>
    knowledge.field_changes.find((change) => change.field === field);
  const pane = (side: 'before' | 'after'): AuthoredWording => {
    const statement = knowledge[`${side}_statement`];
    const fromRow = side === 'before' ? beforeRow : afterRow;
    const change = (field: string) => {
      const found = reported(field);
      return found
        ? ((side === 'before' ? found.before_value : found.after_value) ?? null)
        : undefined;
    };
    if (fromRow)
      return {
        statement: fromRow.statement,
        applicability: fromRow.applicability ?? null,
        conditions: fromRow.essential_conditions,
        exclusions: fromRow.exclusions,
      };
    return {
      statement: statement.state === 'present' ? statement.text : undefined,
      applicability: change('applicability'),
      conditions: knowledge[`${side}_conditions`],
      exclusions: listed(change('exclusions')),
    };
  };
  const short = (revision?: string) => (revision ? revision.slice(0, 8) : 'none');
  const versions = [beforeRow?.display_version, afterRow?.display_version];
  // Display versions only when both are carried and differ: two revisions never read as one.
  const labels: [string, string] =
    versions[0] && versions[1] && versions[0] !== versions[1]
      ? [versions[0], versions[1]]
      : [short(selection.before_revision_id), short(selection.after_revision_id)];
  return { before: pane('before'), after: pane('after'), labels };
}

// A reported field value as the list it is compared as: absent = not carried, null = none recorded.
function listed(value: string | null | undefined): string[] | undefined {
  if (value === undefined) return undefined;
  return value === null ? [] : [value];
}

function ChangedFields({
  fields,
  before,
  after,
}: {
  fields: WordingField[];
  before: AuthoredWording;
  after: AuthoredWording;
}) {
  const shown = fields.filter((field) => field !== 'statement');
  if (!shown.length) return null;
  const text = (value: AuthoredWording[WordingField]) =>
    Array.isArray(value) ? value.join('; ') || 'none' : (value ?? 'none');
  return (
    <ul className={rows} data-testid="review-center-changed-fields">
      {shown.map((field) => (
        <li key={field} data-field={field}>
          {FIELD_NAMES[field]} · before: {text(before[field])} · after: {text(after[field])}
        </li>
      ))}
    </ul>
  );
}

function StatementBody({
  knowledge,
  state,
  wording,
  sides,
  layout,
}: {
  knowledge: ReviewKnowledgePane;
  state: string;
  wording: WordingComparison;
  sides: { before: AuthoredWording; after: AuthoredWording };
  layout: DiffLayout;
}) {
  const { before_statement: before, after_statement: after } = knowledge;
  if (state === 'ambiguous' || state === 'unresolved')
    return (
      <p className={muted} data-testid="review-center-statement-unavailable">
        Before: {before.state} · after: {after.state}. No statement diff is available.
      </p>
    );
  if (state === 'one-sided') return <OneSidedStatement knowledge={knowledge} layout={layout} />;
  const statementMoved = wording.kind === 'changed' && wording.fields.includes('statement');
  // The prose is shown once only when both sides carry it; otherwise each side keeps its own state
  // line (review F5), so an unreadable side is never dropped behind the other side's text.
  const fields =
    wording.kind === 'changed' ? (
      <ChangedFields fields={wording.fields} before={sides.before} after={sides.after} />
    ) : null;
  return (
    <>
      {!statementMoved && before.state === 'present' && after.state === 'present' ? (
        <p className={prose} data-testid="review-center-statement-prose">
          {before.text}
        </p>
      ) : (
        <KnowledgeStatements before={before} after={after} mode={layout} />
      )}
      {fields}
    </>
  );
}

// An added or removed statement is labelled prose (MIK-R31 rule 3), not a split code editor. A side
// whose text is not present keeps the landed state lines, which name why.
function OneSidedStatement({
  knowledge,
  layout,
}: {
  knowledge: ReviewKnowledgePane;
  layout: DiffLayout;
}) {
  const { before_statement: before, after_statement: after } = knowledge;
  const present = before.state === 'present' ? before : after;
  if (present.state !== 'present')
    return <KnowledgeStatements before={before} after={after} mode={layout} />;
  return (
    <p className={prose} data-testid="review-center-statement-prose">
      {present.text}
    </p>
  );
}

export function SelectedStatement({
  knowledge,
  subject,
  layout,
  members = [],
}: {
  knowledge: ReviewKnowledgePane;
  subject: ReviewSubject;
  layout: DiffLayout;
  members?: ReviewFamilyMember[];
}) {
  const selection = selectedRevision(knowledge, subject);
  if (!selection)
    return (
      <p className={muted} data-testid="review-center-member-unavailable">
        The selected subject's revision comparison was not supplied. No statement comparison is
        claimed.
      </p>
    );
  const sides = authoredSides(knowledge, selection, members);
  const wording = wordingComparison(
    sides.before,
    sides.after,
    selection.before_revision_id === selection.after_revision_id,
  );
  const state = statementState(selection, wording);
  return (
    <div data-testid={`review-center-member-${state}`} data-revision-state={selection.state}>
      <p className={muted} data-testid="review-center-statement-label">
        {statementLabel(selection, wording, revisionMeta(...sides.labels))}
      </p>
      <StatementBody
        knowledge={knowledge}
        state={state}
        wording={wording}
        sides={sides}
        layout={layout}
      />
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

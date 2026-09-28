import { useState } from 'react';
import { css } from '../../../styled-system/css';
import {
  useObservedComparison,
  useReviewNavigation,
  type ReviewNavigationState,
  type ReviewSubject,
} from './ReviewNavigation';

import type {
  ReviewApplicabilitySummary,
  ReviewAssessmentDisplay,
  ReviewAuthoredEffect,
  ReviewCollectionPage,
  ReviewContextRecord,
  ReviewDisplayedApplicability,
  ReviewFailure,
  ReviewHistory,
  ReviewKnowledgePane,
  ReviewPagedCollection,
  ReviewPayload,
  ReviewSelectorKind,
  ReviewSignal,
  ReviewUnresolvedReference,
} from '../../data/review';
import {
  REVIEW_WALKABLE_COLLECTIONS,
  carriedPage,
  continuationOf,
  intentOnlyRefusal,
  pageBounds,
} from '../../data/review';
import type { ReviewRefusal } from '../../data/review';
import type { FamilySelection } from './FamilyTree';
import { KnowledgeStatements } from './KnowledgeStatements';
import { type ReviewPageRequest, targetKeyOf, useReviewReadCycle } from './ReviewReadCycle';
import { ReviewRefresh, generationOf } from './ReviewRefresh';
import { type ReviewRead, ReviewOutcomeRegion, problemOf, shownPayload } from './ReviewOutcome';
import { ReviewWorkspace, useWorkspaceState } from './ReviewWorkspace';

export interface ReviewTarget {
  repo: string;
  master: string;
  leaf: string;
  selectorKind?: ReviewSelectorKind;
  selectorId?: string;
  history?: ReviewHistory;
}

const reviewShell = css({
  background: 'bg',
  color: 'ink',
  fontFamily: 'mono',
  fontSize: '0.83rem',
  lineHeight: '1.65',
  padding: '1.2rem',
  overflowWrap: 'anywhere',
  '@media (max-width: 40rem)': { padding: '0.7rem' },
  '& summary': { cursor: 'pointer', color: 'muted', fontSize: '0.75rem', padding: '0.3rem 0' },
  '& details[open] > summary': { color: 'cyan' },
  '& button:not([data-tree-node]):not([data-path]), & select, & input': {
    background: 'bgPanel',
    color: 'ink',
    border: '1px solid var(--grid)',
    borderRadius: '2px',
    font: 'inherit',
    fontSize: '0.75rem',
    padding: '0.4rem 0.6rem',
    cursor: 'pointer',
    maxWidth: '100%',
  },
  '& button:focus-visible, & select:focus-visible, & input:focus-visible, & summary:focus-visible':
    {
      outline: '2px solid var(--amber)',
      outlineOffset: '2px',
    },
  '& button:hover': { color: 'amber' },
  '& [data-testid=review-center-column]': { outlineOffset: '3px' },
});

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

function subjectLabel(
  selectorKind: ReviewSelectorKind | undefined,
  selectorId: string | undefined,
  instead: ReviewFailure | null,
): string {
  if (instead !== null) return 'whole task (no subject selected)';
  if (selectorKind && selectorId) return `${selectorKind} ${selectorId}`;
  return 'whole task (no subject selected)';
}

const retryFor = (read: ReviewRead, reread: () => void) =>
  read.phase === 'failed' ? reread : undefined;

function insteadFor(
  read: ReviewRead,
  problem: ReviewFailure | null,
  instead: ReviewFailure | null,
  offer: (problem: ReviewFailure) => void,
): (() => void) | undefined {
  if (read.phase !== 'refused' || problem === null || instead !== null) return undefined;
  if (!intentOnlyRefusal(problem.code)) return undefined;
  return () => offer(problem);
}

function PageRefusalBlock({
  refusal,
  collection,
  onSelect,
}: {
  refusal: ReviewRefusal;
  collection: ReviewPagedCollection;
  onSelect: (page: ReviewPageRequest | undefined) => void;
}) {
  return (
    <section data-testid="review-page-refusal" data-page-refusal-code={refusal.code}>
      <p style={{ margin: '0.3rem 0' }}>
        {collection} could not be served as a page — {refusal.code}: {refusal.detail}
      </p>
      {refusal.expected !== undefined ? (
        <p
          style={{ margin: '0.2rem 0', color: 'var(--muted)' }}
          data-testid="review-page-refusal-expected"
        >
          expected: {refusal.expected}
        </p>
      ) : null}
      {refusal.observed !== undefined ? (
        <p
          style={{ margin: '0.2rem 0', color: 'var(--muted)' }}
          data-testid="review-page-refusal-observed"
        >
          observed: {refusal.observed}
        </p>
      ) : null}
      <p style={{ margin: '0.2rem 0' }} data-testid="review-page-refusal-action">
        {refusal.next_action}
      </p>
      <button
        type="button"
        data-testid="review-page-refusal-first-page"
        onClick={() => onSelect({ of: collection })}
      >
        first page of {collection}
      </button>
    </section>
  );
}

function PagePicker({
  selection,
  onSelect,
}: {
  selection: ReviewPageRequest | undefined;
  onSelect: (page: ReviewPageRequest | undefined) => void;
}) {
  return (
    <span style={{ display: 'inline-flex', gap: '0.4rem', alignItems: 'center' }}>
      <label style={{ color: 'var(--muted)' }} htmlFor="review-page-collection">
        page over
      </label>
      <select
        id="review-page-collection"
        data-testid="review-page-collection"
        value={selection?.of ?? ''}
        onChange={(event) =>
          onSelect(
            event.target.value === ''
              ? undefined
              : { of: event.target.value as ReviewPagedCollection },
          )
        }
      >
        <option value="">whole review</option>
        {REVIEW_WALKABLE_COLLECTIONS.map((collection) => (
          <option key={collection} value={collection} data-testid="review-page-option">
            {collection}
          </option>
        ))}
      </select>
    </span>
  );
}

function PageActions({
  next,
  page,
  selection,
  onSelect,
}: {
  next: { of: ReviewPagedCollection; continuation: string } | null;
  page: ReviewCollectionPage | null | undefined;
  selection: ReviewPageRequest | undefined;
  onSelect: (page: ReviewPageRequest | undefined) => void;
}) {
  const paging = page !== null && page !== undefined && selection !== undefined;
  return (
    <>
      {next ? (
        <button
          type="button"
          data-testid="review-next-page"
          data-continuation={next.continuation}
          onClick={() => onSelect({ of: next.of, continuation: next.continuation })}
        >
          next page →
        </button>
      ) : null}
      {paging ? (
        <button
          type="button"
          data-testid="review-first-page"
          onClick={() => onSelect({ of: page.collection })}
        >
          first page
        </button>
      ) : null}
    </>
  );
}

function refusedPageOf(
  payload: ReviewPayload,
  selection: ReviewPageRequest | undefined,
): { refusal: ReviewRefusal; collection: ReviewPagedCollection } | null {
  if (selection === undefined || carriedPage(payload) !== null) return null;
  const refusal = payload.page_refusal;
  return refusal === undefined || refusal === null ? null : { refusal, collection: selection.of };
}

function PageBoundsLine({
  payload,
  page,
  refused,
}: {
  payload: ReviewPayload;
  page: ReviewCollectionPage | null | undefined;
  refused: boolean;
}) {
  if (refused) return null;
  if (page) {
    return (
      <p style={{ margin: '0.3rem 0' }} data-testid="review-page-bounds">
        {pageBounds(payload)}
      </p>
    );
  }
  return (
    <p style={{ margin: '0.3rem 0' }} data-testid="review-page-bounds">
      whole review — no bounded collection was paged, so there is no remainder to reach
    </p>
  );
}

function PageControls({
  payload,
  selection,
  onSelect,
}: {
  payload: ReviewPayload;
  selection: ReviewPageRequest | undefined;
  onSelect: (page: ReviewPageRequest | undefined) => void;
}) {
  const page = carriedPage(payload);
  const next = continuationOf(payload);
  const reset = page?.reset ?? null;
  const refused = refusedPageOf(payload, selection);
  return (
    <section
      data-testid="review-page-controls"
      data-page-collection={page?.collection ?? ''}
      data-page-requested={selection?.of ?? ''}
      data-page-refused={refused === null ? 'false' : 'true'}
    >
      <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
        <PagePicker selection={selection} onSelect={onSelect} />
        <PageActions next={next} page={page} selection={selection} onSelect={onSelect} />
      </div>
      {refused === null ? null : (
        <PageRefusalBlock
          refusal={refused.refusal}
          collection={refused.collection}
          onSelect={onSelect}
        />
      )}
      <PageBoundsLine payload={payload} page={page} refused={refused !== null} />
      {reset ? (
        <p style={{ margin: '0.3rem 0' }} data-testid="review-page-reset">
          {reset.code}: {reset.detail} — {reset.next_action}
        </p>
      ) : null}
    </section>
  );
}

function ReviewPanes({
  shown,
  selection,
  onSelect,
  repo,
  master,
  leaf,
  selectorKind,
  selectorId,
  history,
  workspace,
  navigation,
}: {
  shown: ReviewPayload | null;
  selection: ReviewPageRequest | undefined;
  onSelect: (page: ReviewPageRequest | undefined) => void;
  repo: string;
  master: string;
  leaf: string;
  selectorKind?: ReviewSelectorKind;
  selectorId?: string;
  history?: ReviewHistory;
  workspace: ReturnType<typeof useWorkspaceState>;
  navigation: ReviewNavigationState;
}) {
  if (shown === null) return null;
  return (
    <>
      <ReviewWorkspace
        payload={shown}
        repo={repo}
        master={master}
        leaf={leaf}
        selectorKind={selectorKind}
        selectorId={selectorId}
        history={history}
        onPageSelect={onSelect}
        state={workspace}
        navigation={navigation}
      />
      <details data-testid="review-details" style={{ marginTop: '1rem' }}>
        <summary style={{ cursor: 'pointer', color: 'var(--muted)' }}>
          Technical details · records, paging and submission contract
        </summary>
        <div
          className={TAKEOVER}
          style={{
            display: 'grid',
            gridTemplateColumns: 'minmax(0, 1fr)',
            gap: '1rem',
            marginTop: '0.6rem',
          }}
        >
          <SubmissionBlock payload={shown} />
          <PageControls payload={shown} selection={selection} onSelect={onSelect} />
          <KnowledgePane payload={shown} />
          <SourcePane payload={shown} />
          <EvidencePane payload={shown} />
        </div>
      </details>
    </>
  );
}

function ReviewHeader({
  repo,
  master,
  leaf,
  selectorKind,
  selectorId,
  instead,
  history,
  refresh,
  onBack,
}: {
  repo: string;
  master: string;
  leaf: string;
  selectorKind?: ReviewSelectorKind;
  selectorId?: string;
  instead: ReviewFailure | null;
  history?: ReviewHistory;
  refresh?: React.ReactNode;
  onBack: () => void;
}) {
  return (
    <>
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: '0.5rem',
          alignItems: 'center',
          marginBottom: '0.75rem',
        }}
      >
        <button type="button" onClick={onBack} data-testid="review-back">
          ← back
        </button>
        <strong>Intent review · {leaf}</strong>
        <span
          style={{ color: 'var(--muted)', minWidth: 0, overflowWrap: 'anywhere' }}
          data-testid="review-subject"
        >
          <details>
            <summary>Task and subject identifiers</summary>
            {repo} · {master} · {leaf} · {subjectLabel(selectorKind, selectorId, instead)}
          </details>
        </span>
        {refresh}
      </div>
      {history === 'recorded' ? (
        <span data-testid="review-history" className={css({ color: 'muted', fontSize: '0.75rem' })}>
          Historical task comparison
        </span>
      ) : null}
    </>
  );
}

function useSurface({
  repo,
  master,
  leaf,
  selectorKind: initialKind,
  selectorId: initialId,
  history,
  onBack,
}: ReviewTarget & { onBack: () => void }) {
  const navigation = useReviewNavigation({
    repo,
    master,
    leaf,
    selectorKind: initialKind,
    selectorId: initialId,
    history,
  });
  const selectorKind = navigation.subject?.kind;
  const selectorId = navigation.subject?.id;
  const [instead, setInstead] = useState<ReviewFailure | null>(null);
  const [selection, setSelection] = useState<ReviewPageRequest | undefined>(undefined);
  const workspace = useWorkspaceState();
  const selectSubject = (subject: ReviewSubject | undefined, context?: FamilySelection) => {
    workspace.focusSelection.current = true;
    navigation.onSelect(subject);
    setInstead(null);
    setSelection(undefined);
    workspace.setChosen(context ?? null);
  };
  const { read, retained, carried, refresh } = useReviewReadCycle({
    repo,
    master,
    leaf,
    selectorKind,
    selectorId,
    history,
    instead,
    selection,
    hold: navigation.settling,
  });
  const targetKey = targetKeyOf(
    repo,
    master,
    leaf,
    instead,
    selectorKind,
    selectorId,
    selection,
    history,
  );

  const coherent = retained !== null && retained.key === targetKey ? retained.payload : null;
  const shown = shownPayload(read, coherent);
  const problem = problemOf(read);
  useObservedComparison(navigation.observeComparison, shown);

  return {
    repo,
    master,
    leaf,
    selectorKind,
    selectorId,
    history,
    onBack,
    navigation,
    selectSubject,
    instead,
    setInstead,
    selection,
    setSelection,
    workspace,
    read,
    carried,
    refresh,
    coherent,
    shown,
    problem,
  };
}

export function ReviewSurface(props: ReviewTarget & { onBack: () => void }) {
  const {
    repo,
    master,
    leaf,
    selectorKind,
    selectorId,
    history,
    onBack,
    navigation,
    selectSubject,
    instead,
    setInstead,
    selection,
    setSelection,
    workspace,
    read,
    carried,
    refresh,
    coherent,
    shown,
    problem,
  } = useSurface(props);
  return (
    <div
      className={reviewShell}
      data-testid="review-surface"
      data-comparison={shown?.comparison?.reference}
      data-review-target={`${repo}/${master}/${leaf}`}
      data-review-history={history ?? 'live'}
      style={{ height: '100%', minHeight: 0, minWidth: 0, overflowY: 'auto' }}
    >
      <ReviewHeader
        repo={repo}
        master={master}
        leaf={leaf}
        selectorKind={selectorKind}
        selectorId={selectorId}
        instead={instead}
        history={history}
        onBack={onBack}
        refresh={
          <ReviewRefresh
            onRefresh={refresh}
            busy={read.phase === 'loading'}
            generation={generationOf(read, carried, shown)}
          />
        }
      />
      <ReviewOutcomeRegion
        read={read}
        instead={instead}
        shown={shown}
        lastCoherent={read.phase === 'failed' ? coherent : null}
        onRetry={retryFor(read, refresh)}
        onOpenTaskContext={insteadFor(read, problem, instead, setInstead)}
      />
      <ReviewPanes
        shown={shown}
        selection={selection}
        onSelect={setSelection}
        repo={repo}
        master={master}
        leaf={leaf}
        selectorKind={selectorKind}
        selectorId={selectorId}
        history={history}
        workspace={workspace}
        navigation={{ ...navigation, onSelect: selectSubject }}
      />
    </div>
  );
}

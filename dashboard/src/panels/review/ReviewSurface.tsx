// The Intent Reviewer surface: three panes over one comparison, and the refusal states they render.
//
// The surface is display-only. It renders records other owners store, carries every attribution it
// was given, and produces no conclusion of its own: there is no summary, no severity, no score and
// no control that writes anything. The one renderer it reuses is `DiffPane`, fed the two recorded
// statements the comparison published and only when both sides are `present`.

import { useCallback, useEffect, useState } from "react";

import type {
  ReviewAssessmentDisplay,
  ReviewKnowledgePane,
  ReviewAuthoredEffect,
  ReviewPayload,
  ReviewRefusal,
  ReviewSelectorKind,
  ReviewSideContent,
  ReviewSignal,
  ReviewUnresolvedReference,
} from "../../data/review";
import { intentReview } from "../../data/review";
import { DiffPane } from "../changeset/DiffPane";

export interface ReviewTarget {
  repo: string;
  master: string;
  leaf: string;
  selectorKind: ReviewSelectorKind;
  selectorId: string;
}

const TAKEOVER = "changeset-viewer";

const pane = (title: string, children: React.ReactNode) => (
  <section style={{ marginBottom: "1.25rem" }} data-pane={title}>
    <h3 style={{ margin: "0 0 0.4rem" }}>{title}</h3>
    {children}
  </section>
);

const muted = (text: string, testid?: string) => (
  <p style={{ color: "muted", margin: "0.2rem 0" }} data-testid={testid}>
    {text}
  </p>
);

const attribution = (author?: string, inputs: string[] = []) =>
  author === undefined
    ? `author: unresolved reference${inputs.length ? ` · inputs: ${inputs.join(", ")}` : ""}`
    : `author: ${author}${inputs.length ? ` · inputs: ${inputs.join(", ")}` : ""}`;

const unresolvedList = (entries: ReviewUnresolvedReference[]) =>
  entries.length ? (
    <ul style={{ margin: "0.2rem 0 0.6rem", paddingLeft: "1.1rem" }} data-testid="review-unresolved">
      {entries.map((entry, index) => (
        <li key={`${entry.field}:${entry.recorded_reference ?? index}`}>
          unresolved {entry.field}
          {entry.recorded_reference ? ` (${entry.recorded_reference})` : ""}: {entry.detail}
        </li>
      ))}
    </ul>
  ) : null;

const sideState = (side: ReviewSideContent, testid: string) =>
  side.state === "present" ? null : (
    <p style={{ color: "muted", margin: "0.2rem 0" }} data-testid={testid} data-side-state={side.state}>
      {side.state}: {side.detail}
    </p>
  );

function assessmentBlock(entry: ReviewAssessmentDisplay) {
  return (
    <li key={entry.assessment_id} data-testid="review-assessment" data-binding={entry.binding_state}>
      <strong>{entry.disposition}</strong> · {entry.finding}
      <div style={{ color: "muted" }}>{entry.rationale}</div>
      <div style={{ color: "muted" }}>
        {attribution(entry.author_ref, entry.examined_inputs)} · binding: {entry.binding_state}
        {entry.role_ref ? ` · role: ${entry.role_ref}` : ""}
      </div>
    </li>
  );
}

function authoredEffect(effect: ReviewAuthoredEffect) {
  return (
    <li key={`${effect.record_kind}:${effect.record_id}`} data-testid="review-authored-effect">
      <strong>{effect.record_kind}</strong>
      {effect.label ? ` · ${effect.label}` : ""} · {effect.record_id}
      {effect.rationale ? <div>{effect.rationale}</div> : null}
      <div style={{ color: "muted" }}>
        {attribution(effect.author_ref, effect.examined_inputs)}
      </div>
      {unresolvedList(effect.unresolved)}
    </li>
  );
}

function signalBlock(signal: ReviewSignal) {
  return (
    <li key={signal.signal_id} data-testid="review-signal">
      <strong>{signal.condition}</strong> · input set: {signal.input_set} · {signal.signal_id}
      <div style={{ color: "muted" }}>
        extractor: {signal.extractor_version} · policy: {signal.policy_version}
      </div>
      {signal.relationship_paths.length ? (
        <div style={{ color: "muted" }}>paths: {signal.relationship_paths.join(", ")}</div>
      ) : null}
      {signal.scope_limitations.length ? (
        <div style={{ color: "muted" }}>
          scope limitations: {signal.scope_limitations.join(", ")}
        </div>
      ) : null}
    </li>
  );
}

// The mechanical half of pane 1, extracted so the pane's own body reads as a composition: the
// essential conditions each side recorded, how many retained revisions the selection reached on each
// side, and every field transition the comparison itself reported.
function KnowledgeFacts({ knowledge }: { knowledge: ReviewKnowledgePane }) {
  const conditions = knowledge.before_conditions.length || knowledge.after_conditions.length;
  return (
    <>
      {conditions ? (
        <div style={{ color: "muted" }} data-testid="review-conditions">
          before conditions: {knowledge.before_conditions.join("; ") || "none recorded"} · after
          conditions: {knowledge.after_conditions.join("; ") || "none recorded"}
        </div>
      ) : null}
      <p style={{ margin: "0.4rem 0" }} data-testid="review-revision-groups">
        retained revisions —{" "}
        {knowledge.revision_groups
          .map((group) => `${group.side}:${group.record_id}=${group.selected_revision_count}`)
          .join(" · ") || "none selected"}
      </p>
      <ul style={{ margin: "0.2rem 0", paddingLeft: "1.1rem" }} data-testid="review-field-changes">
        {knowledge.field_changes.map((change) => (
          <li key={`${change.item_id}:${change.field}`}>
            {change.field}: {change.before_value ?? "(absent)"} → {change.after_value ?? "(absent)"}
          </li>
        ))}
      </ul>
    </>
  );
}

// The two record collections the pane shows *beside* the mechanical diff, each in its own list and
// under its own heading: an authored effect is never rendered in the shape of a detection fact.
function AuthoredRecords({ knowledge }: { knowledge: ReviewKnowledgePane }) {
  return (
    <>
      <h4 style={{ margin: "0.6rem 0 0.2rem" }}>Authored effects and preservation claims</h4>
      {knowledge.authored_effects.length ? (
        <ul style={{ margin: "0.2rem 0", paddingLeft: "1.1rem" }}>
          {knowledge.authored_effects.map(authoredEffect)}
        </ul>
      ) : (
        muted("No authored effect, preservation claim or unresolved question is recorded here.")
      )}
      <h4 style={{ margin: "0.6rem 0 0.2rem" }}>Detection signals (facts, not findings)</h4>
      {knowledge.signals.length ? (
        <ul style={{ margin: "0.2rem 0", paddingLeft: "1.1rem" }}>
          {knowledge.signals.map(signalBlock)}
        </ul>
      ) : (
        muted("No detection signal was supplied to this rendering.")
      )}
    </>
  );
}

function KnowledgePane({ payload }: { payload: ReviewPayload }) {
  const { knowledge } = payload;
  const bothPresent =
    knowledge.before_statement.state === "present" && knowledge.after_statement.state === "present";
  return pane(
    "Knowledge",
    <>
      <div style={{ color: "muted" }}>
        comparison: {payload.comparison.reference} · policy {payload.comparison.policy_version}
      </div>
      {bothPresent ? (
        <DiffPane
          before={knowledge.before_statement.text ?? ""}
          after={knowledge.after_statement.text ?? ""}
          language={knowledge.before_statement.language}
          mode="split"
          collapse={false}
        />
      ) : (
        <>
          {sideState(knowledge.before_statement, "review-before-state")}
          {sideState(knowledge.after_statement, "review-after-state")}
        </>
      )}
      <KnowledgeFacts knowledge={knowledge} />
      <AuthoredRecords knowledge={knowledge} />
      {knowledge.assessments.length ? (
        <ul style={{ margin: "0.2rem 0", paddingLeft: "1.1rem" }}>
          {knowledge.assessments.map(assessmentBlock)}
        </ul>
      ) : (
        muted("UNASSESSED — no assessment is recorded against this subject.", "review-unassessed")
      )}
      {unresolvedList(knowledge.unresolved)}
    </>,
  );
}

function SourcePane({ payload }: { payload: ReviewPayload }) {
  const { source } = payload;
  return pane(
    "Source",
    <>
      <ul style={{ margin: "0.2rem 0", paddingLeft: "1.1rem" }} data-testid="review-locations">
        {source.locations.map((location) => (
          <li key={`${location.claim_id}:${location.path}`} data-change-state={location.change_state}>
            {location.path} · role: {location.role ?? "unclassified (no role recorded)"}
            {location.before_only ? " · before-only" : ""} · {location.change_state} ·{" "}
            {location.resolution}
            {location.rationale ? <div style={{ color: "muted" }}>{location.rationale}</div> : null}
          </li>
        ))}
      </ul>
      <p style={{ margin: "0.4rem 0" }} data-testid="review-remaining">
        {source.remaining
          .map((count) =>
            count.value === undefined
              ? `${count.name}: not measured (${count.reason ?? "no reason recorded"})`
              : `${count.name}: ${count.value}`,
          )
          .join(" · ")}
      </p>
      {source.unattributed_changed_paths.length ? (
        <p style={{ margin: "0.2rem 0" }} data-testid="review-unattributed">
          changed paths with no registered attribution: {source.unattributed_changed_paths.join(", ")}
        </p>
      ) : null}
      {source.expansion_reference ? (
        <p style={{ color: "muted", margin: "0.2rem 0" }} data-testid="review-expansion">
          full selected-candidate diff: {source.expansion_reference}
          {source.expansion_command ? ` — ${source.expansion_command}` : ""}
        </p>
      ) : (
        muted("The comparison published no source expansion for this selection.")
      )}
      {unresolvedList(source.unresolved)}
    </>,
  );
}

function EvidencePane({ payload }: { payload: ReviewPayload }) {
  const { evidence } = payload;
  return pane(
    "Evidence and assessment",
    <>
      {evidence.evidence_state === "recorded" ? (
        <>
          <ul style={{ margin: "0.2rem 0", paddingLeft: "1.1rem" }} data-testid="review-evidence">
            {evidence.evidence_links.map((link) => (
              <li key={link.claim_id}>
                evidence claim {link.claim_id}
                {link.assessment_refs.length
                  ? ` · assessments: ${link.assessment_refs.join(", ")}`
                  : ""}
                {unresolvedList(link.unresolved)}
              </li>
            ))}
          </ul>
          <ul style={{ margin: "0.2rem 0", paddingLeft: "1.1rem" }} data-testid="review-observations">
            {evidence.observations.map((observation) => (
              <li key={observation.observation_id}>
                observation {observation.observation_id} · result: {observation.execution_result}
                <div style={{ color: "muted" }}>
                  candidate: {observation.tested_candidate ?? "not recorded"} · command:{" "}
                  {observation.command_identity ?? "not recorded"} · artifact:{" "}
                  {observation.result_artifact_ref ?? "not recorded"} (
                  {observation.result_artifact_digest ?? "no digest"}) · environment:{" "}
                  {observation.environment_identity ?? "not recorded"}
                </div>
              </li>
            ))}
          </ul>
        </>
      ) : (
        muted("No recorded evidence links", "review-no-evidence")
      )}
      {evidence.source_inspection_available
        ? muted("Source-based inspection remains available in the Source pane.")
        : null}
      {evidence.assessments.length ? (
        <ul style={{ margin: "0.2rem 0", paddingLeft: "1.1rem" }} data-testid="review-assessments">
          {evidence.assessments.map(assessmentBlock)}
        </ul>
      ) : (
        muted("UNASSESSED — no assessment is recorded against this subject.", "review-unassessed")
      )}
      {unresolvedList(evidence.unresolved)}
    </>,
  );
}

function SubmissionBlock({ payload }: { payload: ReviewPayload }) {
  const { submission, staleness } = payload;
  return (
    <section style={{ marginBottom: "1.25rem" }} data-testid="review-submission">
      {staleness.state === "stale" ? (
        <p style={{ margin: "0.2rem 0" }} data-testid="review-stale">
          {staleness.statement} — previous input: {staleness.previous_comparison_ref}
        </p>
      ) : null}
      <p style={{ color: "muted", margin: "0.2rem 0" }} data-submission-state={submission.state}>
        assessment submission: {submission.state === "disabled_stale" ? "DISABLED" : "not offered"} —{" "}
        {submission.reason}
      </p>
      <p style={{ color: "muted", margin: "0.2rem 0" }}>next: {submission.next_action}</p>
      <p style={{ color: "muted", margin: "0.2rem 0" }}>
        dispositions the existing authority accepts: {submission.proposed_dispositions.join(", ")} —
        none is publication approval.
      </p>
    </section>
  );
}

function RefusalBlock({ refusal }: { refusal: ReviewRefusal }) {
  return (
    <div data-testid="review-refusal">
      <p style={{ margin: "0.2rem 0" }}>
        the review could not be opened ({refusal.code}): {refusal.detail}
      </p>
      <p style={{ color: "muted", margin: "0.2rem 0" }}>next: {refusal.next_action}</p>
      {refusal.offending_input ? (
        <p style={{ color: "muted", margin: "0.2rem 0" }}>offending input: {refusal.offending_input}</p>
      ) : null}
    </div>
  );
}

export function ReviewSurface({
  repo,
  master,
  leaf,
  selectorKind,
  selectorId,
  onBack,
}: ReviewTarget & { onBack: () => void }) {
  const [payload, setPayload] = useState<ReviewPayload | null>(null);
  const [refusal, setRefusal] = useState<ReviewRefusal | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const result = await intentReview(repo, master, leaf, selectorKind, selectorId);
      if (result.state === "review" && result.payload) {
        setPayload(result.payload);
        setRefusal(null);
        return;
      }
      setPayload(null);
      setRefusal(result.refusal ?? null);
    } catch (cause) {
      setPayload(null);
      setRefusal(null);
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, [repo, master, leaf, selectorKind, selectorId]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div
      className="screen"
      data-testid="review-surface"
      data-comparison={payload?.comparison.reference}
      data-review-target={`${repo}/${master}/${leaf}`}
    >
      <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", marginBottom: "0.75rem" }}>
        <button type="button" onClick={onBack} data-testid="review-back">
          ← back
        </button>
        <strong>Intent review</strong>
        <span style={{ color: "muted" }} data-testid="review-subject">
          {repo} · {master} · {leaf} · {selectorKind} {selectorId}
        </span>
      </div>
      {error ? <p data-testid="review-error">the review read failed: {error}</p> : null}
      {refusal ? <RefusalBlock refusal={refusal} /> : null}
      {payload ? (
        <>
          <SubmissionBlock payload={payload} />
          <div className={TAKEOVER} style={{ display: "grid", gap: "1rem" }}>
            <KnowledgePane payload={payload} />
            <SourcePane payload={payload} />
            <EvidencePane payload={payload} />
          </div>
        </>
      ) : null}
    </div>
  );
}

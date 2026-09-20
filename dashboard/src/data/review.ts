// Same-origin client for the read-only Intent Reviewer API (mcp/.../serving/review.py).
//
// Mirrors data/changeset.ts: a `base` arg (same-origin default), typed results taken from the
// application models, a thrown FilesApiError, and NO store mutation. It is a *read* client: the
// surface exposes no submission control, so no function here writes anything.
//
// Every type below mirrors one model in models/knowledge/review.py. A field the server omits is
// absent here rather than defaulted, so an unresolved reference stays unresolved on the client too.

import { getJson, qs } from "./files";

export type ReviewSideState = "present" | "absent" | "binary" | "unresolved";
export type ReviewSelectorKind = "invariant" | "family";

export interface ReviewSideContent {
  state: ReviewSideState;
  text?: string;
  language: string;
  detail: string;
}

export interface ReviewUnresolvedReference {
  field: string;
  recorded_reference?: string;
  detail: string;
}

export interface ReviewCandidateRef {
  repository_id: string;
  master: string;
  leaf_id: string;
  task_ref?: string;
}

export interface ComparisonIdentity {
  reference: string;
  policy_version: string;
  binding_digest: string;
  selector_digest: string;
  before_snapshot_digest: string;
  after_snapshot_digest: string;
  before_code_tree_id?: string;
  after_code_tree_id?: string;
}

export interface ReviewRevisionGroup {
  side: "before" | "after";
  record_id: string;
  selected_revision_count: number;
}

export interface ReviewFieldChange {
  item_id: string;
  item_kind: string;
  field: string;
  before_value?: string;
  after_value?: string;
}

export interface ReviewAuthoredEffect {
  record_kind: string;
  record_id: string;
  revision_id?: string;
  label?: string;
  rationale?: string;
  author_ref?: string;
  examined_inputs: string[];
  unresolved: ReviewUnresolvedReference[];
}

export interface ReviewSignal {
  signal_id: string;
  condition: string;
  input_set: string;
  detected_at?: string;
  relationship_paths: string[];
  extractor_version: string;
  policy_version: string;
  scope_limitations: string[];
}

export interface ReviewAssessmentDisplay {
  assessment_id: string;
  disposition: string;
  finding: string;
  rationale: string;
  author_ref: string;
  role_ref?: string;
  examined_inputs: string[];
  binding_state: string;
  evidence_refs: string[];
}

export interface ReviewKnowledgePane {
  invariant_ids: string[];
  family_ids: string[];
  before_statement: ReviewSideContent;
  after_statement: ReviewSideContent;
  before_conditions: string[];
  after_conditions: string[];
  revision_groups: ReviewRevisionGroup[];
  field_changes: ReviewFieldChange[];
  authored_effects: ReviewAuthoredEffect[];
  signals: ReviewSignal[];
  assessments: ReviewAssessmentDisplay[];
  unresolved: ReviewUnresolvedReference[];
}

export interface ReviewSourceLocation {
  claim_id: string;
  invariant_revision_id?: string;
  path: string;
  role?: string;
  rationale?: string;
  recorded_source_identity: string;
  observed_source_identity?: string;
  resolution: string;
  change_state: "changed" | "unchanged" | "not_selected";
  before_only: boolean;
  reached_via: string[];
}

export interface ReviewRemainingCount {
  name: string;
  value?: number;
  reason?: string;
}

export interface ReviewSourcePane {
  locations: ReviewSourceLocation[];
  remaining: ReviewRemainingCount[];
  expansion_reference?: string;
  expansion_command?: string;
  unattributed_changed_paths: string[];
  attributed_changed_paths: string[];
  unresolved: ReviewUnresolvedReference[];
}

export interface ReviewEvidenceLink {
  claim_id: string;
  revision_id?: string;
  assessment_refs: string[];
  unresolved: ReviewUnresolvedReference[];
}

export interface ReviewObservation {
  observation_id: string;
  revision_id?: string;
  tested_candidate?: string;
  command_identity?: string;
  result_artifact_ref?: string;
  result_artifact_digest?: string;
  execution_result: string;
  environment_identity?: string;
  limitations: string[];
}

export interface ReviewEvidencePane {
  evidence_state: "recorded" | "none_recorded";
  assessment_state: "assessed" | "unassessed";
  evidence_links: ReviewEvidenceLink[];
  observations: ReviewObservation[];
  assessments: ReviewAssessmentDisplay[];
  source_inspection_available: boolean;
  unresolved: ReviewUnresolvedReference[];
}

export interface ReviewStaleness {
  state: "current" | "stale";
  statement: string;
  previous_comparison_ref?: string;
  moved: string[];
}

export interface ReviewSubmission {
  state: "unavailable" | "disabled_stale";
  reason: string;
  next_action: string;
  proposed_dispositions: string[];
  none_is_approval: boolean;
}

export interface ReviewPayload {
  surface_version: string;
  candidate: ReviewCandidateRef;
  comparison: ComparisonIdentity;
  knowledge: ReviewKnowledgePane;
  source: ReviewSourcePane;
  evidence: ReviewEvidencePane;
  staleness: ReviewStaleness;
  submission: ReviewSubmission;
  limitations: string[];
}

export interface ReviewRefusal {
  code: string;
  detail: string;
  next_action: string;
  offending_input?: string;
  expected?: string;
  observed?: string;
}

export interface ReviewResult {
  state: "review" | "refused";
  operation: string;
  repository_id: string;
  payload?: ReviewPayload;
  refusal?: ReviewRefusal;
}

// The one request the surface makes. It names canonical task context and one recorded subject; it
// never names a filesystem path, because the candidate is resolved on the server from the task
// context and the browser must not be able to choose which dataset is reviewed.
export const intentReview = (
  repo: string,
  master: string,
  leaf: string,
  selectorKind: ReviewSelectorKind,
  selectorId: string,
  base = "",
): Promise<ReviewResult> =>
  getJson<ReviewResult>(
    `${base}/api/review/intent?${qs({ repo, master, leaf, selectorKind, selectorId })}`,
  );

// One subject the resolved candidate pair can be reviewed on, as the server selected it. This is
// the ONLY legitimate source of the entry's selector: the id is a recorded identity inside the
// candidate the server resolved from task context, so the browser is handed a subject rather than
// choosing a candidate. There is no path field here on purpose.
export interface ReviewEntry {
  selector_kind: ReviewSelectorKind;
  selector_id: string;
  label: string;
  selected_item_count: number;
}

export interface ReviewEntryListResult {
  state: "entries" | "refused";
  operation: string;
  repository_id: string;
  master: string;
  leaf_id: string;
  entries?: ReviewEntry[];
  refusal?: ReviewRefusal;
}

// The entry read the task view makes before it can offer the Intent review button. It takes the
// same task context the review itself takes and nothing else. A refused read is a normal outcome
// (no live candidate, no dataset yet): it yields no entry and the caller renders no button, which
// is the existing `live && selectorId` semantics and stays correct.
export const intentReviewEntries = (
  repo: string,
  master: string,
  leaf: string,
  base = "",
): Promise<ReviewEntryListResult> =>
  getJson<ReviewEntryListResult>(
    `${base}/api/review/intent/entries?${qs({ repo, master, leaf })}`,
  );

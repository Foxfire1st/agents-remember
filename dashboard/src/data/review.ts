// Same-origin client for the read-only Intent Reviewer API (mcp/.../serving/review.py).
//
// Mirrors data/changeset.ts: a `base` arg (same-origin default), typed results taken from the
// application models, a thrown error, and NO store mutation. It is a *read* client: the surface
// exposes no submission control, so no function here writes anything.
//
// Every type below mirrors one model in models/knowledge/review.py. A field the server omits is
// absent here rather than defaulted, so an unresolved reference stays unresolved on the client too.
// The expansion types mirror models/knowledge/review_source_content.py the same way (ICR-R03).
//
// TRANSPORT (ICR-R16). This route answers with its typed result and maps a refusal onto a 400/404/503
// status, so the refusal is in the *body* of a non-2xx response. `getJson` -- the shared client for
// the other serving routes -- reads only `body.status` and throws, which dropped every typed review
// refusal before a reader could see it. Every read below therefore goes through this route's own
// decode, `data/reviewTransport.ts` (one implementation, re-exported here so the surface and the task
// view import one public entry): the body is read whatever the status and a body carrying this
// route's `state` is the answer, refusal included.

import { getReviewJson } from "./reviewTransport";
import { qs } from "./files";

export {
  ReviewTransportError,
  getReviewJson,
  intentOnlyRefusal,
  reviewFailureToken,
  reviewProblemFromCause,
  reviewProblemFromRefusal,
  unreadableAnswer,
} from "./reviewTransport";
export type { ReviewFailure, ReviewFailureToken, ReviewRefusalFacts } from "./reviewTransport";

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
  // Absent exactly when no knowledge operand was compared (a task-context review): the server
  // omits the selector and both snapshot digests together, and `knowledge_compared` says which
  // shape this is rather than leaving a reader to infer it from three missing fields.
  selector_digest?: string;
  before_snapshot_digest?: string;
  after_snapshot_digest?: string;
  before_code_tree_id?: string;
  after_code_tree_id?: string;
  knowledge_compared: boolean;
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
  // Which question this pane answered: a compared subject, or the task context with no operand.
  selection_state: "subject_selected" | "task_context";
  selection_detail?: string;
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

export type ReviewFileStatus = "added" | "deleted" | "modified" | "type_changed" | "unknown";
export type ReviewFileContent = "text" | "binary" | "symlink" | "submodule" | "unknown";

// One changed path of the bound source pair. `path` is the raw filename exactly as Git recorded it
// -- a tab or a newline inside it is part of the address and not a separator -- so this string is
// what a later read of the same file must use. `content` says whether the content can be rendered
// at all; the entry is listed either way.
export interface ReviewChangedFile {
  path: string;
  status: ReviewFileStatus;
  content: ReviewFileContent;
  mode_change: boolean;
  detail?: string;
}

// One changed path whose NAME cannot be carried as text: a Git pathname is bytes and this surface
// carries text, so a name that is not valid UTF-8 arrives as `path_bytes`, the exact bytes in an
// ASCII-safe spelling (`b'src/caf\xe9-latin1.py'`). The change is listed rather than dropped, and
// nothing is re-encoded: a re-encoded name would address a file the repository does not hold.
export interface ReviewUnrepresentablePath {
  path_bytes: string;
  status: ReviewFileStatus;
  mode_change: boolean;
  detail: string;
}

// The complete source change set of the comparison's bound pair, measured from those two Git
// objects and from nothing else. `state` is the honesty boundary: "measured" is the whole change set
// (including a measured empty one), "unavailable" means nothing was observed and `detail` says why,
// and `partial` means every changed path is listed while part of it could not be reported whole --
// one field of some entries could not be classified, or some paths are in
// `unrepresentable_paths`. A missing or unavailable inventory is never rendered as "no changes".
export interface ReviewSourceInventory {
  state: "measured" | "unavailable";
  entries: ReviewChangedFile[];
  listed_total: number;
  detail: string;
  partial: boolean;
  command: string;
  before_code_tree_id?: string;
  after_code_tree_id?: string;
  unrepresentable_paths: ReviewUnrepresentablePath[];
}

export interface ReviewSourcePane {
  inventory: ReviewSourceInventory;
  locations: ReviewSourceLocation[];
  remaining: ReviewRemainingCount[];
  expansion_reference?: string;
  expansion_command?: string;
  unattributed_changed_paths: string[];
  attributed_changed_paths: string[];
  unresolved: ReviewUnresolvedReference[];
}

// One bound endpoint's content for one listed entry. `state` is the whole truth about what can be
// rendered: `present` (a regular file's text, carried in `text`), `symlink` (the link target),
// `absent` (this endpoint holds no entry at the path), `binary`, `submodule` (a recorded pointer
// with no file bytes) and `unavailable` (the entry could not be read). `text` is present only for
// the two textual states, so a missing or unrenderable side can never arrive as an empty document.
export type ReviewSourceSideState =
  | "present"
  | "absent"
  | "binary"
  | "symlink"
  | "submodule"
  | "unavailable";

export interface ReviewSourceSide {
  state: ReviewSourceSideState;
  text?: string;
  detail: string;
  object_id?: string;
  byte_length?: number;
  truncated: boolean;
}

// One inventory entry opened: both endpoints' content at the exact generation the listing named.
// `currentness` says whether that generation is still the pair the leaf's review binds -- the
// content is the requested generation's either way, and a superseded read never silently becomes a
// read of the newer one.
//
// `path_bound` says which *measured* change set admitted the path: the requested generation's own
// (`requested_generation`), or -- when that measurement could not be made -- the one this leaf's
// review publishes (`leaf_change_set`). A path in no measured change set is refused outright, so
// this field states which measurement was the bound rather than widening the read.
export interface ReviewSourceExpansion {
  path: string;
  status: ReviewFileStatus;
  mode_change: boolean;
  language: string;
  before: ReviewSourceSide;
  after: ReviewSourceSide;
  before_code_tree_id: string;
  after_code_tree_id: string;
  currentness: "current" | "superseded" | "unmeasured";
  currentness_detail: string;
  path_bound: "requested_generation" | "leaf_change_set";
  path_bound_detail: string;
  reference: string;
  command: string;
}

export interface ReviewSourceContentResult {
  state: "content" | "refused";
  operation: string;
  repository_id: string;
  expansion?: ReviewSourceExpansion;
  refusal?: ReviewRefusal;
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
  state: "current" | "stale" | "not_compared";
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
  // Absent exactly when the review compared no knowledge operand; the source pane's inventory is
  // present either way, which is the point of the task-context entry.
  comparison?: ComparisonIdentity;
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

// The one request the surface makes. It names canonical task context and, when there is one, the
// recorded subject being compared; it never names a filesystem path, because the candidate is
// resolved on the server from the task context and the browser must not be able to choose which
// dataset is reviewed. Omitting the selector asks for the task's own review -- the complete source
// change inventory of the resolved pair -- which is the entry a task with no invariant still has.
export const intentReview = (
  repo: string,
  master: string,
  leaf: string,
  selectorKind?: ReviewSelectorKind,
  selectorId?: string,
  base = "",
): Promise<ReviewResult> => {
  const params: Record<string, string> = { repo, master, leaf };
  if (selectorKind !== undefined && selectorId !== undefined) {
    params.selectorKind = selectorKind;
    params.selectorId = selectorId;
  }
  return getReviewJson<ReviewResult>(`${base}/api/review/intent?${qs(params)}`);
};

// One subject the resolved candidate pair can be reviewed on, as the server selected it. This is
// the ONLY legitimate source of the entry's selector: the id is a recorded identity inside the
// candidate the server resolved from task context, so the browser is handed a subject rather than
// choosing a candidate. There is no path field here on purpose.
//
// `presence` is the catalogue's selection state: which of the comparison's two snapshots record
// the identity. A retired (before-only) subject and a newly added (after-only) one travel in the
// same list as the subjects both snapshots hold, so the entry can offer every one of them.
export type ReviewSubjectPresence = "before_only" | "after_only" | "both";

export interface ReviewEntry {
  selector_kind: ReviewSelectorKind;
  selector_id: string;
  label: string;
  presence: ReviewSubjectPresence;
}

export interface ReviewEntryListResult {
  state: "entries" | "refused";
  operation: string;
  repository_id: string;
  master: string;
  leaf_id: string;
  entries?: ReviewEntry[];
  // The labelled totals of the catalogue: every recorded subject, partitioned once into the two
  // reviewable kinds. A caller traversing the list tells a whole catalogue from a first row here.
  total_subjects: number;
  invariant_total: number;
  family_total: number;
  refusal?: ReviewRefusal;
}

// The entry read the task view makes before it can offer the Intent review button. It takes the
// same task context the review itself takes and nothing else. A refused read is a normal outcome
// (no live candidate, no datasets yet) and it is *read* rather than thrown: the answer is `entries`
// with the subjects the pair offers (an empty list is the known-empty answer "no subject is
// recorded here"), or `refused` with the owner's own code, reason and next action, which the task
// view shows beside the entry. The entry itself never depends on this read: it is offered for an
// admitted live candidate, and a refusal here leaves the task-context review reachable.
export const intentReviewEntries = (
  repo: string,
  master: string,
  leaf: string,
  base = "",
): Promise<ReviewEntryListResult> =>
  getReviewJson<ReviewEntryListResult>(`${base}/api/review/intent/entries?${qs({ repo, master, leaf })}`);

// One listed entry's actual content at the two bound code trees (ICR-R03). The generation is an
// *input*: `before`/`after` are the ids the inventory published to this client, echoed back, so the
// content a reader opens is the content of the generation they were looking at -- never re-resolved
// from whatever the leaf holds by the time the request lands.
//
// The transport maps a refused read onto its 400/404 status idiom WITH the typed refusal in the
// body, and for this route a refusal is a normal answer (a path outside the measured change set, a
// baseline that is not this leaf's recorded one). The shared review decode reads the body whatever
// the status, so a refusal arrives as the typed result it is and only a body that is not this
// route's answer becomes a failure.
export const reviewSourceContent = (
  repo: string,
  master: string,
  leaf: string,
  path: string,
  beforeCodeTreeId: string,
  afterCodeTreeId: string,
  base = "",
): Promise<ReviewSourceContentResult> =>
  getReviewJson<ReviewSourceContentResult>(
    `${base}/api/review/intent/source-content?${qs({
      repo,
      master,
      leaf,
      path,
      beforeCodeTreeId,
      afterCodeTreeId,
    })}`,
  );

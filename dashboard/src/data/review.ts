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
import type { ReviewFamilyContext, ReviewRevisionSelection } from "./reviewFamily";

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
// The family half of this contract lives in its own mirror module (ICR-R31@v1) and is re-exported
// here, so every consumer of the review payload imports one public entry.
export {
  FAMILY_CONTEXT_JOIN_KEY,
  FAMILY_SIDES,
  UNRESOLVED_SELECTION_STATES,
  guaranteeComparison,
  memberComparison,
} from "./reviewFamily";
export type {
  FamilyMemberRow,
  GuaranteeComparison,
  MemberComparison,
  ReviewFamilyContext,
  ReviewFamilyContextEntry,
  ReviewFamilyContextReferences,
  ReviewFamilyContextState,
  ReviewFamilyEntryState,
  ReviewFamilyGuarantee,
  ReviewFamilyMember,
  ReviewFamilyMemberSource,
  ReviewFamilyRevisionContext,
  ReviewFamilyRosterPage,
  ReviewFamilySideName,
  ReviewFamilySideState,
  ReviewReadCounts,
  ReviewRevisionSelection,
  ReviewRevisionSelectionState,
} from "./reviewFamily";

export type ReviewSideState = "present" | "absent" | "binary" | "unresolved";
export type ReviewSelectorKind = "invariant" | "family";
// The bounded collections a review can be paged over (ICR-R10, extended by ICR-R31@v1): the
// knowledge comparison's own window, the review-matrix records' window, and the family rosters'
// window. They are named rather than inferred because their cursors are different documents, and a
// cursor is only ever presented with the collection its owner minted it for.
//
// `family_members` is a member of this union because the SERVER accepts it -- narrowing the union
// would misdescribe the wire. It is deliberately NOT one of `REVIEW_WALKABLE_COLLECTIONS`: that
// collection is not one walk but the set of per-family roster walks a response composed, so naming
// it without a cursor addresses no single page and the server refuses it with
// `comparison_page_unreadable` rather than serving an arbitrary walk's first page. A control that
// offered "first page of family_members" would therefore be a control that fetches a refusal, and
// the family walk is instead reached from each family's own roster page -- see
// `REVIEW_WALKABLE_COLLECTIONS`.
export type ReviewPagedCollection = "knowledge" | "records" | "family_members";
export const REVIEW_PAGED_COLLECTIONS: ReviewPagedCollection[] = [
  "knowledge",
  "records",
  "family_members",
];
// The collections a request may name with NO cursor, i.e. the ones whose first page exists. This is
// the set the collection picker offers. `family_members` is excluded on the server's own terms (see
// above) and its walk is continued from `ReviewFamilyRosterPage.continuation` on the family that
// published it.
export const REVIEW_WALKABLE_COLLECTIONS: ReviewPagedCollection[] = ["knowledge", "records"];

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
  applicability?: ReviewDisplayedApplicability;
  record_kind: string;
  record_id: string;
  revision_id?: string;
  label?: string;
  rationale?: string;
  author_ref?: string;
  examined_inputs: string[];
  unresolved: ReviewUnresolvedReference[];
}

// Why one displayed record may appear beside the selected subject (ICR-R26): the treatment its own
// recorded binding earned, with the true subject that binding names and the exact references the
// treatment was decided from. A record of another subject is never in the assessments below; it is
// displayed as labelled context with none of its finding, which belongs to its own review.
export interface ReviewDisplayedApplicability {
  records: string;
  record_id: string;
  state: "direct" | "historical" | "candidate" | "unresolved";
  subject_kind?: string;
  subject_id?: string;
  subject_revision_ids: string[];
  references: string[];
  detail: string;
}

export interface ReviewContextRecord {
  records: string;
  record_id: string;
  label: string;
  subject_kind: string;
  subject_id: string;
  subject_revision_ids: string[];
  relationship: string;
  author_ref?: string;
  role_ref?: string;
  references: string[];
  detail: string;
}

export interface ReviewApplicabilitySummary {
  records: string;
  supplied: number;
  direct: number;
  historical: number;
  context: number;
  candidate: number;
  unresolved: number;
  unrelated: number;
  detail: string;
}

export interface ReviewSignal {
  applicability?: ReviewDisplayedApplicability;
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
  applicability?: ReviewDisplayedApplicability;
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
  revision_selection?: ReviewRevisionSelection | null;
  field_changes: ReviewFieldChange[];
  authored_effects: ReviewAuthoredEffect[];
  signals: ReviewSignal[];
  assessments: ReviewAssessmentDisplay[];
  // Records of *other* subjects this selection reaches through a recorded relationship, and the
  // six-way count of every supplied collection -- including the records the pane does not display
  // as this subject's judgments (ICR-R26).
  context?: ReviewContextRecord[];
  applicability?: ReviewApplicabilitySummary[];
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
  applicability?: ReviewDisplayedApplicability;
  claim_id: string;
  revision_id?: string;
  assessment_refs: string[];
  unresolved: ReviewUnresolvedReference[];
}

export interface ReviewObservation {
  applicability?: ReviewDisplayedApplicability;
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

export interface ReviewRecordChannel {
  records: string;
  state: "recorded" | "none_recorded" | "unavailable" | "not_measured" | "not_selected";
  detail: string;
  unreadable: string[];
  next_action?: string | null;
}

export interface ReviewEvidencePane {
  evidence_state: "recorded" | "none_recorded";
  assessment_state: "assessed" | "unassessed";
  evidence_links: ReviewEvidenceLink[];
  observations: ReviewObservation[];
  assessments: ReviewAssessmentDisplay[];
  source_inspection_available: boolean;
  channels?: ReviewRecordChannel[];
  context?: ReviewContextRecord[];
  applicability?: ReviewApplicabilitySummary[];
  unresolved: ReviewUnresolvedReference[];
}

export interface ReviewStaleness {
  // `not-measured` is the raw-Git identity boundary's state (ICR-R23@v1): the boundary that compares
  // the leaf's declared identities against the repository could not take its comparison at all (a
  // checkout that left its declared branch, a recorded object that is gone, a generation that could
  // not be read), so this surface may not claim the displayed comparison is the candidate's current
  // one. It is not `stale`, which would assert a movement nobody observed and which additionally
  // disables submission.
  state: "current" | "stale" | "not_compared" | "not-measured";
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
  // The comparison-bound family context (ICR-R31@v1): which recorded families the selected subject
  // belongs to on each snapshot, each selected family revision's own authored guarantee, and its
  // complete recorded roster. The route composes one on every answer it returns -- including the
  // task-context review, which states `no_subject_selected` -- so this key being absent means the
  // body did not come from this route (a capture recorded before this field existed, a hand-written
  // body). That is its own fact and the workspace renders it as itself: it is NOT a measured zero,
  // and it is never shown as `no_family_recorded`, which asserts the recorded scope was read and
  // held no family.
  family_context?: ReviewFamilyContext;
  staleness: ReviewStaleness;
  submission: ReviewSubmission;
  // The one bounded collection this response rendered as a page of (ICR-R10). `null` -- or absent on
  // an older body -- means the request named no collection and this is the whole review rather than a
  // page of one; that is a different fact from a page with nothing left in it, and `remaining`
  // distinguishes the two.
  page?: ReviewCollectionPage | null;
  // The refusal a *requested* page earned when the owner could not serve it (a cursor whose
  // comparison moved, a collection that could not be read). A page value needs the owner's own
  // counts, and a refused read has none, so this is the refusal itself: showing "no page" instead
  // would leave the reader on a screen that claims nothing remains. Present exactly when a page was
  // asked for and none came back.
  page_refusal?: ReviewRefusal | null;
  limitations: string[];
}

// One bounded collection's page, as `models/knowledge/review.py::ReviewCollectionPage` publishes it.
//
// `continuation` is the *server's* own opaque cursor for the next page -- never a token this client
// constructs -- and it is present exactly when `remaining` is greater than zero. That equivalence is
// the contract: a body that reports a remainder without a cursor is the "remaining=100 with no way to
// inspect them" this control exists to make impossible, so the control renders no "next" action for
// such a body rather than a button that would fetch nothing.
//
// `scope` names the filters that were active for the counts beside it, in the server's own
// vocabulary, so a number is never shown apart from what it counted.
export interface ReviewCollectionPage {
  collection: ReviewPagedCollection;
  state: "first_page" | "continued" | "reset";
  // What `total` counts. The two owners measure it differently and one rendered sentence must not
  // carry two meanings: `selection` is the size of the whole selection the walk is a page of (the
  // comparison's own constant total, with `returned` cumulative over the walk), and `walk` is the
  // size of the selection this walk still covers (the view measures its remainder from where the
  // walk stands, so its total shrinks as the walk advances). `pageBounds` words each one separately.
  total_basis?: "selection" | "walk";
  total: number;
  returned: number;
  remaining: number;
  continuation?: string | null;
  scope: string[];
  // The cursor this page was asked to continue, echoed back. Absent on a first page and on a reset.
  continued_from?: string | null;
  // The refusal a cursor earned when it no longer bound its comparison: the explicit new-generation
  // action. The page beside it is the first page of the comparison that is there now.
  reset?: ReviewRefusal | null;
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
//
// PAGING (ICR-R10). `page` names the bounded collection this call is a page of and `continuation` is
// the cursor that collection's previous page published for it. They travel together or not at all:
// the server refuses a cursor that names no collection, because which owner's walk it belongs to is
// not a question a client may answer. Omitting both asks for the whole review, exactly as before.
// `pageSize` is a *requested* bound, and the response's own `page.scope` states the bound the server
// actually applied.
//
// HISTORY (ICR-R12). `history` names which record this read is addressed to. Omitting it asks for the
// live candidate, which is what every ordinary entry asks for; `"recorded"` asks for the comparison
// the leaf's own durable generation bound, which is the same answer whether the leaf's worktree is
// still there or cleanup has removed it. It carries the one value the server admits, so a client
// cannot ask for a generation that is not this leaf's.
//
// REFRESH (ICR-R17). `previousBindingDigest` is the comparison the reader was already looking at --
// the `binding_digest` the displayed payload published -- carried only by a read that is REPLACING a
// display rather than making a first one. The response compares it against the comparison it
// rendered, so a candidate that moved while the panel stayed open arrives as the `stale` state with
// the previous identity labelled, instead of silently passing as the same generation. It is the
// *previous* identity and never a substitute for the current one: the read still renders the
// resolved candidate's own comparison, and a caller cannot use this parameter to choose a dataset.
export const intentReview = (
  repo: string,
  master: string,
  leaf: string,
  selectorKind?: ReviewSelectorKind,
  selectorId?: string,
  base = "",
  page?: { of: ReviewPagedCollection; continuation?: string | null; size?: number },
  history?: ReviewHistory,
  previousBindingDigest?: string,
): Promise<ReviewResult> => {
  return getReviewJson<ReviewResult>(
    `${base}/api/review/intent?${qs(
      reviewQuery({ repo, master, leaf, selectorKind, selectorId, page, history, previousBindingDigest }),
    )}`,
  );
};

// The one request's query string, assembled in one place. It is a function rather than a run of
// conditionals inside `intentReview` so that adding a parameter cannot quietly raise the client's
// branch count, and so the two spellings of "absent" (undefined and the empty string a form sends)
// are collapsed once: neither is a value the server should be handed.
function reviewQuery(parts: {
  repo: string;
  master: string;
  leaf: string;
  selectorKind?: ReviewSelectorKind;
  selectorId?: string;
  page?: { of: ReviewPagedCollection; continuation?: string | null; size?: number };
  history?: ReviewHistory;
  previousBindingDigest?: string;
}): Record<string, string> {
  const params: Record<string, string> = { repo: parts.repo, master: parts.master, leaf: parts.leaf };
  if (parts.selectorKind !== undefined && parts.selectorId !== undefined) {
    params.selectorKind = parts.selectorKind;
    params.selectorId = parts.selectorId;
  }
  if (parts.page !== undefined) {
    params.pageOf = parts.page.of;
    if (parts.page.continuation !== undefined && parts.page.continuation !== null) {
      params.continuation = parts.page.continuation;
    }
    if (parts.page.size !== undefined) {
      params.pageSize = String(parts.page.size);
    }
  }
  if (parts.history !== undefined) {
    params.history = parts.history;
  }
  if (parts.previousBindingDigest !== undefined && parts.previousBindingDigest !== "") {
    params[PREVIOUS_BINDING_QUERY] = parts.previousBindingDigest;
  }
  return params;
}

// Which record a review read is addressed to: the live candidate, or the leaf's recorded comparison.
// One value on purpose -- the surface addresses exactly one historical record, the leaf's own
// published generation -- so a caller cannot ask for a comparison the leaf does not hold.
export type ReviewHistory = "recorded";

// The one continuation a page makes reachable, or `null` when this response has none to offer.
//
// It is a function rather than a field read at each call site because the two facts have to agree:
// a page that reported rows remaining while carrying no cursor has no next page, and a caller must
// not be handed the current cursor for it -- presenting the same cursor again would return the page
// it already has. The body's own `remaining` and `continuation` decide it here, so a renderer cannot
// grow a "next" control that fetches nothing or one that cycles on one page.
export const continuationOf = (
  payload: Pick<ReviewPayload, "page"> | undefined,
): { of: ReviewPagedCollection; continuation: string } | null => {
  const page = payload?.page;
  if (!page || !page.continuation || page.remaining <= 0) return null;
  return { of: page.collection, continuation: page.continuation };
};

// One page's own statement about itself, for the control beside it: what was returned out of what,
// what is left, and the filters the counts were taken over. `null` when this response is not a page
// of any collection -- the whole review, which has no remainder to state.
export const pageBounds = (
  payload: Pick<ReviewPayload, "page"> | undefined,
): string | null => {
  const page = carriedPage(payload);
  if (!page) return null;
  const filters = page.scope.length ? ` · ${page.scope.join(" · ")}` : "";
  // The gloss is worded from the refusal's own CODE, not from the state alone: a reset page can mean
  // two different things and only one of them is a moved comparison. A page whose cursor was for
  // another collection is also a `reset` -- nothing moved, the caller used the other walk's token --
  // and calling that "this comparison moved" would assert a generation change that did not happen.
  const state = page.state !== "reset" ? page.state : RESET_GLOSS[page.reset?.code ?? ""] ?? RESET_GLOSS.default;
  // The two bases are worded apart on purpose. The comparison's total is the selection's size and its
  // returned count is cumulative, so it reads "returned 16 of 112"; the view measures its remainder
  // from where the walk stands, so its total is what this walk still covers and the same sentence
  // would have claimed the collection was shrinking. An older body that carries no basis is worded as
  // the selection, which is what that sentence always meant.
  const counts =
    page.total_basis === "walk"
      ? `returned ${page.returned}, ${page.remaining} remaining of the ${page.total} rows this walk still covers`
      : `returned ${page.returned} of ${page.total} in the selection · ${page.remaining} remaining`;
  return `${page.collection}: ${counts} (${state})${filters}`;
};

// The page a payload carries, with the two spellings of "no page" collapsed into one value.
//
// The route serializes its result with `exclude_none=True`, so a payload whose page is absent **omits
// the key** rather than sending `page: null`; a hand-written or older body may send the null. They
// mean the same thing and no consumer should have to know which spelling arrived -- the round-1
// refusal branch compared against `null` alone, so a real refused page skipped it and the reader got
// the sentence the branch existed to remove. Every consumer reads the page through here.
export const carriedPage = (
  payload: Pick<ReviewPayload, "page"> | undefined,
): ReviewCollectionPage | null => payload?.page ?? null;

// The one query parameter that names the comparison a refresh is replacing. It is spelled once here
// because the server's admission reads it by this exact name (`serving/review.py`, alias
// `previousBindingDigest`) and a second spelling at a call site is how a refresh silently stops
// carrying the identity it is measured against.
export const PREVIOUS_BINDING_QUERY = "previousBindingDigest";

// What a `reset` page's state gloss says, chosen by the refusal's code because the state alone cannot
// tell "the comparison moved" from "that cursor is not this collection's". An unrecognised or absent
// code is glossed as the restart it is rather than as a move nothing established.
export const RESET_GLOSS: Record<string, string> = {
  comparison_page_reset: "reset — this comparison moved",
  comparison_page_unreadable: "reset — that cursor is not this collection's",
  default: "reset — this page restarted from the collection's first page",
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

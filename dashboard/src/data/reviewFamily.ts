// The family half of the review mirror (ICR-R31@v1, consumed by ICR-R24@v3).
//
// Every type below mirrors one model in `models/knowledge/review_family_context.py`, the module
// ICR-R31@v1 landed on the server. `data/review.ts` owns the rest of the review payload and
// re-exports these names, so the surface has one public entry for the whole contract; this file
// exists because the family vocabulary is a self-contained value tree and mirroring it inside
// `review.ts` would have pushed that module past the size at which nobody reads it.
//
// WHAT THE MIRROR MUST NOT DO. It may not narrow the server's vocabulary, and it may not default a
// field the server omits. The five context states, the four side states and the two member content
// states are distinct facts that a rendering has to keep apart -- a client that collapsed
// `no_family_recorded` (a measured zero) into `unavailable` (a read that did not happen), or
// `not_recorded` into an empty roster, would be manufacturing the lie the packet forbids. A field the
// server does not send stays `undefined` here.

// What the context answers: whether the recorded scope was read, held no applicable family, could
// not be read, or was never asked about a subject at all. `no_family_recorded` is a *measured* zero.
export type ReviewFamilyContextState =
  | "recorded"
  | "partial"
  | "no_family_recorded"
  | "no_subject_selected"
  | "unavailable";

export type ReviewFamilySideName = "before" | "after";

// One snapshot's side of one family context. None of the four is an empty roster.
export type ReviewFamilySideState = "recorded" | "not_recorded" | "not_resolved" | "unreadable";

export type ReviewFamilyEntryState = "recorded" | "partial" | "unresolved" | "unavailable";

// The one key that joins a member context to the evidence and assessment owners' own collections.
// It is a value rather than prose because joining on a display label or a path would be inventing
// the association.
export const FAMILY_CONTEXT_JOIN_KEY = "invariant_revision_id";

export interface ReviewFamilyGuarantee {
  family_id: string;
  revision_id: string;
  display_version: string;
  joint_guarantee: string;
  state_at_origin: string;
  acceptance_ref?: string;
  provenance: Record<string, unknown>;
  payload_digest: string;
}

export interface ReviewFamilyMemberSource {
  claim_id: string;
  invariant_revision_id: string;
  role: string;
  rationale: string;
  // The address and the resolution travel together or not at all (the server refuses one without the
  // other), so a source reference either carries an observed address with what the read resolved it
  // to, or carries neither and states the reason in `detail`.
  path?: string;
  recorded_source_identity?: string;
  observed_source_identity?: string;
  resolution?: string;
  detail: string;
}

// One recorded membership of a selected family revision, with its exact member revision.
export interface ReviewFamilyMember {
  member_id: string;
  invariant_revision_id: string;
  invariant_id?: string;
  display_label?: string;
  display_version?: string;
  // `recorded` means this page carried that revision's own content; `content_not_on_page` means the
  // membership row is recorded and its content fell outside this page. The server refuses a member
  // whose state and carried content disagree, so this field is the whole truth about the statement.
  state: "recorded" | "content_not_on_page";
  statement?: string;
  applicability?: string;
  essential_conditions: string[];
  exclusions: string[];
  lifecycle?: string;
  provenance: Record<string, unknown>;
  payload_digest?: string;
  other_family_revision_ids: string[];
  sources: ReviewFamilyMemberSource[];
  movement_reference?: string;
  detail: string;
}

// The read owner's own counts, carried whole: the walk's arithmetic is checked by the owner's own
// validator and is never restated by a rendering.
export interface ReviewReadCounts {
  invariant_revisions_total: number;
  family_revisions_total: number;
  memberships_total: number;
  realization_claims_total: number;
  advertised_expansions_total: number;
  primary_items_total: number;
  primary_items_returned: number;
  primary_items_remaining: number;
  distinct_source_locations_total: number;
  distinct_source_paths_total: number;
  unresolved_anchor_total: number;
}

// The read owner's own window of one family revision's recorded scope, stated as its page.
// `complete` is the owner's own `enumeration_complete`: a truncated roster carries the cursor that
// reaches the rest, and the server refuses the two facts disagreeing.
export interface ReviewFamilyRosterPage {
  scope: string[];
  state: "first_page" | "continued";
  counts: ReviewReadCounts;
  complete: boolean;
  members_total: number;
  continuation?: string;
  continued_from?: string;
}

export interface ReviewFamilyRevisionContext {
  side: ReviewFamilySideName;
  family_id: string;
  state: ReviewFamilySideState;
  family_revision_id?: string;
  // Every revision of this family the snapshot records -- a different population from the revisions
  // a selection reached, because a family revision that cites no member is recorded and may not have
  // been chosen. Published so a reader can see the history the selected revision came from.
  recorded_revision_ids: string[];
  guarantee?: ReviewFamilyGuarantee;
  members: ReviewFamilyMember[];
  members_total: number;
  page?: ReviewFamilyRosterPage;
  detail: string;
}

// ICR-R07@v1's own revision-selection value, as the family entry carries it. Mirrored here rather
// than in `review.ts` because the family context is its only consumer on this surface.
export type ReviewRevisionSelectionState =
  | "compared"
  | "ambiguous"
  | "unresolved"
  | "added"
  | "removed";

export interface ReviewRevisionSelection {
  record_kind: "invariant" | "family";
  record_id: string;
  state: ReviewRevisionSelectionState;
  before_revision_id?: string;
  after_revision_id?: string;
  before_heads: string[];
  after_heads: string[];
  before_retained: string[];
  after_retained: string[];
  statement: string;
}

export interface ReviewFamilyContextEntry {
  family_id: string;
  display_label?: string;
  label_side?: ReviewFamilySideName;
  selection: ReviewRevisionSelection;
  before: ReviewFamilyRevisionContext;
  after: ReviewFamilyRevisionContext;
  // Present exactly for the two states that chose no revision, and each candidate carries its own
  // guarantee so a reader inspects the heads instead of being shown one as the answer.
  candidates: ReviewFamilyGuarantee[];
  state: ReviewFamilyEntryState;
  detail: string;
}

export interface ReviewFamilyContextReferences {
  relationship_union: string;
  source_inventory: string;
  evidence_links: string;
  observations: string;
  assessments: string;
  applicability: string;
  join_key: string;
  detail: string;
}

export interface ReviewFamilyContext {
  state: ReviewFamilyContextState;
  detail: string;
  entries: ReviewFamilyContextEntry[];
  families_total: number;
  families_returned: number;
  families_remaining: number;
  membership_rows_total: number;
  unique_member_revision_total: number;
  references: ReviewFamilyContextReferences;
  limitations: string[];
}

// The two snapshots one family context carries, in the order the surface reads them. It is a value
// so a rendering never has to spell the pair out and cannot accidentally read one twice.
export const FAMILY_SIDES: ReviewFamilySideName[] = ["before", "after"];

// One member revision as the tree lists it: the exact membership row and the side it was recorded
// on. A member recorded on both sides with the SAME member revision id is one row, because the
// canonical identity is the revision and not the association -- the shared-identity rule the server
// publishes `other_family_revision_ids` for.
export interface FamilyMemberRow {
  side: ReviewFamilySideName;
  member: ReviewFamilyMember;
}

// Why a family context could not name one selected revision, in the server's own vocabulary. It is a
// distinct answer from "no members": a lineage this context could not reduce to a head is not a
// roster that came back empty.
export const UNRESOLVED_SELECTION_STATES: ReviewRevisionSelectionState[] = [
  "ambiguous",
  "unresolved",
];

// The one sentence a rendering may use about a family's guarantee comparison, and the state it
// describes. It is computed from the two recorded sides and nothing else: a side that recorded no
// revision has no guarantee to compare, and saying "unchanged" for it would claim a comparison the
// store does not support.
export type GuaranteeComparison =
  | { kind: "unrecorded"; detail: string }
  | { kind: "unchanged_revision"; guarantee: ReviewFamilyGuarantee }
  | { kind: "identical_text"; before: ReviewFamilyGuarantee; after: ReviewFamilyGuarantee }
  | { kind: "changed"; before: ReviewFamilyGuarantee; after: ReviewFamilyGuarantee }
  | { kind: "one_sided"; side: ReviewFamilySideName; guarantee: ReviewFamilyGuarantee };

// The three distinguishable "the guarantee did not change" shapes, kept apart on purpose:
//
//   * `unchanged_revision` -- both snapshots selected the SAME family revision, so there is one
//     authored guarantee and nothing was authored between them. This is the only shape in which a
//     surface may say "the guarantee is unchanged".
//   * `identical_text` -- two DISTINCT authored revisions whose guarantee text happens to be equal.
//     Something was authored; the text did not move. Calling this "unchanged" would hide the new
//     revision the store records.
//   * `one_sided` -- exactly one snapshot recorded a revision, so there is no second operand and no
//     comparison was made at all. This is an addition or a removal, never an "unchanged" guarantee.
export function guaranteeComparison(entry: ReviewFamilyContextEntry): GuaranteeComparison {
  const before = entry.before.guarantee;
  const after = entry.after.guarantee;
  if (before === undefined) {
    return after === undefined
      ? { kind: "unrecorded", detail: entry.detail }
      : { kind: "one_sided", side: "after", guarantee: after };
  }
  if (after === undefined) return { kind: "one_sided", side: "before", guarantee: before };
  return twoRecordedGuarantees(before, after);
}

// The three shapes two recorded sides can be, decided by the identities and then by the text: the same
// revision (one authored revision behind both sides), two distinct revisions carrying identical text
// (a revision was authored; the text is what did not move), and two distinct texts.
function twoRecordedGuarantees(
  before: ReviewFamilyGuarantee,
  after: ReviewFamilyGuarantee,
): GuaranteeComparison {
  if (before.revision_id === after.revision_id) {
    return { kind: "unchanged_revision", guarantee: before };
  }
  return before.joint_guarantee === after.joint_guarantee
    ? { kind: "identical_text", before, after }
    : { kind: "changed", before, after };
}

// The shapes a member's statement comparison can be in, decided from the member's own carried content
// and never from the revision ids alone.
//
// `content_not_on_page` is the state the whole distinction rests on: the membership row IS recorded and
// only its revision content fell outside the page. What that leaves a rendering able to say depends on
// how many sides carried content, and the two answers below are different facts a caller must not
// merge:
//
//   * `not_on_page` -- NO side this page reached carried the revision's content, so there is nothing to
//     compare and the caller states that about the page.
//   * `one_sided`   -- at most one side's content is on this page: either the other side listed no row
//     at all, or it listed the row without carrying its content. The caller decides which of those it
//     may say from each member's own `state` and from the roster page's own completeness (see
//     `missingRowNote` in `FamilyReviewCenter.tsx`); this value deliberately carries no sentence.
//
// `unchanged_revision` and `changed` require content on BOTH sides, which is why a row without its
// content can never reach them: comparing a carried revision against one whose content was not on the
// page would claim a comparison the store does not support.
export type MemberComparison =
  | { kind: "not_on_page"; member: ReviewFamilyMember }
  | { kind: "one_sided"; before?: ReviewFamilyMember; after?: ReviewFamilyMember }
  | { kind: "unchanged_revision"; member: ReviewFamilyMember }
  | { kind: "changed"; before: ReviewFamilyMember; after: ReviewFamilyMember };

export function memberComparison(
  before: ReviewFamilyMember | undefined,
  after: ReviewFamilyMember | undefined,
): MemberComparison {
  if (before === undefined || after === undefined) return oneSidedMember(before, after);
  if (before.state !== "recorded" || after.state !== "recorded") {
    return oneSidedMember(before, after);
  }
  return before.invariant_revision_id === after.invariant_revision_id
    ? { kind: "unchanged_revision", member: before }
    : { kind: "changed", before, after };
}

// At most one side's content is on this page. The two answers are different facts and are decided by
// how many sides carried content, NOT by the revision ids:
//
//   * nothing carried (and something was listed) -> `not_on_page`: the page reached a membership row
//     and none of the content it is about;
//   * exactly one carried -> `one_sided`: the other side either listed no row or listed one without
//     its content, and only the rendering knows which, from that side's own state and its roster
//     page's completeness.
//
// Nothing here answers for a side's snapshot: a row missing from a BOUNDED page is a row this page did
// not reach, which is a different fact from a snapshot that records none.
function oneSidedMember(
  before: ReviewFamilyMember | undefined,
  after: ReviewFamilyMember | undefined,
): MemberComparison {
  const carried = [before, after].filter(
    (member): member is ReviewFamilyMember => member !== undefined && member.state === "recorded",
  );
  if (carried.length === 0) {
    const only = before ?? after;
    return only === undefined ? { kind: "one_sided" } : { kind: "not_on_page", member: only };
  }
  return { kind: "one_sided", before, after };
}

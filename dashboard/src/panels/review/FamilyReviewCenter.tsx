// The unified central reading path: one intent-to-expression route for the selected family or member.
//
// WHY THIS IS THE CENTER. ICR-R24@v3 rejects a surface where the reviewer has to reconstruct the
// relationship between an intended guarantee, the exact statement it is about, the source that
// realizes it, the evidence someone executed and the judgment someone authored -- by switching tabs
// or opening a detached inspector. This module renders that route in one column, in the order the
// packet names: the family guarantee and the selected intent first, then the linked expressions, then
// the recorded evidence and authored assessment. Every block reads records other owners store; none
// of them concludes anything about another, and no block can carry a verdict.
//
// THE INDEPENDENT FACTS STAY INDEPENDENT. A statement change, a membership/realization change, a
// source/test change, an execution observation and an authored assessment are five separate states
// owned by five separate modules. This rendering prints each of them under its own heading with the
// owner's own words, so a member that changed cannot read as a claim that its family guarantee holds,
// and a guarantee revision cannot read as an assessment.
//
// WHAT IT REFUSES TO DO. It does not compare two operands the store did not record both of (a side
// that carried no revision has no text to diff, and "unchanged" is a claim about two operands); it
// does not print a member statement whose revision content was not on the page; and it does not show
// a record of another subject as this member's evidence -- records are joined on the recorded
// `invariant_revision_id` the server publishes as the join key, and everything not joined is counted
// and stated rather than displayed.

import { Fragment } from "react";

import { css } from "../../../styled-system/css";
import type {
  ReviewAssessmentDisplay,
  ReviewEvidencePane,
  ReviewFamilyContextEntry,
  ReviewFamilyGuarantee,
  ReviewFamilyMember,
  ReviewFamilyMemberSource,
  ReviewFamilySideName,
  ReviewObservation,
  ReviewPayload,
  ReviewSourceLocation,
} from "../../data/review";
import { FAMILY_SIDES, guaranteeComparison, memberComparison } from "../../data/review";
import { DiffPane } from "../changeset/DiffPane";
import { SourceExplorer, type DiffLayout } from "./SourceExplorer";
import {
  RosterLine,
  RosterNext,
  emptyRosterSentence,
  type FamilySelection,
} from "./FamilyTree";

const shell = css({
  background: "bgPanel",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "3px",
  padding: "0.6rem 0.7rem",
  minWidth: "0",
  display: "grid",
  gap: "0.7rem",
  alignContent: "start",
});

const sectionLabel = css({
  color: "cyan",
  fontSize: "0.72rem",
  letterSpacing: "0.1em",
  textTransform: "uppercase",
  margin: "0 0 0.25rem",
});

const card = css({
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "3px",
  padding: "0.5rem 0.6rem",
  background: "bg",
  minWidth: "0",
});

const muted = css({ color: "muted", fontSize: "0.8rem", margin: "0.2rem 0" });

const prose = css({ whiteSpace: "pre-wrap", margin: "0.2rem 0", fontSize: "0.85rem" });

const rows = css({ margin: "0.2rem 0", paddingLeft: "1.1rem" });

const linkButton = css({
  background: "transparent",
  border: "none",
  color: "amber",
  cursor: "pointer",
  font: "inherit",
  padding: "0",
  textDecoration: "underline",
  _focusVisible: { outline: "1px solid var(--amber)", outlineOffset: "2px" },
});

// One guarantee, printed whole with the identities a reader needs to find it again. The provenance and
// seal are technical detail: they belong in the disclosure, not in the reading path.
function GuaranteeBlock({ guarantee, side }: { guarantee: ReviewFamilyGuarantee; side?: string }) {
  return (
    <div className={card} data-testid="review-center-guarantee" data-revision={guarantee.revision_id}>
      <p className={muted}>
        family {guarantee.family_id}
        {side ? ` · ${side} snapshot` : ""} · revision {guarantee.revision_id} · version{" "}
        {guarantee.display_version} · state at origin: {guarantee.state_at_origin}
        {guarantee.acceptance_ref ? ` · accepted by ${guarantee.acceptance_ref}` : ""}
      </p>
      <p className={prose}>{guarantee.joint_guarantee}</p>
    </div>
  );
}

// The family's guarantee comparison, in the shape the two recorded sides actually support. Each of
// the five branches is a different fact and gets its own sentence; only `unchanged_revision` may say
// the guarantee did not change, because only there is there one authored revision behind both sides.
function GuaranteeComparisonBlock({
  entry,
  layout,
}: {
  entry: ReviewFamilyContextEntry;
  layout: DiffLayout;
}) {
  const comparison = guaranteeComparison(entry);
  if (comparison.kind === "unrecorded") {
    return (
      <p className={muted} data-testid="review-center-guarantee-unrecorded">
        no guarantee is compared here: neither snapshot selected a family revision for this family.
        The composition&apos;s own statement is: {comparison.detail}
      </p>
    );
  }
  if (comparison.kind === "one_sided") {
    const other = comparison.side === "before" ? "after" : "before";
    return (
      <div data-testid="review-center-guarantee-one-sided" data-side={comparison.side}>
        <p className={muted}>
          only the {comparison.side} snapshot records a family revision for this family, so no
          guarantee comparison was made: the {other} snapshot records none. This is a one-sided
          guarantee, not an unchanged one.
        </p>
        <GuaranteeBlock guarantee={comparison.guarantee} side={comparison.side} />
      </div>
    );
  }
  if (comparison.kind === "unchanged_revision") {
    return (
      <div data-testid="review-center-guarantee-unchanged">
        <p className={muted}>
          both snapshots selected the same family revision {comparison.guarantee.revision_id}, so the
          guarantee is unchanged — one authored revision stands behind both sides.
        </p>
        <GuaranteeBlock guarantee={comparison.guarantee} />
      </div>
    );
  }
  if (comparison.kind === "identical_text") {
    return (
      <div data-testid="review-center-guarantee-identical-text">
        <p className={muted}>
          the two snapshots selected different family revisions ({comparison.before.revision_id} →{" "}
          {comparison.after.revision_id}) whose recorded guarantee text is identical. A revision was
          authored between them; the text is what did not move.
        </p>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.5rem" }}>
          <GuaranteeBlock guarantee={comparison.before} side="before" />
          <GuaranteeBlock guarantee={comparison.after} side="after" />
        </div>
      </div>
    );
  }
  return (
    <div data-testid="review-center-guarantee-changed">
      <p className={muted}>
        the family guarantee changed between {comparison.before.revision_id} and{" "}
        {comparison.after.revision_id}:
      </p>
      <DiffPane
        before={comparison.before.joint_guarantee}
        after={comparison.after.joint_guarantee}
        language="text"
        mode={layout}
        collapse={false}
      />
      <p className={muted}>
        before revision {comparison.before.revision_id} · after revision {comparison.after.revision_id}
      </p>
    </div>
  );
}

// One recorded member's own identity line: the label and version it was authored under, the exact
// revision identity and its lifecycle. Printed as its own node so the statement beside it is only ever
// the stored text.
function MemberIdentity({ member }: { member: ReviewFamilyMember }) {
  if (!member.display_label && !member.display_version) return null;
  const label = [member.display_label, member.display_version].filter(Boolean).join(" · ");
  return (
    <p className={muted}>
      {label} · revision {member.invariant_revision_id}
      {member.lifecycle ? ` · lifecycle: ${member.lifecycle}` : ""}
    </p>
  );
}

// The recorded facts a member revision carries beside its statement: the applicability its author
// recorded, its essential conditions and its exclusions. Each is printed only when the store has one,
// so an absent list is absent rather than an empty claim.
function MemberFacts({ member }: { member: ReviewFamilyMember }) {
  return (
    <>
      {member.applicability ? (
        <p className={muted}>recorded applicability: {member.applicability}</p>
      ) : null}
      {member.essential_conditions.length ? (
        <p className={muted}>essential conditions: {member.essential_conditions.join("; ")}</p>
      ) : null}
      {member.exclusions.length ? (
        <p className={muted}>exclusions: {member.exclusions.join("; ")}</p>
      ) : null}
    </>
  );
}

// One member's own statement, printed as the stored text it is. A member whose revision content fell
// outside this page has no statement to show and says so; nothing here can render a missing statement
// as an empty one.
function MemberStatement({ member }: { member: ReviewFamilyMember }) {
  if (member.state !== "recorded") {
    return (
      <p className={muted} data-testid="review-center-member-not-on-page">
        this page did not carry the revision content of member {member.invariant_revision_id}, so no
        statement for it may be shown here: {member.detail}
      </p>
    );
  }
  return (
    <div data-testid="review-center-member-statement" data-member={member.member_id}>
      <MemberIdentity member={member} />
      <p className={prose}>{member.statement}</p>
      <MemberFacts member={member} />
    </div>
  );
}

// What is true about a side this comparison has no row for. The two facts are different and only one
// of them is about the SNAPSHOT (fix round 2, V7):
//
//   * the side's roster was read WHOLE (or the side recorded no roster at all, which its own line above
//     states as `not_recorded`/`not_resolved`/`unreadable`) -> the snapshot really records no such row;
//   * the side's roster is a position in a BOUNDED walk -> this page simply did not reach a row, and
//     saying the snapshot records none would be false about the store. The continuation the roster
//     published is what reaches the rest.
function missingRowNote(entry: ReviewFamilyContextEntry, side: ReviewFamilySideName): string {
  const context = entry[side];
  if (context.state !== "recorded") {
    return `the ${side} side read no roster (${context.state}), so this revision has no ${side} operand`;
  }
  const whole = context.page === undefined || context.page.complete;
  return whole
    ? `the ${side} snapshot records no member row for this revision`
    : `this page did not carry a ${side} member row for this revision, and the ${side} roster is a position in a bounded walk: the continuation beside it reaches the rows this page did not carry`;
}

// One side is missing, or a side's row was listed without its content. What may be said is decided
// from each member's OWN state and from the roster page's own completeness, never from the comparison
// kind -- the round-1 bytes printed "the after snapshot records no member row for this revision" for a
// row the after snapshot DOES record and merely did not carry on that page, while the tree row for the
// same member read "recorded on both snapshots" (fix round 2, V7).
function oneSidedStatement(
  entry: ReviewFamilyContextEntry,
  comparison: { before?: ReviewFamilyMember; after?: ReviewFamilyMember },
) {
  const present = comparison.before ?? comparison.after;
  if (present === undefined) {
    return (
      <p className={muted} data-testid="review-center-member-absent">
        neither snapshot carried a member row for the selected revision.
      </p>
    );
  }
  const listed = FAMILY_SIDES.filter((side) => comparison[side] !== undefined);
  const notCarried = listed.filter((side) => comparison[side]?.state !== "recorded");
  const carried = listed.filter((side) => comparison[side]?.state === "recorded");
  const missing = FAMILY_SIDES.find((side) => comparison[side] === undefined);
  const note =
    notCarried.length > 0
      ? `the ${notCarried.join(" and ")} page listed this revision's membership row but did not carry its revision content; the ${carried.join(" and ")} side's content is below`
      : missing === undefined
        ? "no operand is missing from this comparison"
        : missingRowNote(entry, missing);
  return (
    <div data-testid="review-center-member-one-sided">
      <p className={muted} data-testid="review-center-member-one-sided-note">
        {note} — no before/after comparison is drawn from one side's content.
      </p>
      <MemberStatement member={present} />
    </div>
  );
}

function memberStatementBlock(
  entry: ReviewFamilyContextEntry,
  before: ReviewFamilyMember | undefined,
  after: ReviewFamilyMember | undefined,
  layout: DiffLayout,
) {
  const comparison = memberComparison(before, after);
  if (comparison.kind === "not_on_page") return <MemberStatement member={comparison.member} />;
  if (comparison.kind === "one_sided") return oneSidedStatement(entry, comparison);
  if (comparison.kind === "unchanged_revision") {
    return (
      <div data-testid="review-center-member-unchanged">
        <p className={muted}>
          both snapshots record the same member revision {comparison.member.invariant_revision_id}:
          the statement is unchanged, and the change this review is about is elsewhere.
        </p>
        <MemberStatement member={comparison.member} />
      </div>
    );
  }
  return (
    <div data-testid="review-center-member-changed">
      {comparison.before.state === "recorded" && comparison.after.state === "recorded" ? (
        <DiffPane
          before={comparison.before.statement ?? ""}
          after={comparison.after.statement ?? ""}
          language="text"
          mode={layout}
          collapse={false}
        />
      ) : null}
      {comparison.before.state !== "recorded" ? (
        <MemberStatement member={comparison.before} />
      ) : null}
      {comparison.after.state !== "recorded" ? (
        <MemberStatement member={comparison.after} />
      ) : null}
      <p className={muted}>
        before revision {comparison.before.invariant_revision_id} · after revision{" "}
        {comparison.after.invariant_revision_id}
      </p>
    </div>
  );
}

// The member's recorded realization claims: the exact claim identities, the roles their authors
// recorded and the addresses this read observed. A claim whose address was not observed says so
// instead of being drawn with an empty path, which is what the server refuses to send.
function RealizationClaims({ member }: { member: ReviewFamilyMember }) {
  if (!member.sources.length) {
    return (
      <p className={muted} data-testid="review-center-expressions">
        no realization claim is recorded for this member revision in this payload.
      </p>
    );
  }
  return (
    <ul className={rows} data-testid="review-center-expressions">
      {member.sources.map((claim) => (
        <li key={claim.claim_id}>
          claim {claim.claim_id} · role: {claim.role}
          {claim.path ? (
            <>
              {" "}
              · recorded address: <code>{claim.path}</code>
              {claim.resolution ? ` · this read resolved it as ${claim.resolution}` : ""}
            </>
          ) : (
            " · this read observed no address for it"
          )}
          <div className={muted}>{claim.rationale}</div>
          <div className={muted}>{claim.detail}</div>
        </li>
      ))}
    </ul>
  );
}

// What may be said about a path the comparison's own source inventory does not list. One owner for the
// two sentences, because the member-level attribution and the family-level excerpt collection both
// need them and two copies could drift into disagreeing about the same path.
function unlistedPathNote(listedPartial: boolean): string {
  return listedPartial
    ? "this path is not among the paths this inventory listed, and that inventory is partial: absence from its list is not a measurement that the path did not change."
    : "this path is not a changed path of the comparison's measured change set.";
}

// The measured source attribution of one member revision: the records the source owner publishes whose
// own `invariant_revision_id` is this member's. The join is the value the server names as the join
// key, never a label or a path, and each row states the owner's own `change_state` and `resolution`.
function AttributedPaths({
  locations,
  member,
  listed,
  listedPartial,
  onOpenPath,
}: {
  locations: ReviewSourceLocation[];
  member: ReviewFamilyMember;
  listed: Set<string>;
  listedPartial: boolean;
  onOpenPath: (path: string) => void;
}) {
  const mine = locations.filter(
    (location) => location.invariant_revision_id === member.invariant_revision_id,
  );
  if (!mine.length) {
    return (
      <p className={muted} data-testid="review-center-attribution">
        no source location record in this payload names this member revision — the source owner
        publishes one per selected claim, and none of them is about {member.invariant_revision_id}.
      </p>
    );
  }
  return (
    <ul className={rows} data-testid="review-center-attribution">
      {mine.map((location) => (
        <li key={`${location.claim_id}:${location.path}`} data-change-state={location.change_state}>
          {listed.has(location.path) ? (
            <>
              <button
                type="button"
                className={linkButton}
                data-testid="review-center-open-path"
                data-path={location.path}
                onClick={() => onOpenPath(location.path)}
              >
                {location.path}
              </button>{" "}
              · listed among this comparison&apos;s measured changed paths
            </>
          ) : (
            <code>{location.path}</code>
          )}{" "}
          · role: {location.role ?? "unclassified (no role recorded)"} · {location.change_state}
          {location.before_only ? " · before-only" : ""} · {location.resolution}
          {!listed.has(location.path) ? (
            <div className={muted}>{unlistedPathNote(listedPartial)}</div>
          ) : null}
          {location.rationale ? <div className={muted}>{location.rationale}</div> : null}
          {location.reached_via.length ? (
            <div className={muted}>reached via: {location.reached_via.join(", ")}</div>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

function observationBlock(observation: ReviewObservation) {
  return (
    <li key={observation.observation_id} data-testid="review-center-observation">
      observation {observation.observation_id} · result: {observation.execution_result}
      <div className={muted}>
        candidate: {observation.tested_candidate ?? "not recorded"} · command:{" "}
        {observation.command_identity ?? "not recorded"} · artifact:{" "}
        {observation.result_artifact_ref ?? "not recorded"} (
        {observation.result_artifact_digest ?? "no digest"})
      </div>
    </li>
  );
}

function assessmentBlock(assessment: ReviewAssessmentDisplay) {
  return (
    <li key={assessment.assessment_id} data-testid="review-center-assessment" data-binding={assessment.binding_state}>
      <strong>{assessment.disposition}</strong> · {assessment.finding}
      <div className={muted}>{assessment.rationale}</div>
      <div className={muted}>
        {assessment.author_ref === undefined
          ? "author: unresolved reference"
          : `author: ${assessment.author_ref}`}{" "}
        · binding: {assessment.binding_state}
        {assessment.role_ref ? ` · role: ${assessment.role_ref}` : ""}
      </div>
    </li>
  );
}

// The evidence and the authored judgment of one member revision, joined on the recorded join key. The
// records that name another subject are counted and named as not displayed -- never rendered here,
// because a sibling's finding belongs to the sibling's own review.
function EvidenceBlock({
  evidence,
  member,
}: {
  evidence: ReviewEvidencePane;
  member: ReviewFamilyMember;
}) {
  const revision = member.invariant_revision_id;
  const observations = evidence.observations.filter(
    (observation) => observation.revision_id === revision,
  );
  const assessments = evidence.assessments.filter((assessment) =>
    (assessment.applicability?.subject_revision_ids ?? []).includes(revision),
  );
  const unjoined =
    evidence.observations.length -
    observations.length +
    (evidence.assessments.length - assessments.length);
  return (
    <div data-testid="review-center-evidence" data-evidence-state={evidence.evidence_state}>
      <p className={muted}>
        the comparison&apos;s own evidence state is {evidence.evidence_state}; {observations.length} of{" "}
        {evidence.observations.length} observation(s) name this member revision.
      </p>
      {observations.length ? (
        <ul className={rows}>{observations.map(observationBlock)}</ul>
      ) : (
        <p className={muted} data-testid="review-center-no-observation">
          no recorded observation names this member revision.
        </p>
      )}
      <p className={muted}>
        the comparison&apos;s own assessment state is {evidence.assessment_state};{" "}
        {assessments.length} of {evidence.assessments.length} assessment(s) name this member revision.
      </p>
      {assessments.length ? (
        <ul className={rows}>{assessments.map(assessmentBlock)}</ul>
      ) : (
        <p className={muted} data-testid="review-center-unassessed">
          no authored assessment in this payload is bound to this member revision. That is an absence
          of a recorded judgment about it, not a judgment that it is fine.
        </p>
      )}
      {unjoined ? (
        <p className={muted} data-testid="review-center-unjoined">
          {unjoined} record(s) in this payload name another subject and are not displayed beside this
          member: a record belongs to the review of the subject its own binding names.
        </p>
      ) : null}
    </div>
  );
}

// The centre before the reader has chosen a family or a member. It says what the column is for and
// states that the explorer below is not filtered by the selection -- the one thing a reader must know
// before choosing, because the family navigation is an attribution lens rather than an exclusion
// filter.
//
// ITS FIRST SENTENCE NAMES THE PLANE, AND THAT IS A FIX (register B3). It used to read "No family or
// member is selected", full stop -- while the scope header eight lines above can read "recorded: 1 of 1
// recorded family context(s) composed", the tree can print "this review selected <revision>" twice, and
// a roster line can read "2 membership row(s) and this page carried 2 of them -- the page is the whole
// selection". Every one of those "selected"s is the SERVER's own word for which family revision it read
// and which roster page it carried; none of them is a choice this column can render. So the sentence
// was true about this column's own state and misleading about the screen: a reader comparing it with
// the tree reads a contradiction that is really a collision of two vocabularies.
//
// The measured discriminator is this component's own `data-selection-kind` on its parent: it is
// `none` here, and no tree row carries `aria-current` -- verified on the mounted product, so this is a
// statement about state the pane holds and not a claim about what the reader sees. The replacement says
// which selection is missing (this column's) and points at the affordance that makes one, and invents
// nothing about the composition the header reports.
function UnselectedCenter() {
  return (
    <section className={shell} data-testid="review-center-unselected">
      <h2 className={sectionLabel}>Central review</h2>
      <p className={muted}>
        No family or member has been chosen in this column yet. The composition above is unaffected by
        that: the header reports which family contexts this review composed, and the tree reports the
        family revision and roster page the server selected for each of them — neither is a choice made
        here. The complete source change explorer below is the whole measured review population and is
        not filtered by any choice made here: choose a family guarantee or one of its member statements
        above to read the intent, its linked expressions and its recorded evidence in this column.
      </p>
    </section>
  );
}

// ---------------------------------------------------------------------------------------------
// A4: THE FAMILY'S CHANGED EXPRESSION EXCERPTS, DEDUPLICATED.
//
// WHY THIS IS HERE. The accepted design's whole-family line asks for three things and the centre
// used to render only two of them: "Whole-family selection presents the full guarantee, all members
// including the unchanged sibling, and **deduplicated changed expression excerpts**"
// (`notes/reviewer-design/RENDER-CHECKLIST.md:46`; the prototype's own words are
// `prototype-review.md:49`, "…and 14 deduplicated changed expression excerpts", and
// `README.md:23`, "For a selected family, lead with its own guarantee comparison and complete member
// context, then relevant expressions"). The accepted prototype renders exactly this collection as one
// section headed "Changed expressions in this family" (`accepted-l8-reviewer.html`, the family
// template: `const excerpts=[];const seen=new Set();for(const m of f.members.filter(m =>
// m.expressionState==='changed'))for(const e of m.expressions){const key=e.path+':'+e.after?.start;
// if(!seen.has(key)){seen.add(key);excerpts.push(e);}}`), and its dedup is load-bearing rather than
// decorative: the family this surface actually serves records **4 changed expression rows over 2
// distinct addresses**, so a collection that merely concatenated the member rows would present 4
// rows for 2 excerpts and read as a family with twice the changed expressions it has.
//
// WHAT A "CHANGED EXPRESSION" IS, AND WHAT IT IS NOT. The one measured fact available on a member's
// realization claims is the read's own `resolution` of the address the claim names, and the shipped
// vocabulary has seven values (`models/knowledge/read.py::ANCHOR_RESOLUTIONS`). This module
// partitions them exactly the way the attribution owner already partitions them
// (`application/review_attribution.py`: "``exact_recorded_blob`` is that state and the only value
// this module reads as *resolved*. ``recorded_blob_mismatch`` is the shipped **stale** state … and so
// are ``path_absent``, ``unsupported_locator``, ``entry_not_blob`` and an observation the resolver
// never made"):
//
//   * `exact_recorded_blob` -- RESOLVED. The recorded bytes are at the recorded address. Not changed.
//   * `recorded_blob_mismatch`, `path_absent`, `unsupported_locator`, `entry_not_blob` -- the read did
//     NOT find the recorded bytes at the recorded address. These are the collection's rows, and each
//     row prints the read's own resolution rather than this module's reading of it.
//   * `recorded_object_unavailable`, `not_requested` -- NOT MEASURED. An observation that was not made
//     is never counted as a change; it is counted apart, because "never asked" and "different bytes"
//     are different facts (the same rule the source pane's own `state` follows).
//
// This is the REALIZATION resolution over the claim's recorded address. It is NOT the comparison's
// own measured change set -- that is the source inventory, and on the served family the two disagree
// in the direction that matters: both stale addresses resolve as `recorded_blob_mismatch` while
// neither is a changed path of the comparison's four-path change set. A collection that let those two
// notions pass for each other would be the exact lie this master keeps refusing, so the verdict
// sentence below names which of the two it counted, and every row says where the address stands in
// the comparison's own set.
//
// WHAT THE DEDUP KEY IS. The prototype's key is the excerpt's address (`path + ':' + start`). The
// claims this surface receives carry no line range of their own -- the range is inside the read's own
// `detail` sentence -- so the address identity used here is the path together with the **recorded**
// source identity the claim names. Two claims at one path whose recorded bytes differ are two different
// excerpts and stay two rows; two claims that name the same path and the same recorded bytes are one
// excerpt recorded under more than one member revision (or under more than one role), and they collapse
// into one row that names every member and role that recorded it.
//
// WHY THE OBSERVED IDENTITY IS NOT PART OF THE KEY, MEASURED. The observed identity is what the read
// FOUND at the address in one side's tree, so for a divergent address it is not one value but one value
// per side: the captured `familyReview.walkFinal` body records `src/batch.py` under recorded blob
// `353df144…`, and the before side's read observed that same blob (resolving `exact_recorded_blob`)
// while the after side's read observed `da6bf861…` (resolving `recorded_blob_mismatch`). Folding the
// observed identity into the key therefore split that one address into two keys -- one per side -- so
// the pass that pairs the sides could never find the other one, and the row printed a single side. The
// observed identities are carried PER SIDE instead (`readingsBySide`), beside the resolutions, which is
// where a read result belongs. Keeping `recorded` in the key is what stops over-collapse: it is the only
// thing that keeps two different recorded blobs at one path apart.
// ---------------------------------------------------------------------------------------------

const RESOLVED_ADDRESSES: ReadonlySet<string> = new Set(["exact_recorded_blob"]);
const UNMEASURED_ADDRESSES: ReadonlySet<string> = new Set([
  "recorded_object_unavailable",
  "not_requested",
]);

// One carried membership row of the family: the side it belongs to and the member revision it records.
// The collection is built from THESE and not from the centre's `distinct` list, and that difference is
// measured rather than stylistic: a realization claim's resolution is a fact about the address **in
// that side's tree**, so one member revision can resolve an address on one side and mismatch it on the
// other. The captured `familyReview.walkFinal` body records exactly that -- member revision
// `d24e5187…` records `src/batch.py` under recorded blob `353df144…`, which the before snapshot
// resolves as `exact_recorded_blob` and the after snapshot as `recorded_blob_mismatch`. A collection
// built from one member row per revision (the first-wins `distinct` list) would present that address as
// resolved and hide the change this review exists to show.
export interface FamilyMembershipRow {
  side: ReviewFamilySideName;
  member: ReviewFamilyMember;
}

// The family's carried membership rows, in side order. A4's collection is built from THESE rather than
// from the first-wins `distinct` list, because a resolution is a fact about one side's tree: a member
// recorded on both sides can resolve an address on one and mismatch it on the other, and the captured
// `familyReview.walkFinal` body records exactly that.
function carriedMembership(entry: ReviewFamilyContextEntry): FamilyMembershipRow[] {
  return [
    ...entry.before.members.map((member) => ({ side: "before" as const, member })),
    ...entry.after.members.map((member) => ({ side: "after" as const, member })),
  ];
}

export interface FamilyExcerptOccurrence {
  side: ReviewFamilySideName;
  revision: string;
  label?: string;
}

// What ONE side's read said about an excerpt's address. Both fields are read results, not identity.
export interface FamilyExcerptSideReading {
  side: ReviewFamilySideName;
  // Every resolution that side's read gave this address, deduplicated and sorted. A side that merged a
  // stale claim with an unresolved one says both instead of picking one.
  resolutions: string[];
  // Every source identity that side's read observed at this address, deduplicated and sorted. Empty
  // when the read found no address at all (`path_absent`), which the row prints as "not observed".
  observed: string[];
}

export interface FamilyExpressionExcerpt {
  // The dedup identity, exposed so the rendered collection can be checked against the arithmetic.
  key: string;
  path: string;
  recorded?: string;
  // Each side's own reading of the address, gathered from every claim at it -- including the claims
  // that resolved. A row whose two sides disagree says both, because that disagreement IS the change.
  readingsBySide: FamilyExcerptSideReading[];
  roles: string[];
  details: string[];
  // The carried membership rows that recorded this excerpt as a changed expression, in side order.
  occurrences: FamilyExcerptOccurrence[];
  revisions: string[];
  // How many changed claims collapsed into this excerpt.
  rows: number;
}

export interface FamilyExpressionCollection {
  excerpts: FamilyExpressionExcerpt[];
  // Changed claims across the family's carried membership rows.
  rows: number;
  membershipRows: number;
  bySide: { side: ReviewFamilySideName; rows: number }[];
  membershipRowsWithChanged: number;
  membershipRowsWithoutChanged: number;
  distinctRevisions: number;
  resolved: number;
  unmeasured: number;
}

// The excerpt's identity: its address together with the RECORDED bytes the claim names. The observed
// identity is deliberately absent -- it is what this read found at the address, so it belongs to one
// side and belongs with that side's resolution, not in the identity (see the header note above).
function excerptKey(claim: ReviewFamilyMemberSource): string {
  return `${claim.path ?? ""}\u0000${claim.recorded_source_identity ?? ""}`;
}

// One claim's place in the three-way partition above. A claim with no observed address is not a
// changed expression and not a resolved one either: the server refuses an address without a
// resolution, so a claim carrying neither is "not measured" and is counted there.
function claimClass(claim: ReviewFamilyMemberSource): "changed" | "resolved" | "unmeasured" {
  if (claim.path === undefined || claim.resolution === undefined) return "unmeasured";
  if (RESOLVED_ADDRESSES.has(claim.resolution)) return "resolved";
  if (UNMEASURED_ADDRESSES.has(claim.resolution)) return "unmeasured";
  return "changed";
}

interface ExcerptTally {
  byKey: Map<string, FamilyExpressionExcerpt>;
  occurrences: Map<string, Set<string>>;
  rows: number;
  resolved: number;
  unmeasured: number;
  membershipRowsWithChanged: number;
  bySide: { side: ReviewFamilySideName; rows: number }[];
}

// One changed claim folded into its excerpt: the merged row keeps every role, every read sentence and
// every membership row that named it, so a collapsed row still says who recorded it and how.
function absorbClaim(
  key: string,
  claim: ReviewFamilyMemberSource,
  row: { side: ReviewFamilySideName; member: ReviewFamilyMember },
  tally: ExcerptTally,
): void {
  let excerpt = tally.byKey.get(key);
  if (excerpt === undefined) {
    excerpt = {
      key,
      path: claim.path ?? "",
      recorded: claim.recorded_source_identity,
      readingsBySide: [],
      roles: [],
      details: [],
      occurrences: [],
      revisions: [],
      rows: 0,
    };
    tally.byKey.set(key, excerpt);
    tally.occurrences.set(key, new Set());
  }
  excerpt.rows += 1;
  if (!excerpt.roles.includes(claim.role)) excerpt.roles.push(claim.role);
  if (!excerpt.details.includes(claim.detail)) excerpt.details.push(claim.detail);
  const revision = row.member.invariant_revision_id;
  if (!excerpt.revisions.includes(revision)) excerpt.revisions.push(revision);
  const occurrenceKey = `${row.side}\u0000${revision}`;
  if (!tally.occurrences.get(key)?.has(occurrenceKey)) {
    tally.occurrences.get(key)?.add(occurrenceKey);
    excerpt.occurrences.push({ side: row.side, revision, label: row.member.display_label });
  }
}

// Pass one: every changed claim of every carried membership row, collapsed by the excerpt's identity.
function tallyChangedExcerpts(membership: FamilyMembershipRow[]): ExcerptTally {
  const tally: ExcerptTally = {
    byKey: new Map(),
    occurrences: new Map(),
    rows: 0,
    resolved: 0,
    unmeasured: 0,
    membershipRowsWithChanged: 0,
    bySide: FAMILY_SIDES.map((side) => ({ side, rows: 0 })),
  };
  for (const row of membership) {
    let changedHere = 0;
    for (const claim of row.member.sources) {
      const kind = claimClass(claim);
      if (kind === "resolved") tally.resolved += 1;
      if (kind === "unmeasured") tally.unmeasured += 1;
      if (kind !== "changed") continue;
      tally.rows += 1;
      changedHere += 1;
      const sideTally = tally.bySide.find((entry) => entry.side === row.side);
      if (sideTally !== undefined) sideTally.rows += 1;
      absorbClaim(excerptKey(claim), claim, row, tally);
    }
    if (changedHere > 0) tally.membershipRowsWithChanged += 1;
  }
  return tally;
}

// Pass two: what the read said of those same addresses on EVERY side -- the resolution and the identity
// it observed there -- so an excerpt can say that its two operands disagree rather than printing only
// the side that changed. This is the pairing the per-side fact needs, and it is why the key may not
// carry the observed identity: a divergent address has one observed value per side.
function recordSideReadings(
  membership: FamilyMembershipRow[],
  byKey: Map<string, FamilyExpressionExcerpt>,
): void {
  for (const { side, member } of membership) {
    for (const claim of member.sources) {
      if (claim.path === undefined || claim.resolution === undefined) continue;
      const excerpt = byKey.get(excerptKey(claim));
      if (excerpt === undefined) continue;
      let reading = excerpt.readingsBySide.find((entry) => entry.side === side);
      if (reading === undefined) {
        reading = { side, resolutions: [], observed: [] };
        excerpt.readingsBySide.push(reading);
      }
      if (!reading.resolutions.includes(claim.resolution)) reading.resolutions.push(claim.resolution);
      const observed = claim.observed_source_identity;
      if (observed !== undefined && !reading.observed.includes(observed)) reading.observed.push(observed);
    }
  }
}

// Deterministic order for every collection the pass above filled: sides in the family's own order,
// roles and revisions sorted, excerpts by their own dedup identity.
function orderExcerpts(byKey: Map<string, FamilyExpressionExcerpt>): FamilyExpressionExcerpt[] {
  const sideRank = (side: ReviewFamilySideName) => FAMILY_SIDES.indexOf(side);
  for (const excerpt of byKey.values()) {
    excerpt.readingsBySide.sort((left, right) => sideRank(left.side) - sideRank(right.side));
    for (const reading of excerpt.readingsBySide) reading.observed.sort();
    excerpt.roles.sort();
    excerpt.occurrences.sort(
      (left, right) => sideRank(left.side) - sideRank(right.side) || left.revision.localeCompare(right.revision),
    );
    excerpt.revisions.sort();
  }
  return [...byKey.values()].sort((left, right) => left.key.localeCompare(right.key));
}

export function familyExpressionExcerpts(
  membership: FamilyMembershipRow[],
): FamilyExpressionCollection {
  const tally = tallyChangedExcerpts(membership);
  recordSideReadings(membership, tally.byKey);
  const revisions = new Set(membership.map((row) => row.member.invariant_revision_id));
  return {
    excerpts: orderExcerpts(tally.byKey),
    rows: tally.rows,
    membershipRows: membership.length,
    bySide: tally.bySide,
    membershipRowsWithChanged: tally.membershipRowsWithChanged,
    membershipRowsWithoutChanged: membership.length - tally.membershipRowsWithChanged,
    distinctRevisions: revisions.size,
    resolved: tally.resolved,
    unmeasured: tally.unmeasured,
  };
}

// The sentence the collection leads with: what was counted, on which of the two notions, and what the
// dedup removed. It is one string built from the arithmetic so the reader never has to add up rows.
function familyExpressionVerdict(collection: FamilyExpressionCollection): string {
  const { rows, excerpts, membershipRows, distinctRevisions } = collection;
  const distinct = excerpts.length;
  const removed = rows - distinct;
  const sides = collection.bySide.map((entry) => `${entry.side} ${entry.rows}`).join(" + ");
  if (rows === 0) {
    return (
      `no carried membership row of this family recorded a changed expression: all ${collection.resolved} ` +
      `addressed realization claim(s) carried here resolved to their recorded bytes. A member whose ` +
      "expressions did not change is still presented above, and selecting one shows its unchanged expressions."
    );
  }
  const collapse =
    removed === 0
      ? `${rows} changed expression row(s) across the family's ${membershipRows} carried membership row(s) ` +
        `(${sides}; ${distinctRevisions} distinct member revision(s)) are ${distinct} distinct excerpt(s): ` +
        "no two rows named one address with the same recorded bytes."
      : `${rows} changed expression row(s) across the family's ${membershipRows} carried membership row(s) ` +
        `(${sides}; ${distinctRevisions} distinct member revision(s)) collapse to ${distinct} distinct ` +
        `excerpt(s): ${removed} row(s) named an address and recorded bytes another row already named, and ` +
        "each such row is listed once below with every membership row that recorded it.";
  const membership =
    collection.membershipRowsWithoutChanged === 0
      ? `every one of the ${membershipRows} carried membership row(s) recorded at least one changed expression.`
      : `${collection.membershipRowsWithChanged} of the ${membershipRows} carried membership row(s) recorded a ` +
        `changed expression and ${collection.membershipRowsWithoutChanged} recorded none; the roster above keeps ` +
        "all of them, and a member with none shows its unchanged expressions when it is selected.";
  const notion =
    "Changed here is the read's own resolution of the address each claim names, on the side that resolved " +
    "it: the recorded bytes were not the bytes at that address in that side's tree — the stale state " +
    "(recorded_blob_mismatch) and the unresolved states (path_absent, unsupported_locator, entry_not_blob). " +
    "A resolution is a fact about one side, so the same member revision can resolve an address on one side " +
    "and mismatch it on the other: every row below prints what each side's read made of its address — the " +
    "resolution that side's claim carried — so an address both sides carried prints both readings whenever " +
    "they differ, and an address only one side carried prints that side's alone. This is the realization " +
    "resolution, not " +
    "the comparison's own measured change set, which the source explorer below reports separately; each row " +
    "says where its address stands in that set.";
  const unmeasured =
    collection.unmeasured === 0
      ? "no claim was left unmeasured."
      : `${collection.unmeasured} claim(s) this read did not measure (recorded_object_unavailable, not_requested) ` +
        "are counted apart and are not called changed.";
  return `${collapse} ${membership} ${notion} ${unmeasured}`;
}

// One excerpt: the address, the role(s) recorded for it, what each side's read resolved it as, and every
// membership row that recorded it. The address opens in the complete source explorer when the explorer
// lists it; when it does not, the row says which of the two facts that is -- the same two sentences the
// member-level attribution uses, from the same function.
function FamilyExpressionRow({
  excerpt,
  listed,
  listedPartial,
  onOpenPath,
}: {
  excerpt: FamilyExpressionExcerpt;
  listed: Set<string>;
  listedPartial: boolean;
  onOpenPath: (path: string) => void;
}) {
  return (
    <li
      data-testid="review-center-family-expression"
      data-path={excerpt.path}
      data-dedup-key={excerpt.key}
      data-collapsed-rows={excerpt.rows}
      data-membership-rows={excerpt.occurrences.length}
      data-member-revisions={excerpt.revisions.join(",")}
      data-sides={excerpt.readingsBySide.map((entry) => entry.side).join(",")}
    >
      {listedOrPlainPath(excerpt.path, onOpenPath, listed)}
      {" "}
      · role(s): {excerpt.roles.join(", ")}
      <div className={muted} data-testid="review-center-family-expression-resolution">
        this read resolved it as:{" "}
        {excerpt.readingsBySide
          .map((entry) => `${entry.side} ${entry.resolutions.join(" and ")}`)
          .join(" · ")}
      </div>
      <div className={muted}>
        recorded under {excerpt.occurrences.length} carried membership row(s) over{" "}
        {excerpt.revisions.length} distinct member revision(s):{" "}
        {excerpt.occurrences
          .map((occurrence) => occurrenceName(occurrence))
          .join(", ")}{" "}
        · {excerpt.rows} changed expression row(s) collapsed into this excerpt
      </div>
      <div className={muted}>
        recorded source identity: {excerpt.recorded ?? "not recorded"} · observed source identity:{" "}
        {excerpt.readingsBySide
          .map((entry) => `${entry.side} ${entry.observed.join(" and ") || "not observed"}`)
          .join(" · ")}
      </div>
      {excerpt.details.map((detail) => (
        <div className={muted} key={detail}>
          {detail}
        </div>
      ))}
      {listed.has(excerpt.path) ? (
        <div className={muted}>
          this path is a changed path of the comparison&apos;s measured change set.
        </div>
      ) : (
        <div className={muted}>{unlistedPathNote(listedPartial)}</div>
      )}
    </li>
  );
}

// One carried membership row, named so two rows are never confusable. A display label alone is not
// enough and the served family proves it: ICR30-I-1 is recorded as TWO member revisions of this family,
// so "ICR30-I-1 (before)" twice would name two different rows identically. The revision identity is the
// server's own join key, so it is printed beside the label exactly as `MemberIdentity` prints it.
function occurrenceName(occurrence: FamilyExcerptOccurrence): string {
  const label = occurrence.label === undefined ? "" : `${occurrence.label} · `;
  return `${label}${occurrence.revision} (${occurrence.side})`;
}

// The address, as the control that opens it in the shared explorer when that explorer lists it and as
// plain code when it does not -- one shape for both this collection and the member-level attribution.
function listedOrPlainPath(path: string, onOpenPath: (path: string) => void, listed: Set<string>) {
  if (!listed.has(path)) return <code>{path}</code>;
  return (
    <button
      type="button"
      className={linkButton}
      data-testid="review-center-family-expression-open"
      data-path={path}
      onClick={() => onOpenPath(path)}
    >
      {path}
    </button>
  );
}

// A4's collection, mounted in the centre beneath the family's member context. It is the family-level
// answer to "then relevant expressions" and it is deliberately NOT a second copy of each member's own
// claims: it holds one row per distinct excerpt, and it names every membership row that recorded it.
function FamilyExpressionExcerpts({
  membership,
  listed,
  listedPartial,
  onOpenPath,
}: {
  membership: FamilyMembershipRow[];
  listed: Set<string>;
  listedPartial: boolean;
  onOpenPath: (path: string) => void;
}) {
  const collection = familyExpressionExcerpts(membership);
  return (
    <div className={card} data-testid="review-center-family-expressions">
      <h3 className={sectionLabel}>Changed expression excerpts in this family</h3>
      <p className={muted} data-testid="review-center-family-expressions-verdict">
        {familyExpressionVerdict(collection)}
      </p>
      {collection.excerpts.length ? (
        <ul className={rows}>
          {collection.excerpts.map((excerpt) => (
            <FamilyExpressionRow
              key={excerpt.key}
              excerpt={excerpt}
              listed={listed}
              listedPartial={listedPartial}
              onOpenPath={onOpenPath}
            />
          ))}
        </ul>
      ) : (
        <p className={muted} data-testid="review-center-family-expressions-none">
          this family&apos;s carried membership rows recorded no changed expression, so no excerpt is
          listed here. Nothing is missing from the collection: it is empty because every addressed claim
          this read measured resolved to its recorded bytes.
        </p>
      )}
    </div>
  );
}
// Whether every roster this family context names was carried whole. It is the SAME predicate the tree
// uses to decide whether it may call a roster complete, and it is the read owner's own `complete`
// flag rather than "some rows happen to be here".
function membersComplete(entry: ReviewFamilyContextEntry): boolean {
  return FAMILY_SIDES.every(
    (side) => entry[side].page === undefined || entry[side].page.complete,
  );
}

// The member block's heading. "Complete" is a claim about the recorded context and is printed only
// when the entry really is complete; a bounded or partial context says so in its own heading
// (L24 fix round 1, V2).
function memberContextHeading(entry: ReviewFamilyContextEntry): string {
  if (entry.state === "recorded" && membersComplete(entry)) {
    return "Complete recorded member context";
  }
  return "Recorded member context (partial)";
}

// The member block's counts, decided from the read owner's own counts and the pages' own
// completeness, never from the rows this page happens to carry. The two populations are printed
// apart for the same reason `RosterLine` prints them apart: `members_total` is what the read
// measured, and this page's share of it is what the reader is looking at.
function memberContextCounts(entry: ReviewFamilyContextEntry, carriedCarried: number): string {
  const recorded = FAMILY_SIDES.map((side) => entry[side]).filter(
    (side) => side.state === "recorded",
  );
  if (recorded.length === 0) return `no snapshot records a family revision for this family: ${entry.detail}`;
  const measured = recorded.reduce((total, side) => total + side.members_total, 0);
  const perSide = recorded.map((side) => `${side.side} ${side.members_total}`).join(" + ");
  const head = `${measured} recorded membership row(s) measured by the read across ${recorded.length} recorded side(s) (${perSide}); this page carried ${carriedCarried} member row(s) of them`;
  return membersComplete(entry) ? head : `${head} · the continuations beside the bounded rosters reach the rest`;
}

// One family's context in the CENTRAL reading path: its guarantee comparison, then its member
// context. The roster lines and the continuation controls are the SAME components the tree mounts --
// one implementation each -- so the centre states the owner's own two measures and reaches the rest
// of a bounded walk exactly as the tree does.
// The family's complete recorded member context: the owner's own counts, both roster lines, every
// distinct member revision with its statement, and the bounded walk's continuation. It is one card of
// the family column, extracted rather than inlined so the family column reads as the three things it
// composes -- guarantee, member context, expressions.
function FamilyMemberContext({
  entry,
  distinct,
  onOpenMember,
  onRosterNext,
}: {
  entry: ReviewFamilyContextEntry;
  distinct: ReviewFamilyMember[];
  onOpenMember: (memberRevisionId: string) => void;
  onRosterNext: (familyId: string, side: ReviewFamilySideName, continuation: string) => void;
}) {
  return (
    <div className={card} data-testid="review-center-family-members">
      <h3 className={sectionLabel} data-testid="review-center-member-heading">
        {memberContextHeading(entry)}
      </h3>
      <p className={muted} data-testid="review-center-member-counts">
        {memberContextCounts(entry, distinct.length)}
      </p>
      <p className={muted} data-testid="review-center-member-distinct">
        {distinct.length} distinct member revision(s) among the membership rows this page carried
      </p>
      {FAMILY_SIDES.map((side) => (
        <Fragment key={side}>
          <RosterLine side={entry[side]} testid="review-center-roster" />
        </Fragment>
      ))}
      <ul className={rows}>
        {distinct.map((member) => (
          <li key={member.invariant_revision_id}>
            <button
              type="button"
              className={linkButton}
              data-testid="review-center-open-member"
              data-revision={member.invariant_revision_id}
              onClick={() => onOpenMember(member.invariant_revision_id)}
            >
              {member.display_label ?? member.invariant_revision_id}
            </button>
            {member.state === "recorded" ? (
              <div className={prose}>{member.statement}</div>
            ) : (
              <div className={muted}>statement not carried on this page ({member.state})</div>
            )}
          </li>
        ))}
      </ul>
      {distinct.length === 0 ? (
        <p className={muted} data-testid="review-center-family-empty">
          {emptyRosterSentence(entry)}
        </p>
      ) : null}
      <RosterNext entry={entry} onRosterNext={onRosterNext} testid="review-center-roster-next" />
    </div>
  );
}

function FamilyCenter({
  entry,
  layout,
  listed,
  listedPartial,
  onOpenMember,
  onOpenPath,
  onRosterNext,
}: {
  entry: ReviewFamilyContextEntry;
  layout: DiffLayout;
  listed: Set<string>;
  listedPartial: boolean;
  onOpenMember: (memberRevisionId: string) => void;
  onOpenPath: (path: string) => void;
  onRosterNext: (familyId: string, side: ReviewFamilySideName, continuation: string) => void;
}) {
  const members = [...entry.before.members, ...entry.after.members];
  const distinct = [...new Map(members.map((m) => [m.invariant_revision_id, m])).values()];
  return (
    <section className={shell} data-testid="review-center-family" data-family={entry.family_id}>
      <div>
        <h2 className={sectionLabel}>
          Family {entry.display_label ?? entry.family_id}
        </h2>
        <p className={muted} data-testid="review-center-family-state" data-family-state={entry.state}>
          {entry.state}: {entry.detail}
        </p>
        <p className={muted} data-testid="review-center-family-selection">
          {entry.selection.state}: {entry.selection.statement}
        </p>
      </div>
      <GuaranteeComparisonBlock entry={entry} layout={layout} />
      <FamilyMemberContext
        entry={entry}
        distinct={distinct}
        onOpenMember={onOpenMember}
        onRosterNext={onRosterNext}
      />
      {/* A4's third half. The design's order for a selected family is its own guarantee comparison,
          its complete member context, and then the relevant expressions -- so the collection sits
          after the roster and before the complete source explorer, which every selection shares. */}
      <FamilyExpressionExcerpts
        membership={carriedMembership(entry)}
        listed={listed}
        listedPartial={listedPartial}
        onOpenPath={onOpenPath}
      />
    </section>
  );
}

// The five independent facts, each stated from its own owner's value and from nothing else. This is
// the packet's "statement, membership/realization, source/test and guarantee changes are independent
// displayed facts": the strip exists so a reader can see that a member change produced no guarantee
// revision and no assessment, rather than having to infer it from what is absent further down.
function IndependentFacts({
  entry,
  member,
  payload,
}: {
  entry: ReviewFamilyContextEntry;
  member: ReviewFamilyMember;
  payload: ReviewPayload;
}) {
  const guarantee = guaranteeComparison(entry);
  const before = entry.before.members.find(
    (candidate) => candidate.invariant_revision_id === member.invariant_revision_id,
  );
  const after = entry.after.members.find(
    (candidate) => candidate.invariant_revision_id === member.invariant_revision_id,
  );
  const statement = memberComparison(before, after);
  const sides = [before ? "before" : null, after ? "after" : null].filter(
    (side): side is string => side !== null,
  );
  const attributed = payload.source.locations.filter(
    (location) => location.invariant_revision_id === member.invariant_revision_id,
  );
  const byChange = (state: string) =>
    attributed.filter((location) => location.change_state === state).length;
  const assessments = payload.evidence.assessments.filter((assessment) =>
    (assessment.applicability?.subject_revision_ids ?? []).includes(member.invariant_revision_id),
  );
  const guaranteeFact =
    guarantee.kind === "unchanged_revision"
      ? "unchanged — one authored revision stands behind both sides"
      : guarantee.kind === "identical_text"
        ? "identical text on two distinct authored revisions"
        : guarantee.kind === "changed"
          ? "changed — the family authored a new guarantee text"
          : guarantee.kind === "one_sided"
            ? `recorded on the ${guarantee.side} snapshot only; no comparison was made`
            : "not compared — neither snapshot selected a family revision";
  const statementFact =
    statement.kind === "unchanged_revision"
      ? "unchanged — both snapshots record the same member revision"
      : statement.kind === "changed"
        ? "changed — two distinct member revisions are recorded"
        : statement.kind === "one_sided"
          ? "recorded on one snapshot only; no comparison was made"
          : "not carried on this page, so nothing is compared";
  return (
    <ul className={rows} data-testid="review-center-facts">
      <li data-fact="guarantee">guarantee: {guaranteeFact}</li>
      <li data-fact="statement">member statement: {statementFact}</li>
      <li data-fact="membership">
        membership: recorded on the {sides.join(" and ")} snapshot(s) ·{" "}
        {member.sources.length} recorded realization claim(s) ·{" "}
        {member.other_family_revision_ids.length} other family revision(s) cite this same member
        revision
      </li>
      <li data-fact="source">
        source attribution: {attributed.length} location record(s) name this member revision —{" "}
        {byChange("changed")} changed, {byChange("unchanged")} unchanged,{" "}
        {byChange("not_selected")} not selected
      </li>
      <li data-fact="assessment">
        authored judgment: {assessments.length} assessment(s) in this payload are bound to this member
        revision. No member, membership or guarantee change creates one.
      </li>
    </ul>
  );
}

function MemberCenter({
  entry,
  member,
  layout,
  payload,
  listed,
  onOpenPath,
  onRosterNext,
}: {
  entry: ReviewFamilyContextEntry;
  member: ReviewFamilyMember;
  layout: DiffLayout;
  payload: ReviewPayload;
  listed: Set<string>;
  onOpenPath: (path: string) => void;
  onRosterNext: (familyId: string, side: ReviewFamilySideName, continuation: string) => void;
}) {
  const before = entry.before.members.find(
    (candidate) => candidate.invariant_revision_id === member.invariant_revision_id,
  );
  const after = entry.after.members.find(
    (candidate) => candidate.invariant_revision_id === member.invariant_revision_id,
  );
  return (
    <section className={shell} data-testid="review-center-member" data-family={entry.family_id}>
      {/* The family context is retained above the member, which is the packet's "member selection
          keeps the family context": the guarantee the statement is about is on screen with it. */}
      <div>
        <h2 className={sectionLabel}>
          Member {member.display_label ?? member.invariant_revision_id}
        </h2>
        <p className={muted} data-testid="review-center-member-family">
          in family {entry.display_label ?? entry.family_id} · family revision{" "}
          {entry.after.family_revision_id ?? entry.before.family_revision_id ?? "none selected"}
        </p>
      </div>
      <GuaranteeComparisonBlock entry={entry} layout={layout} />
      <div className={card}>
        <h3 className={sectionLabel}>Independent facts of this review</h3>
        <IndependentFacts entry={entry} member={member} payload={payload} />
      </div>
      <div className={card}>
        <h3 className={sectionLabel}>Selected intent</h3>
        {memberStatementBlock(entry, before, after, layout)}
      </div>
      <div className={card}>
        <h3 className={sectionLabel}>Linked expressions</h3>
        <RealizationClaims member={member} />
        <AttributedPaths
          locations={payload.source.locations}
          member={member}
          listed={listed}
          listedPartial={payload.source.inventory.partial}
          onOpenPath={onOpenPath}
        />
      </div>
      <div className={card}>
        <h3 className={sectionLabel}>Execution evidence and authored assessment</h3>
        <EvidenceBlock evidence={payload.evidence} member={member} />
      </div>
      {/* The bounded walk is reachable from here too: a reader who selected a member whose side's
          content was not carried on this page is exactly the reader who needs the continuation, and
          the row's own note says so. Same component, same cursor, same handler as the tree's. */}
      <RosterNext entry={entry} onRosterNext={onRosterNext} testid="review-center-roster-next" />
    </section>
  );
}

export function FamilyReviewCenter({
  payload,
  selection,
  layout,
  onLayout,
  fullFile,
  onFullFile,
  onOpenMember,
  onRosterNext,
  openPath,
  onOpenPath,
  onOpenFromCenter,
}: {
  payload: ReviewPayload;
  selection: FamilySelection | null;
  layout: DiffLayout;
  onLayout: (next: DiffLayout) => void;
  fullFile: boolean;
  onFullFile: (next: boolean) => void;
  onOpenMember: (familyId: string, memberRevisionId: string) => void;
  onRosterNext: (familyId: string, side: ReviewFamilySideName, continuation: string) => void;
  openPath: string | null;
  onOpenPath: (path: string | null) => void;
  onOpenFromCenter: (path: string) => void;
}) {
  const context = payload.family_context;
  // One owner for "which paths this comparison's own inventory lists": the member-level attribution
  // and the family-level excerpt collection both read this set and both state the same thing about a
  // path that is not in it.
  const listed = new Set(payload.source.inventory.entries.map((entry) => entry.path));
  const entry =
    selection === null
      ? undefined
      : context?.entries.find((candidate) => candidate.family_id === selection.familyId);
  const member =
    entry === undefined || selection?.memberRevisionId === undefined
      ? undefined
      : [...entry.before.members, ...entry.after.members].find(
          (candidate) => candidate.invariant_revision_id === selection.memberRevisionId,
        );

  return (
    <div
      data-testid="review-center"
      data-selection-kind={selection === null ? "none" : member ? "member" : "family"}
    >
      {entry === undefined ? (
        <UnselectedCenter />
      ) : member !== undefined ? (
        <MemberCenter
          entry={entry}
          member={member}
          layout={layout}
          payload={payload}
          listed={listed}
          onOpenPath={onOpenFromCenter}
          onRosterNext={onRosterNext}
        />
      ) : (
        <FamilyCenter
          entry={entry}
          layout={layout}
          listed={listed}
          listedPartial={payload.source.inventory.partial}
          onOpenMember={(revision) => onOpenMember(entry.family_id, revision)}
          onOpenPath={onOpenFromCenter}
          onRosterNext={onRosterNext}
        />
      )}
      <SourceExplorer
        inventory={payload.source.inventory}
        repo={payload.candidate.repository_id}
        master={payload.candidate.master}
        leaf={payload.candidate.leaf_id}
        layout={layout}
        onLayout={onLayout}
        fullFile={fullFile}
        onFullFile={onFullFile}
        open={openPath}
        onOpen={onOpenPath}
      />
    </div>
  );
}

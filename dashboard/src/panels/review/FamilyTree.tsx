// The family tree: recorded families are the semantic parents of the review population.
//
// WHAT IT SHOWS, AND WHY EACH LEVEL IS SEPARATE. A family label is a display fact; beneath it sits the
// family revision's own independently authored joint guarantee, printed whole and wrapped; beneath
// that sit the complete member statements of the roster this page carried -- unchanged siblings
// included, because a member that did not change is still part of what the guarantee is about.
// Nothing here is derived from anything else: the guarantee is the family owner's stored text, a
// member statement is that member revision's stored text, and no level concludes anything about the
// levels beside it.
//
// A TREE IS A PRESENTATION OF RECORDED RELATIONS, NOT A CLAIM THAT THE GRAPH IS SINGLE-PARENT. The
// same invariant revision recorded under two families appears beneath each of them (that is what the
// server publishes `other_family_revision_ids` for), and it is the SAME canonical revision in both
// places rather than a copy: the row is keyed by the member revision identity, and the surface never
// counts a repeated membership as another subject.
//
// THE FIVE CONTEXT STATES ARE NOT ONE STATE. `recorded` and `partial` composed a family; the
// `no_family_recorded` destination states a *measured* zero; the uninitialized/unavailable channel
// states that no recorded scope was read, which is attribution unknown and never silently grouped as
// "no family"; `no_subject_selected` says this review asked about no subject at all. Each renders its
// own sentence, taken from the server's own value where the server sent one.
//
// SELECTION. A family's guarantee control opens that family's review; a member control opens that
// member's review with the family context retained. The current selection is exposed on the tree and
// on each node (`aria-current`), the nodes are real buttons in one roving-focus group, and the arrow
// keys move within it -- so the whole tree is traversable without a mouse.

import { Fragment } from "react";

import { css, cx } from "../../../styled-system/css";
import type {
  ReviewFamilyContext,
  ReviewFamilyContextEntry,
  ReviewFamilyGuarantee,
  ReviewFamilyMember,
  ReviewFamilyRevisionContext,
  ReviewFamilyRosterPage,
  ReviewFamilySideName,
} from "../../data/review";
import { FAMILY_SIDES } from "../../data/review";

// What one tree node addresses: a family's own guarantee review, or one member revision inside it.
export interface FamilySelection {
  familyId: string;
  memberRevisionId?: string;
}

const shell = css({
  background: "bgPanel",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "3px",
  padding: "0.6rem 0.7rem",
  minWidth: "0",
});

const sectionLabel = css({
  color: "cyan",
  fontSize: "0.72rem",
  letterSpacing: "0.1em",
  textTransform: "uppercase",
  margin: "0",
});

const searchInput = css({
  width: "100%",
  background: "bg",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  color: "ink",
  font: "inherit",
  padding: "0.25rem 0.35rem",
  _focusVisible: { outline: "1px solid var(--amber)", outlineOffset: "1px" },
});

const muted = css({ color: "muted", fontSize: "0.78rem", margin: "0.2rem 0" });

const statements = css({ margin: "0.1rem 0", paddingLeft: "1.1rem", listStyle: "none" });

// The tree's one selectable control. Selection is the established amber treatment: the current node
// carries the amber wash, and the keyboard focus ring is the same amber, so which node is current and
// which has focus are both visible without either being inferred from the other.
//
// THE WASH IS MIXED IN `oklab`, THROUGH THE TOKEN (register B1). These two used to be raw
// `oklch(0.82 0.16 75 / …)` literals -- the channels of `--amber` copied by hand, mixed by hand. That
// is the one place in the review surface that did not use AR's own language, which every other
// selection wash in this dashboard states as `color-mix(in oklab, var(--amber) N%, transparent)`
// (`panels/changeset/ChangeSetViewer.tsx:120-121`, `panels/file-viewer/FileTree.tsx:34`,
// `panels/file-viewer/FileViewer.tsx:108`), and `styles/tokens.css:20` states the contract it broke:
// "no raw color literals here: every color is a token var". The accepted design's own finding P2-1 is
// why the interpolation space matters at all: an `oklch` mix interpolates the HUE, which is how the
// selected row came out `oklch(0.324 0.048 215)` -- visibly teal -- before the fix. Mixing in `oklab`
// keeps the wash the amber it is named for, and taking the colour from `var(--amber)` keeps it amber
// when the token moves.
const AMBER_WASH = (percent: number) => `color-mix(in oklab, var(--amber) ${percent}%, transparent)`;

const node = css({
  display: "block",
  width: "100%",
  background: "transparent",
  border: "none",
  borderLeftWidth: "2px",
  borderLeftStyle: "solid",
  borderLeftColor: "transparent",
  color: "ink",
  cursor: "pointer",
  font: "inherit",
  padding: "0.15rem 0.35rem",
  textAlign: "left",
  borderRadius: "2px",
  _focusVisible: { outline: "1px solid var(--amber)", outlineOffset: "1px" },
  _hover: { background: AMBER_WASH(8) },
});

const current = css({
  background: AMBER_WASH(16),
  borderLeftColor: "amber",
});

const guaranteeText = css({
  whiteSpace: "pre-wrap",
  margin: "0.15rem 0",
  fontSize: "0.84rem",
  borderLeftWidth: "1px",
  borderLeftStyle: "solid",
  borderLeftColor: "grid",
  paddingLeft: "0.5rem",
});

const memberText = css({
  whiteSpace: "pre-wrap",
  margin: "0.1rem 0 0.1rem 1.45rem",
  fontSize: "0.82rem",
  color: "ink",
});

const sideTag = css({
  color: "amber",
  fontSize: "0.7rem",
  letterSpacing: "0.06em",
  textTransform: "uppercase",
});

const familyBlock = css({
  marginBottom: "0.7rem",
  borderTopWidth: "1px",
  borderTopStyle: "solid",
  borderTopColor: "grid",
  paddingTop: "0.5rem",
});

// One member row as the union of both sides presents it: the sides a member revision was recorded on,
// and the member values those sides carried. A member recorded on both sides is ONE row, because the
// canonical identity is the revision and not the association.
interface MemberRow {
  invariantRevisionId: string;
  sides: ReviewFamilySideName[];
  member: ReviewFamilyMember;
}

// The roster union for one family context. A revision recorded on both sides yields one row carrying
// both sides, so a repeated membership never inflates the tree; the row's own `member` value is the
// after side's when it carried content, because that is the revision the reader is looking at now,
// and otherwise the before side's.
export function memberRows(entry: ReviewFamilyContextEntry): MemberRow[] {
  const byRevision = new Map<string, MemberRow>();
  for (const side of FAMILY_SIDES) {
    for (const member of entry[side].members) {
      const existing = byRevision.get(member.invariant_revision_id);
      if (existing === undefined) {
        byRevision.set(member.invariant_revision_id, {
          invariantRevisionId: member.invariant_revision_id,
          sides: [side],
          member,
        });
        continue;
      }
      existing.sides.push(side);
      if (member.state === "recorded") existing.member = member;
    }
  }
  return [...byRevision.values()];
}

// One side's own statement about itself, printed with the side's name and its exact state. It is
// drawn for every side that is not a complete roster, because each of those states is a different
// fact a reader has to be told, and none of them is an empty roster.
function SideStates({ entry }: { entry: ReviewFamilyContextEntry }) {
  return (
    <>
      {FAMILY_SIDES.map((side) => {
        const context = entry[side];
        if (context.state === "recorded") return null;
        return (
          <p
            key={side}
            className={muted}
            data-testid="review-family-side"
            data-side={side}
            data-side-state={context.state}
          >
            {side}: {context.state} — {context.detail}
          </p>
        );
      })}
    </>
  );
}

// How much recorded history of this family each snapshot holds. It is the family owner's own revision
// list, which is a LARGER population than the revisions the selection reached: a family revision that
// cites no member is recorded and may not have been chosen, and a sentence that counted only the
// selected population would be false about the store.
function FamilyHistory({ entry }: { entry: ReviewFamilyContextEntry }) {
  return (
    <>
      {FAMILY_SIDES.map((side) => {
        const recorded = entry[side].recorded_revision_ids;
        if (recorded.length === 0) return null;
        const selected = entry[side].family_revision_id;
        return (
          <p key={side} className={muted} data-testid="review-family-history" data-side={side}>
            {recorded.length} recorded revision(s) of this family in the {side} snapshot
            {selected === undefined
              ? "; this review selected none of them"
              : `; this review selected ${selected}`}
          </p>
        );
      })}
    </>
  );
}

// One side's roster page, stated in the read owner's own two measures and nothing else.
//
// The two are DIFFERENT populations and the sentence keeps them apart: `counts.primary_items_*` counts
// the family revision's whole recorded selection -- the membership rows, the revision itself, its
// realization claims and its advertised expansions all contribute, and the owner's own count value
// carries each of those totals separately -- while `members_total` is the owner's count of that
// revision's recorded membership rows alone. A page that carried every membership row can therefore
// still report items remaining, and a sentence that read the item remainder as "more members remain"
// would be false about the store. Both numbers are printed with the population each one measured.
export function RosterLine({
  side,
  testid = "review-family-roster",
}: {
  side: ReviewFamilyRevisionContext;
  testid?: string;
}) {
  const page = side.page;
  if (page === undefined) return null;
  const counts = page.counts;
  return (
    <p
      className={muted}
      data-testid={testid}
      data-roster-complete={page.complete ? "true" : "false"}
    >
      {side.side} roster ({page.state}): this page carried {counts.primary_items_returned} of{" "}
      {counts.primary_items_total} item(s) of this family revision&apos;s recorded selection,{" "}
      {counts.primary_items_remaining} remaining. The selected family revision records{" "}
      {side.members_total} membership row(s) and this page carried {side.members.length} of them
      {completionNote(page)}
      {counts.unresolved_anchor_total
        ? ` · ${counts.unresolved_anchor_total} unresolved anchor(s) in this selection`
        : ""}{" "}
      · {page.scope.join(" · ") || "no scope recorded"}
    </p>
  );
}

// What a COMPLETE page's own completeness says, which depends on which page it is. ``complete`` is the
// read walk's flag, not the page's: a walk the read took in one page IS the whole selection, while the
// final page of a multi-page walk is only the last position in it -- the pages before it carried the
// rows this one did not. Saying "the page is the whole selection" for the second was false about the
// store, and it became reachable the moment the walk's completion guard was corrected to let a final
// page exist at all (ICR-L24 fix round 3, V9).
function completionNote(page: ReviewFamilyRosterPage): string {
  if (!page.complete) return "";
  return page.state === "first_page"
    ? " — the page is the whole selection"
    : " — this page completes the walk; the pages before it carried the rows this one did not";
}

// One recorded side's own carried-of-measured count, in the owner's two numbers.
function carriedOf(side: ReviewFamilyRevisionContext): string {
  return `${side.side}: ${side.members.length} of the ${side.members_total} recorded membership row(s) it measured`;
}

// What may be said when this page carried no member row at all. Three facts, kept apart:
//
//   * neither snapshot recorded a family revision -> there is no roster to describe;
//   * the read measured zero memberships -> the measured zero, and ONLY here may that be said;
//   * the read measured rows this page did not carry -> a bounded page, stated as page-scoped.
export function emptyRosterSentence(entry: ReviewFamilyContextEntry): string {
  const recorded = FAMILY_SIDES.map((side) => entry[side]).filter(
    (side) => side.state === "recorded",
  );
  if (recorded.length === 0) return `no member row is carried here: ${entry.detail}`;
  const measured = recorded.reduce((total, side) => total + side.members_total, 0);
  if (measured === 0) {
    return "the read measured zero memberships for the selected family revision: it records 0 recorded membership row(s).";
  }
  const counts = recorded.map(carriedOf).join("; ");
  const bounded = recorded.some((side) => side.page !== undefined && !side.page.complete);
  // No roster is still open when nothing is bounded, so the sentence stops at the page-scoped fact:
  // it does not reach for an explanation of where the other rows went. An explanation that no case
  // exercises is a sentence this leaf cannot prove, and every unpinned user-visible sentence here has
  // already cost a verification round; the row above already prints the owner's own two numbers and
  // the roster line beside it says which page of the walk this one is.
  return bounded
    ? `this page carried no member row — ${counts}. The continuation beside each bounded roster reaches the rows this page did not carry.`
    : `this page carried no member row — ${counts}.`;
}

function RosterLines({ entry }: { entry: ReviewFamilyContextEntry }) {
  return (
    <>
      {FAMILY_SIDES.map((side) => (
        <Fragment key={side}>
          <RosterLine side={entry[side]} />
        </Fragment>
      ))}
    </>
  );
}

// The continuation controls for a roster that is a position in a walk rather than the whole of it.
//
// This is where the family collection is walked, and it is the ONLY place: `family_members` is not one
// walk but the set of per-family walks a response composes, so naming it with no cursor earns the
// server's own refusal rather than an arbitrary walk's first page. Each cursor below is the one the
// family context's own roster page published, which is exactly the value the server's refusal action
// sentence tells a reader to present.
export function RosterNext({
  entry,
  onRosterNext,
  testid = "review-family-roster-next",
}: {
  entry: ReviewFamilyContextEntry;
  onRosterNext: (familyId: string, side: ReviewFamilySideName, continuation: string) => void;
  testid?: string;
}) {
  return (
    <>
      {FAMILY_SIDES.map((side) => {
        const page = entry[side].page;
        if (page === undefined || page.complete || page.continuation === undefined) return null;
        return (
          <button
            key={side}
            type="button"
            className={node}
            data-testid={testid}
            data-family={entry.family_id}
            data-side={side}
            data-continuation={page.continuation}
            onClick={() => onRosterNext(entry.family_id, side, page.continuation as string)}
          >
            continue the {side} roster walk of {familyLabel(entry)} at the cursor this page published
          </button>
        );
      })}
    </>
  );
}

function familyLabel(entry: ReviewFamilyContextEntry): string {
  return entry.display_label ?? entry.family_id;
}

// The guarantees this family context may print, each with the side it is and the sentence that says
// so. Three shapes, kept apart: both snapshots selected the SAME revision (one guarantee, labelled as
// both sides' own record); both recorded a revision and they differ (two guarantees, each labelled
// with its side); one side recorded one (one guarantee, labelled with its side, beside the other
// side's own state line). A side that recorded no revision contributes nothing here: it has no
// guarantee to print, and printing its neighbour's text as if it were shared is what this refuses.
function guaranteesOf(
  entry: ReviewFamilyContextEntry,
): { guarantee: ReviewFamilyGuarantee; side: string; note: string }[] {
  const before = entry.before.guarantee;
  const after = entry.after.guarantee;
  if (before === undefined && after === undefined) return [];
  if (before !== undefined && after !== undefined) {
    if (before.revision_id === after.revision_id) {
      return [
        {
          guarantee: before,
          side: "both",
          note: `both snapshots selected this family revision (${before.revision_id}):`,
        },
      ];
    }
    return [
      { guarantee: before, side: "before", note: `before (${before.revision_id}):` },
      { guarantee: after, side: "after", note: `after (${after.revision_id}):` },
    ];
  }
  const one = (before ?? after) as ReviewFamilyGuarantee;
  const side = before !== undefined ? "before" : "after";
  return [{ guarantee: one, side, note: `${side} only (${one.revision_id}):` }];
}

function FamilyGuarantees({ entry }: { entry: ReviewFamilyContextEntry }) {
  const guarantees = guaranteesOf(entry);
  if (!guarantees.length) {
    return (
      <p className={muted} data-testid="review-family-guarantee">
        no family revision was selected for this family, so no guarantee of it may be shown:{" "}
        {entry.detail}
      </p>
    );
  }
  return (
    <>
      {guarantees.map((recorded) => (
        <p
          key={`${recorded.side}:${recorded.guarantee.revision_id}`}
          className={guaranteeText}
          data-testid="review-family-guarantee"
          data-guarantee-side={recorded.side}
          data-guarantee-revision={recorded.guarantee.revision_id}
        >
          <span className={muted}>{recorded.note} </span>
          {recorded.guarantee.joint_guarantee}
        </p>
      ))}
    </>
  );
}

// The recorded heads an ambiguous or broken lineage left the composition unable to choose between,
// each named by its own revision identity. No guarantee is presented as the family's own in that
// state, which is the server's rule and this rendering's.
function FamilyCandidates({ entry }: { entry: ReviewFamilyContextEntry }) {
  if (!entry.candidates.length) return null;
  return (
    <p className={muted} data-testid="review-family-candidates">
      {entry.candidates.length} recorded head(s) this context declined to choose between:{" "}
      {entry.candidates.map((candidate) => candidate.revision_id).join(", ")}
    </p>
  );
}

// One member row: its own selectable control, its statement when this page carried the revision's
// content, and the recorded facts about the membership. A member whose content was not on this page
// says so -- it is never rendered with a blank statement, which would read as a recorded empty one.
// One row's control label and the sentence naming the sides it was recorded on. Both are decided from
// the row's own carried value, so the button text and the membership fact beside it cannot disagree.
function memberLabel(member: ReviewFamilyMember): string {
  const identity = member.display_label ?? member.display_version ?? member.invariant_revision_id;
  return member.display_label && member.display_version
    ? `${identity} · ${member.display_version}`
    : identity;
}

function memberSidesNote(row: MemberRow): string {
  return row.sides.length === 2
    ? "recorded on both snapshots"
    : `recorded on the ${row.sides[0]} snapshot only`;
}

function MemberNode({
  entry,
  row,
  selected,
  onSelect,
}: {
  entry: ReviewFamilyContextEntry;
  row: MemberRow;
  selected: FamilySelection | null;
  onSelect: (selection: FamilySelection) => void;
}) {
  const member = row.member;
  const isCurrent =
    selected?.familyId === entry.family_id &&
    selected?.memberRevisionId === member.invariant_revision_id;
  return (
    <li
      data-testid="review-family-member"
      data-member={member.member_id}
      data-sides={row.sides.join("+")}
    >
      <button
        type="button"
        className={cx(node, isCurrent ? current : undefined)}
        data-tree-node="member"
        data-testid="review-family-member-open"
        data-revision={member.invariant_revision_id}
        aria-current={isCurrent ? "true" : undefined}
        onClick={() =>
          onSelect({ familyId: entry.family_id, memberRevisionId: member.invariant_revision_id })
        }
        onKeyDown={treeArrow}
      >
        {memberLabel(member)}
        {" · "}
        <span className={sideTag}>{row.sides.join("+")}</span>
        {` · ${memberSidesNote(row)}`}
      </button>
      {member.state === "recorded" ? (
        <p className={memberText}>{member.statement}</p>
      ) : (
        <p className={muted} data-testid="review-family-member-state">
          statement not carried on this page ({member.state}): {member.detail}
        </p>
      )}
      {member.other_family_revision_ids.length ? (
        <p className={muted} data-testid="review-family-member-shared">
          this same member revision is recorded under {member.other_family_revision_ids.length} other
          family revision(s): {member.other_family_revision_ids.join(", ")}
        </p>
      ) : null}
    </li>
  );
}

// The complete roster this page carried, or the explicit statement that it carried no row. The second
// sentence is only ever printed for a roster the read really took: a side that recorded no context
// has no page at all and states its own fact above instead.
//
// Every sentence it can print is decided from the READ OWNER'S OWN COUNTS (`members_total`) and the
// page's own `complete` flag, never from "a page exists" (L24 fix round 1, V1). A page that carried
// none of N recorded membership rows is a different fact from a read that measured zero memberships,
// and one sentence containing both was false about the store: the same block printed the owner's
// "records N membership row(s) and this page carried 0 of them" two lines above it.
function MemberRoster({
  entry,
  selected,
  onSelect,
}: {
  entry: ReviewFamilyContextEntry;
  selected: FamilySelection | null;
  onSelect: (selection: FamilySelection) => void;
}) {
  const rows = memberRows(entry);
  if (rows.length) {
    return (
      <ul className={statements}>
        {rows.map((row) => (
          <MemberNode
            key={row.invariantRevisionId}
            entry={entry}
            row={row}
            selected={selected}
            onSelect={onSelect}
          />
        ))}
      </ul>
    );
  }
  return (
    <p className={muted} data-testid="review-family-empty-roster">
      {emptyRosterSentence(entry)}
    </p>
  );
}

// One family: its label, the independently authored joint guarantee, the member statements, and the
// two side facts beside them. The guarantee control and the member controls are separate nodes,
// because they open different reviews.
function FamilyNode({
  entry,
  selected,
  onSelect,
  onRosterNext,
}: {
  entry: ReviewFamilyContextEntry;
  selected: FamilySelection | null;
  onSelect: (selection: FamilySelection) => void;
  onRosterNext: (familyId: string, side: ReviewFamilySideName, continuation: string) => void;
}) {
  const familyCurrent =
    selected?.familyId === entry.family_id && selected.memberRevisionId === undefined;
  return (
    <li
      className={familyBlock}
      data-testid="review-family"
      data-family={entry.family_id}
      data-family-state={entry.state}
    >
      <button
        type="button"
        className={cx(node, familyCurrent ? current : undefined)}
        data-tree-node="family"
        data-testid="review-family-open"
        data-family={entry.family_id}
        aria-current={familyCurrent ? "true" : undefined}
        onClick={() => onSelect({ familyId: entry.family_id })}
        onKeyDown={treeArrow}
      >
        {familyLabel(entry)} · family guarantee review
      </button>
      {entry.display_label !== undefined && entry.label_side !== undefined ? (
        <p className={muted} data-testid="review-family-label-side">
          the label “{entry.display_label}” is the {entry.label_side} snapshot&apos;s recorded label
        </p>
      ) : null}
      <FamilyGuarantees entry={entry} />
      <FamilyCandidates entry={entry} />
      <SideStates entry={entry} />
      <FamilyHistory entry={entry} />
      <RosterLines entry={entry} />
      <MemberRoster entry={entry} selected={selected} onSelect={onSelect} />
      <RosterNext entry={entry} onRosterNext={onRosterNext} />
    </li>
  );
}

// The filter scope line: what the reader's own filter matched, and the explicit statement that it is a
// display filter. It never restates the comparison's totals as if the filter had changed them.
function filterScope(
  query: string,
  shownFamilies: number,
  totalFamilies: number,
  shownMembers: number,
  totalMembers: number,
): string {
  if (query === "") {
    return `${totalFamilies} family context(s) and ${totalMembers} recorded member row(s) are shown; no filter is applied.`;
  }
  return `filter “${query}” matches ${shownFamilies} of ${totalFamilies} family context(s) and ${shownMembers} of ${totalMembers} recorded member row(s). This is a filter on this display only: the comparison's recorded totals are unchanged, and clearing the filter restores every row.`;
}

// One family matches when its label, its id, its guarantee text or any member's label/statement/id
// matches. A match inside a member keeps that member's family and its siblings on screen, which is
// what "search retains the matching family's context" means.
export function familyMatches(entry: ReviewFamilyContextEntry, needle: string): boolean {
  const haystack = [
    entry.family_id,
    entry.display_label ?? "",
    entry.before.guarantee?.joint_guarantee ?? "",
    entry.after.guarantee?.joint_guarantee ?? "",
    ...entry.candidates.map((candidate) => candidate.joint_guarantee),
    ...memberRows(entry).flatMap((row) => [
      row.invariantRevisionId,
      row.member.invariant_revision_id,
      row.member.display_label ?? "",
      row.member.statement ?? "",
    ]),
  ]
    .join("\n")
    .toLowerCase();
  return haystack.includes(needle);
}

// Arrow-key traversal inside the tree: one roving focus group over the selectable nodes, in DOM
// order, so the whole tree is reachable from the keyboard. Enter and Space stay the buttons' own
// activation, which is why selection remains a real button click and this handler moves focus only.
// The handler sits on the buttons themselves rather than on their container: a listener on a plain
// list would give the list an interaction role it does not have and cannot honour.
function treeArrow(event: React.KeyboardEvent<HTMLButtonElement>): void {
  const delta = event.key === "ArrowDown" ? 1 : event.key === "ArrowUp" ? -1 : 0;
  if (delta === 0) return;
  const nodes = Array.from(
    event.currentTarget
      .closest("[data-testid=review-family-list]")
      ?.querySelectorAll<HTMLButtonElement>("[data-tree-node]") ?? [],
  );
  const index = nodes.indexOf(event.currentTarget);
  if (index === -1) return;
  event.preventDefault();
  nodes[(index + delta + nodes.length) % nodes.length]?.focus();
}

function FamilyList({
  shown,
  selected,
  onSelect,
  onRosterNext,
}: {
  shown: ReviewFamilyContextEntry[];
  selected: FamilySelection | null;
  onSelect: (selection: FamilySelection) => void;
  onRosterNext: (familyId: string, side: ReviewFamilySideName, continuation: string) => void;
}) {
  return (
    <ul className={statements} data-testid="review-family-list">
      {shown.map((entry) => (
        <FamilyNode
          key={entry.family_id}
          entry={entry}
          selected={selected}
          onSelect={onSelect}
          onRosterNext={onRosterNext}
        />
      ))}
    </ul>
  );
}

export function FamilyTree({
  context,
  selection,
  onSelect,
  onRosterNext,
  query,
  onQuery,
}: {
  context: ReviewFamilyContext;
  selection: FamilySelection | null;
  onSelect: (selection: FamilySelection) => void;
  onRosterNext: (familyId: string, side: ReviewFamilySideName, continuation: string) => void;
  query: string;
  onQuery: (next: string) => void;
}) {
  const needle = query.trim().toLowerCase();
  const allMembers = context.entries.reduce((total, entry) => total + memberRows(entry).length, 0);
  const shown = context.entries.filter((entry) => needle === "" || familyMatches(entry, needle));
  const shownMembers = shown.reduce((total, entry) => total + memberRows(entry).length, 0);
  const composed = context.entries.length > 0;

  return (
    <section className={shell} data-testid="review-family-tree" data-family-state={context.state}>
      <label className={sectionLabel} htmlFor="review-family-filter">
        Family tree
      </label>
      <input
        id="review-family-filter"
        className={searchInput}
        data-testid="review-family-filter"
        type="search"
        placeholder="filter families, guarantees and members"
        value={query}
        onChange={(event) => onQuery(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape") onQuery("");
        }}
      />
      <p className={muted} data-testid="review-family-filter-scope">
        {filterScope(query, shown.length, context.entries.length, shownMembers, allMembers)}
      </p>
      <p className={muted} data-testid="review-family-context" data-context-state={context.state}>
        {context.state}: {context.detail}
      </p>
      {composed ? (
        <p className={muted} data-testid="review-family-counts">
          {context.families_returned} of {context.families_total} recorded family context(s) composed
          {context.families_remaining
            ? ` · ${context.families_remaining} remaining`
            : " · none remaining"}{" "}
          · {context.membership_rows_total} membership row(s) ·{" "}
          {context.unique_member_revision_total} distinct member revision(s)
        </p>
      ) : null}
      {context.limitations.map((limitation) => (
        <p key={limitation} className={muted} data-testid="review-family-limitation">
          limitation: {limitation}
        </p>
      ))}
      {shown.length ? (
        <FamilyList
          shown={shown}
          selected={selection}
          onSelect={onSelect}
          onRosterNext={onRosterNext}
        />
      ) : (
        <p className={muted} data-testid="review-family-none-shown">
          {composed
            ? `no family context matches “${query}”. The ${context.entries.length} composed context(s) are unchanged by this filter; clear it to see them.`
            : "this review composed no family context, so there is nothing here to filter."}
        </p>
      )}
    </section>
  );
}

// The family-centered review workspace: scope header, family tree, unified central reading path.
//
// WHAT THIS OWNS. The three things the accepted layout treats as one composition: which family or
// member is selected, the reader's display preferences (diff layout, full-file disclosure, and which
// listed path is expanded), and the narrow-screen route from the tree to the selected review. The
// payload's own panes stay in `ReviewSurface`, which mounts this workspace inside the read cycle it
// already owns: one read, one question, one outcome region, one page control.
//
// WHY THE PREFERENCES LIVE HERE AND NOT IN THE COMPONENTS BELOW. ICR-R24@v3 requires the reader's
// full-file disclosure and current selection to survive a diff-layout switch and a change of
// selection. State owned by the tree, by the center or by the explorer would be reset by exactly the
// interaction the requirement is about, so it is lifted to the one component whose lifetime spans
// them. The preferences are display facts only: they change no request and no stored value.
//
// FOCUS. Selecting a linked expression opens the entry in the explorer and the reader can close it
// from there; closing returns focus to the control that opened it. The tree is one roving-focus group
// with arrow-key traversal and exposes the current node, so a keyboard reader can always tell which
// selection is current and which node has focus.

import { useRef, useState } from "react";

import { css } from "../../../styled-system/css";
import type {
  ReviewFamilyContext,
  ReviewFamilySideName,
  ReviewPayload,
  ReviewSelectorKind,
} from "../../data/review";
import { carriedPage } from "../../data/review";
import type { ReviewPageRequest } from "./ReviewReadCycle";
import { FamilyReviewCenter } from "./FamilyReviewCenter";
import { FamilyTree, type FamilySelection } from "./FamilyTree";
import type { DiffLayout } from "./SourceExplorer";

const workspace = css({
  display: "grid",
  gridTemplateColumns: "minmax(0, 22rem) minmax(0, 1fr)",
  gap: "0.75rem",
  alignItems: "start",
  "@media (max-width: 60rem)": { gridTemplateColumns: "minmax(0, 1fr)" },
});

const fullWidth = css({ gridColumn: "1 / -1" });

const shell = css({
  background: "bgPanel",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "3px",
  padding: "0.6rem 0.7rem",
  minWidth: "0",
});

const title = css({
  color: "cyan",
  fontSize: "0.78rem",
  letterSpacing: "0.12em",
  textTransform: "uppercase",
  margin: "0 0 0.2rem",
});

const muted = css({ color: "muted", fontSize: "0.78rem", margin: "0.15rem 0" });

const mono = css({ fontFamily: "mono", fontSize: "0.78rem" });

// The narrow-screen route to the selected review. It is a real button rather than a styled anchor
// because there is no URL to change: it moves focus to the center column, which is what a reader on a
// narrow screen needs after scrolling a long tree.
const jump = css({
  justifySelf: "start",
  background: "transparent",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "amber",
  borderRadius: "2px",
  color: "amber",
  cursor: "pointer",
  font: "inherit",
  fontSize: "0.78rem",
  padding: "0.15rem 0.4rem",
  "@media (min-width: 60.01rem)": { display: "none" },
  _focusVisible: { outline: "1px solid var(--amber)", outlineOffset: "2px" },
});

// The scope/status header: which task context is open, which subject of it, WHICH RECORD the panes
// below are read from, the comparison's mode and exact endpoints, the measured changed-path count and
// the review state. Technical identities are printed because they are what a reader quotes to
// reproduce the read; nothing here summarises what the panes conclude, because they conclude nothing.
function ScopeHeader({
  payload,
  repo,
  master,
  leaf,
  selectorKind,
  selectorId,
  history,
}: {
  payload: ReviewPayload;
  repo: string;
  master: string;
  leaf: string;
  selectorKind?: ReviewSelectorKind;
  selectorId?: string;
  history?: "recorded";
}) {
  const inventory = payload.source.inventory;
  const context = payload.family_context;
  return (
    <header className={`${shell} ${fullWidth}`} data-testid="review-scope-header">
      <h2 className={title}>Complete code and intent review</h2>
      <p className={muted} data-testid="review-scope-task">
        task {repo} · master {master} · leaf {leaf} · subject{" "}
        {selectorKind && selectorId
          ? `${selectorKind} ${selectorId}`
          : "whole task (no subject selected)"}
      </p>
      <p className={muted} data-testid="review-scope-record">
        record: {history === "recorded" ? "the leaf's recorded comparison" : "the live candidate"} ·
        source inventory: {inventory.state}
        {inventory.partial ? " (partial)" : ""} · changed paths listed: {inventory.listed_total}
      </p>
      {payload.comparison ? (
        <p className={muted} data-testid="review-scope-comparison">
          comparison {payload.comparison.reference} · policy {payload.comparison.policy_version} ·
          before {payload.comparison.before_code_tree_id ?? "not recorded"} → after{" "}
          {payload.comparison.after_code_tree_id ?? "not recorded"}
        </p>
      ) : (
        <p className={muted} data-testid="review-scope-comparison">
          no knowledge comparison was made for this read; the complete source change inventory below
          is the review population.
        </p>
      )}
      <p className={muted} data-testid="review-scope-families">
        {context === undefined
          ? "this body carries no family context, so no family reading may be made from it — that is not a measured zero."
          : `${context.state}: ${context.families_returned} of ${context.families_total} recorded family context(s) composed.`}
      </p>
    </header>
  );
}

// Which roster walk the displayed page belongs to. A `family_members` page is a position in ONE
// family revision's walk, and the page's own scope names it; this line prints that scope so a reader
// never has to guess which family the cursor continues.
function RosterWalkNotice({ payload }: { payload: ReviewPayload }) {
  const page = carriedPage(payload);
  if (page === null || page.collection !== "family_members") return null;
  return (
    <p className={`${muted} ${fullWidth}`} data-testid="review-roster-walk">
      this response is a page of one family revision&apos;s roster walk —{" "}
      {page.scope.join(" · ") || "the page published no scope"} ·{" "}
      {page.continued_from
        ? "continued from the cursor this walk published"
        : "the walk's first page"}
      .
    </p>
  );
}

// The body's own statement when it composed no family tree. The three states are rendered apart,
// because they are different facts: a body that carries no family context at all is neither a measured
// zero nor an unavailable read, and the source explorer below is unaffected by all three.
function FamilyNotComposed({ context }: { context: ReviewFamilyContext | undefined }) {
  return (
    <section
      className={shell}
      data-testid="review-family-tree"
      data-family-state={context?.state ?? "absent"}
    >
      <h2 className={title}>Family tree</h2>
      <p
        data-testid="review-family-context"
        data-context-state={context?.state ?? "absent"}
        className={muted}
      >
        {context === undefined
          ? "this body carries no family context. No recorded family scope was read into it, so nothing here is shown as a family population — this is not a measured zero and not an unavailable read, and the complete source explorer below is unaffected."
          : `${context.state}: ${context.detail}`}
      </p>
      {context !== undefined && context.limitations.length ? (
        <p className={muted} data-testid="review-family-limitation">
          {context.limitations.length} recorded limitation(s): {context.limitations.join(" · ")}
        </p>
      ) : null}
    </section>
  );
}

// The tree column: the interactive family tree when this body composed one, and the body's own state
// when it did not.
function composed(context: ReviewFamilyContext | undefined): boolean {
  return context !== undefined && (context.state === "recorded" || context.state === "partial");
}

function FamilyColumn({
  context,
  selection,
  onSelect,
  onRosterNext,
  query,
  onQuery,
}: {
  context: ReviewFamilyContext | undefined;
  selection: FamilySelection | null;
  onSelect: (selection: FamilySelection) => void;
  onRosterNext: (familyId: string, side: ReviewFamilySideName, continuation: string) => void;
  query: string;
  onQuery: (next: string) => void;
}) {
  if (!composed(context)) return <FamilyNotComposed context={context} />;
  return (
    <FamilyTree
      context={context as ReviewFamilyContext}
      selection={selection}
      onSelect={onSelect}
      onRosterNext={onRosterNext}
      query={query}
      onQuery={onQuery}
    />
  );
}

// The narrow-screen route and the centre column it leads to. The route is a real button rather than a
// styled anchor because there is no URL to change: it moves focus to the column below, which is what a
// reader on a narrow screen needs after scrolling a long tree. On a wide layout the column is already
// beside the tree and the control is hidden by the stylesheet.
function SelectionColumn({
  payload,
  selection,
  onSelectMember,
  layout,
  onLayout,
  fullFile,
  onFullFile,
  openPath,
  onOpenPath,
  onOpenFromCenter,
  onRosterNext,
  center,
}: {
  payload: ReviewPayload;
  selection: FamilySelection | null;
  onSelectMember: (selection: FamilySelection) => void;
  layout: DiffLayout;
  onLayout: (next: DiffLayout) => void;
  fullFile: boolean;
  onFullFile: (next: boolean) => void;
  openPath: string | null;
  onOpenPath: (path: string | null) => void;
  onOpenFromCenter: (path: string) => void;
  onRosterNext: (familyId: string, side: ReviewFamilySideName, continuation: string) => void;
  center: React.RefObject<HTMLDivElement | null>;
}) {
  return (
    <>
      <button
        type="button"
        className={jump}
        data-testid="review-jump-to-selection"
        onClick={() => center.current?.focus()}
      >
        ↓ jump to the selected review
      </button>
      <CenterColumn
        payload={payload}
        selection={selection}
        layout={layout}
        onLayout={onLayout}
        fullFile={fullFile}
        onFullFile={onFullFile}
        openPath={openPath}
        onOpenPath={onOpenPath}
        onOpenFromCenter={onOpenFromCenter}
        onOpenMember={(familyId, memberRevisionId) =>
          onSelectMember({ familyId, memberRevisionId })
        }
        onRosterNext={onRosterNext}
        center={center}
      />
    </>
  );
}

// The centre column and its own display controls. It is one component so the workspace below reads as
// the composition it is: scope, tree, narrow-screen route, centre.
function CenterColumn({
  payload,
  selection,
  layout,
  onLayout,
  fullFile,
  onFullFile,
  openPath,
  onOpenPath,
  onOpenFromCenter,
  onOpenMember,
  onRosterNext,
  center,
}: {
  payload: ReviewPayload;
  selection: FamilySelection | null;
  layout: DiffLayout;
  onLayout: (next: DiffLayout) => void;
  fullFile: boolean;
  onFullFile: (next: boolean) => void;
  openPath: string | null;
  onOpenPath: (path: string | null) => void;
  onOpenFromCenter: (path: string) => void;
  onOpenMember: (familyId: string, memberRevisionId: string) => void;
  onRosterNext: (familyId: string, side: ReviewFamilySideName, continuation: string) => void;
  center: React.RefObject<HTMLDivElement | null>;
}) {
  return (
    <div ref={center} tabIndex={-1} data-testid="review-center-column">
      <FamilyReviewCenter
        payload={payload}
        selection={selection}
        layout={layout}
        onLayout={onLayout}
        fullFile={fullFile}
        onFullFile={onFullFile}
        onOpenMember={onOpenMember}
        onRosterNext={onRosterNext}
        openPath={openPath}
        onOpenPath={onOpenPath}
        onOpenFromCenter={onOpenFromCenter}
      />
      <DisplayControls
        layout={layout}
        onLayout={onLayout}
        fullFile={fullFile}
        onFullFile={onFullFile}
      />
      <p className={muted} data-testid="review-display-state">
        display: {layout} diff · {fullFile ? "full file" : "changed regions only"}
        {openPath ? (
          <>
            {" "}
            · expanded: <code className={mono}>{openPath}</code>
          </>
        ) : (
          " · no entry expanded"
        )}
      </p>
    </div>
  );
}

// One workspace's local state: which family or member is selected, the reader's filter, the three
// display preferences, and the focus memory that closing an expansion restores.
//
// IT IS EXPORTED, AND ITS OWNER IS THE SURFACE RATHER THAN THIS COMPONENT (ICR-L24 fix round 5, V10).
// A page request changes the read's question, so the payload is null while it is in flight and the
// panes -- this whole subtree included -- unmount and mount again. State owned here would therefore be
// destroyed and re-initialised by every page read, which is what the round-4 verification measured:
// the selection fell back to `none`, the filter to `""`, the diff layout to `split` and full-file to
// `true`, and the centre's own continuation control became single-use because its selection was gone
// by the time the page arrived. The surface holds this state and passes it down, so a page read cannot
// reach it.
export interface WorkspaceState {
  chosen: FamilySelection | null;
  setChosen: (selection: FamilySelection) => void;
  query: string;
  setQuery: (next: string) => void;
  layout: DiffLayout;
  setLayout: (next: DiffLayout) => void;
  fullFile: boolean;
  setFullFile: (next: boolean) => void;
  openPath: string | null;
  openFromCenter: (path: string) => void;
  closePath: (path: string | null) => void;
  center: React.RefObject<HTMLDivElement | null>;
}

export function useWorkspaceState(): WorkspaceState {
  const [chosen, setChosen] = useState<FamilySelection | null>(null);
  const [query, setQuery] = useState("");
  const [layout, setLayout] = useState<DiffLayout>("split");
  const [fullFile, setFullFile] = useState(true);
  const [openPath, setOpenPath] = useState<string | null>(null);
  // The control that opened the current expansion, so closing it can put focus back where the reader
  // was rather than at the top of the document.
  const opener = useRef<HTMLElement | null>(null);
  const center = useRef<HTMLDivElement>(null);
  const openFromCenter = (path: string) => {
    opener.current = document.activeElement as HTMLElement | null;
    setOpenPath(path);
  };
  const closePath = (path: string | null) => {
    setOpenPath(path);
    if (path === null) opener.current?.focus();
  };
  return {
    chosen,
    setChosen,
    query,
    setQuery,
    layout,
    setLayout,
    fullFile,
    setFullFile,
    openPath,
    openFromCenter,
    closePath,
    center,
  };
}

// The header band: which task context this is, which record it was read from, and -- when the
// response is a page of a roster walk -- which family revision's walk it is a position in.
function WorkspaceHeader({
  payload,
  repo,
  master,
  leaf,
  selectorKind,
  selectorId,
  history,
}: {
  payload: ReviewPayload;
  repo: string;
  master: string;
  leaf: string;
  selectorKind?: ReviewSelectorKind;
  selectorId?: string;
  history?: "recorded";
}) {
  return (
    <>
      <ScopeHeader
        payload={payload}
        repo={repo}
        master={master}
        leaf={leaf}
        selectorKind={selectorKind}
        selectorId={selectorId}
        history={history}
      />
      <RosterWalkNotice payload={payload} />
    </>
  );
}

// One roster-walk handler for the whole workspace: the tree's control and the centre's control are the
// same component over the same value, so they cannot ask different questions. The family and side are
// the page's own scope rather than the request's -- a roster cursor names the one walk it continues --
// which is why only the continuation travels.
function rosterWalk(
  onPageSelect: (page: ReviewPageRequest | undefined) => void,
): (familyId: string, side: ReviewFamilySideName, continuation: string) => void {
  return (_familyId, _side, continuation) =>
    onPageSelect({ of: "family_members", continuation });
}

export function ReviewWorkspace({
  payload,
  repo,
  master,
  leaf,
  selectorKind,
  selectorId,
  history,
  onPageSelect,
  state,
}: {
  payload: ReviewPayload;
  repo: string;
  master: string;
  leaf: string;
  selectorKind?: ReviewSelectorKind;
  selectorId?: string;
  history?: "recorded";
  // The surface's one page control. A family roster continuation is a page request like any other, so
  // it goes through the read cycle the surface already owns rather than a second reader.
  onPageSelect: (page: ReviewPageRequest | undefined) => void;
  // The reader's local state, owned by the surface: a page read unmounts this subtree (V10).
  state: WorkspaceState;
}) {
  const walkRoster = rosterWalk(onPageSelect);
  const {
    chosen,
    setChosen,
    query,
    setQuery,
    layout,
    setLayout,
    fullFile,
    setFullFile,
    openPath,
    openFromCenter,
    closePath,
    center,
  } = state;

  return (
    <div
      className={workspace}
      data-testid="review-workspace"
      data-diff-layout={layout}
      data-full-file={fullFile ? "true" : "false"}
    >
      <WorkspaceHeader
        payload={payload}
        repo={repo}
        master={master}
        leaf={leaf}
        selectorKind={selectorKind}
        selectorId={selectorId}
        history={history}
      />
      <FamilyColumn
        context={payload.family_context}
        selection={chosen}
        onSelect={setChosen}
        onRosterNext={walkRoster}
        query={query}
        onQuery={setQuery}
      />
      <SelectionColumn
        payload={payload}
        selection={chosen}
        onSelectMember={setChosen}
        onRosterNext={walkRoster}
        layout={layout}
        onLayout={setLayout}
        fullFile={fullFile}
        onFullFile={setFullFile}
        openPath={openPath}
        onOpenPath={closePath}
        onOpenFromCenter={openFromCenter}
        center={center}
      />
    </div>
  );
}

// The same two display values, reachable from the centre column as well as from the explorer's own
// bar: a reader looking at a diff should not have to scroll to the explorer to change how it is
// drawn. Both controls write the one pair of values this component owns.
function DisplayControls({
  layout,
  onLayout,
  fullFile,
  onFullFile,
}: {
  layout: DiffLayout;
  onLayout: (next: DiffLayout) => void;
  fullFile: boolean;
  onFullFile: (next: boolean) => void;
}) {
  return (
    <p className={muted} data-testid="review-center-display-controls">
      <label htmlFor="review-center-diff-layout">diff layout </label>
      <select
        id="review-center-diff-layout"
        data-testid="review-center-diff-layout"
        value={layout}
        onChange={(event) => onLayout(event.target.value === "inline" ? "inline" : "split")}
      >
        <option value="split">split</option>
        <option value="inline">inline</option>
      </select>{" "}
      <button
        type="button"
        data-testid="review-center-full-file"
        aria-pressed={fullFile}
        onClick={() => onFullFile(!fullFile)}
      >
        {fullFile ? "showing full file" : "showing changed regions"}
      </button>
    </p>
  );
}

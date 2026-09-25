// The complete source change explorer: every changed path of the comparison's bound pair, openable at
// the generation the listing named, with an explicit diff layout and full-file control.
//
// WHY THIS IS ITS OWN MODULE. `ReviewSurface.tsx` is over the repository's file-size rail and the
// explorer is a responsibility of its own: it owns one inventory's three states, the entries' opening
// controls and the two display preferences a reader sets while reading. The surface mounts it; the
// change-set viewer and the file viewer keep their own owners, and this module reuses `SourceContent`
// (ICR-R03) and `DiffPane` rather than restating either.
//
// THE TWO PREFERENCES ARE THE READER'S, NOT THE SELECTION'S (ICR-R24@v3). `layout` (split/inline) and
// `fullFile` are owned by the caller and passed down, so switching the diff layout while a file is
// expanded cannot reset that expansion, and selecting another family or member cannot either: the
// open path is the only state this component holds, and it is keyed by the path the server published.
//
// WHAT IT MUST NOT DO. It lists every changed path the inventory measured, including the ones no
// family or invariant attribution reaches: the family navigation is an attribution lens, never an
// exclusion filter. A measured empty set says the two trees agree; an unavailable measurement says
// nothing was observed and why; a partial one says which entries could not be classified or carried
// as names. None of the three is rendered as another.

import { css } from "../../../styled-system/css";
import type {
  ReviewChangedFile,
  ReviewSourceInventory,
  ReviewUnrepresentablePath,
} from "../../data/review";
import { SourceContent } from "./SourceContent";

export type DiffLayout = "split" | "inline";

const shell = css({
  background: "bgPanel",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "3px",
  padding: "0.6rem 0.7rem",
});

const bar = css({
  display: "flex",
  gap: "0.5rem",
  alignItems: "center",
  flexWrap: "wrap",
  marginBottom: "0.4rem",
});

const sectionLabel = css({
  color: "cyan",
  fontSize: "0.72rem",
  letterSpacing: "0.1em",
  textTransform: "uppercase",
  margin: "0",
});

const muted = css({ color: "muted", fontSize: "0.8rem", margin: "0.2rem 0" });

const rows = css({ margin: "0.2rem 0", paddingLeft: "1.1rem" });

// The path is a `mono` string with no spaces in it, so it has a very large MIN-CONTENT width. Without
// a break opportunity the button takes that width, and every grid item above it refuses to shrink
// (`min-width: auto`), so the whole column grows past its track: measured on the mounted product at
// 320px, one `review-inventory-open` button was 556px wide inside a 294px column and its right 262px
// was cut off by the app shell's `overflow-x: hidden` -- with no panning ancestor, so the rest of the
// path was neither visible nor reachable. Register B7 states the criterion this broke: "long
// guarantees and long paths remain readable and wrapped; controls are unclipped". `overflow-wrap:
// anywhere` gives the path the break opportunity it needs (and, unlike `break-word`, it also lowers
// the element's min-content width, which is what lets the column shrink instead of overflow);
// `max-width: 100%` states that the button never claims more than its container.
const rowButton = css({
  background: "transparent",
  border: "none",
  color: "ink",
  cursor: "pointer",
  font: "inherit",
  padding: "0",
  textAlign: "left",
  maxWidth: "100%",
  overflowWrap: "anywhere",
  _focusVisible: { outline: "1px solid var(--amber)", outlineOffset: "2px" },
});

// One inventory entry. The path is printed exactly as the server published it -- a tab or a newline
// inside a name is part of the address -- and the status and renderability are printed beside it,
// because a path whose content cannot be rendered is still a change that must be listed.
//
// The entry is also the way into its own content (ICR-R03): opening it reads the file at the two code
// trees the inventory published, and the generation ids travel with the request, so a row opened after
// the branch moved still shows the generation the reader was looking at.
function inventoryEntry(
  entry: ReviewChangedFile,
  repo: string,
  master: string,
  leaf: string,
  generation: { before?: string; after?: string },
  open: string | null,
  onOpen: (path: string | null) => void,
  layout: DiffLayout,
  fullFile: boolean,
) {
  const notes = [
    entry.mode_change ? "mode changed" : null,
    entry.content === "unknown" ? null : `content: ${entry.content}`,
    entry.detail ?? null,
  ].filter((note): note is string => note !== null);
  const expandable = generation.before !== undefined && generation.after !== undefined;
  const isOpen = open === entry.path;
  return (
    <li key={entry.path} data-testid="review-inventory-entry" data-status={entry.status}>
      {expandable ? (
        <button
          type="button"
          className={rowButton}
          data-testid="review-inventory-open"
          data-path={entry.path}
          aria-expanded={isOpen}
          onClick={() => onOpen(isOpen ? null : entry.path)}
        >
          {isOpen ? "▾ " : "▸ "}
          {entry.path}
        </button>
      ) : (
        <code>{entry.path}</code>
      )}{" "}
      · {entry.status}
      {notes.length ? <span className={muted}> · {notes.join(" · ")}</span> : null}
      {isOpen && expandable ? (
        <SourceContent
          repo={repo}
          master={master}
          leaf={leaf}
          entry={entry}
          beforeCodeTreeId={generation.before as string}
          afterCodeTreeId={generation.after as string}
          mode={layout}
          collapse={!fullFile}
        />
      ) : null}
    </li>
  );
}

// One changed path this surface cannot name as text, printed by its exact byte form. It is a change
// like any other: it is listed, its status is shown, and the reason it has no name is stated rather
// than left as a gap in a list that would otherwise look complete.
//
// It carries no expansion control, and says so: this vocabulary carries text, so the only spelling
// that could address the row's content cannot be expressed in a request (ICR-R03's boundary).
function byteNamedEntry(entry: ReviewUnrepresentablePath) {
  return (
    <li key={entry.path_bytes} data-testid="review-inventory-byte-path" data-status={entry.status}>
      <code>{entry.path_bytes}</code> · {entry.status}
      {entry.mode_change ? " · mode changed" : ""}
      <div className={muted}>{entry.detail}</div>
      <div className={muted} data-testid="review-byte-path-not-addressable">
        this row&apos;s content is not openable through this surface: its name is carried as bytes for
        identification, and no expansion request can name it.
      </div>
    </li>
  );
}

// The reader's two display preferences. They are controls and not decorations: each one says what it
// is about, and neither claims anything about the comparison.
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
    <span className={bar} data-testid="review-display-controls" data-diff-layout={layout}>
      <label className={muted} htmlFor="review-diff-layout">
        diff layout
      </label>
      <select
        id="review-diff-layout"
        data-testid="review-diff-layout"
        value={layout}
        onChange={(event) => onLayout(event.target.value === "inline" ? "inline" : "split")}
      >
        <option value="split">split</option>
        <option value="inline">inline</option>
      </select>
      <button
        type="button"
        data-testid="review-full-file"
        data-full-file={fullFile ? "true" : "false"}
        aria-pressed={fullFile}
        onClick={() => onFullFile(!fullFile)}
      >
        {fullFile ? "showing full file" : "showing changed regions"}
      </button>
    </span>
  );
}

// The listed rows: every entry of the measured change set, then every path carried by byte form.
// Extracted so the explorer's own body reads as the composition it is (population sentence, controls,
// rows, the command that reproduces the measurement).
function InventoryRows({
  inventory,
  repo,
  master,
  leaf,
  layout,
  fullFile,
  open,
  onOpen,
}: {
  inventory: ReviewSourceInventory;
  repo: string;
  master: string;
  leaf: string;
  layout: DiffLayout;
  fullFile: boolean;
  open: string | null;
  onOpen: (path: string | null) => void;
}) {
  const generation = {
    before: inventory.before_code_tree_id,
    after: inventory.after_code_tree_id,
  };
  const byByteForm = inventory.unrepresentable_paths ?? [];
  return (
    <>
      {inventory.entries.length ? (
        <ul className={rows}>
          {inventory.entries.map((entry) =>
            inventoryEntry(entry, repo, master, leaf, generation, open, onOpen, layout, fullFile),
          )}
        </ul>
      ) : null}
      {byByteForm.length ? (
        <ul className={rows}>{byByteForm.map(byteNamedEntry)}</ul>
      ) : null}
    </>
  );
}

export function SourceExplorer({
  inventory,
  repo,
  master,
  leaf,
  layout,
  onLayout,
  fullFile,
  onFullFile,
  open,
  onOpen,
}: {
  inventory: ReviewSourceInventory;
  repo: string;
  master: string;
  leaf: string;
  layout: DiffLayout;
  onLayout: (next: DiffLayout) => void;
  fullFile: boolean;
  onFullFile: (next: boolean) => void;
  // Which listed path is expanded. It is owned by the workspace rather than by this component so that
  // a linked expression in the central review can open the same entry, and so that neither a diff
  // layout switch nor a family selection collapses it (ICR-R24@v3).
  open: string | null;
  onOpen: (path: string | null) => void;
}) {
  const unclassified = inventory.entries.filter(
    (entry) => entry.status === "unknown" || entry.content === "unknown",
  );
  const byByteForm = inventory.unrepresentable_paths ?? [];
  return (
    <section
      className={shell}
      data-testid="review-source-explorer"
      data-inventory-state={inventory.state}
    >
      <div className={bar}>
        <h2 className={sectionLabel}>Complete source change explorer</h2>
        <DisplayControls
          layout={layout}
          onLayout={onLayout}
          fullFile={fullFile}
          onFullFile={onFullFile}
        />
      </div>
      {/* The population sentence is the inventory's own: it is the measured change set of the bound
          pair and it does not depend on any family or member selection, which is why it is stated
          here rather than inside the family navigation. */}
      <p data-testid="review-inventory" data-inventory-state={inventory.state}>
        source change inventory ({inventory.state}
        {inventory.partial ? ", partial" : ""}): {inventory.listed_total} listed path(s)
        {byByteForm.length ? ` + ${byByteForm.length} by byte form` : ""} — {inventory.detail}
      </p>
      <p className={muted} data-testid="review-population-scope">
        this explorer is the whole measured change set of the comparison&apos;s bound pair. A family
        or member selection below attributes changes; it never removes one from this list.
      </p>
      <InventoryRows
        inventory={inventory}
        repo={repo}
        master={master}
        leaf={leaf}
        layout={layout}
        fullFile={fullFile}
        open={open}
        onOpen={onOpen}
      />
      {unclassified.length ? (
        <p data-testid="review-inventory-unclassified">
          {unclassified.length} listed path(s) carry no classified kind or status these owners could
          report: {unclassified.map((entry) => entry.path).join(", ")}
        </p>
      ) : null}
      <p className={muted} data-testid="review-inventory-command">
        reproduce: {inventory.command}
        {inventory.before_code_tree_id && inventory.after_code_tree_id
          ? ` · ${inventory.before_code_tree_id} → ${inventory.after_code_tree_id}`
          : ""}
      </p>
    </section>
  );
}

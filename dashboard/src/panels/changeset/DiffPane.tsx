// Read-only before/after diff pane (L4) — the one genuinely new CodeMirror primitive. It reuses
// FilePane's exact extension set + codeTheme + langExtension, so tokens are identical across the
// plain code pane and the diff. `split` = @codemirror/merge MergeView (a=before, b=after, side by
// side); `inline` = unifiedMergeView over a single EditorView (doc=after) with the changes marked
// in place. Both are read-only: the editors are readOnly/non-editable and no revert/merge controls
// are shown. Built imperatively in an effect, torn down on unmount / prop change (disposed guard
// against a late async language resolve).
import { useEffect, useLayoutEffect, useRef } from "react";
import { MergeView, unifiedMergeView } from "@codemirror/merge";
import { Compartment, EditorState, type Extension } from "@codemirror/state";
import { EditorView, ViewPlugin } from "@codemirror/view";

import { css } from "../../../styled-system/css";
import { codeTheme } from "../file-viewer/codemirrorTheme";
import { numberedFrom } from "../file-viewer/lineNumbering";
import { langExtension } from "../file-viewer/langByExtension";
import {
  type DrawnEditors,
  type MarkedPane,
  type MarkSide,
  type PaneMarks,
  useMarkedPane,
} from "../file-viewer/markGutter";

const host = css({
  height: "100%",
  minHeight: "0",
  overflow: "hidden",
  // Inline (unifiedMergeView) / single-editor mode: a DIRECT .cm-editor fills the host and its own
  // .cm-scroller scrolls (like FilePane). Scoped to the direct child so it does NOT clamp the
  // editors nested inside a split MergeView.
  "& > .cm-editor": { height: "100%" },
  // Split (MergeView) mode: .cm-mergeView IS the scroll container — its own theme sets
  // overflowY:auto and forces the inner editors' scrollers to overflow:visible (they grow to full
  // content height). Bounding it to 100% (and NOT clamping the inner editors) is what lets it scroll.
  "& .cm-mergeView": { height: "100%" },
  // L4a: changed text reads as a full-height highlight RECTANGLE, not @codemirror/merge's default thin
  // bottom underline (developer preference — far more legible). TWO things make it a box: the HEIGHT —
  // a `bottom / 100% 16px` band fills the line box (the default uses ~2px → a line) — and the COLOR —
  // deliberately DARK, muted fills (low-lightness green for additions, red for deletions), NOT the
  // bright `--mint`/`--amber` FOREGROUND tokens, which as a background would wash out the light diff
  // text. `!important` is required to beat @codemirror/merge's own runtime-injected theme rule
  // (`.ͼN.cm-merge-b .cm-changedText`), which outranks a plain host-scoped selector. Split mode marks
  // deletions in the `a` editor (the `.cm-merge-a` rule) and additions in `b`; inline (unifiedMergeView)
  // marks additions in place — both covered by the general rule.
  "& .cm-changedText": {
    background: "linear-gradient(#255a25aa, #255a25aa) bottom / 100% 16px no-repeat !important",
  },
  "& .cm-merge-a .cm-changedText": {
    background: "linear-gradient(#5a2525aa, #5a2525aa) bottom / 100% 16px no-repeat !important",
  },
});

// A focused excerpt (the review page's blocks, statements and guarantees) sizes to its content
// instead of filling a fixed-height parent: the editors grow to their lines and no pane is a scroll
// box (MIK-R89 v2 rule 1). The merge view's own theme makes `.cm-mergeView` scroll (`overflowY:
// auto`); here it does not, and with wrapped lines no `.cm-scroller` overflows, so the review page's
// reading column scrolls them all.
//
// Its diff is drawn as the approved mockup draws one (D401; mockup styles.css:216-239, diff.js
// uniRow/sideRow): a removed row is one red band and an added row one green band (the mockup's
// --del-bg and --add-bg), with − or + in a marker column before the text on its faint tint; a removed
// row's text is alarm red, as the mockup's `.del` row is (styles.css:95), and a syntax token with a
// colour of its own keeps it, the rule being on the row; the words inside a band are not boxed again;
// where one side of a split diff has no line opposite the other's (@codemirror/merge's spacer), the
// gap is hatched; a fold reads as the mockup's `.fold` bar. Every
// row keeps the marker column's room, so the text of all rows lines up. The marker sits in the line's
// own padding, so CodeMirror's gutters (the line numbers and the intent marks) are untouched; the
// merge view's 3px change bar gives way to it. A property CodeMirror's own themes also set carries
// `!important`: Panda's utilities sit in a cascade layer, which CodeMirror's unlayered sheet outranks.
const fitHost = css({
  minHeight: "0",
  "& > .cm-editor": { height: "auto" },
  "& .cm-mergeView": { height: "auto", overflowY: "visible !important" },
  "& .cm-scroller": { overflow: "visible !important" },
  "& .cm-content > .cm-line": {
    paddingLeft: "calc(1.2em + 6px) !important",
    position: "relative",
  },
  "& .cm-deletedChunk": { paddingLeft: "0 !important", background: "transparent !important" },
  "& .cm-merge-b .cm-changedLine": {
    background: "linear-gradient(to right, rgba(84, 248, 189, 0.07) 1.2em, rgba(37, 90, 37, 0.42) 1.2em) !important",
  },
  "& .cm-merge-a .cm-changedLine": {
    background: "linear-gradient(to right, rgba(255, 72, 74, 0.07) 1.2em, rgba(90, 37, 37, 0.5) 1.2em) !important",
    color: "alarm",
  },
  "& .cm-deletedChunk > .cm-deletedLine": {
    paddingLeft: "calc(1.2em + 6px)",
    paddingRight: "2px",
    position: "relative",
    background: "linear-gradient(to right, rgba(255, 72, 74, 0.07) 1.2em, rgba(90, 37, 37, 0.5) 1.2em)",
    color: "alarm",
  },
  "& .cm-merge-b .cm-changedLine::before": {
    content: '"+"',
    color: "mint",
    position: "absolute",
    left: 0,
    width: "1.2em",
    textAlign: "center",
    userSelect: "none",
  },
  "& .cm-merge-a .cm-changedLine::before": {
    content: '"−"',
    color: "alarm",
    position: "absolute",
    left: 0,
    width: "1.2em",
    textAlign: "center",
    userSelect: "none",
  },
  "& .cm-deletedChunk > .cm-deletedLine::before": {
    content: '"−"',
    color: "alarm",
    position: "absolute",
    left: 0,
    width: "1.2em",
    textAlign: "center",
    userSelect: "none",
  },
  "& .cm-changedText": { background: "none !important" },
  "& .cm-deletedText": { background: "none !important" },
  "& .cm-changeGutter": { display: "none !important" },
  "& .cm-mergeSpacer": {
    background: "repeating-linear-gradient(135deg, transparent 0 6px, rgba(38, 47, 56, 0.35) 6px 7px)",
  },
  "& .cm-collapsedLines": {
    background: "linear-gradient(to bottom, transparent, #121b24 30%, #121b24 70%, transparent) !important",
    color: "muted !important",
    fontSize: "0.72rem",
    borderBlockWidth: "1px",
    borderBlockStyle: "solid",
    borderBlockColor: "rgba(38, 47, 56, 0.55)",
  },
});

export type DiffMode = "split" | "inline";

// A fold ("N unchanged lines", @codemirror/merge's collapse widget) opens by keyboard too (MIK-R89
// v2 rule 11): each is a focusable button whose Enter or Space does what its click does -- expand
// that stretch in place, on both sides of a split diff.
function markFolds(view: EditorView): void {
  for (const fold of view.dom.querySelectorAll<HTMLElement>(".cm-collapsedLines")) {
    if (fold.dataset.foldKeys) continue;
    fold.dataset.foldKeys = "on";
    fold.tabIndex = 0;
    fold.setAttribute("role", "button");
    fold.setAttribute("aria-label", `${fold.textContent ?? "Unchanged lines"} · expand`);
    fold.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      fold.click();
    });
  }
}

const foldKeys = ViewPlugin.fromClass(
  class {
    constructor(view: EditorView) {
      view.requestMeasure({ read: () => null, write: () => markFolds(view) });
    }
    update(update: { view: EditorView }) {
      update.view.requestMeasure({ read: () => null, write: () => markFolds(update.view) });
    }
  },
);

// What both builders below share: the two documents, where each starts in its file, the common
// read-only extensions and the collapse setting, plus the pane's mark gutters.
interface DiffBuild {
  parent: HTMLElement;
  before: string;
  after: string;
  beforeFirst: number;
  afterFirst: number;
  common: Extension[];
  collapseUnchanged?: { margin: number };
  marked: Pick<MarkedPane, "gutterFor" | "drawn">;
}

// A built pane: its editors, and each editor's mark gutter in a compartment of its own, so that a
// change of the marks can be drawn into the same editors.
interface Built {
  destroy: () => void;
  editors: DrawnEditors;
  gutters: { view: EditorView; side: MarkSide; first: number; slot: Compartment }[];
}

function splitView(build: DiffBuild): Built {
  const { parent, before, after, beforeFirst, afterFirst, common, collapseUnchanged, marked } = build;
  const slots = { before: new Compartment(), after: new Compartment() };
  const merge = new MergeView({
    a: {
      doc: before,
      extensions: [slots.before.of(marked.gutterFor("before", beforeFirst)), numberedFrom(beforeFirst), ...common],
    },
    b: {
      doc: after,
      extensions: [slots.after.of(marked.gutterFor("after", afterFirst)), numberedFrom(afterFirst), ...common],
    },
    parent,
    gutter: true,
    collapseUnchanged,
    // no `revertControls` -> read-only diff (the editors are non-editable anyway).
  });
  const editors = {
    before: { view: merge.a, first: beforeFirst },
    after: { view: merge.b, first: afterFirst },
  };
  marked.drawn(editors);
  return {
    destroy: () => merge.destroy(),
    editors,
    gutters: [
      { view: merge.a, side: "before", first: beforeFirst, slot: slots.before },
      { view: merge.b, side: "after", first: afterFirst, slot: slots.after },
    ],
  };
}

function inlineView(build: DiffBuild): Built {
  const { parent, before, after, afterFirst, common, collapseUnchanged, marked } = build;
  const slot = new Compartment();
  const view = new EditorView({
    parent,
    state: EditorState.create({
      doc: after,
      extensions: [
        slot.of(marked.gutterFor("after", afterFirst)),
        unifiedMergeView({
          original: before,
          mergeControls: false, // read-only: no accept/reject chips
          gutter: true,
          collapseUnchanged,
        }),
        numberedFrom(afterFirst),
        ...common,
      ],
    }),
  });
  const editors = { after: { view, first: afterFirst } };
  marked.drawn(editors);
  return { destroy: () => view.destroy(), editors, gutters: [{ view, side: "after", first: afterFirst, slot }] };
}

export function DiffPane({
  before,
  after,
  language,
  mode,
  collapse = true,
  firstLine,
  fit = false,
  marks,
}: {
  before: string;
  after: string;
  language: string;
  mode: DiffMode;
  // change-set view collapses unchanged regions; full-file (highlighted) view shows everything.
  collapse?: boolean;
  // Where each side's document starts in its file (an excerpt); absent = line 1 on both sides.
  firstLine?: { before: number; after: number };
  fit?: boolean;
  // Marks on file lines (the reviewer's per-hunk intent markers); absent = no mark gutter at all.
  marks?: PaneMarks;
}) {
  const beforeFirst = firstLine?.before ?? 1;
  const afterFirst = firstLine?.after ?? 1;
  const ref = useRef<HTMLDivElement>(null);
  const { portals, placement, gutterFor, drawn } = useMarkedPane(marks);
  // The marks' gutter and the pane's report of its editors as of the last render: a build reads them
  // once its language pack has resolved.
  const marking = useRef({ gutterFor, drawn });
  useLayoutEffect(() => {
    marking.current = { gutterFor, drawn };
  });
  const built = useRef<Built | null>(null);
  // A fitted pane (the review page) draws a change of its marks into the same editors (below);
  // elsewhere a change of the marks builds the pane again.
  const rebuildOn = fit ? "" : placement;

  useEffect(() => {
    const parent = ref.current;
    if (!parent) return;
    let disposed = false;

    // Language packs are async (code-split); guard against a late resolve after teardown.
    void langExtension(language).then((lang) => {
      if (disposed || !parent) return;
      const common: Extension[] = [
        EditorState.readOnly.of(true),
        EditorView.editable.of(false),
        EditorView.lineWrapping,
        codeTheme,
      ];
      if (lang) common.push(lang);
      if (collapse) common.push(foldKeys);
      const build: DiffBuild = {
        parent,
        before,
        after,
        beforeFirst,
        afterFirst,
        common,
        collapseUnchanged: collapse ? { margin: 3 } : undefined,
        marked: marking.current,
      };
      built.current = mode === "split" ? splitView(build) : inlineView(build);
    });

    return () => {
      disposed = true;
      marking.current.drawn(null);
      built.current?.destroy();
      built.current = null;
    };
    // `rebuildOn` rebuilds a pane that is not fitted when a mark moves (the marks are read at build).
  }, [before, after, language, mode, collapse, beforeFirst, afterFirst, rebuildOn]);

  // A fitted pane draws its marks in place: each editor's mark gutter is configured again and nothing
  // is destroyed, so the pane keeps its drawing while its marks land (rule 9, ruling fb5d04f6; WP8 on
  // A4-F4: the marks' landing at 2–3 s rebuilt the review page's panes in view, each standing empty
  // until its next editor was drawn). `data-marks` tells the excerpt that the marks are drawn
  // (ExpressionCards.tsx, HeldExcerpt).
  useEffect(() => {
    const current = built.current;
    if (!fit || !current) return;
    for (const { view, side, first, slot } of current.gutters)
      view.dispatch({ effects: slot.reconfigure(gutterFor(side, first)) });
    drawn(current.editors);
    ref.current?.setAttribute("data-marks", placement);
  }, [fit, placement, gutterFor, drawn]);

  return (
    <>
      <div ref={ref} className={fit ? fitHost : host} data-testid="diff-pane" />
      {portals}
    </>
  );
}

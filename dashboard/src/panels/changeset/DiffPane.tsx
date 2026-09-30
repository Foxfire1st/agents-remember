// Read-only before/after diff pane (L4) — the one genuinely new CodeMirror primitive. It reuses
// FilePane's exact extension set + codeTheme + langExtension, so tokens are identical across the
// plain code pane and the diff. `split` = @codemirror/merge MergeView (a=before, b=after, side by
// side); `inline` = unifiedMergeView over a single EditorView (doc=after) with the changes marked
// in place. Both are read-only: the editors are readOnly/non-editable and no revert/merge controls
// are shown. Built imperatively in an effect, torn down on unmount / prop change (disposed guard
// against a late async language resolve).
import { useEffect, useRef } from "react";
import { MergeView, unifiedMergeView } from "@codemirror/merge";
import { EditorState, type Extension } from "@codemirror/state";
import { EditorView } from "@codemirror/view";

import { css } from "../../../styled-system/css";
import { codeTheme } from "../file-viewer/codemirrorTheme";
import { numberedFrom } from "../file-viewer/lineNumbering";
import { langExtension } from "../file-viewer/langByExtension";
import { type MarkedPane, type PaneMarks, useMarkedPane } from "../file-viewer/markGutter";

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

// A focused excerpt (the reviewer's expression cards) sizes to its content instead of filling a
// fixed-height parent: the editors grow to their lines and the card, not the pane, scrolls.
const fitHost = css({
  minHeight: "0",
  "& > .cm-editor": { height: "auto" },
  "& .cm-mergeView": { height: "auto", maxHeight: "32rem" },
  "& .cm-changedText": {
    background: "linear-gradient(#255a25aa, #255a25aa) bottom / 100% 16px no-repeat !important",
  },
  "& .cm-merge-a .cm-changedText": {
    background: "linear-gradient(#5a2525aa, #5a2525aa) bottom / 100% 16px no-repeat !important",
  },
});

export type DiffMode = "split" | "inline";

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

function splitView(build: DiffBuild): { destroy: () => void } {
  const { parent, before, after, beforeFirst, afterFirst, common, collapseUnchanged, marked } = build;
  const merge = new MergeView({
    a: {
      doc: before,
      extensions: [marked.gutterFor("before", beforeFirst), numberedFrom(beforeFirst), ...common],
    },
    b: {
      doc: after,
      extensions: [marked.gutterFor("after", afterFirst), numberedFrom(afterFirst), ...common],
    },
    parent,
    gutter: true,
    collapseUnchanged,
    // no `revertControls` -> read-only diff (the editors are non-editable anyway).
  });
  marked.drawn({
    before: { view: merge.a, first: beforeFirst },
    after: { view: merge.b, first: afterFirst },
  });
  return merge;
}

function inlineView(build: DiffBuild): { destroy: () => void } {
  const { parent, before, after, afterFirst, common, collapseUnchanged, marked } = build;
  const view = new EditorView({
    parent,
    state: EditorState.create({
      doc: after,
      extensions: [
        marked.gutterFor("after", afterFirst),
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
  marked.drawn({ after: { view, first: afterFirst } });
  return view;
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

  useEffect(() => {
    const parent = ref.current;
    if (!parent) return;
    let view: { destroy: () => void } | null = null;
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
      const build: DiffBuild = {
        parent,
        before,
        after,
        beforeFirst,
        afterFirst,
        common,
        collapseUnchanged: collapse ? { margin: 3 } : undefined,
        marked: { gutterFor, drawn },
      };
      view = mode === "split" ? splitView(build) : inlineView(build);
    });

    return () => {
      disposed = true;
      drawn(null);
      view?.destroy();
    };
    // `placement` rebuilds the pane when a mark moves (the marks themselves are read at build).
  }, [before, after, language, mode, collapse, beforeFirst, afterFirst, placement, gutterFor, drawn]);

  return (
    <>
      <div ref={ref} className={fit ? fitHost : host} data-testid="diff-pane" />
      {portals}
    </>
  );
}

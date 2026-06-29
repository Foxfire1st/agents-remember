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
import { EditorView, lineNumbers } from "@codemirror/view";

import { css } from "../../../styled-system/css";
import { codeTheme } from "../file-viewer/codemirrorTheme";
import { langExtension } from "../file-viewer/langByExtension";

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
});

export type DiffMode = "split" | "inline";

export function DiffPane({
  before,
  after,
  language,
  mode,
  collapse = true,
}: {
  before: string;
  after: string;
  language: string;
  mode: DiffMode;
  // change-set view collapses unchanged regions; full-file (highlighted) view shows everything.
  collapse?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const parent = ref.current;
    if (!parent) return;
    let view: { destroy: () => void } | null = null;
    let disposed = false;

    // Language packs are async (code-split); guard against a late resolve after teardown.
    void langExtension(language).then((lang) => {
      if (disposed || !parent) return;
      const base: Extension[] = [
        lineNumbers(),
        EditorState.readOnly.of(true),
        EditorView.editable.of(false),
        EditorView.lineWrapping,
        codeTheme,
      ];
      if (lang) base.push(lang);

      const collapseUnchanged = collapse ? { margin: 3 } : undefined;
      if (mode === "split") {
        view = new MergeView({
          a: { doc: before, extensions: base },
          b: { doc: after, extensions: base },
          parent,
          gutter: true,
          collapseUnchanged,
          // no `revertControls` -> read-only diff (the editors are non-editable anyway).
        });
      } else {
        view = new EditorView({
          parent,
          state: EditorState.create({
            doc: after,
            extensions: [
              unifiedMergeView({
                original: before,
                mergeControls: false, // read-only: no accept/reject chips
                gutter: true,
                collapseUnchanged,
              }),
              ...base,
            ],
          }),
        });
      }
    });

    return () => {
      disposed = true;
      view?.destroy();
    };
  }, [before, after, language, mode, collapse]);

  return <div ref={ref} className={host} data-testid="diff-pane" />;
}

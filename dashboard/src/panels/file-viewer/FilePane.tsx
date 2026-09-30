// Reusable read-only CodeMirror 6 pane — the shared code primitive. L4's Change-Set Viewer
// reuses it by swapping the single EditorView for @codemirror/merge (same editor core + the
// same HighlightStyle => identical tokens across plain + diff). The EditorView is created
// imperatively in an effect and torn down on unmount / content change.
import { useEffect, useRef } from "react";
import { EditorState, type Extension } from "@codemirror/state";
import { EditorView } from "@codemirror/view";

import { css } from "../../../styled-system/css";
import { codeTheme } from "./codemirrorTheme";
import { numberedFrom } from "./lineNumbering";
import { langExtension } from "./langByExtension";
import { type PaneMarks, useMarkedPane } from "./markGutter";

const host = css({
  height: "100%",
  minHeight: "0",
  overflow: "hidden",
  "& .cm-editor": { height: "100%" },
});

// An excerpt sizes to its content (`fit`) and keeps its file's line numbering (`firstLine`).
const fitHost = css({ minHeight: "0", "& .cm-editor": { height: "auto", maxHeight: "32rem" } });

export function FilePane({
  content,
  language,
  firstLine = 1,
  fit = false,
  marks,
}: {
  content: string;
  language: string;
  firstLine?: number;
  fit?: boolean;
  // Marks on file lines (the reviewer's per-hunk intent markers); absent = no mark gutter at all.
  marks?: PaneMarks;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const { portals, placement, gutterFor, drawn } = useMarkedPane(marks);

  useEffect(() => {
    const parent = ref.current;
    if (!parent) return;
    let view: EditorView | null = null;
    let disposed = false;

    // Language packs are async (code-split); guard against a late resolve after teardown.
    void langExtension(language).then((lang) => {
      if (disposed || !parent) return;
      const extensions: Extension[] = [
        gutterFor("all", firstLine),
        numberedFrom(firstLine),
        EditorState.readOnly.of(true),
        EditorView.editable.of(false),
        EditorView.lineWrapping,
        codeTheme,
      ];
      if (lang) extensions.push(lang);
      view = new EditorView({ parent, state: EditorState.create({ doc: content, extensions }) });
      drawn({ after: { view, first: firstLine } });
    });

    return () => {
      disposed = true;
      drawn(null);
      view?.destroy();
    };
    // `placement` rebuilds the pane when a mark moves (the marks themselves are read at build).
  }, [content, language, firstLine, placement, gutterFor, drawn]);

  return (
    <>
      <div ref={ref} className={fit ? fitHost : host} data-testid="file-pane" />
      {portals}
    </>
  );
}

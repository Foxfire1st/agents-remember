// Reusable read-only CodeMirror 6 pane — the shared code primitive. L4's Change-Set Viewer
// reuses it by swapping the single EditorView for @codemirror/merge (same editor core + the
// same HighlightStyle => identical tokens across plain + diff). The EditorView is created
// imperatively in an effect and torn down on unmount / content change.
import { useEffect, useRef } from "react";
import { EditorState, type Extension, type Text } from "@codemirror/state";
import { Decoration, EditorView } from "@codemirror/view";

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
  highlightedLines,
}: {
  content: string;
  language: string;
  firstLine?: number;
  fit?: boolean;
  // Marks on file lines (the reviewer's per-hunk intent markers); absent = no mark gutter at all.
  marks?: PaneMarks;
  highlightedLines?: readonly [number, number];
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
      const doc = EditorState.create({ doc: content }).doc;
      if (highlightedLines) extensions.push(citationHighlight(doc, highlightedLines, firstLine));
      view = new EditorView({ parent, state: EditorState.create({ doc: content, extensions }) });
      if (highlightedLines)
        view.dispatch({
          effects: EditorView.scrollIntoView(
            doc.line(Math.max(1, Math.min(doc.lines, highlightedLines[0] - firstLine + 1))).from,
            { y: "start" },
          ),
        });
      drawn({ after: { view, first: firstLine } });
    });

    return () => {
      disposed = true;
      drawn(null);
      view?.destroy();
    };
    // `placement` rebuilds the pane when a mark moves (the marks themselves are read at build).
  }, [content, language, firstLine, placement, gutterFor, drawn, highlightedLines]);

  return (
    <>
      <div ref={ref} className={fit ? fitHost : host} data-testid="file-pane" />
      {portals}
    </>
  );
}

function citationHighlight(
  doc: Text,
  [first, last]: readonly [number, number],
  firstLine: number,
): Extension {
  const lines = [];
  for (
    let line = Math.max(1, first - firstLine + 1);
    line <= Math.min(doc.lines, last - firstLine + 1);
    line++
  )
    lines.push(
      Decoration.line({
        attributes: {
          class: "cm-citedLine",
          "data-located": "true",
          "data-line": String(line + firstLine - 1),
        },
      }).range(doc.line(line).from),
    );
  return [
    EditorView.decorations.of(Decoration.set(lines)),
    EditorView.theme({
      ".cm-citedLine": { background: "color-mix(in oklab, var(--cyan) 15%, transparent)" },
    }),
  ];
}

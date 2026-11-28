// Reusable read-only CodeMirror 6 pane — the shared code primitive. The Change-Set Viewer
// reuses the same theme and server-computed tree-sitter highlight decorations via @codemirror/merge
// so tokens are identical across plain + diff panes. Highlights reconfigure in place through a
// Compartment without rebuilding the EditorView across unchanged polls.
import { useEffect, useLayoutEffect, useRef } from "react";
import { Compartment, EditorState, type Extension, type Text } from "@codemirror/state";
import { Decoration, EditorView } from "@codemirror/view";

import { css } from "../../../styled-system/css";
import type { SyntaxHighlights } from "../../data/highlights";
import { codeTheme } from "./codemirrorTheme";
import { numberedFrom } from "./lineNumbering";
import { highlightDigest, serverHighlightsExtension } from "./serverHighlights";
import { type PaneMarks, useMarkedPane } from "./markGutter";
import { trayLineGutter, type FileLineTarget } from "../../tray/fileLineGutter";

const host = css({
  height: "100%",
  minHeight: "0",
  overflow: "hidden",
  "& .cm-editor": { height: "100%" },
});

// An excerpt sizes to its content (`fit`) and keeps its file's line numbering (`firstLine`). It has
// no scroll box of its own (MIK-R89 v2 rule 1): the editor grows to its lines, its wrapped lines
// never overflow, and the review page's one reading scroller scrolls it. Only the review page's panes
// fit (its cards, statements and guarantees).
const fitHost = css({
  minHeight: "0",
  "& .cm-editor": { height: "auto" },
  // CodeMirror's base theme sets `overflow-x: auto` and is injected after this sheet.
  "& .cm-scroller": { overflow: "visible !important" },
});

interface FilePaneProps {
  content: string;
  language: string;
  highlights?: SyntaxHighlights | null;
  firstLine?: number;
  fit?: boolean;
  // Marks on file lines (the reviewer's per-hunk intent markers); absent = no mark gutter at all.
  marks?: PaneMarks;
  highlightedLines?: readonly [number, number];
  // The File Viewer's file: a hover ⊕ in the line gutter takes its lines into the AR tray
  // (MIK-R188 rule 1). Absent = no tray gutter (excerpts, the reviewer's cards, the Knowledge page).
  trayLines?: FileLineTarget;
}

function buildExtensions(args: {
  gutter: Extension;
  firstLine: number;
  tray?: FileLineTarget;
  slot: Compartment;
  highlights?: SyntaxHighlights | null;
  doc: Text;
  highlightedLines?: readonly [number, number];
}): Extension[] {
  const { gutter, firstLine, tray, slot, highlights, doc, highlightedLines } = args;
  const extensions: Extension[] = [
    gutter,
    numberedFrom(firstLine),
    tray ? trayLineGutter(tray, firstLine) : [],
    EditorState.readOnly.of(true),
    EditorView.editable.of(false),
    EditorView.lineWrapping,
    codeTheme,
    slot.of(serverHighlightsExtension(highlights, doc.length)),
  ];
  if (highlightedLines) extensions.push(citationHighlight(doc, highlightedLines, firstLine));
  return extensions;
}

export function FilePane({
  content,
  language: _language,
  highlights,
  firstLine = 1,
  fit = false,
  marks,
  highlightedLines,
  trayLines,
}: FilePaneProps) {
  const ref = useRef<HTMLDivElement>(null);
  const { portals, placement, gutterFor, drawn } = useMarkedPane(marks);
  const trayRepository = trayLines?.repository;
  const trayScope = trayLines?.scope;
  const trayPath = trayLines?.path;
  const latestHighlights = useRef<SyntaxHighlights | null | undefined>(highlights);
  useLayoutEffect(() => {
    latestHighlights.current = highlights;
  });
  const built = useRef<{ view: EditorView; slot: Compartment } | null>(null);
  const digest = highlightDigest(highlights);

  useEffect(() => {
    const parent = ref.current;
    if (!parent) return;
    const slot = new Compartment();
    const doc = EditorState.create({ doc: content }).doc;
    const tray =
      trayRepository && trayScope && trayPath
        ? { repository: trayRepository, scope: trayScope, path: trayPath }
        : undefined;
    const extensions = buildExtensions({
      gutter: gutterFor("all", firstLine),
      firstLine,
      tray,
      slot,
      highlights: latestHighlights.current,
      doc,
      highlightedLines,
    });
    const view = new EditorView({ parent, state: EditorState.create({ doc: content, extensions }) });
    built.current = { view, slot };
    if (highlightedLines) {
      const targetLine = Math.max(1, Math.min(doc.lines, highlightedLines[0] - firstLine + 1));
      view.dispatch({ effects: EditorView.scrollIntoView(doc.line(targetLine).from, { y: "start" }) });
    }
    drawn({ after: { view, first: firstLine } });

    return () => {
      drawn(null);
      view.destroy();
      built.current = null;
    };
    // `placement` rebuilds the pane when a mark moves (the marks themselves are read at build).
    // `highlights` is deliberately omitted: updates reconfigure `slot` in place below.
  }, [content, firstLine, placement, gutterFor, drawn, highlightedLines, trayRepository, trayScope, trayPath]);

  useEffect(() => {
    const current = built.current;
    if (!current) return;
    current.view.dispatch({
      effects: current.slot.reconfigure(
        serverHighlightsExtension(latestHighlights.current, current.view.state.doc.length),
      ),
    });
  }, [digest]);

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

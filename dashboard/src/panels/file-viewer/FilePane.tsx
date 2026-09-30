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
}: {
  content: string;
  language: string;
  firstLine?: number;
  fit?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const parent = ref.current;
    if (!parent) return;
    let view: EditorView | null = null;
    let disposed = false;

    // Language packs are async (code-split); guard against a late resolve after teardown.
    void langExtension(language).then((lang) => {
      if (disposed || !parent) return;
      const extensions: Extension[] = [
        numberedFrom(firstLine),
        EditorState.readOnly.of(true),
        EditorView.editable.of(false),
        EditorView.lineWrapping,
        codeTheme,
      ];
      if (lang) extensions.push(lang);
      view = new EditorView({ parent, state: EditorState.create({ doc: content, extensions }) });
    });

    return () => {
      disposed = true;
      view?.destroy();
    };
  }, [content, language, firstLine]);

  return <div ref={ref} className={fit ? fitHost : host} data-testid="file-pane" />;
}

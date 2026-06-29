// Reusable read-only CodeMirror 6 pane — the shared code primitive. L4's Change-Set Viewer
// reuses it by swapping the single EditorView for @codemirror/merge (same editor core + the
// same HighlightStyle => identical tokens across plain + diff). The EditorView is created
// imperatively in an effect and torn down on unmount / content change.
import { useEffect, useRef } from "react";
import { EditorState, type Extension } from "@codemirror/state";
import { EditorView, lineNumbers } from "@codemirror/view";

import { css } from "../../../styled-system/css";
import { codeTheme } from "./codemirrorTheme";
import { langExtension } from "./langByExtension";

const host = css({
  height: "100%",
  minHeight: "0",
  overflow: "hidden",
  "& .cm-editor": { height: "100%" },
});

export function FilePane({ content, language }: { content: string; language: string }) {
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
        lineNumbers(),
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
  }, [content, language]);

  return <div ref={ref} className={host} data-testid="file-pane" />;
}

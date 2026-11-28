// Map the podracer OKLCH palette (styles/tokens.css :root vars) onto CodeMirror 6 so the
// read-only code pane matches the cockpit. Token classes are foreground-only (plus italic
// comments / bold headings) so citation highlights and merge diff backgrounds stay intact.
// No CSS animation here (GSAP/Motion only — master invariant).
import type { Extension } from "@codemirror/state";
import { EditorView } from "@codemirror/view";

const chrome = EditorView.theme(
  {
    "&": {
      backgroundColor: "var(--bg-panel)",
      color: "var(--ink)",
      height: "100%",
      fontSize: "0.8rem",
    },
    ".cm-scroller": { fontFamily: "var(--font-mono)", lineHeight: "1.55" },
    ".cm-gutters": { backgroundColor: "var(--bg)", color: "var(--grid)", border: "none" },
    ".cm-activeLine": { backgroundColor: "transparent" },
    ".cm-activeLineGutter": { backgroundColor: "transparent" },
    ".cm-selectionBackground, &.cm-focused .cm-selectionBackground": {
      backgroundColor: "color-mix(in oklab, var(--amber) 22%, transparent)",
    },
    "&.cm-focused": { outline: "none" },
    // Server-computed tree-sitter token classes (foreground-only so line/diff tints show through).
    ".cm-ar-tok-keyword": { color: "var(--amber)" },
    ".cm-ar-tok-string": { color: "var(--mint)" },
    // Dimmed-but-readable ink, NOT --grid (the 0.30-L gutter/border colour, which is near-invisible
    // on the 0.16-L background); a mid-lightness ink/bg blend keeps comments legible yet recessive.
    ".cm-ar-tok-comment": {
      color: "color-mix(in oklab, var(--ink) 60%, var(--bg))",
      fontStyle: "italic",
    },
    ".cm-ar-tok-number": { color: "var(--cyan)" },
    ".cm-ar-tok-function": { color: "var(--cyan)" },
    ".cm-ar-tok-type": { color: "var(--amber)" },
    ".cm-ar-tok-tag": { color: "var(--amber)" },
    ".cm-ar-tok-attribute": { color: "var(--ink)" },
    ".cm-ar-tok-heading": { color: "var(--amber)", fontWeight: "600" },
    // Punctuation/brackets/operators: readable but recessive (just under identifier brightness) —
    // NOT --grid (the gutter/border tone, near-invisible on the bg). Gutter line numbers keep --grid.
    ".cm-ar-tok-operator": { color: "color-mix(in oklab, var(--ink) 75%, var(--bg))" },
    ".cm-ar-tok-punctuation": { color: "color-mix(in oklab, var(--ink) 75%, var(--bg))" },
  },
  { dark: true },
);

// The read-only theme bundle: chrome + server-computed token classes.
export const codeTheme: Extension = chrome;

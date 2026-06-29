// Map the podracer OKLCH palette (styles/tokens.css :root vars) onto CodeMirror 6 so the
// read-only code pane matches the cockpit. EditorView.theme owns the chrome; HighlightStyle
// owns the syntax tokens. No CSS animation here (GSAP/Motion only — master invariant).
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import type { Extension } from "@codemirror/state";
import { EditorView } from "@codemirror/view";
import { tags as t } from "@lezer/highlight";

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
  },
  { dark: true },
);

const highlight = HighlightStyle.define([
  { tag: t.keyword, color: "var(--amber)" },
  { tag: [t.string, t.special(t.string)], color: "var(--mint)" },
  // Dimmed-but-readable ink, NOT --grid (the 0.30-L gutter/border colour, which is near-invisible
  // on the 0.16-L background); a mid-lightness ink/bg blend keeps comments legible yet recessive.
  {
    tag: [t.comment, t.lineComment, t.blockComment],
    color: "color-mix(in oklab, var(--ink) 60%, var(--bg))",
    fontStyle: "italic",
  },
  { tag: [t.number, t.bool, t.null], color: "var(--cyan)" },
  { tag: [t.function(t.variableName), t.definition(t.variableName)], color: "var(--cyan)" },
  { tag: [t.typeName, t.className, t.tagName, t.namespace], color: "var(--amber)" },
  { tag: [t.propertyName, t.attributeName], color: "var(--ink)" },
  // Punctuation/brackets/operators: readable but recessive (just under identifier brightness) —
  // NOT --grid (the gutter/border tone, near-invisible on the bg). Gutter line numbers keep --grid.
  { tag: [t.operator, t.punctuation, t.bracket], color: "color-mix(in oklab, var(--ink) 75%, var(--bg))" },
]);

// The read-only theme bundle: chrome + syntax tokens.
export const codeTheme: Extension = [chrome, syntaxHighlighting(highlight)];

// Map server-computed UTF-16 highlight spans onto CodeMirror 6 Decoration.mark ranges.
// All offsets are safely clamped to the active document length so truncated excerpts or
// stripped trailing newlines never throw inside RangeSetBuilder.
import { type Extension, RangeSetBuilder } from "@codemirror/state";
import { Decoration, type DecorationSet, EditorView } from "@codemirror/view";

import type { HighlightKind, SyntaxHighlights } from "../../data/highlights";

const KNOWN_KINDS: ReadonlySet<string> = new Set<HighlightKind>([
  "keyword",
  "string",
  "comment",
  "number",
  "function",
  "type",
  "operator",
  "punctuation",
  "tag",
  "attribute",
  "heading",
]);

const MARKS = new Map<string, Decoration>();

function markForKind(kind: unknown): Decoration | null {
  if (typeof kind !== "string" || !KNOWN_KINDS.has(kind)) return null;
  let mark = MARKS.get(kind);
  if (!mark) {
    mark = Decoration.mark({ class: `cm-ar-tok-${kind}` });
    MARKS.set(kind, mark);
  }
  return mark;
}

function clampSpan(
  rawStart: unknown,
  rawEnd: unknown,
  prevEnd: number,
  docLength: number,
): [number, number] | null {
  if (!Number.isFinite(rawStart) || !Number.isFinite(rawEnd)) return null;
  const from = Math.max(prevEnd, Math.min(docLength, Math.trunc(rawStart as number)));
  const to = Math.max(0, Math.min(docLength, Math.trunc(rawEnd as number)));
  return to > from ? [from, to] : null;
}

export function highlightDigest(highlights: SyntaxHighlights | null | undefined): string {
  return highlights?.digest ?? "";
}

export function buildHighlightDecorations(
  highlights: SyntaxHighlights | null | undefined,
  docLength: number,
): DecorationSet {
  if (!highlights || docLength <= 0 || !highlights.kinds.length || highlights.spans.length < 3) {
    return Decoration.none;
  }
  const { kinds, spans } = highlights;
  const builder = new RangeSetBuilder<Decoration>();
  let prevEnd = 0;
  for (let i = 0; i + 2 < spans.length; i += 3) {
    const range = clampSpan(spans[i], spans[i + 1], prevEnd, docLength);
    if (!range) continue;
    const mark = markForKind(kinds[spans[i + 2]]);
    if (!mark) continue;
    builder.add(range[0], range[1], mark);
    prevEnd = range[1];
  }
  return builder.finish();
}

export function serverHighlightsExtension(
  highlights: SyntaxHighlights | null | undefined,
  docLength: number,
): Extension {
  return EditorView.decorations.of(buildHighlightDecorations(highlights, docLength));
}

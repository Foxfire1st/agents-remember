// Diff block (design §12.2). Full by default up to a logical source-line threshold, then a per-file
// summary plus a real text disclosure button with an exact hidden line count (known from source
// lines). Whitespace is preserved inside a labeled, keyboard-scrollable overflow region — a wide diff
// never forces page-level horizontal scroll (§12.2, §14.3).

import { useState } from "react";

import { css } from "../../../../styled-system/css";
import { ClampButton, sourceLineCount, useClampIds } from "./primitives";

const DIFF_THRESHOLD_LINES = 24;

const wrap = css({ display: "grid", gap: "0.15rem", minWidth: "0" });
const pathLabel = css({ fontSize: "0.68rem", color: "cyan", fontFamily: "mono", overflowWrap: "anywhere" });
const region = css({
  maxInlineSize: "100%",
  overflowX: "auto",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  background: "color-mix(in oklch, token(colors.grid) 25%, transparent)",
});
const pre = css({ margin: "0", padding: "0.35rem 0.5rem", fontFamily: "mono", fontSize: "0.72rem", whiteSpace: "pre", lineHeight: "1.4" });
const clampedPre = css({ maxHeight: "18rem", overflow: "hidden" });
const add = css({ color: "mint" });
const del = css({ color: "alarm" });

function DiffLine({ line }: { line: string }) {
  if (line.startsWith("+") && !line.startsWith("+++")) return <span className={add}>{line}</span>;
  if (line.startsWith("-") && !line.startsWith("---")) return <span className={del}>{line}</span>;
  return <span>{line}</span>;
}

export function DiffBlock({
  path,
  unified,
  oldText,
  newText,
}: {
  path?: string;
  unified?: string;
  oldText?: string;
  newText?: string;
}) {
  const { regionId } = useClampIds();
  const [expanded, setExpanded] = useState(false);
  const body = unified ?? synthesizeUnified(oldText, newText);
  const lines = body.length > 0 ? body.split("\n") : [];
  const total = sourceLineCount(body);
  const clampable = total > DIFF_THRESHOLD_LINES;
  const hiddenLines = clampable && !expanded ? Math.max(0, total - DIFF_THRESHOLD_LINES) : 0;
  const visibleLines = clampable && !expanded ? lines.slice(0, DIFF_THRESHOLD_LINES) : lines;

  return (
    <div className={wrap} data-testid="conversation-diff">
      {path ? (
        <span className={pathLabel} title={path}>
          {path}
        </span>
      ) : null}
      <div className={region} tabIndex={-1} role="group" aria-label={path ? `diff of ${path}` : "diff"}>
        <pre id={regionId} className={`${pre} ${clampable && !expanded ? clampedPre : ""}`.trim()}>
          {visibleLines.map((line, index) => (
            <DiffLine key={index} line={line + (index < visibleLines.length - 1 ? "\n" : "")} />
          ))}
        </pre>
      </div>
      {clampable ? (
        <ClampButton
          expanded={expanded}
          onToggle={() => setExpanded((value) => !value)}
          controlsId={regionId}
          hiddenLines={hiddenLines}
        />
      ) : null}
    </div>
  );
}

function synthesizeUnified(oldText?: string, newText?: string): string {
  // The server prefers `unified`; when only old/new are present, show a minimal labeled pair rather
  // than fabricating hunk headers (honesty over guessed diff math).
  const parts: string[] = [];
  if (oldText !== undefined) parts.push(...oldText.split("\n").map((line) => `- ${line}`));
  if (newText !== undefined) parts.push(...newText.split("\n").map((line) => `+ ${line}`));
  return parts.join("\n");
}

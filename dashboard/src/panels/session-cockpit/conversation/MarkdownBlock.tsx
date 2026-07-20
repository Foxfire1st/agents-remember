// Streaming-safe Markdown (design §12.3). One stable memoized component renders the whole block from
// a single markdown string, so token deltas re-render text without remounting the DOM (no typewriter
// re-mount per token). Incomplete fences/tables render best-effort while streaming; completion is one
// final stable render. Prose wraps unbroken tokens within the stage; code lives in its own labeled,
// keyboard-scrollable overflow region so a long line never widens the page (§12.2, §14.3).

import { memo } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { css } from "../../../../styled-system/css";

const prose = css({
  minInlineSize: "0",
  maxInlineSize: "100%",
  overflowWrap: "anywhere",
  fontSize: "0.8rem",
  lineHeight: "1.5",
  color: "ink",
  "& p": { marginBlock: "0.25rem" },
  "& ul, & ol": { marginBlock: "0.25rem", paddingInlineStart: "1.1rem" },
  "& li": { marginBlock: "0.1rem" },
  "& h1, & h2, & h3, & h4": {
    fontSize: "0.85rem",
    fontWeight: "600",
    marginBlock: "0.4rem 0.2rem",
    color: "ink",
  },
  "& a": { color: "cyan", textDecoration: "underline", overflowWrap: "anywhere" },
  "& code": {
    fontFamily: "mono",
    fontSize: "0.76rem",
    background: "color-mix(in oklch, token(colors.grid) 40%, transparent)",
    paddingInline: "0.2rem",
    borderRadius: "2px",
  },
  // Fenced code: its own overflow region — whitespace preserved, keyboard-scrollable, no page overflow.
  "& pre": {
    maxInlineSize: "100%",
    overflowX: "auto",
    background: "color-mix(in oklch, token(colors.grid) 30%, transparent)",
    borderWidth: "1px",
    borderStyle: "solid",
    borderColor: "grid",
    borderRadius: "2px",
    padding: "0.4rem 0.5rem",
    marginBlock: "0.3rem",
  },
  "& pre code": { background: "transparent", padding: "0", whiteSpace: "pre" },
  "& blockquote": {
    borderLeftWidth: "2px",
    borderLeftStyle: "solid",
    borderLeftColor: "grid",
    paddingInlineStart: "0.5rem",
    color: "muted",
    marginBlock: "0.3rem",
  },
  "& table": { borderCollapse: "collapse", display: "block", overflowX: "auto", maxInlineSize: "100%" },
  "& th, & td": {
    borderWidth: "1px",
    borderStyle: "solid",
    borderColor: "grid",
    padding: "0.15rem 0.35rem",
    textAlign: "left",
  },
});

function MarkdownBlockImpl({ markdown, testId }: { markdown: string; testId?: string }) {
  return (
    <div
      className={prose}
      // A fenced code region can overflow; keep it keyboard-focusable with a stable accessible label.
      tabIndex={-1}
      data-testid={testId ?? "conversation-markdown"}
    >
      <Markdown remarkPlugins={[remarkGfm]}>{markdown}</Markdown>
    </div>
  );
}

// Re-render only when the markdown text actually changes — the reducer mutates the string in place
// per delta, so a stable memo keeps React notification batched (§12.3).
export const MarkdownBlock = memo(MarkdownBlockImpl);

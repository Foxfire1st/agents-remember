// Operator / assistant / user / system message item (design §12.2). Full streaming Markdown with no
// card noise; a completed long assistant message clamps ONLY behind a real text button with an exact
// source-line count (§14.2). Images always render with a non-empty accessible alt plus its
// supplied-vs-fallback provenance (§6.6) — an image is never shown with missing alt.

import { useState } from "react";

import { css } from "../../../../styled-system/css";
import type { ConversationContentBlock, ConversationItem } from "../../../data/conversation/types";
import { MarkdownBlock } from "./MarkdownBlock";
import { ClampButton, SourceBadge, sourceLineCount, useClampIds } from "./primitives";

const CLAMP_THRESHOLD_LINES = 40;

const wrap = css({ display: "grid", gap: "0.2rem", minWidth: "0" });
const userWrap = css({
  display: "grid",
  gap: "0.2rem",
  minWidth: "0",
  borderLeftWidth: "2px",
  borderLeftStyle: "solid",
  borderLeftColor: "color-mix(in oklch, token(colors.amber) 55%, transparent)",
  paddingInlineStart: "0.5rem",
  background: "color-mix(in oklch, token(colors.amber) 5%, transparent)",
});
const headRow = css({ display: "flex", alignItems: "baseline", gap: "0.4rem", minWidth: "0" });
const roleGlyph = css({ color: "amber", flex: "none", fontSize: "0.8rem" });
// A STREAMING message carries ToolItem's phase grammar (FB7.4): a phase-colored ● plus the dim
// lowercase wire word — color is never the only carrier.
const streamDot = css({ flex: "none", fontSize: "0.66rem", lineHeight: "1", color: "cyan" });
const phaseWord = css({ flex: "none", fontSize: "0.7rem", color: "muted" });
const clampRegion = css({ position: "relative" });
const fileRow = css({
  display: "inline-flex",
  gap: "0.35rem",
  alignItems: "baseline",
  fontSize: "0.74rem",
  color: "cyan",
});
const imageRef = css({
  display: "inline-flex",
  gap: "0.35rem",
  alignItems: "baseline",
  fontSize: "0.74rem",
  color: "cyan",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  paddingInline: "0.4rem",
  paddingBlock: "0.15rem",
});
const imageMeta = css({ fontSize: "0.62rem", color: "muted" });

function combinedSourceText(blocks: readonly ConversationContentBlock[]): string {
  return blocks
    .map((block) => {
      if (block.type === "markdown" || block.type === "text") {
        return block.type === "markdown" ? block.markdown : block.text;
      }
      return "";
    })
    .filter((text) => text.length > 0)
    .join("\n");
}

function Block({ block }: { block: ConversationContentBlock }) {
  switch (block.type) {
    case "markdown":
      return <MarkdownBlock markdown={block.markdown} />;
    case "text":
      return <MarkdownBlock markdown={block.text} />;
    case "code":
      return <MarkdownBlock markdown={"```" + (block.language ?? "") + "\n" + block.text + "\n```"} />;
    case "image-ref":
      // No asset-read route exists in the backend yet (the design defines none; `assetId` is a
      // submit-side reference — F11). Render the required accessible alt + provenance as a labeled
      // reference rather than an invented `/api/assets/...` URL that would 404. When an asset-read
      // seam lands, swap this for an <img> with the same alt/provenance.
      return (
        <span className={imageRef} data-testid="conversation-image-ref">
          <span aria-hidden="true">🖼</span>
          <span data-testid="image-alt-provenance">
            {block.alt}
            {block.altProvenance === "filename-mime-fallback" ? " · filename/type fallback" : ""}
          </span>
          <span className={imageMeta}>{block.mimeType}</span>
        </span>
      );
    case "file-ref":
    case "resource-ref":
      return (
        <span className={fileRow} data-testid="conversation-file-ref">
          <span aria-hidden="true">📎</span>
          <span>{block.name}</span>
          {block.mimeType ? <span className={imageMeta}>{block.mimeType}</span> : null}
        </span>
      );
    default:
      return null;
  }
}

export function MessageItem({ item }: { item: ConversationItem }) {
  const { regionId } = useClampIds();
  const [expanded, setExpanded] = useState(false);
  const sourceText = combinedSourceText(item.blocks);
  const isAssistant = item.role === "assistant";
  const completed = item.phase === "completed";
  const { clampable, collapsed, hiddenLines, collapsedText } = messageClampState(
    sourceText,
    isAssistant,
    completed,
    expanded,
  );
  // Clamp by SLICING to the logical line threshold (F12), so the `+N lines` count is exactly what is
  // hidden — never a maxHeight visual clamp whose count diverges from the pixels hidden.
  const isUser = item.role === "user";

  return (
    <div className={isUser ? userWrap : wrap}>
      <div className={headRow}>
        {isUser ? <span className={roleGlyph} aria-hidden="true">&gt;</span> : null}
        <SourceBadge lane={item.lane} source={item.source} />
        {item.phase === "streaming" ? (
          <>
            <span className={streamDot} aria-hidden="true">
              ●
            </span>
            <span className={phaseWord} data-testid="message-phase">
              {item.phase}
            </span>
          </>
        ) : null}
      </div>
      <div className={clampRegion}>
        <div id={regionId}>
          {collapsed ? (
            <MarkdownBlock markdown={collapsedText} />
          ) : (
            item.blocks.map((block) => <Block key={block.blockId} block={block} />)
          )}
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
    </div>
  );
}

function messageClampState(
  sourceText: string,
  isAssistant: boolean,
  completed: boolean,
  expanded: boolean,
): { clampable: boolean; collapsed: boolean; hiddenLines: number; collapsedText: string } {
  const totalLines = sourceLineCount(sourceText);
  const clampable = isAssistant && completed && totalLines > CLAMP_THRESHOLD_LINES;
  const collapsed = clampable && !expanded;
  return {
    clampable,
    collapsed,
    hiddenLines: collapsed ? Math.max(0, totalLines - CLAMP_THRESHOLD_LINES) : 0,
    collapsedText: collapsed
      ? sourceText.split("\n").slice(0, CLAMP_THRESHOLD_LINES).join("\n")
      : "",
  };
}

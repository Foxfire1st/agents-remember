// Thinking item (design §12.2, §14.2). Full inline, dim/italic; NEVER clamped behind Show more.
// Governed by the global hide-thinking preference (instant, non-destructive — content stays in the
// store, only rendering is suppressed). A large thinking body uses content-visibility so it stays
// sequentially readable/navigable without one enormous forced-layout DOM node.

import { css } from "../../../../styled-system/css";
import { useHideThinking } from "../../../data/conversation/thinkingPreference";
import type { ConversationContentBlock, ConversationItem } from "../../../data/conversation/types";
import { MarkdownBlock } from "./MarkdownBlock";

const wrap = css({
  display: "grid",
  gap: "0.15rem",
  minWidth: "0",
  color: "muted",
  fontStyle: "italic",
  // Bounded rendered segments for a pathological thinking body — accessible, not deleted (§14.2).
  contentVisibility: "auto",
  containIntrinsicSize: "auto 4rem",
});
// FB7.4 — Claude Code's inline thinking marker (`✻ thinking`), lowercase, not a boxed uppercase label.
const label = css({
  fontSize: "0.66rem",
  color: "muted",
  fontStyle: "normal",
});
const hiddenMarker = css({ fontSize: "0.64rem", color: "dormant", fontStyle: "italic" });

function thinkingText(block: ConversationContentBlock): string | null {
  if (block.type === "thinking" || block.type === "markdown") return block.markdown;
  if (block.type === "text") return block.text;
  return null;
}

export function ThinkingItem({ item }: { item: ConversationItem }) {
  const hidden = useHideThinking();
  if (hidden) {
    return (
      <div className={hiddenMarker} data-testid="thinking-hidden">
        thinking hidden
      </div>
    );
  }
  return (
    <div className={wrap} data-testid="conversation-thinking">
      <span className={label}>
        <span aria-hidden="true">✻</span> thinking
      </span>
      {item.blocks.map((block) => {
        const text = thinkingText(block);
        if (text === null) return null;
        return <MarkdownBlock key={block.blockId} markdown={text} testId="thinking-markdown" />;
      })}
    </div>
  );
}

// Kind dispatcher only (design §12.1). It owns no data/paging/streaming/cursor logic — it maps a
// normalized item to its block-grammar renderer, and provides the stable accessible name the feed
// article uses for aria-labelledby (§14.2).

import { memo } from "react";

import type { ConversationItem } from "../../../data/conversation/types";
import { InteractionItem } from "./InteractionItem";
import { MessageItem } from "./MessageItem";
import { ThinkingItem } from "./ThinkingItem";
import { ToolItem } from "./ToolItem";
import { TurnResultItem } from "./TurnResultItem";

/** A stable, human-readable name for the feed article — text label, never color-only (§14.2). */
export function itemAccessibleName(item: ConversationItem): string {
  const ordinal = `#${item.globalOrdinal}`;
  switch (item.kind) {
    case "message":
      return `${ordinal} ${item.role} message`;
    case "thinking":
      return `${ordinal} thinking`;
    case "plan":
      return `${ordinal} plan`;
    case "tool-call":
    case "tool-result":
      return `${ordinal} tool ${item.phase}`;
    case "interaction":
      return `${ordinal} interaction ${item.phase}`;
    case "turn-result":
      return `${ordinal} turn ${item.phase}`;
    case "error":
      return `${ordinal} error`;
    case "notice":
      return `${ordinal} notice`;
    case "telemetry":
      return `${ordinal} telemetry`;
    case "unknown-vendor":
      return `${ordinal} unknown vendor event`;
    default:
      return `${ordinal} item`;
  }
}

function ConversationItemViewImpl({ item }: { item: ConversationItem }) {
  switch (item.kind) {
    case "message":
    case "plan":
      return <MessageItem item={item} />;
    case "thinking":
      return <ThinkingItem item={item} />;
    case "tool-call":
    case "tool-result":
      return <ToolItem item={item} />;
    case "interaction":
      return <InteractionItem item={item} />;
    case "turn-result":
    case "error":
    case "notice":
    case "telemetry":
    case "unknown-vendor":
      return <TurnResultItem item={item} />;
    default:
      return <TurnResultItem item={item} />;
  }
}

// Re-render an item row only when its identity/revision changes — keeps 10k-item timelines cheap.
export const ConversationItemView = memo(
  ConversationItemViewImpl,
  (prev, next) => prev.item === next.item,
);

// Kind dispatcher only (design §12.1). It owns no data/paging/streaming/cursor logic — it maps a
// normalized item to its block-grammar renderer, and provides the stable accessible name the feed
// article uses for aria-labelledby (§14.2).

import { memo, type ReactNode } from "react";

import type { ConversationItem } from "../../../data/conversation/types";
import { InteractionItem } from "./InteractionItem";
import { MessageItem } from "./MessageItem";
import { ThinkingItem } from "./ThinkingItem";
import { ToolItem } from "./ToolItem";
import { TurnResultItem } from "./TurnResultItem";

/** A stable, human-readable name for the feed article — text label, never color-only (§14.2). */
const KIND_NAMES: Record<string, string> = {
  thinking: "thinking",
  plan: "plan",
  interaction: "interaction",
  error: "error",
  notice: "notice",
  telemetry: "telemetry",
  "unknown-vendor": "unknown vendor event",
};

function accessibleKindLabel(item: ConversationItem): string {
  switch (item.kind) {
    case "message":
      return `${item.role} message`;
    case "tool-call":
    case "tool-result":
      return `tool ${item.phase}`;
    case "interaction":
      return `interaction ${item.phase}`;
    case "turn-result":
      return `turn ${item.phase}`;
    default:
      return KIND_NAMES[item.kind] ?? "item";
  }
}

export function itemAccessibleName(item: ConversationItem): string {
  const ordinal = `#${item.globalOrdinal}`;
  return `${ordinal} ${accessibleKindLabel(item)}`;
}

const ITEM_VIEWS: Record<string, (props: { item: ConversationItem }) => ReactNode> = {
  message: MessageItem,
  plan: MessageItem,
  thinking: ThinkingItem,
  "tool-call": ToolItem,
  "tool-result": ToolItem,
  interaction: InteractionItem,
  "turn-result": TurnResultItem,
  error: TurnResultItem,
  notice: TurnResultItem,
  telemetry: TurnResultItem,
  "unknown-vendor": TurnResultItem,
};

function ConversationItemViewImpl({ item }: { item: ConversationItem }) {
  const View = ITEM_VIEWS[item.kind] ?? TurnResultItem;
  return <View item={item} />;
}

// Re-render an item row only when its identity/revision changes — keeps 10k-item timelines cheap.
export const ConversationItemView = memo(
  ConversationItemViewImpl,
  (prev, next) => prev.item === next.item,
);

// Shared geometry shim + message factory for the ConversationTimeline test family. jsdom has no
// layout engine, so @tanstack/react-virtual measures a 0-height viewport and renders no rows;
// every split file registers the fixed geometry via this module (beforeAll at import time).
import { beforeAll } from "vitest";

// jsdom has no layout engine, so @tanstack/react-virtual measures a 0-height viewport and renders no
// rows. Give elements a fixed geometry for this file so the virtualizer produces the feed rows the
// semantics tests assert against (a standard jsdom + tanstack-virtual shim).
beforeAll(() => {
  // tanstack-virtual reads offsetWidth/offsetHeight (getRect) synchronously on mount.
  Object.defineProperty(HTMLElement.prototype, "offsetHeight", { configurable: true, value: 600 });
  Object.defineProperty(HTMLElement.prototype, "offsetWidth", { configurable: true, value: 800 });
});

import type { ConversationItem } from "../../../../data/conversation/types";

export function msg(overrides: Partial<ConversationItem> & { itemId: string; globalOrdinal: number }): ConversationItem {
  return {
    revision: 1,
    lane: "harness",
    source: "harness-live",
    provenance: { strength: "exact", origin: "codex" },
    role: "assistant",
    kind: "message",
    phase: "completed",
    blocks: [{ blockId: `${overrides.itemId}-b`, type: "markdown", markdown: "hi" }],
    ...overrides,
  };
}

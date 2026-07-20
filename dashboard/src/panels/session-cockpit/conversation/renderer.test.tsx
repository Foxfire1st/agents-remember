import axe from "axe-core";
import { render, screen, within } from "@testing-library/react";
import { beforeAll, describe, expect, it } from "vitest";

// jsdom has no layout engine, so @tanstack/react-virtual measures a 0-height viewport and renders no
// rows. Give elements a fixed geometry for this file so the virtualizer produces the feed rows the
// semantics tests assert against (a standard jsdom + tanstack-virtual shim).
beforeAll(() => {
  // tanstack-virtual reads offsetWidth/offsetHeight (getRect) synchronously on mount.
  Object.defineProperty(HTMLElement.prototype, "offsetHeight", { configurable: true, value: 600 });
  Object.defineProperty(HTMLElement.prototype, "offsetWidth", { configurable: true, value: 800 });
});

import type { ConversationItem } from "../../../data/conversation/types";
import { ConversationTimeline } from "./ConversationTimeline";
import { MessageItem } from "./MessageItem";
import { TerminalDiagnosticsDrawer } from "./TerminalDiagnosticsDrawer";

function msg(overrides: Partial<ConversationItem> & { itemId: string; globalOrdinal: number }): ConversationItem {
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

describe("ConversationTimeline — one navigable role=feed (R5, §14.2)", () => {
  it("exposes a role=feed and articles keyed to the server globalOrdinal, with aria-setsize when total is known", () => {
    render(
      <ConversationTimeline
        items={[msg({ itemId: "a", globalOrdinal: 7 }), msg({ itemId: "b", globalOrdinal: 8 })]}
        totalItems={2}
        hasOlder={false}
        busy={false}
        onLoadOlder={() => {}}
      />,
    );
    const feed = screen.getByRole("feed");
    expect(feed).not.toBeNull();
    const articles = within(feed).getAllByRole("article");
    expect(articles.length).toBeGreaterThan(0);
    // aria-posinset comes from the server ordinal, never the array index.
    expect(articles[0].getAttribute("aria-posinset")).toBe("7");
    expect(articles[0].getAttribute("aria-setsize")).toBe("2");
    expect(articles[0].getAttribute("aria-live")).toBe("off");
  });

  it("omits aria-setsize and says 'total unknown' on the pager when the total is not honestly known", () => {
    render(
      <ConversationTimeline
        items={[msg({ itemId: "a", globalOrdinal: 1 })]}
        totalItems={undefined}
        hasOlder
        busy={false}
        onLoadOlder={() => {}}
      />,
    );
    const article = screen.getAllByRole("article")[0];
    expect(article.getAttribute("aria-setsize")).toBeNull();
    expect(screen.getByTestId("conversation-load-older").textContent).toContain("total unknown");
  });
});

describe("MessageItem — grammar, images, clamp (R3, §12.2)", () => {
  it("renders an image reference with a non-empty accessible alt + provenance, and NO fabricated fetch URL (F11)", () => {
    const { container } = render(
      <MessageItem
        item={msg({
          itemId: "img",
          globalOrdinal: 1,
          blocks: [
            {
              blockId: "img-b",
              type: "image-ref",
              assetId: "asset-1",
              alt: "a bar chart of usage",
              altProvenance: "supplied-description",
              mimeType: "image/png",
            },
          ],
        })}
      />,
    );
    expect(screen.getByTestId("image-alt-provenance").textContent).toContain("a bar chart of usage");
    // No <img> is rendered (no asset-read route exists) — never an invented /api/assets URL.
    expect(container.querySelector("img")).toBeNull();
  });

  it("clamps a long completed assistant message behind a real button with an exact +N lines count", () => {
    const longText = Array.from({ length: 80 }, (_, i) => `line ${i}`).join("\n");
    render(
      <MessageItem
        item={msg({ itemId: "long", globalOrdinal: 1, blocks: [{ blockId: "l-b", type: "markdown", markdown: longText }] })}
      />,
    );
    const clamp = screen.getByTestId("conversation-clamp");
    expect(clamp.tagName).toBe("BUTTON");
    expect(clamp.getAttribute("aria-expanded")).toBe("false");
    expect(clamp.textContent).toMatch(/\+\d+ lines/);
  });

  it("badges an agent-bus delivery (origin changes interpretation) but not an ordinary operator message", () => {
    const { rerender } = render(
      <MessageItem item={msg({ itemId: "bus", globalOrdinal: 1, role: "user", lane: "agent-bus", source: "durable-inbox" })} />,
    );
    expect(screen.getByTestId("conversation-source-badge").textContent).toBe("agent bus");
    rerender(
      <MessageItem item={msg({ itemId: "op", globalOrdinal: 1, role: "user", lane: "operator", source: "cockpit-composer" })} />,
    );
    expect(screen.queryByTestId("conversation-source-badge")).toBeNull();
  });
});

describe("TerminalDiagnosticsDrawer — default off (R2/R7, §12.6)", () => {
  it("is closed by default: inert, hidden from the a11y tree, and renders no PTY frame", () => {
    render(<TerminalDiagnosticsDrawer focused={undefined} open={false} onClose={() => {}} />);
    const drawer = screen.getByTestId("terminal-diagnostics-drawer");
    expect(drawer.getAttribute("data-open")).toBe("false");
    expect(drawer.getAttribute("aria-hidden")).toBe("true");
    expect(drawer.hasAttribute("inert")).toBe(true);
    // The R7 negative proof: no diagnostic content is mounted when closed.
    expect(screen.queryByTestId("terminal-diagnostics-frame")).toBeNull();
  });
});

describe("axe — no structural accessibility violations on the rendered grammar", () => {
  it("passes axe on a small feed + a closed diagnostics drawer", async () => {
    const { container } = render(
      <div>
        <ConversationTimeline
          items={[msg({ itemId: "a", globalOrdinal: 1 }), msg({ itemId: "b", globalOrdinal: 2, role: "user", lane: "operator", source: "cockpit-composer" })]}
          totalItems={2}
          hasOlder={false}
          busy={false}
          onLoadOlder={() => {}}
        />
        <TerminalDiagnosticsDrawer focused={undefined} open={false} onClose={() => {}} />
      </div>,
    );
    const results = await axe.run(container, {
      // jsdom has no layout engine, so skip the rules that require rendered geometry.
      rules: { "color-contrast": { enabled: false }, region: { enabled: false } },
    });
    expect(results.violations).toEqual([]);
  });
});

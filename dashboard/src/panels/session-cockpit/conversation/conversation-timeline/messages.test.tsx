import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import axe from "axe-core";

import { ConversationTimeline } from "./ConversationTimeline";
import { MessageItem } from "../MessageItem";
import { TerminalDiagnosticsDrawer } from "../TerminalDiagnosticsDrawer";
import { msg } from "./test-utils";

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

  it("shows the streaming phase cue (accent dot + wire word) ONLY on a streamed message (FB7.4)", () => {
    const { rerender } = render(
      <MessageItem item={msg({ itemId: "stream", globalOrdinal: 1, phase: "streaming" })} />,
    );
    expect(screen.getByTestId("message-phase").textContent).toBe("streaming");
    rerender(
      <MessageItem item={msg({ itemId: "done", globalOrdinal: 2, phase: "completed" })} />,
    );
    expect(screen.queryByTestId("message-phase")).toBeNull();
  });
});

describe("TerminalDiagnosticsDrawer — default off (R2/R7, §12.6)", () => {
  it("is closed by default: inert, hidden from the a11y tree, and renders no PTY frame", () => {
    render(<TerminalDiagnosticsDrawer focused={undefined} open={false} onClose={() => {}} />);
    const drawer = screen.getByTestId("terminal-diagnostics-drawer");
    expect(drawer.getAttribute("data-open")).toBe("false");
    expect(drawer.getAttribute("aria-hidden")).toBe("true");
    expect(drawer.hasAttribute("inert")).toBe(true);
    // The negative proof: no diagnostic content is mounted when closed.
    expect(screen.queryByTestId("terminal-diagnostics-frame")).toBeNull();
  });
});

// The checked-in DOM + interaction baseline at 10,000 tool-heavy items — a standing regression
// tripwire. The invariant under proof: the mounted DOM is virtualized by stable item and stays
// BOUNDED regardless of history depth, so the feed cannot degrade into a 10k-node tree.

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

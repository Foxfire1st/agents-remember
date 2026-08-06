import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ConversationTimeline } from "./ConversationTimeline";
import { msg } from "./test-utils";

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

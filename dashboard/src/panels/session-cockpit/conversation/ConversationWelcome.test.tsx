import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { ConversationProcessState } from "../../../data/conversation/types";
import { ConversationWelcome } from "./ConversationWelcome";

afterEach(cleanup);

describe("ConversationWelcome", () => {
  it("leads with the product, then presents its three coherence pillars", () => {
    const view = render(<ConversationWelcome harness="claude" processState="connected" />);

    expect(view.getByTestId("conversation-welcome").textContent).toContain("Agents Remember");
    expect(view.getByText("One mission. Many agents. Coherence survives.")).toBeTruthy();
    expect(view.getByRole("list", { name: "Product pillars" }).textContent).toContain(
      "AgentsOrchestrate attention",
    );
    expect(view.getByRole("list", { name: "Product pillars" }).textContent).toContain(
      "TasksCommit intent",
    );
    expect(view.getByRole("list", { name: "Product pillars" }).textContent).toContain(
      "MemoryPreserve truth",
    );
  });

  // M7 — this welcome used to print "<harness> link ready" beside the mint live dot from SSE
  // liveness alone, taking no readiness input at all. The cases below exist to fail loudly if that
  // fabrication returns: a not-connected process must produce neither the ready wording nor the
  // mint/glow live dot.
  const notReady: ConversationProcessState[] = ["starting", "disconnected", "exited", "failed"];
  it.each(notReady)("never claims the link is ready when the process is %s", (state) => {
    const view = render(<ConversationWelcome harness="codex" processState={state} />);

    const line = view.getByTestId("conversation-welcome-harness");
    expect(line.textContent).not.toContain("ready");
    expect(line.textContent).toContain(`codex link ${state}`);

    const dot = view.getByTestId("conversation-welcome-link-dot");
    expect(dot.className).not.toContain("bg_mint");
    expect(dot.className).not.toContain("bx-sh"); // the glow belongs to the live dot only
  });

  it("mirrors the uncertainty when the projection has applied no status frame yet", () => {
    // The current call site passes only `harness`; an absent status is not evidence of a live link.
    const view = render(<ConversationWelcome harness="codex" />);

    const line = view.getByTestId("conversation-welcome-harness");
    expect(line.textContent).not.toContain("ready");
    expect(line.textContent).toContain("codex link state unknown");
    expect(view.getByTestId("conversation-welcome-link-dot").className).not.toContain("bg_mint");
  });

  it("says the link is ready — with the mint live dot — only for a connected process", () => {
    const view = render(<ConversationWelcome harness="claude" processState="connected" />);

    expect(view.getByTestId("conversation-welcome-harness").textContent).toContain(
      "claude link ready",
    );
    expect(view.getByTestId("conversation-welcome-link-dot").className).toContain("bg_mint");
  });
});

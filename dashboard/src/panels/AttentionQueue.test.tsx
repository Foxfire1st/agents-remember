import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { dashboardStore } from "../data/store";
import { GALLERY } from "../dev/fixtures";
import type { AttentionItem } from "../types/projection";
import { AttentionQueue } from "./AttentionQueue";

// §9 (slice 5f S6/S3): a pre-contract blocked start is a server-computed attention item. The queue
// renders it generically by severity, so the same alarm the agent raises in chat reaches the cockpit.
const blockedStart: AttentionItem = {
  id: "blocked-start:v12-feat-ar",
  kind: "blocked-start",
  severity: "warn",
  lane: "worktree",
  title: "Worktree start blocked",
  detail: "no exact ledger mapping for selected code base commit",
};

afterEach(cleanup);

describe("AttentionQueue blocked-start alarm parity (5f S3)", () => {
  it("renders a §9 blocked-start attention item", () => {
    const base = GALLERY.find((entry) => entry.name === "engine-fleet")?.projection;
    if (!base?.analytics) throw new Error("fixture missing analytics");
    dashboardStore.getState().applySnapshot({
      ...base,
      analytics: { ...base.analytics, attentionQueue: [blockedStart] },
    });

    const { getAllByTestId, getByText } = render(<AttentionQueue onSelect={() => {}} />);
    const items = getAllByTestId("attn-item");
    expect(items.some((el) => el.textContent?.includes("Worktree start blocked"))).toBe(true);
    expect(getByText("no exact ledger mapping for selected code base commit")).not.toBeNull();
  });
});

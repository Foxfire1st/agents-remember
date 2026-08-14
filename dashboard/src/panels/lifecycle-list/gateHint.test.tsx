import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { LifecycleList } from "./LifecycleList";
import {
  EMPTY_ANALYTICS,
  enclosure,
  installLifecycleListCleanup,
  lifecycle,
  projection,
  seed,
  taskDoc,
} from "./test-utils";

installLifecycleListCleanup();

describe("LifecycleList gate hint (L17 — no bare-ask affordance)", () => {
  it("renders no gate hint for a lifecycle carrying a bare ask but no durable gate", () => {
    seed(
      projection({
        lifecycles: [
          lifecycle({
            id: "LC-ASK",
            repoId: "agents-remember",
            state: "running",
            // A wait-loop-era `ask` payload with NO durable gate: under notify-and-continue this must
            // NOT resurface as a gate affordance in the row (the retired fallback showed the question).
            ask: { question: "Approve the plan?" },
          }),
        ],
        enclosures: [
          enclosure({ enclosure: "/contracts/ask", lifecycleId: "LC-ASK", leafId: "01_ask-only" }),
        ],
        analytics: {
          ...EMPTY_ANALYTICS,
          taskDocuments: [
            taskDoc({
              id: "01",
              lifecycleId: undefined,
              title: "Ask Only Leaf",
              docPath: "/tasks/260610_browser-dashboard/01_ask-only.json",
            }),
          ],
        },
      }),
    );
    const { getByText } = render(<LifecycleList selectedId={null} onSelect={vi.fn()} />);
    const row = getByText("Ask Only Leaf");
    // The lifecycle IS bound (its state annotates the row) — so the absent gate line is the contract,
    // not a missing binding. Neither a "Gate:" line nor the ask question leaks into the row.
    expect(row.getAttribute("title")).toContain("State: running");
    expect(row.getAttribute("title")).not.toContain("Gate:");
    expect(row.getAttribute("title")).not.toContain("Approve the plan?");
  });
});


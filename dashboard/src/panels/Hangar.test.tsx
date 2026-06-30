import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { dashboardStore } from "../data/store";
import type { EnclosureNode } from "../types/projection";
import { Hangar } from "./Hangar";

function enclosure(partial: Partial<EnclosureNode> & { enclosure: string }): EnclosureNode {
  const { enclosure: enclosurePath, ...rest } = partial;
  return {
    actions: [],
    cleanup: "pending",
    closeoutStatus: "not-started",
    enclosure: enclosurePath,
    enclosureId: enclosurePath,
    humanReviewStatus: "pending-review",
    integrationStatus: "not-started",
    leafId: "leaf",
    lifecycleId: "lc-1",
    repoName: "agents-remember",
    taskId: "T",
    taskName: "t",
    taskRoot: "tasks/agents-remember/t",
    worktreeGroup: "g-ar",
    ...rest,
  };
}

afterEach(() => {
  cleanup();
  dashboardStore.getState().reset();
});

describe("Hangar lists only live worktrees", () => {
  it("hides archived (cleanup completed/abandoned) enclosures so the count reflects physical worktrees", () => {
    // A finalized worktree keeps its enclosure contract on disk — the hangar must not count those, or it
    // only ever piles up. Two of these three are archived; only the live one survives.
    dashboardStore.setState({
      lifecycles: {},
      enclosures: {
        live: enclosure({ enclosure: "live", cleanup: "pending" }),
        done: enclosure({ enclosure: "done", cleanup: "completed" }),
        gone: enclosure({ enclosure: "gone", cleanup: "abandoned" }),
      },
    });

    const { getByTestId, getAllByTestId } = render(<Hangar onSelect={vi.fn()} />);

    expect(getAllByTestId("hangar-row")).toHaveLength(1);
    expect(getByTestId("hangar").textContent).toContain("Hangar · 1 worktrees");
  });

  it("fully reduces to the empty state once every worktree is archived", () => {
    dashboardStore.setState({
      lifecycles: {},
      enclosures: {
        a: enclosure({ enclosure: "a", cleanup: "completed" }),
        b: enclosure({ enclosure: "b", cleanup: "completed" }),
      },
    });

    const { queryAllByTestId, getByText } = render(<Hangar onSelect={vi.fn()} />);

    expect(queryAllByTestId("hangar-row")).toHaveLength(0);
    expect(getByText(/no live persistent worktrees/i)).not.toBeNull();
  });
});

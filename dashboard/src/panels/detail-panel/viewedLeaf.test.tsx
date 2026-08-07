import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DetailPanel } from "./DetailPanel";
import {
  seedSeries,
  seedTaskDocuments,
  taskDoc,
} from "./test-utils";

describe("DetailPanel viewed-leaf reporting (L5 fix 1)", () => {
  it("reports the drilled leaf's qualified key to onViewLeaf, not the master", () => {
    seedSeries();
    const onViewLeaf = vi.fn();
    const { getByTestId } = render(<DetailPanel selectedId="series" onViewLeaf={onViewLeaf} />);
    // Master overview: no single leaf is shown.
    expect(onViewLeaf).toHaveBeenLastCalledWith(undefined);

    // Drill into the master's sub-task → the leaf's qualified id (repo/master/leafId), not the master.
    fireEvent.click(getByTestId("subtask-open-1"));
    expect(onViewLeaf).toHaveBeenLastCalledWith("repo-a/series/1");

    // Returning to the master index clears the leaf slot again.
    fireEvent.click(getByTestId("series-breadcrumb"));
    expect(onViewLeaf).toHaveBeenLastCalledWith(undefined);
  });

  it("reports a directly-opened leaf doc's qualified key", () => {
    const leafPath = "/tasks/agents-remember/260628_operations-integration/05_sidebar.json";
    const doc = taskDoc({
      id: "260628-L5",
      lifecycleId: undefined,
      kind: "subTask",
      title: "Sidebar chat attachment",
      repository: "agents-remember",
      docPath: leafPath,
    });
    seedTaskDocuments([doc]);
    const onViewLeaf = vi.fn();
    render(<DetailPanel selectedId={`taskdoc:${leafPath}`} onViewLeaf={onViewLeaf} />);
    expect(onViewLeaf).toHaveBeenLastCalledWith(
      "agents-remember/260628_operations-integration/260628-L5",
    );
  });
});

// The L9 notes API stub: /api/notes/list answers a fixed listing, /api/notes/read a fixed body.
// Everything else (e.g. change-set counters) answers a bare ok so unrelated fetches stay inert.

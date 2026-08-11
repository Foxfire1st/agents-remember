import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DetailPanel } from "./DetailPanel";
import {
  seedSeries,
  seedTaskDocuments,
  taskDoc,
} from "./test-utils";

describe("DetailPanel viewed-task reporting", () => {
  it("reports the drilled leaf's canonical task document and presentation key", () => {
    seedSeries();
    const onViewTask = vi.fn();
    const { getByTestId } = render(<DetailPanel selectedId="series" onViewTask={onViewTask} />);
    // Master overview: no single leaf is shown.
    expect(onViewTask).toHaveBeenLastCalledWith(undefined);

    // Drill into the master's sub-task → the leaf's qualified id (repo/master/leafId), not the master.
    fireEvent.click(getByTestId("subtask-open-1"));
    expect(onViewTask).toHaveBeenLastCalledWith(expect.objectContaining({
      taskDocumentRef: { repository: "repo-a", path: "series/01_first.json" },
      leafKey: "repo-a/series/1",
    }));

    // Returning to the master index clears the leaf slot again.
    fireEvent.click(getByTestId("series-breadcrumb"));
    expect(onViewTask).toHaveBeenLastCalledWith(undefined);
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
    const onViewTask = vi.fn();
    render(<DetailPanel selectedId={`taskdoc:${leafPath}`} onViewTask={onViewTask} />);
    expect(onViewTask).toHaveBeenLastCalledWith({
      taskDocumentRef: {
        repository: "agents-remember",
        path: "260628_operations-integration/05_sidebar.json",
      },
      leafKey: "agents-remember/260628_operations-integration/260628-L5",
    });
  });
});

// The L9 notes API stub: /api/notes/list answers a fixed listing, /api/notes/read a fixed body.
// Everything else (e.g. change-set counters) answers a bare ok so unrelated fetches stay inert.

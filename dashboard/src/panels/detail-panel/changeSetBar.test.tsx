import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DetailPanel } from "./DetailPanel";
import {
  enclosure,
  seedProjection,
  seedTaskDocuments,
  stubCounters,
  taskDoc,
} from "./test-utils";

describe("DetailPanel doc-reader change-set bar (L4a)", () => {
  const leafPath = "/tasks/agents-remember/260628_operations-integration/04a_changeset-everywhere.json";

  it("shows a committed button on a leaf doc reader (no live enclosure) and opens the leaf target", async () => {
    stubCounters();
    const doc = taskDoc({
      id: "260628-L4a",
      lifecycleId: undefined,
      kind: "subTask",
      title: "Change-set everywhere",
      repository: "agents-remember",
      docPath: leafPath,
      objective: "Leaf objective.",
    });
    seedTaskDocuments([doc]);
    const onOpenChangeSet = vi.fn();
    const { findAllByTestId } = render(
      <DetailPanel selectedId={`taskdoc:${leafPath}`} onOpenChangeSet={onOpenChangeSet} />,
    );
    // identity comes from the doc node, so the bar shows with NO active enclosure (the L4 gap);
    // committed is always present, working only when live -> exactly one button here.
    const buttons = await findAllByTestId("open-changeset");
    expect(buttons).toHaveLength(1);
    expect(buttons[0].textContent).toContain("committed");
    fireEvent.click(buttons[0]);
    expect(onOpenChangeSet).toHaveBeenCalledWith({
      repo: "agents-remember",
      master: "260628_operations-integration",
      leaf: "260628-L4a",
      mode: "committed",
    });
  });

  it("shows a series button on a master doc reader", async () => {
    stubCounters();
    const master = taskDoc({
      lifecycleId: undefined,
      kind: "master",
      title: "Operations Integration",
      repository: "agents-remember",
      docPath: "/tasks/agents-remember/260628_operations-integration/task.json",
      objective: "Master objective.",
    });
    seedTaskDocuments([master]);
    const onOpenChangeSet = vi.fn();
    const { findAllByTestId } = render(
      <DetailPanel
        selectedId="taskdoc:/tasks/agents-remember/260628_operations-integration/task.json"
        onOpenChangeSet={onOpenChangeSet}
      />,
    );
    const buttons = await findAllByTestId("open-changeset");
    expect(buttons).toHaveLength(1);
    expect(buttons[0].textContent).toContain("series");
    fireEvent.click(buttons[0]);
    expect(onOpenChangeSet).toHaveBeenCalledWith({
      repo: "agents-remember",
      master: "260628_operations-integration",
    });
  });

  it("adds a working button when the leaf's enclosure is live", async () => {
    stubCounters();
    const doc = taskDoc({
      id: "260628-l4a",
      lifecycleId: undefined,
      kind: "subTask",
      title: "Change-set everywhere",
      repository: "agents-remember",
      docPath: leafPath,
    });
    seedProjection({
      enclosures: [
        enclosure({
          enclosure: "/contracts/l4a",
          lifecycleId: "X",
          leafId: "260628-l4a",
          repoName: "agents-remember",
          taskName: "260628_operations-integration",
          worktreeGroup: "/worktrees/changeset-everywhere-ar",
        }),
      ],
      activeWorktreeGroups: ["changeset-everywhere-ar"],
      analytics: {
        driftSnapshots: [],
        stalestSidecars: [],
        setupSummaries: [],
        setupProgress: [],
        routeCoverage: [],
        toolReports: [],
        ledgers: [],
        taskDocuments: [doc],
        series: [],
        attentionQueue: [],
        engineProcesses: [],
        agentPickups: [],
        expectationRows: [],
      },
    });
    const onOpenChangeSet = vi.fn();
    const { findAllByTestId } = render(
      <DetailPanel selectedId={`taskdoc:${leafPath}`} onOpenChangeSet={onOpenChangeSet} />,
    );
    const labels = (await findAllByTestId("open-changeset")).map((b) => b.textContent ?? "");
    expect(labels).toHaveLength(2);
    expect(labels.some((t) => t.includes("committed"))).toBe(true);
    expect(labels.some((t) => t.includes("working"))).toBe(true);
  });

  it("omits the bar entirely when no onOpenChangeSet handler is wired", () => {
    stubCounters();
    const doc = taskDoc({
      id: "260628-L4a",
      lifecycleId: undefined,
      kind: "subTask",
      title: "Change-set everywhere",
      repository: "agents-remember",
      docPath: leafPath,
    });
    seedTaskDocuments([doc]);
    const { queryAllByTestId } = render(<DetailPanel selectedId={`taskdoc:${leafPath}`} />);
    expect(queryAllByTestId("open-changeset")).toHaveLength(0);
  });
});

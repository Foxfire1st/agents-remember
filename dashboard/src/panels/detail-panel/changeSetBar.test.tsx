import { act, fireEvent, render } from "@testing-library/react";
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
    expect(labels).toHaveLength(3);
    expect(labels.some((t) => t.includes("committed"))).toBe(true);
    expect(labels.some((t) => t.includes("working"))).toBe(true);
    expect(labels.some((t) => t.includes("Intent review"))).toBe(true);
  });

  // The reviewed subject is a REFINEMENT of the entry and never its gate: the entry is the task
  // context, so a live leaf opens its source review whether or not the server offers a subject.
  // Each case below pins one of the three answers the entry route can give.
  const liveLeaf = () => {
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
  };

  const reviewButton = async (findAllByTestId: (id: string) => Promise<HTMLElement[]>) =>
    (await findAllByTestId("open-changeset")).find((b) =>
      (b.textContent ?? "").includes("Intent review"),
    );

  it("opens the task-context review when the server offers no subject", async () => {
    // The packet's own failing case: no invariant is recorded, so the entry list is empty. The
    // entry must still be offered and must carry the task context rather than a selector.
    stubCounters({
      state: "entries",
      operation: "list_knowledge_review_entries",
      repository_id: "agents-remember",
      master: "260628_operations-integration",
      leaf_id: "260628-l4a",
      entries: [],
    });
    liveLeaf();
    const onOpenChangeSet = vi.fn();
    const { findAllByTestId } = render(
      <DetailPanel selectedId={`taskdoc:${leafPath}`} onOpenChangeSet={onOpenChangeSet} />,
    );
    const button = await reviewButton(findAllByTestId);
    expect(button).toBeDefined();
    fireEvent.click(button as HTMLElement);
    expect(onOpenChangeSet).toHaveBeenCalledWith({
      repo: "agents-remember",
      master: "260628_operations-integration",
      leaf: "260628-l4a",
      review: {},
    });
  });

  it("carries the server's recorded subject when the pair offers one", async () => {
    stubCounters({
      state: "entries",
      operation: "list_knowledge_review_entries",
      repository_id: "agents-remember",
      master: "260628_operations-integration",
      leaf_id: "260628-l4a",
      entries: [
        {
          selector_kind: "invariant",
          selector_id: "inv-1",
          label: "Retries share one budget",
          selected_item_count: 3,
        },
      ],
    });
    liveLeaf();
    const onOpenChangeSet = vi.fn();
    const { findAllByTestId } = render(
      <DetailPanel selectedId={`taskdoc:${leafPath}`} onOpenChangeSet={onOpenChangeSet} />,
    );
    const button = (await reviewButton(findAllByTestId)) as HTMLElement;

    // The entry exists before this flush -- it is gated on the leaf being live and never on the
    // server's subject list -- and the subject *refines* it once the read answers. The flush is what
    // separates the two states rather than timing the assertion against a pending promise: a click
    // that lands before the answer opens the whole-task review, which is the entry the two other
    // cases pin down (an empty list and a failing read both keep it).
    await act(async () => {});
    fireEvent.click(button);
    expect(onOpenChangeSet).toHaveBeenLastCalledWith({
      repo: "agents-remember",
      master: "260628_operations-integration",
      leaf: "260628-l4a",
      review: { selectorKind: "invariant", selectorId: "inv-1" },
    });
  });

  it("keeps the entry when the entry read itself fails", async () => {
    // An unreadable entry read (a refusal, a transport failure) is not a reason to hide the source
    // review: the task context is the entry, and the review's own refusal is rendered in the pane.
    const failing = vi.fn(async () => {
      throw new Error("404 candidate_dataset_absent");
    });
    stubCounters();
    vi.stubGlobal("fetch", failing);
    liveLeaf();
    const onOpenChangeSet = vi.fn();
    const { findAllByTestId } = render(
      <DetailPanel selectedId={`taskdoc:${leafPath}`} onOpenChangeSet={onOpenChangeSet} />,
    );
    const button = await reviewButton(findAllByTestId);
    expect(button).toBeDefined();
    fireEvent.click(button as HTMLElement);
    expect(onOpenChangeSet).toHaveBeenCalledWith({
      repo: "agents-remember",
      master: "260628_operations-integration",
      leaf: "260628-l4a",
      review: {},
    });
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

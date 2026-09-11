import { fireEvent, render } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { DetailPanel } from "./DetailPanel";
import type { LifecycleProjection } from "../../types/projection";
import {
  nestedProgressSteps,
  seedProjection,
  seedSeries,
  seedSeriesOrdering,
  seedTaskDocuments,
  taskDoc,
} from "./test-utils";

describe("DetailPanel master series navigation (6g)", () => {
  it("renders an unbound planning leaf task document by typed taskdoc selection", () => {
    const doc = taskDoc({
      lifecycleId: undefined,
      kind: "subTask",
      title: "Plan Before Worktree",
      docPath: "/tasks/repo-a/planning/01_plan.json",
      objective: "Plan the document before opening a worktree.",
    });
    seedTaskDocuments([doc]);

    const { getAllByText, getByText, queryByText } = render(
      <DetailPanel selectedId="taskdoc:/tasks/repo-a/planning/01_plan.json" />,
    );

    expect(getAllByText("Plan Before Worktree").length).toBeGreaterThan(0);
    expect(getByText("Plan the document before opening a worktree.")).toBeTruthy();
    expect(queryByText("No task document bound to this task.")).toBeNull();
  });

  it("renders an unbound master task document by kind", () => {
    const master = taskDoc({
      lifecycleId: undefined,
      kind: "master",
      title: "Planning Master",
      docPath: "/tasks/repo-a/planning/task.json",
      objective: "Master plan objective.",
      subTasks: [
        {
          number: "99",
          name: "Leaf created first",
          file: "01_leaf.md",
          status: "planning",
          scope: "",
        },
      ],
    });
    const leaf = taskDoc({
      id: "99",
      lifecycleId: undefined,
      kind: "subTask",
      title: "Leaf created first",
      docPath: "/tasks/repo-a/planning/01_leaf.json",
      objective: "Leaf objective.",
    });
    seedTaskDocuments([master, leaf]);

    const { getByTestId, getByText, queryByText } = render(
      <DetailPanel selectedId="taskdoc:/tasks/repo-a/planning/task.json" />,
    );

    expect(getByText("Master plan objective.")).toBeTruthy();
    expect(getByTestId("subtask-open-1").textContent).toContain("99. Leaf created first");
    expect(queryByText("No task document bound to this task.")).toBeNull();
  });

  it("renders discarded-before-start history separately from the live sub-task index", () => {
    const master = taskDoc({
      lifecycleId: undefined,
      kind: "master",
      title: "Audited planning master",
      docPath: "/tasks/repo-a/planning/task.json",
      objective: "Keep planning removal visible without inventing completion.",
      subTasks: [
        {
          number: "1",
          name: "Live planning leaf",
          file: "01_live.md",
          status: "planning",
          scope: "",
        },
      ],
      discardedCount: 1,
      discardedSubTasks: [
        {
          number: "2",
          name: "Never started",
          file: "02_never_started.md",
          scope: "retired planning work",
          disposition: "discard-unstarted",
          reason: "No implementation was needed",
          discardedAt: "2026-08-24T12:00:00+00:00",
          proof: {
            childJson: { state: "missing" },
            childMarkdown: { state: "missing" },
            version: "task-unstarted-evidence/v1",
            taskDocumentRef: {
              repository: "repo-a",
              path: "planning/02_never_started.json",
            },
            taskState: "planning-unstarted",
            enclosureState: "absent",
            locatorState: "absent",
            doorState: "absent",
            operationState: "absent",
            seatState: "absent",
            reviewState: "absent",
            commitState: "absent",
            fingerprint: "a".repeat(64),
          },
        },
      ],
    });
    seedTaskDocuments([master]);

    const { getByTestId, getByText } = render(
      <DetailPanel selectedId="taskdoc:/tasks/repo-a/planning/task.json" />,
    );

    expect(getByText("Discarded before start (1)")).toBeTruthy();
    const discarded = getByText("2. Never started").closest("li");
    expect(discarded?.textContent).toContain("No implementation was needed");
    expect(discarded?.textContent).toContain("proof " + "a".repeat(64));
    expect(getByTestId("subtask-open-1").textContent).toContain("1. Live planning leaf");
  });

  it("opens authored master leaves from the full projected pool when the master is lifecycle-bound", () => {
    const lc: LifecycleProjection = {
      id: "ROOT",
      state: "running",
      phase: "build",
      fleeting: false,
      tokens: 0,
      startedAt: "2026-06-20T09:00:00+00:00",
      lastEventTs: "2026-06-20T09:00:30+00:00",
      stateEnteredAt: "2026-06-20T09:00:00+00:00",
      inferred: false,
      actions: [],
      tokenSeries: [],
    };
    const master = taskDoc({
      lifecycleId: "ROOT",
      kind: "master",
      title: "Lifecycle Master",
      docPath: "/tasks/repo-a/planning/task.json",
      objective: "Master plan objective.",
      subTasks: [
        {
          number: "1",
          name: "Leaf from projected pool",
          file: "01_leaf.md",
          status: "planning",
          scope: "",
        },
      ],
    });
    const leaf = taskDoc({
      id: "1",
      lifecycleId: "ROOT",
      kind: "subTask",
      title: "Leaf from projected pool",
      docPath: "/tasks/repo-a/planning/01_leaf.json",
      objective: "Leaf objective from the projected pool.",
    });
    seedProjection({
      lifecycles: [lc],
      analytics: {
        driftSnapshots: [],
        stalestSidecars: [],
        setupSummaries: [],
        setupProgress: [],
        routeCoverage: [],
        toolReports: [],
        ledgers: [],
        taskDocuments: [master, leaf],
        series: [],
        attentionQueue: [],
        engineProcesses: [],
        agentPickups: [],
        expectationRows: [],
      },
    });

    const { getByTestId, getByText, queryByText } = render(
      <DetailPanel selectedId="taskdoc:/tasks/repo-a/planning/task.json" />,
    );

    expect(getByTestId("subtask-open-1").tagName.toLowerCase()).toBe("button");
    fireEvent.click(getByTestId("subtask-open-1"));
    expect(getByText("Leaf objective from the projected pool.")).toBeTruthy();
    expect(queryByText("No task document bound to this task.")).toBeNull();
  });

  it("keeps master rows static when the referenced leaf has no authored task document", () => {
    const master = taskDoc({
      lifecycleId: undefined,
      kind: "master",
      title: "Planning Master",
      docPath: "/tasks/repo-a/planning/task.json",
      objective: "Master plan objective.",
      subTasks: [
        {
          number: "1",
          name: "Missing leaf",
          file: "01_missing.md",
          status: "planning",
          scope: "",
        },
      ],
    });
    seedTaskDocuments([master]);

    const { getByTestId } = render(
      <DetailPanel selectedId="taskdoc:/tasks/repo-a/planning/task.json" />,
    );

    expect(getByTestId("subtask-open-1").tagName.toLowerCase()).toBe("div");
    expect(getByTestId("subtask-open-1").textContent).toContain("1. Missing leaf");
  });

  it("renders structured ids with step and code example titles", () => {
    const doc = taskDoc({
      lifecycleId: undefined,
      kind: "subTask",
      title: "Id Display",
      docPath: "/tasks/repo-a/planning/ids.json",
      steps: [
        {
          id: "S11",
          title: "Make Operations disappearance archive/delete-based",
          status: "pending",
          substeps: [
            {
              id: "S11.1",
              title: "Exclude archived task documents",
              status: "pending",
            },
          ],
        },
      ],
      codeExamples: [
        {
          id: "E4",
          title: "Master leaf list uses task-specific numbers",
          distinctChange: "Series/master reader ordering and enumeration.",
          why: "The display number is structured presentation.",
          language: "tsx",
          snippet: "<LeafTaskRow />",
        },
      ],
    });
    seedTaskDocuments([doc]);

    const { getAllByText, getByText, queryByText } = render(
      <DetailPanel selectedId="taskdoc:/tasks/repo-a/planning/ids.json" />,
    );

    expect(getAllByText("S11 — Make Operations disappearance archive/delete-based")).toHaveLength(1);
    expect(getAllByText("S11.1 — Exclude archived task documents")).toHaveLength(1);
    expect(getByText("E4 — Master leaf list uses task-specific numbers")).toBeTruthy();
    expect(queryByText("Make Operations disappearance archive/delete-based")).toBeNull();
    expect(queryByText("Master leaf list uses task-specific numbers")).toBeNull();
  });

  it("labels parent and nested intentional skips without relabeling ordinary done", () => {
    const disposition = {
      kind: "intentionalSkip" as const,
      reason: "Superseded by the accepted path.",
      recordedAt: "2026-08-03T12:00:00+00:00",
      recordedVia: "task_doc.skip_step" as const,
    };
    const doc = taskDoc({
      lifecycleId: undefined,
      kind: "subTask",
      title: "Skip display",
      docPath: "/tasks/repo-a/planning/skips.json",
      stepsDone: 3,
      stepsTotal: 3,
      steps: [
        {
          id: "S1",
          title: "Skipped parent",
          status: "done",
          disposition,
          substeps: [
            {
              id: "C1",
              title: "Skipped child",
              status: "done",
              disposition: { ...disposition, reason: "Child became unnecessary." },
            },
          ],
        },
        { id: "S2", title: "Ordinary done", status: "done", substeps: [] },
      ],
    });
    seedTaskDocuments([doc]);

    const { getAllByText, getByText } = render(
      <DetailPanel selectedId="taskdoc:/tasks/repo-a/planning/skips.json" />,
    );

    expect(getAllByText("SKIPPED")).toHaveLength(2);
    expect(getByText(/Superseded by the accepted path/)).toBeTruthy();
    expect(getByText(/Child became unnecessary/)).toBeTruthy();
    expect(getByText("S2 — Ordinary done").closest("li")?.textContent).not.toContain("SKIPPED");
  });

  it("pins the sub-task index above the description and keeps the in-section copy", () => {
    seedSeries();
    const { getAllByText, getByTestId, getByText } = render(
      <DetailPanel selectedId="series" />,
    );
    expect(getAllByText("My Series").length).toBeGreaterThan(0);
    const objective = getByText("Series objective text");
    const topIndex = getByTestId("subtask-open-1"); // pinned navigation copy
    expect(getByTestId("subtask-mid-1")).toBeTruthy(); // authored in-section copy stays
    // the pinned index precedes the description in the DOM (FOLLOWING set => objective is after it)
    expect(
      topIndex.compareDocumentPosition(objective) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("drills into a slice's reader and returns via the breadcrumb", () => {
    seedSeries();
    const { getAllByText, getByTestId, getByText, queryByText } = render(
      <DetailPanel selectedId="series" />,
    );
    // The master overview is shown, the slice body is not.
    expect(queryByText("Slice objective text")).toBeNull();

    fireEvent.click(getByTestId("subtask-open-1"));
    expect(getByText("Slice objective text")).toBeTruthy(); // the slice's full reader
    expect(getAllByText("S1 — do the thing")).toHaveLength(1);

    fireEvent.click(getByTestId("series-breadcrumb"));
    expect(getByTestId("subtask-open-1")).toBeTruthy(); // back to the master index
    expect(queryByText("Slice objective text")).toBeNull();
  });

  it("jumps lifecycles from a cross-master row on a master TASK DOC", () => {
    // The cross-series jump is reachable only from a master task document: `linkedLifecycleId`
    // lives on `TaskSubTaskRefNode`. A SeriesNode's rows are `SeriesSubTaskNode`, which the
    // server never stamps with one — this fixture used to be a series, so it was exercising a
    // projection the server cannot produce.
    const master = taskDoc({
      lifecycleId: undefined,
      kind: "master",
      title: "Cross-linking Master",
      docPath: "/tasks/repo-a/planning/task.json",
      objective: "Master plan objective.",
      subTasks: [
        { number: "1", name: "Own slice", file: "01_leaf.md", status: "planning", scope: "" },
        {
          number: "2",
          name: "Parallel series",
          file: "../other/task.md",
          status: "inProgress",
          scope: "",
          linkedLifecycleId: "LC-OTHER",
        },
      ],
    });
    seedTaskDocuments([master]);
    const onOpenLifecycle = vi.fn();
    const { getByTestId } = render(
      <DetailPanel
        selectedId="taskdoc:/tasks/repo-a/planning/task.json"
        onOpenLifecycle={onOpenLifecycle}
      />,
    );
    fireEvent.click(getByTestId("subtask-open-link-2")); // the "→" cross-series row
    expect(onOpenLifecycle).toHaveBeenCalledWith("LC-OTHER");
  });

  it("opens the commanded master from a sprint masterRef row, then drills to its leaf (L14-R2)", () => {
    // The sprint → master → leaf click path: a sprint row's typed `masterRef` resolves against
    // the full projected pool (the master lives in ANOTHER folder, never in `sliceDocs`) and the
    // row dispatches the task-doc selection for it — the same `taskdoc:` key the Cockpit's open()
    // passes through to `setSelectedId`.
    const sprint = taskDoc({
      id: "SPRINT",
      lifecycleId: undefined,
      kind: "master",
      title: "Commanding Sprint",
      docPath: "/tasks/repo-a/sprint/task.json",
      orchestrates: ["master"],
      subTasks: [
        {
          number: "1",
          name: "Commanded Master",
          file: "",
          status: "inProgress",
          scope: "",
          masterRef: { repository: "repo-a", path: "master/task.json" },
        },
      ],
    });
    const master = taskDoc({
      id: "MASTER",
      lifecycleId: undefined,
      kind: "master",
      title: "Commanded Master",
      docPath: "/tasks/repo-a/master/task.json",
      subTasks: [
        { number: "1", name: "Leaf one", file: "01_leaf.md", status: "planning", scope: "" },
      ],
    });
    const leaf = taskDoc({
      id: "1",
      lifecycleId: undefined,
      kind: "subTask",
      title: "Leaf one",
      docPath: "/tasks/repo-a/master/01_leaf.json",
      objective: "Leaf objective.",
    });
    seedTaskDocuments([sprint, master, leaf]);

    // A stateful shell standing in for the Cockpit: the row jump becomes the next selection.
    function Shell() {
      const [selectedId, setSelectedId] = useState<string | null>(
        "taskdoc:/tasks/repo-a/sprint/task.json",
      );
      return <DetailPanel selectedId={selectedId} onOpenLifecycle={setSelectedId} />;
    }
    const { getAllByText, getByTestId, getByText } = render(<Shell />);

    // sprint → master: the typed row renders as the "⇒" master link, not a slice or static row
    const masterRow = getByTestId("subtask-open-master-1");
    expect(masterRow.textContent).toContain("⇒ 1. Commanded Master");
    fireEvent.click(masterRow);

    // the master document reader renders, with its leaf rows visible
    expect(getAllByText("Commanded Master").length).toBeGreaterThan(0);
    const leafRow = getByTestId("subtask-open-1");
    expect(leafRow.textContent).toContain("Leaf one");

    // master → leaf: the full sprint → master → leaf drill-down
    fireEvent.click(leafRow);
    expect(getByText("Leaf objective.")).toBeTruthy();
  });

  it("keeps a masterRef row static when the commanded master is not projected", () => {
    // Fallback honesty: a masterRef whose target is absent from the projected pool (bounded
    // summary limit, another repo's docs) degrades to the row's older behavior — here, with no
    // same-folder slice and no cross-series link, the static index row.
    const sprint = taskDoc({
      id: "SPRINT",
      lifecycleId: undefined,
      kind: "master",
      title: "Commanding Sprint",
      docPath: "/tasks/repo-a/sprint/task.json",
      orchestrates: ["missing"],
      subTasks: [
        {
          number: "1",
          name: "Unprojected Master",
          file: "",
          status: "planning",
          scope: "",
          masterRef: { repository: "repo-a", path: "missing/task.json" },
        },
      ],
    });
    seedTaskDocuments([sprint]);

    const { getByTestId, queryByTestId } = render(
      <DetailPanel selectedId="taskdoc:/tasks/repo-a/sprint/task.json" />,
    );

    expect(queryByTestId("subtask-open-master-1")).toBeNull();
    const row = getByTestId("subtask-open-1");
    expect(row.tagName.toLowerCase()).toBe("div");
    expect(row.textContent).toContain("1. Unprojected Master");
  });

  it("renders markdown in master sections (GFM table + bold), not raw", () => {
    seedSeries();
    const { container, queryByText } = render(<DetailPanel selectedId="series" />);
    const table = container.querySelector("table");
    expect(table).toBeTruthy(); // the GFM table is a real <table>, not raw pipes
    expect(container.querySelector("th")?.textContent).toBe("Slice");
    expect(container.querySelector("strong")?.textContent).toBe("strong");
    expect(queryByText(/\| Slice \| Status \|/)).toBeNull(); // raw markdown is gone
  });

  it("renders aggregate series tokens on the master reader", () => {
    seedSeries();
    const { getByLabelText, getByText } = render(<DetailPanel selectedId="series" />);

    expect(getByText("series tokens")).toBeTruthy();
    expect(getByText("1,500 tok")).toBeTruthy();
    expect(getByLabelText("1500 aggregate series tokens")).toBeTruthy();
  });

  it("orders master leaves by creation time and displays task-specific numbers", () => {
    seedSeriesOrdering();
    const { getByTestId, queryByText } = render(<DetailPanel selectedId="series" />);

    expect(getByTestId("subtask-open-1").textContent).toContain("01. Zulu earlier");
    expect(getByTestId("subtask-open-2").textContent).toContain("99. Alpha later");
    expect(queryByText("1. Zulu earlier")).toBeNull();
    expect(queryByText("2. Alpha later")).toBeNull();
  });

  it("summarizes task progress with every declared parent and nested step", () => {
    seedSeries({
      sliceDoc: {
        title: "Parallel Leaf Enclosure Workflow",
        stepsDone: 46,
        stepsTotal: 49,
        steps: nestedProgressSteps(),
      },
    });
    const { getByRole, getByTestId, queryByText } = render(<DetailPanel selectedId="series" />);

    const row = getByTestId("subtask-open-1");
    expect(row.textContent).toContain("46/49 · inProgress");
    expect(row.textContent).not.toContain("6/7");

    fireEvent.click(row);
    expect(getByRole("img", { name: "steps done" }).textContent).toBe("46/49");
    expect(queryByText("6/7")).toBeNull();
  });

  it("renders master content when a selected task-id lifecycle maps to the series task name", () => {
    seedSeries({
      lifecycleId: "SERIES_TASK",
      enclosureTaskId: "SERIES_TASK",
      enclosureTaskName: "series",
    });
    const { getByText, queryByText } = render(<DetailPanel selectedId="SERIES_TASK" />);

    expect(getByText("Series objective text")).toBeTruthy();
    expect(getByText("Current State")).toBeTruthy();
    expect(queryByText("series 1 task slices")).toBeNull();
    expect(queryByText("No task document bound to this task.")).toBeNull();
  });

  it("does not use parent taskName as content for a leaf lifecycle without a projected doc", () => {
    seedSeries({
      lifecycleId: "LEAF_TASK",
      enclosureTaskId: "SERIES_TASK",
      enclosureTaskName: "series",
      sliceDoc: { lifecycleId: "OTHER_TASK" },
    });
    const { getByText, queryByText } = render(<DetailPanel selectedId="LEAF_TASK" />);

    expect(getByText("No task document bound to this task.")).toBeTruthy();
    expect(queryByText("Series objective text")).toBeNull();
    expect(queryByText("Current State")).toBeNull();
  });

  it("keeps a direct leaf lifecycle document ahead of the parent series mapping", () => {
    seedSeries({
      lifecycleId: "LEAF_TASK",
      enclosureTaskId: "SERIES_TASK",
      enclosureTaskName: "series",
    });
    const { getAllByText, getByText, queryByText } = render(<DetailPanel selectedId="LEAF_TASK" />);

    expect(getByText("Slice objective text")).toBeTruthy();
    expect(getAllByText("S1 — do the thing")).toHaveLength(1);
    expect(queryByText("Series objective text")).toBeNull();
    expect(queryByText("Current State")).toBeNull();
  });

});

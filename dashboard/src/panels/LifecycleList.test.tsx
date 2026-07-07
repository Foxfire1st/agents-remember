import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { dashboardStore } from "../data/store";
import type {
  Analytics,
  EnclosureNode,
  LifecycleProjection,
  SeriesNode,
  TaskDocNode,
  WorkspaceProjection,
} from "../types/projection";
import { LifecycleList } from "./LifecycleList";

const EMPTY_ANALYTICS: Analytics = {
  driftSnapshots: [],
  stalestSidecars: [],
  setupSummaries: [],
  setupProgress: [],
  routeCoverage: [],
  toolReports: [],
  ledgers: [],
  taskDocuments: [],
  series: [],
  attentionQueue: [],
  engineProcesses: [],
};

function lifecycle(over: Partial<LifecycleProjection> & Pick<LifecycleProjection, "id">) {
  return {
    state: "paused",
    phase: "build",
    fleeting: false,
    tokens: 0,
    startedAt: "2026-06-24T06:00:00+00:00",
    lastEventTs: "2026-06-24T06:00:30+00:00",
    inferred: false,
    actions: [],
    tokenSeries: [],
    ...over,
  } satisfies LifecycleProjection;
}

function enclosure(over: Partial<EnclosureNode> & Pick<EnclosureNode, "enclosure" | "lifecycleId">) {
  return {
    enclosureId: over.enclosure,
    leafId: over.enclosure,
    taskRoot: "/tasks/260610_browser-dashboard",
    taskId: "260610_BROWSER-DASHBOARD",
    taskName: "260610_browser-dashboard",
    repoName: "agents-remember",
    worktreeGroup: "/worktrees/260610-browser-dashboard",
    humanReviewStatus: "pending-review",
    closeoutStatus: "not-started",
    integrationStatus: "not-started",
    cleanup: "pending",
    codeWorktreeExists: true,
    memoryWorktreeExists: true,
    actions: [],
    ...over,
  } satisfies EnclosureNode;
}

function taskDoc(over: Partial<TaskDocNode> & Pick<TaskDocNode, "title">) {
  return {
    id: "doc",
    repository: "agents-remember",
    status: "inProgress",
    kind: "subTask",
    stepsDone: 0,
    stepsTotal: 0,
    docPath: "/tasks/260610_browser-dashboard/01.json",
    steps: [],
    objective: "",
    requirements: [],
    codeExamples: [],
    decisions: [],
    openQuestions: [],
    references: [],
    subTasks: [],
    sections: [],
    ...over,
  } satisfies TaskDocNode;
}

function seriesNode(over: Partial<SeriesNode> & Pick<SeriesNode, "seriesId">) {
  return {
    repository: "agents-remember",
    title: "Browser Dashboard Series",
    status: "inProgress",
    objective: "",
    subTasks: [],
    doneCount: 0,
    totalCount: 0,
    seriesTokenTotal: 0,
    sections: [],
    decisions: [],
    docPath: "/tasks/260610_browser-dashboard/task.json",
    ...over,
  } satisfies SeriesNode;
}

function seed(projection: WorkspaceProjection) {
  dashboardStore.getState().applySnapshot(projection);
}

function projection(over: Partial<WorkspaceProjection>): WorkspaceProjection {
  const lifecycles = over.lifecycles ?? [];
  return {
    version: 2,
    generatedAt: "2026-06-24T06:01:00+00:00",
    lifecycles,
    enclosures: [],
    providers: [],
    activeWorktreeGroups: [],
    metrics: {
      lifecycleCount: lifecycles.length,
      runningCount: lifecycles.filter((entry) => entry.state === "running").length,
      blockedCount: lifecycles.filter((entry) => entry.state === "blocked").length,
      pausedCount: lifecycles.filter((entry) => entry.state === "paused").length,
      totalTokens: lifecycles.reduce((sum, entry) => sum + entry.tokens, 0),
      stalenessHistogram: {},
    },
    analytics: EMPTY_ANALYTICS,
    ...over,
  };
}

afterEach(() => {
  cleanup();
  dashboardStore.getState().reset();
});

describe("LifecycleList task labels", () => {
  it("limits sidebar rows to root docs, enclosure-matched leaves, and enclosure fallbacks", () => {
    const onSelect = vi.fn();
    seed(
      projection({
        lifecycles: [
          lifecycle({
            id: "260610_BROWSER-DASHBOARD",
            repoId: "agents-remember",
            phase: "close",
            enclosure: "/contracts/15",
          }),
          lifecycle({
            id: "LC-16",
            repoId: "agents-remember",
            enclosure: "/contracts/16",
          }),
          lifecycle({
            id: "LC-EMPTY",
            repoId: "agents-remember",
            enclosure: "/contracts/empty",
          }),
          lifecycle({
            id: "LC-ABANDONED",
            state: "abandoned",
            phase: "reframe-research",
            fleeting: true,
          }),
        ],
        enclosures: [
          enclosure({
            enclosure: "/contracts/15",
            lifecycleId: "260610_BROWSER-DASHBOARD",
            leafId: "15_parallel-leaf-enclosure-workflow",
          }),
          enclosure({
            enclosure: "/contracts/16",
            lifecycleId: "LC-16",
            leafId: "16_engine-room-stack-entry-height",
            cleanup: "completed",
            codeWorktreeExists: false,
            memoryWorktreeExists: false,
          }),
          enclosure({
            enclosure: "/contracts/empty",
            lifecycleId: "LC-EMPTY",
            leafId: "empty-state-backdrop-zoom-stability",
          }),
        ],
        analytics: {
          ...EMPTY_ANALYTICS,
          taskDocuments: [
            taskDoc({
              kind: "master",
              lifecycleId: "260610_BROWSER-DASHBOARD",
              title: "Browser Dashboard Series",
              docPath: "/tasks/260610_browser-dashboard/task.json",
            }),
            taskDoc({
              id: "15",
              lifecycleId: "260610_BROWSER-DASHBOARD",
              title: "Parallel Leaf Enclosure Workflow",
              docPath: "/tasks/260610_browser-dashboard/15_parallel-leaf-enclosure-workflow.json",
            }),
            taskDoc({
              id: "16",
              lifecycleId: "LC-16",
              title: "Engine Room Stack Entry Height",
              docPath: "/tasks/260610_browser-dashboard/16_engine-room-stack-entry-height.json",
            }),
            taskDoc({
              lifecycleId: "260610_BROWSER-DASHBOARD",
              title: "Lifecycle + Event + Gate Design",
              docPath: "/tasks/260610_browser-dashboard/01_lifecycle-event-gate-design.json",
            }),
            taskDoc({
              lifecycleId: undefined,
              title: "Plan Before Worktree",
              docPath: "/tasks/260610_browser-dashboard/19_plan-before-worktree.json",
              status: "planning",
            }),
          ],
          series: [
            seriesNode({
              seriesId: "260610_browser-dashboard",
              subTasks: [
                {
                  number: "15",
                  name: "Parallel Leaf Enclosure Workflow",
                  file: "15_parallel-leaf-enclosure-workflow.md",
                  status: "inProgress",
                  scope: "",
                  createdAt: "2026-06-20T09:00:00+00:00",
                },
                {
                  number: "16",
                  name: "Engine Room Stack Entry Height",
                  file: "16_engine-room-stack-entry-height.md",
                  status: "inProgress",
                  scope: "",
                  createdAt: "2026-06-21T09:00:00+00:00",
                },
              ],
            }),
          ],
        },
      }),
    );

    const { getByText, queryByText } = render(<LifecycleList selectedId={null} onSelect={onSelect} />);

    expect(getByText("Tasks · 3")).toBeTruthy();
    expect(getByText("Browser Dashboard Series")).toBeTruthy();
    expect(getByText("15. Parallel Leaf Enclosure Workflow")).toBeTruthy();
    expect(queryByText("16. Engine Room Stack Entry Height")).toBeNull();
    expect(getByText("empty-state-backdrop-zoom-stability")).toBeTruthy();
    expect(queryByText("Lifecycle + Event + Gate Design")).toBeNull();
    expect(queryByText("Plan Before Worktree")).toBeNull();
    expect(queryByText("LC-ABANDONED")).toBeNull();
    expect(getByText("15. Parallel Leaf Enclosure Workflow").closest("[data-depth='1']")).toBeTruthy();
    expect(getByText("15. Parallel Leaf Enclosure Workflow").closest("[data-parent-key]")?.getAttribute("data-parent-key")).toBe(
      "taskdoc:/tasks/260610_browser-dashboard/task.json",
    );

    fireEvent.click(getByText("Browser Dashboard Series"));
    expect(onSelect).toHaveBeenCalledWith("taskdoc:/tasks/260610_browser-dashboard/task.json");
    fireEvent.click(getByText("15. Parallel Leaf Enclosure Workflow"));
    expect(onSelect).toHaveBeenCalledWith(
      "taskdoc:/tasks/260610_browser-dashboard/15_parallel-leaf-enclosure-workflow.json",
    );

    fireEvent.click(getByText("BY PHASE"));
    expect(getByText("15. Parallel Leaf Enclosure Workflow").closest("[data-depth='0']")).toBeTruthy();
  });

  it("admits an active leaf doc whose enclosure leafId differs from the doc id only by case (L10)", () => {
    // The real series shape: enclosure leaf ids are lowercase directory names (260628-l7) while doc
    // ids are uppercase (260628-L7) and the docPath stem is a numbered slug matching neither.
    seed(
      projection({
        lifecycles: [
          lifecycle({ id: "LC-L7", repoId: "agents-remember", enclosure: "/contracts/l7" }),
        ],
        enclosures: [
          enclosure({
            enclosure: "/contracts/l7",
            lifecycleId: "LC-L7",
            leafId: "260628-l7",
          }),
        ],
        analytics: {
          ...EMPTY_ANALYTICS,
          taskDocuments: [
            taskDoc({
              id: "260628-L7",
              lifecycleId: "LC-L7",
              title: "CGC Dependency Command Repair",
              docPath: "/tasks/260610_browser-dashboard/07_cgc-dependency-command-repair.json",
            }),
          ],
        },
      }),
    );

    const onSelect = vi.fn();
    const { getByText } = render(<LifecycleList selectedId={null} onSelect={onSelect} />);

    // The doc renders as a task-document row (bound through its enclosure), not a bare runtime
    // lifecycle fallback row: clicking it selects the taskdoc, not the lifecycle id.
    fireEvent.click(getByText(/CGC Dependency Command Repair/));
    expect(onSelect).toHaveBeenCalledWith(
      "taskdoc:/tasks/260610_browser-dashboard/07_cgc-dependency-command-repair.json",
    );
  });

  it("nests numbered leaf docs whose enclosure leaf id is shorter than the task file stem", () => {
    const onSelect = vi.fn();
    seed(
      projection({
        lifecycles: [lifecycle({ id: "LC-31", repoId: "agents-remember" })],
        enclosures: [
          enclosure({
            enclosure: "/contracts/31",
            lifecycleId: "LC-31",
            leafId: "31",
          }),
        ],
        analytics: {
          ...EMPTY_ANALYTICS,
          taskDocuments: [
            taskDoc({
              kind: "master",
              title: "Browser Dashboard Series",
              docPath: "/tasks/260610_browser-dashboard/task.json",
            }),
            taskDoc({
              id: "31",
              lifecycleId: "LC-31",
              title: "Provider State Refresh and Engine Room Honesty",
              docPath: "/tasks/260610_browser-dashboard/31_provider-state-refresh-and-engine-room-honesty.json",
            }),
          ],
          series: [
            seriesNode({
              seriesId: "260610_browser-dashboard",
              subTasks: [
                {
                  number: "31",
                  name: "Provider State Refresh and Engine Room Honesty",
                  file: "31_provider-state-refresh-and-engine-room-honesty.md",
                  status: "inProgress",
                  scope: "",
                  createdAt: "2026-06-27T22:33:00+00:00",
                },
              ],
            }),
          ],
        },
      }),
    );

    const { getByText } = render(<LifecycleList selectedId={null} onSelect={onSelect} />);
    const row = getByText("31. Provider State Refresh and Engine Room Honesty");

    expect(getByText("Tasks · 2")).toBeTruthy();
    expect(row.closest("[data-depth='1']")).toBeTruthy();
    expect(row.closest("[data-parent-key]")?.getAttribute("data-parent-key")).toBe(
      "taskdoc:/tasks/260610_browser-dashboard/task.json",
    );
  });

  it("hides a reopened leaf (cleanup=reopened, no worktrees on disk) until worktree_start recreates them", () => {
    // The tasks-surface visibility rule (L11): a leaf appears ONLY while a worktree physically
    // exists. A reopened contract is a reset awaiting restart — its worktrees are gone — so it
    // must NOT render as live work, even though its cleanup state is not completed/abandoned
    // (the proxy that cleanup=reopened outflanked).
    const onSelect = vi.fn();
    seed(
      projection({
        lifecycles: [],
        enclosures: [
          enclosure({
            enclosure: "/contracts/29",
            lifecycleId: "",
            leafId: "29_event-river-retention-and-projection-freshness",
            cleanup: "reopened",
            codeWorktreeExists: false,
            memoryWorktreeExists: false,
          }),
        ],
        analytics: {
          ...EMPTY_ANALYTICS,
          taskDocuments: [
            taskDoc({
              kind: "master",
              title: "Browser Dashboard Series",
              docPath: "/tasks/260610_browser-dashboard/task.json",
            }),
            taskDoc({
              id: "29",
              lifecycleId: undefined,
              title: "Event-River Retention and Projection Freshness",
              docPath:
                "/tasks/260610_browser-dashboard/29_event-river-retention-and-projection-freshness.json",
            }),
          ],
          series: [
            seriesNode({
              seriesId: "260610_browser-dashboard",
              subTasks: [
                {
                  number: "29",
                  name: "Event-River Retention and Projection Freshness",
                  file: "29_event-river-retention-and-projection-freshness.md",
                  status: "planning",
                  scope: "",
                  createdAt: "2026-06-27T22:33:00+00:00",
                },
              ],
            }),
          ],
        },
      }),
    );

    const { getByText, queryByText } = render(<LifecycleList selectedId={null} onSelect={onSelect} />);
    expect(getByText("Tasks · 1")).toBeTruthy(); // the master alone — the reopened leaf is hidden
    expect(queryByText(/Event-River Retention and Projection Freshness/)).toBeNull();
  });

  it("re-admits a reopened leaf once its worktrees physically exist again (after worktree_start)", () => {
    // Existence — not the cleanup label — is the visibility rule, so the row returns the moment
    // the projection stats the recreated worktrees.
    const onSelect = vi.fn();
    seed(
      projection({
        lifecycles: [],
        enclosures: [
          enclosure({
            enclosure: "/contracts/29",
            lifecycleId: "",
            leafId: "29_event-river-retention-and-projection-freshness",
            cleanup: "reopened",
            codeWorktreeExists: true,
            memoryWorktreeExists: true,
          }),
        ],
        analytics: {
          ...EMPTY_ANALYTICS,
          taskDocuments: [
            taskDoc({
              kind: "master",
              title: "Browser Dashboard Series",
              docPath: "/tasks/260610_browser-dashboard/task.json",
            }),
            taskDoc({
              id: "29",
              lifecycleId: undefined,
              title: "Event-River Retention and Projection Freshness",
              docPath:
                "/tasks/260610_browser-dashboard/29_event-river-retention-and-projection-freshness.json",
            }),
          ],
          series: [
            seriesNode({
              seriesId: "260610_browser-dashboard",
              subTasks: [
                {
                  number: "29",
                  name: "Event-River Retention and Projection Freshness",
                  file: "29_event-river-retention-and-projection-freshness.md",
                  status: "planning",
                  scope: "",
                  createdAt: "2026-06-27T22:33:00+00:00",
                },
              ],
            }),
          ],
        },
      }),
    );

    const { getByText } = render(<LifecycleList selectedId={null} onSelect={onSelect} />);
    const row = getByText("29. Event-River Retention and Projection Freshness");
    expect(getByText("Tasks · 2")).toBeTruthy();
    expect(row.closest("[data-depth='1']")).toBeTruthy();
    expect(row.closest("[data-parent-key]")?.getAttribute("data-parent-key")).toBe(
      "taskdoc:/tasks/260610_browser-dashboard/task.json",
    );
  });

  it("renders ONE task entry per enclosureId: a bound lifecycle annotates the doc row, never duplicates it", () => {
    // The L9-reopen defect's second half (L11): the doc row and the live lifecycle's card both
    // rendered for the same leaf. The identity rule is one row per enclosureId — the lifecycle
    // bound to the enclosure (by the contract's lifecycleId, or by its own enclosure anchor)
    // annotates the doc row with its state/gate/staleness instead of adding a card.
    const onSelect = vi.fn();
    seed(
      projection({
        lifecycles: [
          // Bound through the contract's recorded lifecycleId; the doc itself carries no stamp.
          lifecycle({
            id: "LC-CONTRACT",
            repoId: "agents-remember",
            state: "blocked",
            staleSeconds: 120,
            gate: {
              id: "gate-11",
              kind: "closeout-approval",
              state: "open",
              decisions: ["approve", "revise"],
              packet: {},
              ts: "2026-06-24T06:00:40+00:00",
            },
          }),
          // Bound only by its own anchor (lifecycle.enclosure); the contract binding is unset.
          lifecycle({
            id: "LC-ANCHOR",
            repoId: "agents-remember",
            state: "running",
            enclosure: "/contracts/12",
          }),
        ],
        enclosures: [
          enclosure({
            enclosure: "/contracts/11",
            lifecycleId: "LC-CONTRACT",
            leafId: "11_hangar-worktree-truth",
          }),
          enclosure({
            enclosure: "/contracts/12",
            lifecycleId: "",
            leafId: "12_three-party-loops",
          }),
        ],
        analytics: {
          ...EMPTY_ANALYTICS,
          taskDocuments: [
            taskDoc({
              id: "11",
              lifecycleId: undefined,
              title: "Hangar Worktree Truth",
              docPath: "/tasks/260610_browser-dashboard/11_hangar-worktree-truth.json",
            }),
            taskDoc({
              id: "12",
              lifecycleId: undefined,
              title: "Three Party Loops",
              docPath: "/tasks/260610_browser-dashboard/12_three-party-loops.json",
            }),
          ],
        },
      }),
    );

    const { getByText, getAllByRole, queryByText } = render(
      <LifecycleList selectedId={null} onSelect={onSelect} />,
    );

    // One entry per enclosureId — the two doc rows and nothing else (no bare lifecycle cards).
    expect(getByText("Tasks · 2")).toBeTruthy();
    expect(getAllByRole("option")).toHaveLength(2);
    expect(queryByText("11_hangar-worktree-truth")).toBeNull(); // the would-be duplicate's label
    expect(queryByText("12_three-party-loops")).toBeNull();

    // The bound lifecycle ANNOTATES the single row: state, gate, and staleness flow through it.
    const contractRow = getByText("Hangar Worktree Truth");
    expect(contractRow.getAttribute("title")).toContain("Lifecycle: LC-CONTRACT");
    expect(contractRow.getAttribute("title")).toContain("State: blocked");
    expect(contractRow.getAttribute("title")).toContain("Gate: closeout-approval");
    expect(getByText("closeout-approval")).toBeTruthy();
    expect(getByText(/2m/)).toBeTruthy(); // staleSeconds: 120 formatted into the row meta

    const anchorRow = getByText("Three Party Loops");
    expect(anchorRow.getAttribute("title")).toContain("Lifecycle: LC-ANCHOR");
    expect(anchorRow.getAttribute("title")).toContain("State: running");

    // Selecting the single row selects the task document — the leaf's durable identity.
    fireEvent.click(contractRow);
    expect(onSelect).toHaveBeenCalledWith(
      "taskdoc:/tasks/260610_browser-dashboard/11_hangar-worktree-truth.json",
    );
  });

  it("hides an abandoned enclosure from the active operations rows", () => {
    // worktree_abandon discards the worktrees and records cleanup=abandoned; the enclosure is
    // history, not active work, so neither a lifecycle row nor a doc row may render for it (L11).
    const onSelect = vi.fn();
    seed(
      projection({
        lifecycles: [
          lifecycle({ id: "LC-DEAD", repoId: "agents-remember", state: "paused", inferred: true }),
        ],
        enclosures: [
          enclosure({
            enclosure: "/contracts/dead-leaf",
            lifecycleId: "LC-DEAD",
            leafId: "260628-l10-r1",
            cleanup: "abandoned",
            codeWorktreeExists: false,
            memoryWorktreeExists: false,
          }),
        ],
        analytics: {
          ...EMPTY_ANALYTICS,
          taskDocuments: [
            taskDoc({
              id: "260628-L10-R1",
              lifecycleId: "LC-DEAD",
              title: "Dead Reopen Attempt",
              docPath: "/tasks/260628_operations-integration/10r1_dead.json",
            }),
          ],
        },
      }),
    );

    const { queryByText, getByText } = render(<LifecycleList selectedId={null} onSelect={onSelect} />);
    expect(getByText("Tasks · 0")).toBeTruthy();
    expect(queryByText(/Dead Reopen Attempt/)).toBeNull();
    expect(queryByText(/260628-l10-r1/)).toBeNull();
  });

  it("nests a doc-less orphan lifecycle under its master instead of floating top-level", () => {
    const onSelect = vi.fn();
    seed(
      projection({
        lifecycles: [
          lifecycle({ id: "LC-ORPHAN", repoId: "agents-remember", state: "paused", inferred: true }),
        ],
        enclosures: [
          enclosure({
            enclosure: "/contracts/orphan-s7",
            lifecycleId: "LC-ORPHAN",
            leafId: "30_some-leaf-s7",
          }),
        ],
        analytics: {
          ...EMPTY_ANALYTICS,
          taskDocuments: [
            taskDoc({
              kind: "master",
              title: "Browser Dashboard Series",
              docPath: "/tasks/260610_browser-dashboard/task.json",
            }),
          ],
          series: [seriesNode({ seriesId: "260610_browser-dashboard" })],
        },
      }),
    );

    const { getByText } = render(<LifecycleList selectedId={null} onSelect={onSelect} />);
    // No task doc references this lifecycle, so it renders via the bare lifecycle row — but it still
    // nests under its master (resolved from the enclosure's taskRoot), not as a top-level phantom.
    const row = getByText("30_some-leaf-s7");
    expect(row.closest("[data-depth='1']")).toBeTruthy();
    expect(row.closest("[data-parent-key]")?.getAttribute("data-parent-key")).toBe(
      "taskdoc:/tasks/260610_browser-dashboard/task.json",
    );
  });

  it("keeps standalone root task documents visible without listing loose leaf docs", () => {
    const onSelect = vi.fn();
    seed(
      projection({
        lifecycles: [],
        analytics: {
          ...EMPTY_ANALYTICS,
          taskDocuments: [
            taskDoc({
              kind: "light",
              title: "Standalone Planning Task",
              docPath: "/tasks/repo-a/plan/task.json",
              createdAt: "2026-06-20T09:00:00+00:00",
              status: "planning",
            }),
            taskDoc({
              title: "Loose Leaf Plan",
              docPath: "/tasks/repo-a/plan/01_plan.json",
              createdAt: "2026-06-21T09:00:00+00:00",
              status: "planning",
            }),
          ],
        },
      }),
    );

    const { getByText, queryByText } = render(<LifecycleList selectedId={null} onSelect={onSelect} />);

    expect(getByText("Standalone Planning Task")).toBeTruthy();
    expect(queryByText("Loose Leaf Plan")).toBeNull();
    fireEvent.click(getByText("Standalone Planning Task"));
    expect(onSelect).toHaveBeenCalledWith("taskdoc:/tasks/repo-a/plan/task.json");
  });

  it("renders the orchestration tier above its commanded masters with the V4 treatment (L14)", () => {
    // An orchestration task is a master doc carrying `orchestrates` (the owner data-model ruling).
    // It renders gold-tier at depth 0; a master it names nests one step with the purple tier; that
    // master's leaves keep today's rendering one step further; an uncommanded master is unchanged.
    const onSelect = vi.fn();
    seed(
      projection({
        lifecycles: [],
        enclosures: [
          enclosure({
            enclosure: "/contracts/15",
            lifecycleId: "",
            leafId: "15_parallel-leaf-enclosure-workflow",
          }),
        ],
        analytics: {
          ...EMPTY_ANALYTICS,
          taskDocuments: [
            taskDoc({
              id: "SPRINT-02",
              kind: "master",
              title: "SPRINT 02 · rollout",
              docPath: "/tasks/sprint-02/task.json",
              orchestrates: ["260610_browser-dashboard"],
              createdAt: "2026-06-19T09:00:00+00:00",
            }),
            taskDoc({
              kind: "master",
              title: "Browser Dashboard Series",
              docPath: "/tasks/260610_browser-dashboard/task.json",
              createdAt: "2026-06-20T08:00:00+00:00",
            }),
            taskDoc({
              kind: "master",
              title: "Free Standing Series",
              docPath: "/tasks/260620_free-standing/task.json",
              createdAt: "2026-06-21T08:00:00+00:00",
            }),
            taskDoc({
              id: "15",
              title: "Parallel Leaf Enclosure Workflow",
              docPath: "/tasks/260610_browser-dashboard/15_parallel-leaf-enclosure-workflow.json",
              createdAt: "2026-06-22T08:00:00+00:00",
            }),
          ],
          series: [
            seriesNode({
              seriesId: "260610_browser-dashboard",
              subTasks: [
                {
                  number: "15",
                  name: "Parallel Leaf Enclosure Workflow",
                  file: "15_parallel-leaf-enclosure-workflow.md",
                  status: "inProgress",
                  scope: "",
                  createdAt: "2026-06-20T09:00:00+00:00",
                },
              ],
            }),
          ],
        },
      }),
    );

    const { getByText } = render(<LifecycleList selectedId={null} onSelect={onSelect} />);

    // Gold tier: the orchestration row, top-level, chevron badge rendered.
    const sprintRow = getByText("SPRINT 02 · rollout").closest("[role='option']");
    expect(sprintRow?.getAttribute("data-tier")).toBe("orchestration");
    expect(sprintRow?.getAttribute("data-depth")).toBe("0");
    expect(sprintRow?.querySelector("[data-rank-tier='orchestration']")).not.toBeNull();

    // Purple tier: the commanded master nests under the orchestration row at 22px.
    const masterRow = getByText("Browser Dashboard Series").closest("[role='option']");
    expect(masterRow?.getAttribute("data-tier")).toBe("management");
    expect(masterRow?.getAttribute("data-depth")).toBe("1");
    expect(masterRow?.getAttribute("data-parent-key")).toBe("taskdoc:/tasks/sprint-02/task.json");
    expect((masterRow as HTMLElement).style.marginLeft).toBe("22px");
    expect(masterRow?.querySelector("[data-rank-tier='management']")).not.toBeNull();

    // Leaves keep today's rendering one step further (depth 2, one 22px margin step + nested look).
    const leafRow = getByText("15. Parallel Leaf Enclosure Workflow").closest("[role='option']");
    expect(leafRow?.getAttribute("data-depth")).toBe("2");
    expect(leafRow?.getAttribute("data-tier")).toBeNull();
    expect((leafRow as HTMLElement).style.marginLeft).toBe("22px");
    expect(leafRow?.querySelector("[data-rank-tier]")).toBeNull();

    // The uncommanded master is untouched: top-level, no tier, no badge, no margin.
    const freeRow = getByText("Free Standing Series").closest("[role='option']");
    expect(freeRow?.getAttribute("data-tier")).toBeNull();
    expect(freeRow?.getAttribute("data-depth")).toBe("0");
    expect((freeRow as HTMLElement).style.marginLeft).toBe("");
    expect(freeRow?.querySelector("[data-rank-tier]")).toBeNull();
  });

  it("renders NO orchestration row or insignia in a flat run (D3 regression)", () => {
    // No doc carries `orchestrates` ⇒ the list is byte-identical to the pre-L14 rendering:
    // masters top-level, leaves one nested step, zero tier attributes, zero badges.
    const onSelect = vi.fn();
    seed(
      projection({
        lifecycles: [],
        enclosures: [
          enclosure({
            enclosure: "/contracts/15",
            lifecycleId: "",
            leafId: "15_parallel-leaf-enclosure-workflow",
          }),
        ],
        analytics: {
          ...EMPTY_ANALYTICS,
          taskDocuments: [
            taskDoc({
              kind: "master",
              title: "Browser Dashboard Series",
              docPath: "/tasks/260610_browser-dashboard/task.json",
            }),
            taskDoc({
              id: "15",
              title: "Parallel Leaf Enclosure Workflow",
              docPath: "/tasks/260610_browser-dashboard/15_parallel-leaf-enclosure-workflow.json",
            }),
          ],
          series: [
            seriesNode({
              seriesId: "260610_browser-dashboard",
              subTasks: [
                {
                  number: "15",
                  name: "Parallel Leaf Enclosure Workflow",
                  file: "15_parallel-leaf-enclosure-workflow.md",
                  status: "inProgress",
                  scope: "",
                  createdAt: "2026-06-20T09:00:00+00:00",
                },
              ],
            }),
          ],
        },
      }),
    );

    const { container, getByText } = render(<LifecycleList selectedId={null} onSelect={onSelect} />);
    expect(container.querySelector("[data-tier]")).toBeNull();
    expect(container.querySelector("[data-rank-tier]")).toBeNull();
    const masterRow = getByText("Browser Dashboard Series").closest("[role='option']");
    expect(masterRow?.getAttribute("data-depth")).toBe("0");
    expect((masterRow as HTMLElement).style.marginLeft).toBe("");
    const leafRow = getByText("15. Parallel Leaf Enclosure Workflow").closest("[role='option']");
    expect(leafRow?.getAttribute("data-depth")).toBe("1");
    expect((leafRow as HTMLElement).style.marginLeft).toBe("");
  });

  it("exposes the full long task title and row context on title hover", () => {
    const longTitle =
      "Operations task reader row title that is intentionally long enough to require ellipsis in the left rail";
    seed(
      projection({
        lifecycles: [
          lifecycle({
            id: "01KVWK7Z8PQZ7BV9T6QPXFHM3B",
            state: "blocked",
            phase: "reframe-research",
            repoId: "agents-remember",
            gate: {
              id: "gate-1",
              kind: "plan-approval",
              state: "open",
              decisions: ["approve", "revise"],
              packet: {},
              ts: "2026-06-24T06:00:40+00:00",
            },
          }),
        ],
        enclosures: [
          enclosure({
            enclosure: "/contracts/long-title",
            lifecycleId: "01KVWK7Z8PQZ7BV9T6QPXFHM3B",
            leafId: "01",
          }),
        ],
        analytics: {
          ...EMPTY_ANALYTICS,
          taskDocuments: [
            taskDoc({
              lifecycleId: "01KVWK7Z8PQZ7BV9T6QPXFHM3B",
              title: longTitle,
              currentStep: "Constrain Tasks panel row title layout",
              stepsDone: 1,
              stepsTotal: 4,
            }),
          ],
        },
      }),
    );

    const { getByText } = render(<LifecycleList selectedId={null} onSelect={() => {}} />);

    const title = getByText(longTitle);
    const row = title.closest("[role='option']");
    expect(row?.className).toContain("min-w_0");
    expect(row?.className).toContain("max-w_100%");
    expect(row?.lastElementChild?.className).toContain("tov_ellipsis");
    expect(row?.lastElementChild?.className).not.toContain("ml_auto");
    expect(title.className).toContain("flex_1_1_0");
    expect(title.getAttribute("title")).toContain(`Title: ${longTitle}`);
    expect(title.getAttribute("title")).toContain("Lifecycle: 01KVWK7Z8PQZ7BV9T6QPXFHM3B");
    expect(title.getAttribute("title")).toContain("State: blocked");
    expect(title.getAttribute("title")).toContain("Phase: reframe-research");
    expect(title.getAttribute("title")).toContain("Repo: agents-remember");
    expect(title.getAttribute("title")).toContain("Gate: plan-approval");
    expect(title.getAttribute("title")).toContain(
      "Current step: Constrain Tasks panel row title layout",
    );
  });
});

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

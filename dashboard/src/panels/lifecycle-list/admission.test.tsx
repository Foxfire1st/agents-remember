import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { LifecycleList } from "./LifecycleList";
import {
  EMPTY_ANALYTICS,
  enclosure,
  installLifecycleListCleanup,
  lifecycle,
  projection,
  seed,
  seriesNode,
  taskDoc,
} from "./test-utils";

installLifecycleListCleanup();

describe("LifecycleList task labels — row admission", () => {
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
    // The real series shape: enclosure leaf ids are lowercase directory names while doc
    // ids are uppercase and the docPath stem is a numbered slug matching neither.
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
    // The tasks-surface visibility rule: a leaf appears ONLY while a worktree physically
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
    // The reopen defect's second half: the doc row and the live lifecycle's card both
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
              evidenceRefs: [],
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
    // history, not active work, so neither a lifecycle row nor a doc row may render for it.
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

});


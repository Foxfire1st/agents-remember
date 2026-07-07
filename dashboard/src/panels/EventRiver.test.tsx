import { cleanup, render } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";

import { dashboardStore } from "../data/store";
import type { ObserverEvent } from "../types/event";
import type {
  Analytics,
  EnclosureNode,
  LifecycleProjection,
  TaskDocNode,
} from "../types/projection";
import { EventRiver } from "./EventRiver";

// The Event River list is virtualized (TanStack), which mounts only the rows whose positions fall
// inside the scroll viewport. TanStack measures the viewport and rows from offsetWidth/offsetHeight,
// which jsdom reports as 0 (no layout), so nothing would mount. Give every box a non-zero size so a
// real viewport + measured rows let the newest window of rows mount and be asserted; off-screen rows
// stay virtualized out (the no-cap behaviour is asserted via the retained count in the header).
const sizedBox: Record<string, PropertyDescriptor | undefined> = {};
beforeAll(() => {
  for (const [prop, value] of [
    ["offsetHeight", 600],
    ["offsetWidth", 320],
  ] as const) {
    sizedBox[prop] = Object.getOwnPropertyDescriptor(HTMLElement.prototype, prop);
    Object.defineProperty(HTMLElement.prototype, prop, { configurable: true, get: () => value });
  }
});
afterAll(() => {
  for (const prop of ["offsetHeight", "offsetWidth"]) {
    const original = sizedBox[prop];
    if (original) Object.defineProperty(HTMLElement.prototype, prop, original);
  }
});

afterEach(cleanup);
beforeEach(() =>
  dashboardStore.setState({
    analytics: null,
    enclosures: {},
    events: [],
    eventsHydrated: false,
    lifecycles: {},
  }),
);

function ev(partial: Partial<ObserverEvent> & { kind: string }): ObserverEvent {
  return {
    schema: "ar-observer-event/v1",
    id: partial.id ?? `e-${partial.kind}-${Math.random().toString(36).slice(2)}`,
    ts: "2026-06-23T10:11:12+00:00",
    trust: "observed",
    actor: "model",
    ...partial,
  } as ObserverEvent;
}

function lifecycle(partial: Partial<LifecycleProjection> & { id: string }): LifecycleProjection {
  const { id, ...rest } = partial;
  return {
    actions: [],
    fleeting: false,
    id,
    inferred: false,
    lastEventTs: "2026-06-23T10:11:12+00:00",
    phase: "build",
    startedAt: "2026-06-23T10:00:00+00:00",
    state: "running",
    tokenSeries: [],
    tokens: 0,
    ...rest,
  };
}

function enclosure(partial: Partial<EnclosureNode> & { enclosure: string }): EnclosureNode {
  const { enclosure: enclosurePath, ...rest } = partial;
  return {
    actions: [],
    cleanup: "pending",
    closeoutStatus: "not-started",
    codeWorktreeExists: true,
    memoryWorktreeExists: true,
    enclosure: enclosurePath,
    enclosureId: enclosurePath,
    humanReviewStatus: "pending-review",
    integrationStatus: "not-started",
    leafId: "20_event-river-readable-activity-feed",
    lifecycleId: "lc-1",
    repoName: "agents-remember",
    taskId: "260610_BROWSER-DASHBOARD",
    taskName: "260610_browser-dashboard",
    taskRoot: "tasks/agents-remember/260610_browser-dashboard",
    worktreeGroup: "260610-browser-dashboard-s20-ar",
    ...rest,
  };
}

function taskDoc(partial: Partial<TaskDocNode> & { docPath: string }): TaskDocNode {
  const { docPath, ...rest } = partial;
  return {
    codeExamples: [],
    decisions: [],
    docPath,
    id: "20",
    kind: "subTask",
    objective: "",
    openQuestions: [],
    references: [],
    repository: "agents-remember",
    requirements: [],
    sections: [],
    status: "planning",
    steps: [],
    stepsDone: 0,
    stepsTotal: 21,
    subTasks: [],
    title: "Event River Readable Activity Feed",
    ...rest,
  };
}

function analyticsWithTaskDocuments(taskDocuments: TaskDocNode[]): Analytics {
  return {
    attentionQueue: [],
    driftSnapshots: [],
    engineProcesses: [],
    ledgers: [],
    routeCoverage: [],
    series: [],
    setupProgress: [],
    setupSummaries: [],
    stalestSidecars: [],
    taskDocuments,
    toolReports: [],
  };
}

describe("EventRiver readable activity feed", () => {
  it("waits for the raw event stream to hydrate before claiming an empty river", () => {
    const view = render(<EventRiver />);
    expect(view.getByText("Syncing event history.")).not.toBeNull();
    expect(view.queryByText("No events yet.")).toBeNull();

    dashboardStore.setState({ eventsHydrated: true });
    view.rerender(<EventRiver />);
    expect(view.getByText("No events yet.")).not.toBeNull();
  });

  it("renders 'Read: <basename>' with the repo, and the full path on hover", () => {
    const full = "mcp/src/agents_remember/kernel/coordination_context/contracts.py";
    dashboardStore.setState({
      events: [
        ev({
          kind: "read.packet",
          lifecycleId: "lc-1",
          data: {
            repoId: "agents-remember",
            files: [{ path: full, lines: "full", status: "found", bytes: 2270 }],
          },
        }),
      ],
      lifecycles: { "lc-1": lifecycle({ id: "lc-1" }) },
    });
    const { getByText } = render(<EventRiver />);
    const label = getByText("Read: contracts.py");
    expect(label.getAttribute("title")).toBe(full);
    const item = label.closest("[data-testid='river-item']");
    expect(item?.textContent).toContain("agents-remember");
  });

  it("summarizes a multi-file batch as '+N more' with every path on hover", () => {
    dashboardStore.setState({
      events: [
        ev({
          kind: "read.packet",
          data: {
            repoId: "agents-remember",
            files: [{ path: "a/one.py" }, { path: "b/two.py" }, { path: "c/three.py" }],
          },
        }),
      ],
    });
    const { getByText } = render(<EventRiver />);
    const label = getByText("Read: one.py +2 more");
    const title = label.getAttribute("title") ?? "";
    expect(title).toContain("a/one.py");
    expect(title).toContain("b/two.py");
    expect(title).toContain("c/three.py");
  });

  it("retains and virtualizes the full window beyond the old newest-60 cap", () => {
    dashboardStore.setState({
      events: [
        ev({
          id: "first",
          kind: "read.packet",
          data: { repoId: "agents-remember", files: [{ path: "oldest/first.py" }] },
        }),
        ...Array.from({ length: 65 }, (_, i) =>
          ev({
            id: `extra-${i}`,
            kind: "read.packet",
            data: { repoId: "agents-remember", files: [{ path: `batch/file-${i}.py` }] },
          }),
        ),
      ],
    });
    const { getByText } = render(<EventRiver />);
    // The whole window is retained and counted (no newest-N display slice); the newest row mounts.
    // Off-screen rows are virtualized out of the DOM, not dropped from the feed.
    expect(getByText(/Event river · 66/)).not.toBeNull();
    expect(getByText("Read: file-64.py")).not.toBeNull();
  });

  it("translates tool.completed rows with friendly tool copy, result state, tokens, and agent actor copy", () => {
    dashboardStore.setState({
      events: [
        ev({
          kind: "tool.completed",
          lifecycleId: "lc-1",
          data: { tool: "context_packet", ok: true, tokens: 1234 },
        }),
      ],
      lifecycles: { "lc-1": lifecycle({ id: "lc-1" }) },
    });
    const { getByText } = render(<EventRiver />);
    expect(getByText("Checked workspace context")).not.toBeNull();
    const item = getByText("Checked workspace context").closest("[data-testid='river-item']");
    expect(item?.textContent).toContain("agent");
    expect(item?.textContent).toContain("observed");
    expect(item?.textContent).toContain("ok");
    expect(item?.textContent).toContain("1,234 tokens");
  });

  it("waits for lifecycle summary context before rendering lifecycle-bound rows", () => {
    dashboardStore.setState({
      events: [
        ev({
          kind: "tool.completed",
          lifecycleId: "lc-loading",
          data: { tool: "worktree_status", ok: true },
        }),
      ],
    });
    const view = render(<EventRiver />);
    expect(view.queryByText("Checked worktree status")).toBeNull();

    dashboardStore.setState({
      analytics: analyticsWithTaskDocuments([
        taskDoc({
          docPath: "29_event-river-retention-and-projection-freshness.json",
          lifecycleId: "lc-loading",
          title: "Event River Retention And Projection Freshness",
        }),
      ]),
    });
    view.rerender(<EventRiver />);

    expect(view.getByText("Checked worktree status")).not.toBeNull();
  });

  it("shows the destination phase for lifecycle.phase-changed", () => {
    dashboardStore.setState({
      events: [ev({ kind: "lifecycle.phase-changed", data: { phase: "close" } })],
    });
    const { getByText } = render(<EventRiver />);
    expect(getByText("Moved to Close")).not.toBeNull();
  });

  it("renders blocked lifecycle rows around the actual ask prompt", () => {
    dashboardStore.setState({
      events: [
        ev({
          kind: "lifecycle.blocked",
          data: { ask: { kind: "decision", prompt: "Approve the plan?" } },
        }),
      ],
    });
    const { getByText } = render(<EventRiver />);
    expect(getByText("Waiting: Approve the plan?")).not.toBeNull();
    const item = getByText("Waiting: Approve the plan?").closest("[data-testid='river-item']");
    expect(item?.textContent).toContain("Decision request");
  });

  it("uses task labels from lifecycle enclosures instead of raw lifecycle ids", () => {
    const lc = lifecycle({ enclosure: "enc-1", id: "lc-1", repoId: "agents-remember" });
    dashboardStore.setState({
      analytics: analyticsWithTaskDocuments([
        taskDoc({ docPath: "20_event-river-readable-activity-feed.json", lifecycleId: "lc-1" }),
      ]),
      enclosures: { "enc-1": enclosure({ enclosure: "enc-1", lifecycleId: "lc-1" }) },
      events: [
        ev({
          kind: "tool.completed",
          lifecycleId: "lc-1",
          data: { tool: "worktree_attach", ok: true },
        }),
      ],
      lifecycles: { "lc-1": lc },
    });
    const { getByText } = render(<EventRiver />);
    const item = getByText("Attached to task").closest("[data-testid='river-item']");
    expect(item?.textContent).toContain("20_event-river-readable-activity-feed");
    expect(item?.textContent).not.toContain("lc-1");
  });

  it("uses task document labels when event history no longer has a live lifecycle row", () => {
    dashboardStore.setState({
      analytics: analyticsWithTaskDocuments([
        taskDoc({
          docPath: "20_event-river-readable-activity-feed.json",
          lifecycleId: "lc-completed",
        }),
      ]),
      events: [
        ev({
          kind: "tool.completed",
          lifecycleId: "lc-completed",
          data: { tool: "worktree_cleanup", ok: true },
        }),
      ],
    });
    const { getByText } = render(<EventRiver />);
    const item = getByText("Cleaned up task worktree").closest("[data-testid='river-item']");
    expect(item?.textContent).toContain("Event River Readable Activity Feed");
    expect(item?.textContent).not.toContain("lc-completed");
  });

  it("hides lifecycle heartbeat rows from the default river while keeping other rows", () => {
    dashboardStore.setState({
      events: [
        ev({ kind: "lifecycle.heartbeat", data: { phase: "build", state: "running" } }),
        ev({ kind: "lifecycle.phase-changed", data: { phase: "build" } }),
      ],
    });
    const { getByText, queryByText } = render(<EventRiver />);
    expect(queryByText("Heartbeat")).toBeNull();
    expect(getByText("Moved to Build")).not.toBeNull();
  });

  it("falls back honestly to unknown raw event kinds", () => {
    dashboardStore.setState({ events: [ev({ kind: "custom.unhandled" })] });
    const { getByText } = render(<EventRiver />);
    expect(getByText("custom.unhandled")).not.toBeNull();
  });
});

import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { sessionStore } from "../data/sessions";
import { dashboardStore } from "../data/store";
import { GALLERY } from "../dev/fixtures";
import type { LifecycleProjection, TaskDocNode, WorkspaceProjection } from "../types/projection";
import { CockpitShell } from "./Cockpit";

function seed(stateName: string) {
  const fixture = GALLERY.find((entry) => entry.name === stateName);
  if (!fixture) throw new Error(`fixture not found: ${stateName}`);
  dashboardStore.getState().applySnapshot(fixture.projection);
}

// Mock the lazy Terminal so toggling the rail to chat never pulls xterm (a canvas probe) into jsdom.
vi.mock("../panels/Terminal", () => ({
  Terminal: ({ sessionId }: { sessionId: string }) => <div data-testid={`term-${sessionId}`} />,
}));

function taskDoc(over: Partial<TaskDocNode> & Pick<TaskDocNode, "kind" | "docPath" | "id">): TaskDocNode {
  return {
    lifecycleId: "ROOT",
    repository: "repo-a",
    title: "doc",
    status: "inProgress",
    createdAt: "2026-06-20T09:00:00+00:00",
    stepsDone: 0,
    stepsTotal: 0,
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
  } as TaskDocNode;
}

// A lifecycle-bound master with one authored, drillable leaf — the drilled-leaf fixture for fix 1.
function seedDrillableMaster() {
  const lc: LifecycleProjection = {
    id: "ROOT",
    state: "running",
    phase: "build",
    fleeting: false,
    repoId: "repo-a",
    tokens: 0,
    startedAt: "2026-06-20T09:00:00+00:00",
    lastEventTs: "2026-06-20T09:00:30+00:00",
    inferred: false,
    actions: [],
    tokenSeries: [],
  };
  const master = taskDoc({
    id: "master-x",
    kind: "master",
    title: "Ops Master",
    docPath: "/tasks/repo-a/ops/task.json",
    objective: "Master objective.",
    subTasks: [
      {
        number: "1",
        name: "Leaf One",
        file: "01_leaf.md",
        status: "inProgress",
        scope: "",
        createdAt: "2026-06-20T09:00:00+00:00",
      },
    ],
  });
  const leaf = taskDoc({
    id: "leaf-one",
    kind: "subTask",
    title: "Leaf One",
    docPath: "/tasks/repo-a/ops/01_leaf.json",
    objective: "Leaf objective.",
  });
  const projection: WorkspaceProjection = {
    version: 2,
    generatedAt: "2026-06-20T09:01:00+00:00",
    lifecycles: [lc],
    enclosures: [],
    providers: [],
    activeWorktreeGroups: [],
    metrics: {
      lifecycleCount: 1,
      runningCount: 1,
      blockedCount: 0,
      pausedCount: 0,
      totalTokens: 0,
      stalenessHistogram: {},
    },
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
    },
  };
  dashboardStore.getState().applySnapshot(projection);
}

afterEach(() => {
  cleanup();
  sessionStore.setState({ sessions: [], activeId: null, count: 0 });
  dashboardStore.getState().reset();
  window.localStorage.clear(); // the rail toggle now persists its choice; isolate it between tests
});

describe("CockpitShell full-bleed machine-map views (5f S1)", () => {
  it("rails the Operations view but goes full-bleed (no rails) for the Engine Room", () => {
    seed("engine-fleet");
    const { container, getByRole } = render(<CockpitShell />);

    // Operations (default): the railed 3-column shell.
    expect(container.querySelector(".shell__body")?.getAttribute("data-fullbleed")).toBe("false");
    expect(container.querySelector(".rail--left")).not.toBeNull();
    expect(container.querySelector(".rail--right")).not.toBeNull();

    // Switch to the Engine Room machine-map view via the mode bar.
    fireEvent.click(getByRole("radio", { name:"Engine Room" }));

    // Full-bleed: both rails gone, single full-width column, and the room's own 3-zone layout
    // (header + boot/diagnostics zone) is present.
    expect(container.querySelector(".shell__body")?.getAttribute("data-fullbleed")).toBe("true");
    expect(container.querySelector(".rail--left")).toBeNull();
    expect(container.querySelector(".rail--right")).toBeNull();
    expect(container.querySelector('[data-testid="engine-room-header"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="engine-room-diagnostics"]')).not.toBeNull();
  });

  it("keeps the rails for the Operations and Memory views", () => {
    seed("engine-fleet");
    const { container, getByRole } = render(<CockpitShell />);

    fireEvent.click(getByRole("radio", { name:"Memory" }));
    expect(container.querySelector(".shell__body")?.getAttribute("data-fullbleed")).toBe("false");
    expect(container.querySelector(".rail--left")).not.toBeNull();
    expect(container.querySelector(".rail--right")).not.toBeNull();
  });
});

describe("right-rail River⇄Chat toggle (L5 S2)", () => {
  it("swaps the rail--right content between the Event River and the single-instance chat", () => {
    seed("engine-fleet");
    const { container, getByTestId } = render(<CockpitShell />);

    const railRight = container.querySelector(".rail--right");
    expect(railRight).not.toBeNull();
    // Default = the Event River; the chat surface is not mounted.
    expect(railRight?.querySelector('[data-testid="event-river"]')).not.toBeNull();
    expect(railRight?.querySelector('[data-testid="rail-chat"]')).toBeNull();

    // Toggle to Chat: the river is gone, the single-instance chat is mounted in its place.
    fireEvent.click(getByTestId("rail-toggle-chat"));
    expect(railRight?.querySelector('[data-testid="rail-chat"]')).not.toBeNull();
    expect(railRight?.querySelector('[data-testid="event-river"]')).toBeNull();

    // Toggle back to River restores it.
    fireEvent.click(getByTestId("rail-toggle-river"));
    expect(railRight?.querySelector('[data-testid="event-river"]')).not.toBeNull();
    expect(railRight?.querySelector('[data-testid="rail-chat"]')).toBeNull();
  });

  it("remembers the rail choice across a window refresh (localStorage)", () => {
    seed("engine-fleet");
    const first = render(<CockpitShell />);
    // Default = River, then switch to Chat.
    expect(first.container.querySelector('.rail--right [data-testid="event-river"]')).not.toBeNull();
    fireEvent.click(first.getByTestId("rail-toggle-chat"));
    expect(window.localStorage.getItem("cockpit.rail-chat")).toBe("1");
    first.unmount();

    // A fresh mount (the window refresh) restores Chat from localStorage — the river is not shown.
    const second = render(<CockpitShell />);
    const railRight = second.container.querySelector(".rail--right");
    expect(railRight?.querySelector('[data-testid="rail-chat"]')).not.toBeNull();
    expect(railRight?.querySelector('[data-testid="event-river"]')).toBeNull();
  });
});

describe("Operations rails are resizable + persisted", () => {
  it("renders a resize gutter on each rail and applies the persisted widths to the grid", () => {
    window.localStorage.setItem("cockpit.rail-left-w", "430");
    window.localStorage.setItem("cockpit.rail-right-w", "250");
    seed("engine-fleet");
    const { container, getByTestId } = render(<CockpitShell />);

    // Each rail owns a drag gutter, and the railed grid uses the stored widths (centre takes the rest).
    expect(getByTestId("rail-resize-left")).not.toBeNull();
    expect(getByTestId("rail-resize-right")).not.toBeNull();
    const body = container.querySelector(".shell__body") as HTMLElement;
    expect(body.style.gridTemplateColumns).toBe("430px minmax(380px, 1fr) 250px");
  });

  it("nudges a rail width with the keyboard and persists the new width", () => {
    seed("engine-fleet"); // no stored width -> default 340
    const { getByTestId } = render(<CockpitShell />);

    fireEvent.keyDown(getByTestId("rail-resize-left"), { key: "ArrowRight" });
    expect(window.localStorage.getItem("cockpit.rail-left-w")).toBe("364"); // 340 + 24

    // The right rail's gutter is mirror-imaged: ArrowLeft grows it.
    fireEvent.keyDown(getByTestId("rail-resize-right"), { key: "ArrowLeft" });
    expect(window.localStorage.getItem("cockpit.rail-right-w")).toBe("324"); // 300 + 24
  });
});

describe("rail chat keys by the drilled leaf, not the master (L5 fix 1)", () => {
  it("keys the rail chat by the leaf once a master's sub-task is drilled open", () => {
    seedDrillableMaster();
    const { getByText, getByTestId } = render(<CockpitShell />);

    // Select the master, then toggle the rail to the chat surface.
    fireEvent.click(getByText("Ops Master"));
    fireEvent.click(getByTestId("rail-toggle-chat"));

    // Master overview shown (no leaf drilled): the rail is NOT blocked — it offers the
    // create-from-anywhere empty state, and the heading carries no leaf id yet (not the master's).
    expect(getByTestId("rail-chat-empty")).not.toBeNull();
    expect(getByTestId("rail-chat-heading").textContent).not.toContain("master-x");

    // Drill into the master's sub-task → the rail keys by THAT leaf id, not the master.
    fireEvent.click(getByTestId("subtask-open-1"));
    const heading = getByTestId("rail-chat-heading");
    expect(heading.textContent).toContain("leaf-one");
    expect(heading.textContent).not.toContain("master-x");
  });
});

describe("Operations drill survives a view switch (DetailPanel mount preservation)", () => {
  it("keeps the drilled sub-task open after switching to another tab and back", () => {
    seedDrillableMaster();
    const { getByText, getByRole, getByTestId, queryByTestId } = render(<CockpitShell />);

    // Select the master, then drill into its sub-task → the leaf reader (a breadcrumb back to the
    // master) replaces the sub-task index.
    fireEvent.click(getByText("Ops Master"));
    fireEvent.click(getByTestId("subtask-open-1"));
    expect(getByTestId("series-breadcrumb")).not.toBeNull();
    expect(queryByTestId("subtask-open-1")).toBeNull(); // we're in the reader, not the index

    // Leave Operations for another tab, then come back: the drill is preserved (the panel was hidden,
    // not unmounted), so it does NOT reset to the master overview.
    fireEvent.click(getByRole("radio", { name: "Memory" }));
    fireEvent.click(getByRole("radio", { name: "Operations" }));
    expect(getByTestId("series-breadcrumb")).not.toBeNull();
    expect(queryByTestId("subtask-open-1")).toBeNull();
  });
});

describe("Chats persistence across view switches (6e hardening)", () => {
  it("keeps <Chats> mounted (hidden) on other views and shows the same node on Chats", () => {
    seed("engine-fleet");
    const { container, getByRole } = render(<CockpitShell />);

    // Default Operations view: Chats is already mounted but hidden — the live terminal it owns is
    // never torn down, so a view switch can't throw the session's visuals away.
    const chats = container.querySelector('[data-testid="chats"]');
    expect(chats).not.toBeNull();
    expect((chats?.parentElement as HTMLElement).style.display).toBe("none");

    // Switching to Chats reveals the *same* element (it was never remounted).
    fireEvent.click(getByRole("radio", { name: "Chats" }));
    expect(container.querySelector('[data-testid="chats"]')).toBe(chats);
    expect((chats?.parentElement as HTMLElement).style.display).toBe("flex");

    // Leaving Chats hides it again without unmounting (still the same node).
    fireEvent.click(getByRole("radio", { name: "Operations" }));
    expect(container.querySelector('[data-testid="chats"]')).toBe(chats);
    expect((chats?.parentElement as HTMLElement).style.display).toBe("none");
  });
});

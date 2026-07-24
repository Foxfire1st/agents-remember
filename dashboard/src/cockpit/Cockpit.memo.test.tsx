import { cleanup, fireEvent, render } from "@testing-library/react";
import { type ComponentProps } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { sessionStore } from "../data/sessions";
import { dashboardStore } from "../data/store";
import { GALLERY } from "../dev/fixtures";
import type { LifecycleProjection, TaskDocNode, WorkspaceProjection } from "../types/projection";
import { CockpitShell } from "./Cockpit";

// Render-count probes (tab-switch CPU, the memoization contract in Cockpit.tsx): every
// persistent layer is replaced by a memoized probe that counts a render and then renders the REAL
// panel. The probe's memo gate has the same default shallow compare as the production export, so
// the count is exactly the layer's PARENT-driven render count — what a setView used to churn.
// Store-driven updates inside a panel never touch the probe, matching the fix's scope.
const counts = vi.hoisted(() => ({
  engineRoom: 0,
  detailPanel: 0,
  sessionsView: 0,
  fileViewer: 0,
  attentionQueue: 0,
  lifecycleList: 0,
  eventRiver: 0,
}));

vi.mock("../panels/EngineRoom", async (importOriginal) => {
  const { memo } = await import("react");
  const mod = await importOriginal<typeof import("../panels/EngineRoom")>();
  const Real = mod.EngineRoom;
  const Counted = memo(function CountedEngineRoom() {
    counts.engineRoom += 1;
    return <Real />;
  });
  return { ...mod, EngineRoom: Counted };
});

vi.mock("../panels/DetailPanel", async (importOriginal) => {
  const { memo } = await import("react");
  const mod = await importOriginal<typeof import("../panels/DetailPanel")>();
  const Real = mod.DetailPanel;
  const Counted = memo(function CountedDetailPanel(props: ComponentProps<typeof Real>) {
    counts.detailPanel += 1;
    return <Real {...props} />;
  });
  return { ...mod, DetailPanel: Counted };
});

vi.mock("../panels/session-cockpit/SessionsView", async (importOriginal) => {
  const { memo } = await import("react");
  const mod = await importOriginal<typeof import("../panels/session-cockpit/SessionsView")>();
  const Real = mod.SessionsView;
  const Counted = memo(function CountedSessionsView(props: ComponentProps<typeof Real>) {
    counts.sessionsView += 1;
    return <Real {...props} />;
  });
  return { ...mod, SessionsView: Counted };
});

vi.mock("../panels/file-viewer/FileViewer", async (importOriginal) => {
  const { memo } = await import("react");
  const mod = await importOriginal<typeof import("../panels/file-viewer/FileViewer")>();
  const Real = mod.FileViewer;
  const Counted = memo(function CountedFileViewer(props: ComponentProps<typeof Real>) {
    counts.fileViewer += 1;
    return <Real {...props} />;
  });
  return { ...mod, FileViewer: Counted };
});

vi.mock("../panels/AttentionQueue", async (importOriginal) => {
  const { memo } = await import("react");
  const mod = await importOriginal<typeof import("../panels/AttentionQueue")>();
  const Real = mod.AttentionQueue;
  const Counted = memo(function CountedAttentionQueue(props: ComponentProps<typeof Real>) {
    counts.attentionQueue += 1;
    return <Real {...props} />;
  });
  return { ...mod, AttentionQueue: Counted };
});

vi.mock("../panels/LifecycleList", async (importOriginal) => {
  const { memo } = await import("react");
  const mod = await importOriginal<typeof import("../panels/LifecycleList")>();
  const Real = mod.LifecycleList;
  const Counted = memo(function CountedLifecycleList(props: ComponentProps<typeof Real>) {
    counts.lifecycleList += 1;
    return <Real {...props} />;
  });
  return { ...mod, LifecycleList: Counted };
});

vi.mock("../panels/EventRiver", async (importOriginal) => {
  const { memo } = await import("react");
  const mod = await importOriginal<typeof import("../panels/EventRiver")>();
  const Real = mod.EventRiver;
  const Counted = memo(function CountedEventRiver() {
    counts.eventRiver += 1;
    return <Real />;
  });
  return { ...mod, EventRiver: Counted };
});

// Mock the lazy Terminal so toggling the rail to chat never pulls xterm (a canvas probe) into jsdom.
vi.mock("../panels/Terminal", () => ({
  Terminal: ({ sessionId }: { sessionId: string }) => <div data-testid={`term-${sessionId}`} />,
}));

function seed(stateName: string) {
  const fixture = GALLERY.find((entry) => entry.name === stateName);
  if (!fixture) throw new Error(`fixture not found: ${stateName}`);
  dashboardStore.getState().applySnapshot(fixture.projection);
}

function taskDoc(
  over: Partial<TaskDocNode> & Pick<TaskDocNode, "kind" | "docPath" | "id">,
): TaskDocNode {
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

// A lifecycle-bound master with one authored, drillable leaf (same fixture shape as Cockpit.test.tsx).
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

beforeEach(() => {
  for (const key of Object.keys(counts) as (keyof typeof counts)[]) counts[key] = 0;
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  sessionStore.setState({ sessions: [], activeId: null, count: 0 });
  dashboardStore.getState().reset();
  window.localStorage.clear(); // the rail toggle now persists its choice; isolate it between tests
});

describe("persistent layers skip the setView reconcile (260721 tab-switch CPU)", () => {
  it("keeps unchanged layers still and sends rail visibility only across bleed boundaries", () => {
    seed("engine-fleet");
    const view = render(<CockpitShell />);

    // Every persistent layer mounted exactly once (all are keep-alive, hidden via display).
    expect(counts.engineRoom).toBe(1);
    expect(counts.detailPanel).toBe(1);
    expect(counts.sessionsView).toBe(1);
    expect(counts.fileViewer).toBe(1);
    expect(counts.attentionQueue).toBe(1);
    expect(counts.lifecycleList).toBe(1);
    expect(counts.eventRiver).toBe(1);

    // A full mode-bar sweep — railed↔full-bleed grid flips in both directions. Before the fix
    // EVERY layer reconciled on each of these clicks; now the memo gate holds wherever the
    // layer's props are unchanged, so a switch costs only the shell's style/layout flip.
    for (const name of ["Engine Room", "Chats", "Memory", "Topology", "Hangar", "Operations"]) {
      fireEvent.click(view.getByRole("radio", { name }));
    }

    // No props at all / unchanged props: never re-rendered by the sweep.
    expect(counts.engineRoom).toBe(1);
    expect(counts.detailPanel).toBe(1); // no selection; stable callbacks
    expect(counts.eventRiver).toBe(1);
    expect(counts.fileViewer).toBe(1); // File Viewer never visited — `active` stayed false
    // The two left-rail panels receive only the railed↔full-bleed visibility edges: engine
    // (hide), memory (show), topology (hide), hangar (show). LifecycleList's rendered React Aria
    // collection has its own memo gate, so these controller renders do not rebuild it while hidden.
    expect(counts.attentionQueue).toBe(5);
    expect(counts.lifecycleList).toBe(5);
    // `active` flipped exactly twice: entering Chats and leaving it.
    expect(counts.sessionsView).toBe(3);
  });

  it("keeps the visibility/aria contract and DOM identity across switches (keep-alive intact)", () => {
    seed("engine-fleet");
    const { container, getByRole } = render(<CockpitShell />);
    const railLeft = container.querySelector(".rail--left") as HTMLElement;
    const railRight = container.querySelector(".rail--right") as HTMLElement;
    const room = container.querySelector('[data-testid="engine-room"]') as HTMLElement;
    const roomLayer = room.parentElement as HTMLElement;
    const chats = container.querySelector('[data-testid="sessions-view"]') as HTMLElement;
    const chatsLayerEl = chats.parentElement as HTMLElement;

    // Railed Operations: rails shown, keep-alive layers hidden via display + aria-hidden.
    expect(railLeft.style.display).toBe("flex");
    expect(roomLayer.style.display).toBe("none");
    expect(roomLayer.getAttribute("aria-hidden")).toBe("true");
    expect(chatsLayerEl.style.display).toBe("none");

    fireEvent.click(getByRole("radio", { name: "Engine Room" }));
    expect(railLeft.style.display).toBe("none");
    expect(railRight.style.display).toBe("none");
    expect(railLeft.getAttribute("aria-hidden")).toBe("true");
    expect(roomLayer.style.display).toBe("flex");
    expect(roomLayer.getAttribute("aria-hidden")).toBe("false");

    fireEvent.click(getByRole("radio", { name: "Chats" }));
    expect(chatsLayerEl.style.display).toBe("flex");
    expect(chatsLayerEl.getAttribute("aria-hidden")).toBe("false");
    expect(roomLayer.style.display).toBe("none");

    fireEvent.click(getByRole("radio", { name: "Operations" }));
    // Same DOM nodes throughout — hidden-not-unmounted survived the memo change.
    expect(container.querySelector(".rail--left")).toBe(railLeft);
    expect(container.querySelector(".rail--right")).toBe(railRight);
    expect(container.querySelector('[data-testid="engine-room"]')).toBe(room);
    expect(container.querySelector('[data-testid="sessions-view"]')).toBe(chats);
    expect(railLeft.style.display).toBe("flex");
    expect(roomLayer.style.display).toBe("none");
    expect(chatsLayerEl.style.display).toBe("none");
  });

  it("still re-renders a layer when its real props change (the memo gate passes selection)", () => {
    seedDrillableMaster();
    const view = render(<CockpitShell />);
    expect(counts.detailPanel).toBe(1);
    expect(counts.lifecycleList).toBe(1);

    // Selecting the master changes selectedId — a REAL prop change the memo must pass through.
    fireEvent.click(view.getByText("Ops Master"));
    expect(counts.detailPanel).toBe(2);
    expect(counts.lifecycleList).toBe(2);
    // …while layers whose props genuinely didn't change still skip.
    expect(counts.attentionQueue).toBe(1);
    expect(counts.engineRoom).toBe(1);
    expect(counts.eventRiver).toBe(1);
  });

  it("swaps the right rail between River and Chat (memoized RailToggle still updates)", () => {
    seed("engine-fleet");
    const view = render(<CockpitShell />);
    const railRight = view.container.querySelector(".rail--right");
    expect(railRight?.querySelector('[data-testid="event-river"]')).not.toBeNull();

    fireEvent.click(view.getByTestId("rail-toggle-chat"));
    expect(railRight?.querySelector('[data-testid="rail-chat"]')).not.toBeNull();
    expect(railRight?.querySelector('[data-testid="event-river"]')).toBeNull();

    fireEvent.click(view.getByTestId("rail-toggle-river"));
    expect(railRight?.querySelector('[data-testid="event-river"]')).not.toBeNull();
    expect(railRight?.querySelector('[data-testid="rail-chat"]')).toBeNull();
  });
});

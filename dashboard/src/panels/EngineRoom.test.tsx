import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { dashboardStore } from "../data/store";
import { GALLERY } from "../dev/fixtures";
import type { LifecycleProjection, ProviderNode, WorkspaceProjection } from "../types/projection";
import { EngineRoom } from "./EngineRoom";
import { buildEngineRoomModel } from "./engine-room/buildEngineRoomModel";

// Delegate-to-real spy: proves WHEN the model is rebuilt (memo) without changing behavior.
vi.mock("./engine-room/buildEngineRoomModel", async (importOriginal) => {
  const mod = await importOriginal<typeof import("./engine-room/buildEngineRoomModel")>();
  return { ...mod, buildEngineRoomModel: vi.fn(mod.buildEngineRoomModel) };
});
const buildSpy = vi.mocked(buildEngineRoomModel);

function seed(name: string) {
  const projection = GALLERY.find((entry) => entry.name === name)?.projection;
  if (!projection) throw new Error(`fixture not found: ${name}`);
  dashboardStore.getState().applySnapshot(projection);
}

function seedGateRoom() {
  const fixture = GALLERY.find((entry) => entry.name === "engine-cleanup-pending");
  if (!fixture) throw new Error("fixture not found: engine-cleanup-pending");
  const lifecycle: LifecycleProjection = {
    id: "LC-GATE",
    state: "blocked",
    phase: "close",
    fleeting: false,
    tokens: 0,
    startedAt: "2026-06-23T10:00:00+00:00",
    lastEventTs: "2026-06-23T10:01:00+00:00",
    inferred: false,
    actions: [],
    tokenSeries: [],
    gate: {
      id: "G1",
      kind: "cleanup-approval",
      state: "open",
      decisions: [],
      packet: { question: "cleanup?" },
      ts: "2026-06-23T10:01:00+00:00",
    },
  };
  const process = {
    ...fixture.projection.analytics.engineProcesses[0],
    lifecycleId: lifecycle.id,
  };
  const projection: WorkspaceProjection = {
    ...fixture.projection,
    lifecycles: [lifecycle],
    metrics: { ...fixture.projection.metrics, lifecycleCount: 1, blockedCount: 1 },
    analytics: {
      ...fixture.projection.analytics,
      engineProcesses: [process],
    },
  };
  dashboardStore.getState().applySnapshot(projection);
}

function workspaceProvider(over: Partial<ProviderNode> & Pick<ProviderNode, "id" | "role" | "repoId">): ProviderNode {
  return {
    state: "ready",
    ok: true,
    watcherUp: true,
    indexingState: "indexed",
    scope: "workspace",
    ...over,
  };
}

function cgc(repoId: string, over: Partial<ProviderNode> = {}): ProviderNode {
  return workspaceProvider({
    id: `cgc-${repoId}`,
    role: "code",
    repoId,
    ...over,
  });
}

function grepai(repoId: string, over: Partial<ProviderNode> = {}): ProviderNode {
  return workspaceProvider({
    id: `grepai-${repoId}`,
    role: "memory",
    repoId,
    ...over,
  });
}

function seedOfficialProviders(providers: ProviderNode[]) {
  const fixture = GALLERY.find((entry) => entry.name === "engine-bootstrap");
  if (!fixture) throw new Error("fixture not found: engine-bootstrap");
  dashboardStore.getState().applySnapshot({ ...fixture.projection, providers });
}

function seedParallelLeafRoom() {
  const fixture = GALLERY.find((entry) => entry.name === "engine-bootstrap");
  if (!fixture) throw new Error("fixture not found: engine-bootstrap");
  const base = fixture.projection.analytics.engineProcesses[0];
  if (!base) throw new Error("engine-bootstrap fixture missing process");
  dashboardStore.getState().applySnapshot({
    ...fixture.projection,
    analytics: {
      ...fixture.projection.analytics,
      engineProcesses: [
        {
          ...base,
          id: "/tasks/260610_browser-dashboard/enclosures/15_parallel-leaf-enclosure-workflow/series-contract.md",
          enclosure: "/tasks/260610_browser-dashboard/enclosures/15_parallel-leaf-enclosure-workflow/series-contract.md",
          worktreeGroup: "/worktrees/260610-browser-dashboard-s15-ar",
          taskName: "260610_browser-dashboard",
          leafId: "15_parallel-leaf-enclosure-workflow",
          phase: "cleanup-pending",
          closeoutStatus: "completed",
          integrationStatus: "completed",
        },
        {
          ...base,
          id: "/tasks/260610_browser-dashboard/enclosures/16_engine-room-stack-entry-height/series-contract.md",
          enclosure: "/tasks/260610_browser-dashboard/enclosures/16_engine-room-stack-entry-height/series-contract.md",
          worktreeGroup: "/worktrees/260610-browser-dashboard-s16-ar",
          taskName: "260610_browser-dashboard",
          leafId: "16_engine-room-stack-entry-height",
          phase: "worktree-started",
        },
      ],
    },
  });
}

// Freeze motion so the phase-activity flag (not the pulse) is what's asserted.
beforeEach(() => {
  document.documentElement.dataset.effects = "off";
});
afterEach(() => {
  document.documentElement.removeAttribute("data-effects");
  cleanup();
});

describe("EngineRoom lifecycle phase motion (5f S5, T12–T18)", () => {
  it("marks the header phase-active for a human-gated lifecycle phase (cleanup-pending)", () => {
    seed("engine-cleanup-pending");
    const { getByTestId } = render(<EngineRoom />);
    expect(getByTestId("engine-room-header").getAttribute("data-phase-active")).toBe("true");
  });

  it("does not mark phase-active for a freshly started worktree", () => {
    seed("engine-bootstrap");
    const { getByTestId } = render(<EngineRoom />);
    expect(getByTestId("engine-room-header").getAttribute("data-phase-active")).toBe("false");
  });

  it("renders the projected gate responder in diagnostics and passes the gate kind to the canvas", () => {
    seedGateRoom();
    const { getByTestId } = render(<EngineRoom />);
    expect(getByTestId("engine-gate-responder").textContent).toContain("Respond");
    expect(getByTestId("enclosure-canvas").getAttribute("data-gate-kind")).toBe("cleanup-approval");
  });

  it("aggregates same-state official CGC engines into one strip chip", () => {
    seedOfficialProviders([
      ...Array.from({ length: 7 }, (_, i) => cgc(`repo-${i + 1}`)),
      grepai("agents-remember-memory"),
    ]);

    const { getAllByTestId, getByTestId } = render(<EngineRoom />);
    const strip = getByTestId("official-strip");
    const groups = getAllByTestId("official-engine-group");
    const cgcGroup = groups.find((group) => group.textContent === "7 CGC · nominal");

    expect(strip.textContent?.match(/CGC · nominal/g)?.length).toBe(1);
    expect(cgcGroup?.getAttribute("title")).toContain("repo-1");
    expect(cgcGroup?.getAttribute("title")).toContain("repo-7");
    expect(strip.textContent).toContain("GrepAI · nominal");
  });

  it("keeps official CGC aggregate chips separated by runtime state", () => {
    seedOfficialProviders([
      cgc("alpha"),
      cgc("beta"),
      cgc("gamma", { indexingState: "indexing" }),
      cgc("delta", { state: "stopped", ok: false, indexingState: "unknown" }),
      grepai("agents-remember-memory"),
    ]);

    const { getAllByTestId } = render(<EngineRoom />);
    const groups = getAllByTestId("official-engine-group");
    const labels = groups.map((group) => group.textContent);
    const nominal = groups.find((group) => group.textContent === "2 CGC · nominal");
    const indexing = groups.find((group) => group.textContent === "CGC · indexing");
    const down = groups.find((group) => group.textContent === "CGC · down");

    expect(labels).toContain("2 CGC · nominal");
    expect(labels).toContain("CGC · indexing");
    expect(labels).toContain("CGC · down");
    expect(labels).toContain("GrepAI · nominal");
    expect(nominal?.getAttribute("title")).toContain("alpha");
    expect(nominal?.getAttribute("title")).toContain("beta");
    expect(indexing?.getAttribute("title")).toContain("gamma");
    expect(down?.getAttribute("title")).toContain("delta");
  });

  it("renders leaf enclosure identity ahead of the parent series task name", () => {
    seedParallelLeafRoom();

    const { getAllByTestId, getByTestId } = render(<EngineRoom />);
    const rows = getAllByTestId("enclosure-stack-item");

    expect(rows[0]?.textContent).toContain("16_engine-room-stack-entry-height");
    expect(rows[0]?.textContent).toContain("260610_browser-dashboard");
    expect(rows[0]?.textContent).toContain("worktree-started");
    expect(rows[1]?.textContent).toContain("15_parallel-leaf-enclosure-workflow");
    expect(rows[1]?.textContent).toContain("cleanup-pending");
    expect(getByTestId("engine-room-header").textContent).toContain(
      "16_engine-room-stack-entry-height",
    );
  });
});

describe("EngineRoom — memoized model + narrowed analytics subscription (260721 C3)", () => {
  beforeEach(() => {
    buildSpy.mockClear();
  });

  it("does not rebuild the model on a re-render with unchanged slices (identical inputs → no rebuild)", () => {
    seed("engine-bootstrap");
    const view = render(<EngineRoom />);
    const calls = buildSpy.mock.calls.length;
    expect(calls).toBeGreaterThan(0);
    view.rerender(<EngineRoom />);
    expect(buildSpy.mock.calls.length).toBe(calls);
  });

  it("ignores an analytics delta that leaves engineProcesses + ledgers untouched", () => {
    seed("engine-bootstrap");
    render(<EngineRoom />);
    const calls = buildSpy.mock.calls.length;
    const analytics = dashboardStore.getState().analytics;
    if (!analytics) throw new Error("analytics missing");
    // A wholesale analytics replacement (the wire protocol) touching only the attention queue: the
    // stableEquals slice selectors keep the previous identities → no re-render, no model rebuild.
    act(() =>
      dashboardStore.getState().applyDelta("analytics", {
        ...analytics,
        attentionQueue: [{ id: "att-1", severity: "warn" }],
      }),
    );
    expect(buildSpy.mock.calls.length).toBe(calls);
  });

  it("rebuilds when an engineProcesses field actually changes (the memo invalidates honestly)", () => {
    seed("engine-bootstrap");
    render(<EngineRoom />);
    const calls = buildSpy.mock.calls.length;
    const analytics = dashboardStore.getState().analytics;
    if (!analytics) throw new Error("analytics missing");
    act(() =>
      dashboardStore.getState().applyDelta("analytics", {
        ...analytics,
        engineProcesses: analytics.engineProcesses.map((process, index) =>
          index === 0 ? { ...process, summary: "changed summary" } : process,
        ),
      }),
    );
    expect(buildSpy.mock.calls.length).toBeGreaterThan(calls);
  });
});

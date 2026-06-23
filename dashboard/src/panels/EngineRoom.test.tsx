import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { dashboardStore } from "../data/store";
import { GALLERY } from "../dev/fixtures";
import type { LifecycleProjection, WorkspaceProjection } from "../types/projection";
import { EngineRoom } from "./EngineRoom";

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
});

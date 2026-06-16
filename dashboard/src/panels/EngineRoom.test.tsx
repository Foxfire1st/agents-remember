import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { dashboardStore } from "../data/store";
import { GALLERY } from "../dev/fixtures";
import { EngineRoom } from "./EngineRoom";

function seed(name: string) {
  const projection = GALLERY.find((entry) => entry.name === name)?.projection;
  if (!projection) throw new Error(`fixture not found: ${name}`);
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
});

import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CockpitShell } from "../../cockpit/Cockpit";
import { dashboardStore } from "../../data/store";
import { GALLERY } from "../../dev/fixtures";
import { FileViewer } from "./FileViewer";

beforeEach(() => {
  // The viewer fetches the repo catalog on mount; stub it so jsdom never hits the network.
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () => ({ ok: true, status: 200, json: async () => ({ repos: [] }) }) as unknown as Response,
    ),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("File Viewer center tab", () => {
  it("registers a full-bleed File Viewer view selectable from the mode bar", () => {
    dashboardStore.getState().applySnapshot(GALLERY.find((g) => g.name === "engine-fleet")!.projection);
    const { container, getByRole } = render(<CockpitShell />);

    fireEvent.click(getByRole("radio", { name: "File Viewer" }));

    expect(container.querySelector('[data-testid="file-viewer"]')).not.toBeNull();
    // The multi-column viewer drops the rails (full-bleed) like Engine Room / Topology.
    expect(container.querySelector(".shell__body")?.getAttribute("data-fullbleed")).toBe("true");
    expect(container.querySelector(".rail--left")).toBeNull();
  });

  it("shows the empty-state backdrop prompt before any file is selected", () => {
    const { container } = render(<FileViewer />);
    expect(container.querySelector('[data-testid="file-viewer"]')).not.toBeNull();
    // The siege-tank empty-state backdrop fills the pane (no per-side placeholders) until a file opens.
    expect(container.textContent).toContain("Select a code file");
  });

  it("keeps the File Viewer mounted (hidden) on other views so its state survives a switch", () => {
    dashboardStore.getState().applySnapshot(GALLERY.find((g) => g.name === "engine-fleet")!.projection);
    const { container, getByRole } = render(<CockpitShell />);

    // Mounted from the start (default Operations view), but hidden via CSS.
    const fv = container.querySelector('[data-testid="file-viewer"]');
    expect(fv).not.toBeNull();
    expect((fv!.parentElement as HTMLElement).style.display).toBe("none");

    // Switching to File Viewer reveals the SAME element (never remounted → state preserved).
    fireEvent.click(getByRole("radio", { name: "File Viewer" }));
    expect(container.querySelector('[data-testid="file-viewer"]')).toBe(fv);
    expect((fv!.parentElement as HTMLElement).style.display).toBe("flex");

    // Leaving hides it again without unmounting (still the same node).
    fireEvent.click(getByRole("radio", { name: "Operations" }));
    expect(container.querySelector('[data-testid="file-viewer"]')).toBe(fv);
    expect((fv!.parentElement as HTMLElement).style.display).toBe("none");
  });
});

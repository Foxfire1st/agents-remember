import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
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
    // The multi-column viewer hides the rails (full-bleed) like Engine Room / Topology — the
    // asides stay mounted (keep-alive), hidden via display.
    expect(container.querySelector(".shell__body")?.getAttribute("data-fullbleed")).toBe("true");
    expect((container.querySelector(".rail--left") as HTMLElement).style.display).toBe("none");
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

  it("defers the boot catalog read until first shown, and never re-reads on hide/show", async () => {
    const fn = vi.fn(
      async () => ({ ok: true, status: 200, json: async () => ({ repos: [] }) }) as unknown as Response,
    );
    vi.stubGlobal("fetch", fn);

    // Hidden: mounted but inactive — no catalog read.
    const { rerender } = render(<FileViewer active={false} />);
    expect(fn).not.toHaveBeenCalled();

    // First showing fires exactly one read.
    rerender(<FileViewer active={true} />);
    await waitFor(() => expect(fn).toHaveBeenCalledTimes(1));
    expect((fn.mock.calls as unknown as string[][])[0][0]).toBe("/api/files/repos");

    // A hide/show cycle keeps the settled catalog — the once-per-lifetime boot posture.
    rerender(<FileViewer active={false} />);
    rerender(<FileViewer active={true} />);
    await act(async () => {});
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("reads the repo catalog only once the File Viewer view is selected (Cockpit wiring)", async () => {
    dashboardStore.getState().applySnapshot(GALLERY.find((g) => g.name === "engine-fleet")!.projection);
    const fn = vi.fn(
      async () => ({ ok: true, status: 200, json: async () => ({ repos: [] }) }) as unknown as Response,
    );
    vi.stubGlobal("fetch", fn);
    const { getByRole } = render(<CockpitShell />);

    const filesReads = () =>
      (fn.mock.calls as unknown as string[][]).map((c) => c[0]).filter((u) => u.includes("/api/files/"));
    // Default Operations view: the hidden viewer must not touch the files API at boot.
    await act(async () => {});
    expect(filesReads()).toEqual([]);

    fireEvent.click(getByRole("radio", { name: "File Viewer" }));
    await waitFor(() => expect(filesReads()).toContain("/api/files/repos"));
  });
});

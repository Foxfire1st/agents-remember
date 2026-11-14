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
    const { container, getByRole } = render(<CockpitShell initialView="operations" />);

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
    const { container, getByRole } = render(<CockpitShell initialView="operations" />);

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
    const { getByRole } = render(<CockpitShell initialView="operations" />);

    const filesReads = () =>
      (fn.mock.calls as unknown as string[][]).map((c) => c[0]).filter((u) => u.includes("/api/files/"));
    // Default Operations view: the hidden viewer must not touch the files API at boot.
    await act(async () => {});
    expect(filesReads()).toEqual([]);

    fireEvent.click(getByRole("radio", { name: "File Viewer" }));
    await waitFor(() => expect(filesReads()).toContain("/api/files/repos"));
  });

  it("opens a document rendered, and Back and Forward lead through opened files", async () => {
    const documents: Record<string, string> = {
      "a.md": "# Alpha\n\n[beta](b.md#beta)\n\nfirst document\n",
      "b.md": "# Beta\n\nsecond document\n",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (location: string) => {
        const url = new URL(location, "http://localhost");
        const path = url.searchParams.get("path") ?? "";
        const reply = (body: unknown) =>
          ({ ok: true, status: 200, json: async () => body }) as unknown as Response;
        if (url.pathname === "/api/files/repos")
          return reply({
            repos: [
              {
                repo: "R",
                mainline: { scope: "mainline", label: "R", exists: true },
                worktrees: [],
              },
            ],
          });
        if (url.pathname === "/api/files/list")
          return reply({
            scope: "mainline",
            dir: "",
            code: ["a.md", "b.md"].map((name) => ({
              name,
              path: name,
              kind: "file",
              language: "markdown",
            })),
            onboarding: [],
          });
        if (url.pathname === "/api/files/read")
          return reply({
            scope: "mainline",
            path,
            language: "markdown",
            size: (documents[path] ?? "").length,
            truncated: false,
            content: documents[path] ?? "",
            onboarding: { status: "missing" },
          });
        if (url.pathname === "/api/files/onboarding")
          return reply({ status: "missing", codePath: path, body: null });
        if (url.pathname === "/api/files/resolve")
          return reply({ exists: path === "b.md", path, kind: "file", language: "markdown" });
        return reply({ exists: false, path });
      }),
    );

    const view = render(<FileViewer active={true} />);
    fireEvent.click(await view.findByText("a.md"));
    await waitFor(() => expect(view.getByRole("heading", { name: "Alpha" })).not.toBeNull());
    expect(view.getByTestId("file-viewer-rendered").getAttribute("aria-pressed")).toBe("true");

    // The document's link opens b.md at its heading, then the reader scrolls away from it.
    fireEvent.click(await view.findByTestId("document-file-link"));
    await waitFor(() => expect(view.getByRole("heading", { name: "Beta" })).not.toBeNull());
    const scroller = view.getByTestId("document-rendered");
    scroller.scrollTop = 600;

    fireEvent.click(view.getByTestId("file-viewer-back"));
    await waitFor(() => expect(view.getByRole("heading", { name: "Alpha" })).not.toBeNull());
    await waitFor(() => expect(view.getByTestId("document-rendered").scrollTop).toBe(0));
    expect(view.getByTestId("file-viewer-forward")).not.toBeNull();
    fireEvent.click(view.getByTestId("file-viewer-forward"));
    await waitFor(() => expect(view.getByRole("heading", { name: "Beta" })).not.toBeNull());
    // Back/Forward restore the place the reader left, not the link's original heading.
    await waitFor(() => expect(view.getByTestId("document-rendered").scrollTop).toBe(600));

    // A reader who returns to the linked document and saves zero must get zero back, not the
    // heading the link first named.
    view.getByTestId("document-rendered").scrollTop = 0;
    fireEvent.click(view.getByTestId("file-viewer-back"));
    await waitFor(() => expect(view.getByTestId("document-rendered").scrollTop).toBe(0));
    fireEvent.click(view.getByTestId("file-viewer-forward"));
    await waitFor(() => expect(view.getByRole("heading", { name: "Beta" })).not.toBeNull());
    await waitFor(() => expect(view.getByTestId("document-rendered").scrollTop).toBe(0));
  });
});

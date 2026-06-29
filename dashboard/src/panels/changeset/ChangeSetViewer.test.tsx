import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CockpitShell } from "../../cockpit/Cockpit";
import { dashboardStore } from "../../data/store";
import { GALLERY } from "../../dev/fixtures";
import { DetailPanel } from "../DetailPanel";
import { ChangeSetViewer } from "./ChangeSetViewer";

const TASK_CHANGESET = {
  scope: "wt-a",
  code: [{ path: "dashboard/src/x.ts", insertions: 3, deletions: 1, status: "M", hasSidecar: true }],
  memory: [{ path: "onboarding/dashboard/src/x.ts.md", insertions: 2, deletions: 0, status: "A" }],
  counters: { code: { files: 1, insertions: 3, deletions: 1 }, memory: { files: 1, insertions: 2, deletions: 0 } },
};
const MASTER_CHANGESET = {
  master: "browser-dashboard",
  leaves: [{ leafId: "260628-l1", counters: { code: { files: 1, insertions: 3, deletions: 1 }, memory: { files: 0, insertions: 0, deletions: 0 } } }],
  code: [{ path: "a.ts", insertions: 3, deletions: 1, status: "M", leafCount: 1 }],
  memory: [],
  counters: { code: { files: 1, insertions: 3, deletions: 1 }, memory: { files: 0, insertions: 0, deletions: 0 } },
};

// A URL-aware fetch stub: the change-set endpoints return our fixtures; everything else is empty.
function stubChangeset() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const body = url.includes("/api/changeset/master")
        ? MASTER_CHANGESET
        : url.includes("/api/changeset/task")
          ? TASK_CHANGESET
          : {};
      return { ok: true, status: 200, json: async () => body } as unknown as Response;
    }),
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ChangeSetViewer screen", () => {
  it("renders the changed-file rows + counters for a task scope", async () => {
    stubChangeset();
    const { container, findByTestId, getByText } = render(
      <ChangeSetViewer repo="agents-remember" scope="wt-a" onBack={vi.fn()} />,
    );
    await findByTestId("changeset-counters");
    expect(container.querySelector('[data-testid="changeset-viewer"]')).not.toBeNull();
    // col1: the changed code + onboarding rows from the stubbed task change-set.
    expect(getByText("dashboard/src/x.ts")).not.toBeNull();
    expect(getByText("onboarding/dashboard/src/x.ts.md")).not.toBeNull();
    // counters summarise both sides; col2 shows the stable "select a file" placeholder.
    expect(container.querySelector('[data-testid="changeset-counters"]')?.textContent).toContain("+3");
    expect(container.querySelector('[data-testid="pane-placeholder"]')?.textContent).toContain(
      "Select a changed file",
    );
  });

  it("calls onBack when the back link is clicked", async () => {
    stubChangeset();
    const onBack = vi.fn();
    const { findByTestId } = render(
      <ChangeSetViewer repo="agents-remember" scope="wt-a" onBack={onBack} />,
    );
    fireEvent.click(await findByTestId("changeset-back"));
    expect(onBack).toHaveBeenCalledTimes(1);
  });

  it("shows the accumulated-summary placeholder in master mode (no single scope to diff)", async () => {
    stubChangeset();
    const { findByTestId, container } = render(
      <ChangeSetViewer repo="agents-remember" master="browser-dashboard" onBack={vi.fn()} />,
    );
    await findByTestId("changeset-counters");
    expect(container.querySelector('[data-testid="pane-placeholder"]')?.textContent).toContain(
      "Accumulated series summary",
    );
  });
});

describe("DetailPanel change-set entry (L4)", () => {
  it("renders a series change-set button for an enclosure-backed lifecycle and opens the target", async () => {
    stubChangeset();
    dashboardStore.getState().applySnapshot(GALLERY.find((g) => g.name === "full")!.projection);
    const onOpenChangeSet = vi.fn();
    const { findByTestId } = render(
      <DetailPanel selectedId="lifecycle:build-001" onOpenChangeSet={onOpenChangeSet} />,
    );
    const button = await findByTestId("open-changeset");
    fireEvent.click(button);
    // "full" has no activeWorktreeGroups, so only the series (master) button shows -> wt-a's taskName.
    expect(onOpenChangeSet).toHaveBeenCalledWith({ repo: "agents-remember", master: "browser-dashboard" });
  });
});

describe("Cockpit change-set takeover wiring", () => {
  it("does not show the takeover initially and keeps the Operations rails", () => {
    stubChangeset();
    dashboardStore.getState().applySnapshot(GALLERY.find((g) => g.name === "full")!.projection);
    const { container } = render(<CockpitShell />);
    expect(container.querySelector('[data-testid="changeset-viewer"]')).toBeNull();
    expect(container.querySelector(".rail--left")).not.toBeNull();
    expect(container.querySelector(".shell__body")?.getAttribute("data-fullbleed")).toBe("false");
  });
});

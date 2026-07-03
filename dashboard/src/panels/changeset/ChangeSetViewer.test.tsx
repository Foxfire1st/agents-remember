import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CockpitShell } from "../../cockpit/Cockpit";
import { dashboardStore } from "../../data/store";
import { GALLERY } from "../../dev/fixtures";
import { DetailPanel } from "../DetailPanel";
import { ChangeSetViewer } from "./ChangeSetViewer";

// Mock the diff column so the screen tests never construct a CodeMirror MergeView in jsdom
// (matching the L2 approach — the live editor render is covered by build + typecheck).
vi.mock("./ChangeSetPane", () => ({
  ChangeSetPane: ({ diff }: { diff: { path: string } }) => (
    <div data-testid="changeset-pane">{diff.path}</div>
  ),
}));

const TASK_CHANGESET = {
  scope: "wt-a",
  code: [{ path: "dashboard/src/x.ts", insertions: 3, deletions: 1, status: "M", hasSidecar: true }],
  memory: [{ path: "onboarding/dashboard/src/x.ts.md", insertions: 2, deletions: 0, status: "A" }],
  counters: { code: { files: 1, insertions: 3, deletions: 1 }, memory: { files: 1, insertions: 2, deletions: 0 } },
};
const MASTER_CHANGESET = {
  master: "browser-dashboard",
  leaves: [{ leafId: "260628-l1", counters: { code: { files: 1, insertions: 3, deletions: 1 }, memory: { files: 0, insertions: 0, deletions: 0 } } }],
  // Net diff (master base -> series tip): plain ChangedFile rows, no leafCount.
  code: [{ path: "a.ts", insertions: 3, deletions: 1, status: "M" }],
  memory: [],
  counters: { code: { files: 1, insertions: 3, deletions: 1 }, memory: { files: 0, insertions: 0, deletions: 0 } },
};
const FILE_DIFF = {
  scope: "browser-dashboard",
  kind: "code",
  path: "a.ts",
  language: "typescript",
  before: { content: "old\n" },
  after: { content: "old\nnew\n" },
};

// A URL-aware fetch stub: the change-set endpoints return our fixtures; everything else is empty.
function stubChangeset() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const body = url.includes("/api/changeset/file-diff")
        ? FILE_DIFF
        : url.includes("/api/changeset/master")
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
    // counters summarise both sides; col2 shows the empty-state backdrop prompt until a file is picked.
    expect(container.querySelector('[data-testid="changeset-counters"]')?.textContent).toContain("+3");
    expect(container.textContent).toContain("Select a changed file");
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

  it("loads a leaf committed change-set via the task route, labels it, and is per-file inspectable", async () => {
    stubChangeset(); // /api/changeset/task (which the leaf view rides) returns TASK_CHANGESET
    const { findByTestId, getByText, container } = render(
      <ChangeSetViewer
        repo="agents-remember"
        master="260628_operations-integration"
        leaf="260628-l4a"
        mode="committed"
        onBack={vi.fn()}
      />,
    );
    await findByTestId("changeset-counters");
    // the header distinguishes the committed leaf view from a series / enclosure scope
    expect(container.querySelector('[data-testid="changeset-viewer"]')?.textContent).toContain(
      "committed · 260628-l4a",
    );
    // unlike the old master summary, leaf rows ARE clickable into a per-file diff (leaf+mode
    // file-diff). The stubbed file-diff fixture resolves to "a.ts", proving the click opened a pane.
    fireEvent.click(getByText("dashboard/src/x.ts"));
    expect((await findByTestId("changeset-pane")).textContent).toBe("a.ts");
  });

  it("labels the working leaf view as uncommitted", async () => {
    stubChangeset();
    const { findByTestId, container } = render(
      <ChangeSetViewer
        repo="agents-remember"
        master="260628_operations-integration"
        leaf="260628-l4a"
        mode="working"
        onBack={vi.fn()}
      />,
    );
    await findByTestId("changeset-counters");
    expect(container.querySelector('[data-testid="changeset-viewer"]')?.textContent).toContain(
      "working · 260628-l4a · uncommitted",
    );
  });

  it("auto-polls the working view — the list AND the open file — but never committed/series", async () => {
    vi.useFakeTimers();
    const fetchFn = vi.fn(
      async () => ({ ok: true, status: 200, json: async () => TASK_CHANGESET }) as unknown as Response,
    );
    vi.stubGlobal("fetch", fetchFn);
    const countOf = (frag: string) =>
      (fetchFn.mock.calls as unknown as string[][]).filter((c) => String(c[0]).includes(frag)).length;

    // working: initial load, open a file, then let one interval tick fire.
    const working = render(
      <ChangeSetViewer repo="r" master="m" leaf="l" mode="working" onBack={vi.fn()} />,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0); // the initial change-set load resolves + renders the list
    });
    fireEvent.click(working.getByText("dashboard/src/x.ts")); // open a code file's diff
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2600);
    });
    expect(countOf("/api/changeset/task")).toBeGreaterThanOrEqual(2); // list: load + poll
    expect(countOf("/api/changeset/file-diff")).toBeGreaterThanOrEqual(2); // open diff: click + poll
    working.unmount();

    // committed: same interactions, but neither the list nor the open diff polls.
    fetchFn.mockClear();
    const committed = render(
      <ChangeSetViewer repo="r" master="m" leaf="l" mode="committed" onBack={vi.fn()} />,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    fireEvent.click(committed.getByText("dashboard/src/x.ts"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2600);
    });
    expect(countOf("/api/changeset/task")).toBe(1); // load only
    expect(countOf("/api/changeset/file-diff")).toBe(1); // click only, no poll
    vi.useRealTimers();
  });

  it("opens a per-file NET diff from a clickable row in master mode", async () => {
    stubChangeset();
    const { findByTestId, getByText, container } = render(
      <ChangeSetViewer repo="agents-remember" master="browser-dashboard" onBack={vi.fn()} />,
    );
    await findByTestId("changeset-counters");
    // master mode now lists the net changed files with the normal empty-state backdrop prompt
    // (not the old accumulated-summary message), and the rows are clickable.
    expect(container.textContent).toContain("Select a changed file");
    fireEvent.click(getByText("a.ts"));
    expect((await findByTestId("changeset-pane")).textContent).toBe("a.ts");
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

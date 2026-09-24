import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CockpitShell } from "../../cockpit/Cockpit";
import { dashboardStore } from "../../data/store";
import { GALLERY } from "../../dev/fixtures";
import { DetailPanel } from "../detail-panel/DetailPanel";
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
  vi.useRealTimers();
});

describe("ChangeSetViewer screen", () => {
  it("shows loading until the request resolves instead of rendering a zero-file result", async () => {
    let resolveFetch!: (response: Response) => void;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            resolveFetch = resolve;
          }),
      ),
    );
    const { getByTestId, findByTestId } = render(
      <ChangeSetViewer repo="agents-remember" scope="wt-a" onBack={vi.fn()} />,
    );
    expect(getByTestId("pane-placeholder").textContent).toContain("Loading change-set");
    resolveFetch({
      ok: true,
      status: 200,
      json: async () => TASK_CHANGESET,
    } as unknown as Response);
    await findByTestId("changeset-counters");
  });

  it("retains the explicit request error state", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          ({
            ok: false,
            status: 404,
            statusText: "Not Found",
            json: async () => ({ status: "not-found" }),
          }) as unknown as Response,
      ),
    );
    const { findByText } = render(
      <ChangeSetViewer repo="agents-remember" scope="gone" onBack={vi.fn()} />,
    );
    expect(await findByText("not-found (404)")).not.toBeNull();
  });

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
  });

  it("does not start another working refresh until the list and open-file requests settle", async () => {
    vi.useFakeTimers();
    let defer = false;
    const pending: { url: string; resolve: (response: Response) => void }[] = [];
    const responseFor = (url: string) =>
      ({
        ok: true,
        status: 200,
        json: async () =>
          url.includes("/api/changeset/file-diff") ? FILE_DIFF : TASK_CHANGESET,
      }) as unknown as Response;
    const fetchFn = vi.fn((url: string) => {
      if (!defer) return Promise.resolve(responseFor(url));
      return new Promise<Response>((resolve) => pending.push({ url, resolve }));
    });
    vi.stubGlobal("fetch", fetchFn);

    const working = render(
      <ChangeSetViewer repo="r" master="m" leaf="l" mode="working" onBack={vi.fn()} />,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    fireEvent.click(working.getByText("dashboard/src/x.ts"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(fetchFn).toHaveBeenCalledTimes(2);

    defer = true;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });
    expect(fetchFn).toHaveBeenCalledTimes(4);
    expect(pending).toHaveLength(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(fetchFn).toHaveBeenCalledTimes(4);

    defer = false;
    await act(async () => {
      for (const request of pending.splice(0)) request.resolve(responseFor(request.url));
      await Promise.resolve();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });
    expect(fetchFn).toHaveBeenCalledTimes(6);
  });

  it("opens a per-file NET diff from a clickable row in master mode", async () => {
    stubChangeset();
    const { findByTestId, getByText, container } = render(
      <ChangeSetViewer repo="agents-remember" master="browser-dashboard" onBack={vi.fn()} />,
    );
    await findByTestId("changeset-counters");
    // The master read carries the net's per-leaf attribution (ICR-R33.2): the breakdown the route
    // answers with is asked for, so the total above the file lists is attributable leaf by leaf.
    expect(
      (vi.mocked(fetch).mock.calls as unknown as string[][]).some((call) =>
        String(call[0]).includes("includeLeaves=true"),
      ),
    ).toBe(true);
    // master mode now lists the net changed files with the normal empty-state backdrop prompt
    // (not the old accumulated-summary message), and the rows are clickable.
    expect(container.textContent).toContain("Select a changed file");
    fireEvent.click(getByText("a.ts"));
    expect((await findByTestId("changeset-pane")).textContent).toBe("a.ts");
  });

  it("binds master file expansions to the generation the net listing published", async () => {
    // The listing names its exact endpoints; the viewer shows the bound generation and carries
    // its pins into each file expansion, so an opened entry stays bound after the branch
    // advances instead of re-resolving the live tip.
    const generation = {
      codeBase: "b0",
      codeTip: "t2",
      memoryBase: "",
      memoryTip: "",
      digest: "d".repeat(64),
    };
    const fetchFn = vi.fn(async (url: string) => {
      const body = url.includes("/api/changeset/file-diff")
        ? FILE_DIFF
        : { ...MASTER_CHANGESET, generation, currentness: "current", scope: "integrated" };
      return { ok: true, status: 200, json: async () => body } as unknown as Response;
    });
    vi.stubGlobal("fetch", fetchFn);
    const { findByTestId, getByText, getByTestId } = render(
      <ChangeSetViewer repo="agents-remember" master="browser-dashboard" onBack={vi.fn()} />,
    );
    await findByTestId("changeset-counters");
    expect(getByTestId("changeset-generation").textContent).toContain("gen dddddddd");
    expect(getByTestId("changeset-generation").textContent).toContain("current");
    fireEvent.click(getByText("a.ts"));
    expect((await findByTestId("changeset-pane")).textContent).toBe("a.ts");
    const diffUrls = (fetchFn.mock.calls as unknown as string[][])
      .map((c) => String(c[0]))
      .filter((u) => u.includes("/api/changeset/file-diff"));
    expect(diffUrls).toHaveLength(1);
    expect(diffUrls[0]).toContain("codeBase=b0");
    expect(diffUrls[0]).toContain("codeTip=t2");
  });

  // ── ICR-R33: the net's leaves, the range each one opens, and what a read that cannot answer says ──
  // The delivered defect: the master view asked for `includeLeaves: false` and threw the per-leaf
  // breakdown away, so a reviewer saw one net total with no leaf attribution — and a landed leaf's
  // own range was reachable only from its own document reader, if the reviewer could find it at all.
  const MASTER_WITH_LEAVES = {
    master: "260921_complete-code-and-intent-review",
    leaves: [
      {
        leafId: "260921-ICR-L1",
        state: "committed" as const,
        counters: {
          code: { files: 8, insertions: 1258, deletions: 235 },
          memory: { files: 20, insertions: 1326, deletions: 406 },
        },
      },
      {
        leafId: "260921-ICR-L25",
        state: "working" as const,
        counters: {
          code: { files: 2, insertions: 10, deletions: 3 },
          memory: { files: 1, insertions: 4, deletions: 0 },
        },
      },
    ],
    code: [{ path: "dashboard/src/x.ts", insertions: 3, deletions: 1, status: "M" }],
    memory: [{ path: "onboarding/dashboard/src/x.ts.md", insertions: 2, deletions: 0, status: "A" }],
    counters: { code: { files: 1, insertions: 3, deletions: 1 }, memory: { files: 1, insertions: 2, deletions: 0 } },
    generation: { codeBase: "b0", codeTip: "t2", memoryBase: "", memoryTip: "", digest: "d".repeat(64) },
    currentness: "current" as const,
    scope: "integrated" as const,
  };

  function stubMaster(body: unknown, status = 200) {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          ({
            ok: status < 400,
            status,
            statusText: status === 200 ? "OK" : "Not Found",
            json: async () => body,
          }) as unknown as Response,
      ),
    );
  }

  it("lists the master net's leaves, each with its own code and memory counters (R33.2)", async () => {
    stubMaster(MASTER_WITH_LEAVES);
    const { findAllByTestId, findByTestId, getByText } = render(
      <ChangeSetViewer
        repo="agents-remember"
        master="260921_complete-code-and-intent-review"
        onBack={vi.fn()}
      />,
    );
    await findByTestId("changeset-counters");

    // The net total is printed beside the breakdown it is the sum of.
    expect(getByText("by leaf (2)")).toBeTruthy();
    const rows = await findAllByTestId("changeset-leaf-row");
    expect(rows).toHaveLength(2);
    // Each row carries the leaf's OWN half-by-half measurement: files, insertions and deletions for
    // code and for memory — the attribution a reviewer rates the leaf on.
    const counters = await findAllByTestId("changeset-leaf-counters");
    expect(counters[0].textContent).toBe("code 8 file(s) +1258 −235 · memory 20 file(s) +1326 −406");
    expect(counters[1].textContent).toBe("code 2 file(s) +10 −3 · memory 1 file(s) +4 −0");
    expect(rows[0].querySelector("[data-leaf-id]")?.getAttribute("data-leaf-state")).toBe(
      "committed",
    );
  });

  it("opens a landed leaf's committed change-set — and a working leaf's working delta — from the net (R33.3)", async () => {
    stubMaster(MASTER_WITH_LEAVES);
    const onOpenLeaf = vi.fn();
    const { findAllByTestId, findByTestId } = render(
      <ChangeSetViewer
        repo="agents-remember"
        master="260921_complete-code-and-intent-review"
        onBack={vi.fn()}
        onOpenLeaf={onOpenLeaf}
      />,
    );
    await findByTestId("changeset-counters");
    const rows = await findAllByTestId("changeset-leaf-row");

    // A landed leaf (state "committed") opens the historical committed route — the one that needs no
    // live worktree.
    fireEvent.click(rows[0].querySelector("button") as HTMLElement);
    expect(onOpenLeaf).toHaveBeenCalledWith({
      repo: "agents-remember",
      master: "260921_complete-code-and-intent-review",
      leaf: "260921-ICR-L1",
      mode: "committed",
    });
    // A leaf the read reports as still working opens the range that exists for it.
    fireEvent.click(rows[1].querySelector("button") as HTMLElement);
    expect(onOpenLeaf).toHaveBeenLastCalledWith({
      repo: "agents-remember",
      master: "260921_complete-code-and-intent-review",
      leaf: "260921-ICR-L25",
      mode: "working",
    });
  });

  it("names the refusal when a landed leaf's committed range cannot be shown (R33.3)", async () => {
    // THE REAL ANSWER, captured from the running route:
    //   HTTP 404 {"status":"not-found","path":"no leaf contract for '260921-ICR-L99'"}
    // The changeset family publishes `bad-path` / `not-found` / `bad-request` only
    // (mcp/src/agents_remember/serving/changeset.py), so the token this client renders is
    // `not-found` — not the generic `?? "domain-refused"` fallback an invented code would produce,
    // which would let this case pass while pinning nothing the product actually does.
    stubMaster({ status: "not-found", path: "no leaf contract for '260921-ICR-L99'" }, 404);
    const { findByTestId, queryByText } = render(
      <ChangeSetViewer
        repo="agents-remember"
        master="260921_complete-code-and-intent-review"
        leaf="260921-ICR-L99"
        mode="committed"
        onBack={vi.fn()}
      />,
    );

    // Waited for BY NAME: the refusal element only exists once the read has answered, so this cannot
    // race the loading placeholder the way a wait on the shared testid would.
    const refusal = await findByTestId("changeset-refusal");
    expect(refusal.textContent).toBe("not-found (404)");
    const placeholder = refusal.closest("[data-testid='pane-placeholder']") as HTMLElement;
    // The reason the route published reaches the reader verbatim (it is the body's `path`, which this
    // family uses for the reason)…
    expect(placeholder.textContent).toContain("no leaf contract for '260921-ICR-L99'");
    // …under the token the route's own code maps to.
    expect(placeholder.getAttribute("data-review-state")).toBe("not-found");
    expect(placeholder.getAttribute("data-review-code")).toBe("not-found");
    // Not an empty pane: nothing claims the change-set was measured empty.
    expect(queryByText(/measured empty/)).toBeNull();
  });

  it("names a measured-empty change-set instead of leaving the pane to the pick-a-file backdrop (R33.3)", async () => {
    stubMaster({
      master: "260921_complete-code-and-intent-review",
      leaves: [],
      code: [],
      memory: [],
      counters: { code: { files: 0, insertions: 0, deletions: 0 }, memory: { files: 0, insertions: 0, deletions: 0 } },
      generation: { codeBase: "b0", codeTip: "b0", memoryBase: "m0", memoryTip: "m0", digest: "e".repeat(64) },
      currentness: "current",
      scope: "integrated",
    });
    const { findByTestId, queryByText } = render(
      <ChangeSetViewer repo="agents-remember" master="m" onBack={vi.fn()} />,
    );

    const empty = await findByTestId("changeset-empty");
    expect(empty.textContent).toContain("measured empty");
    expect(queryByText("Select a changed file")).toBeNull();
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

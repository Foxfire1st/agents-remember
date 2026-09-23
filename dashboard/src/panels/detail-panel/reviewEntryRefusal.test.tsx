// R16 (visible structured refusals) at the persistent, discoverable ENTRY: the task document's
// Intent-review bar shows what the entry read answered instead of dropping it.
//
// WHAT THIS EXERCISES. The real `DocChangeSetBar` over the real review client; only `fetch` is
// stubbed. The bar's entry read goes to `/api/review/intent/entries`, whose refusals the route
// publishes in the body of a non-2xx response -- the read that used to be thrown away, leaving the
// entry with nothing to say (VERIFICATION.md F08: "Entry catches this and hides itself").
//
// WHERE THE VALUES COME FROM. The refusal body is the measured output of the REAL route over REAL
// HTTP in this leaf's evidence run, recorded in
// `ar-coordination/temp/icr/evidence-l16-refusals.txt` as `body-normalized` (the per-run fixture uuid
// `repository_id` replaced by `<repository_id>`) with sha256-normalized
// fdbabc97219c6f0a7b531acbe1032622bbb21f3bbfc441f406f1ee34a2bf828e. The refusal's own code, reason,
// offending input and next action are verbatim; `repository_id`/`master`/`leaf_id` are the request
// echoes of this module's own task context, which is what the route echoes them from.
//
// THE DEFECT THESE CASES CATCH. The hook set its subject to `undefined` on any failure, so a refused
// read and an empty list were indistinguishable and neither was ever shown -- the reader saw an entry
// with no reason, or (before that) no entry at all. Every case below fails against that hook.

import { act, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { dashboardStore } from "../../data/store";
import { DocChangeSetBar } from "./changeSetBar";
import { enclosure, seedProjection } from "./test-utils";

const REPO = "agents-remember";
const MASTER = "260921_complete-code-and-intent-review";
const LEAF = "260921-ICR-L16";

// The real route's 404 for the entry read of a never-initialized task (measured body-normalized;
// see the module header for its digest).
const ENTRY_DETAIL =
  "the resolved baseline dataset is absent, so the pair has nothing to compare; the review reads " +
  "neither of its two halves out of the live coordination tree and substitutes no other dataset";
const ENTRY_NEXT =
  "author the candidate's knowledge in the leaf's disposable knowledge root, and place the dataset " +
  "it forks from in the baseline half if this leaf has one; the surface substitutes no other dataset";
const entryRefusal = {
  state: "refused",
  operation: "list_knowledge_review_entries",
  repository_id: "<repository_id>",
  master: MASTER,
  leaf_id: LEAF,
  entries: [],
  refusal: {
    code: "candidate_dataset_absent",
    detail: ENTRY_DETAIL,
    next_action: ENTRY_NEXT,
    offending_input: "knowledge-candidate.sqlite",
  },
};

const COUNTERS = {
  counters: {
    code: { files: 0, insertions: 0, deletions: 0 },
    memory: { files: 0, insertions: 0, deletions: 0 },
  },
};

// The bar's other read (the committed change-set counters) is incidental here; anything that is not
// the entry route answers it, exactly as the shared task-bar helper does.
function serveEntry(status: number, body: unknown, statusText = "") {
  const fetchFn = vi.fn(async (url: string) => {
    if (url.startsWith("/api/review/intent/entries")) {
      return {
        ok: status >= 200 && status < 300,
        status,
        statusText,
        json: async () => body,
      } as unknown as Response;
    }
    return { ok: true, status: 200, json: async () => COUNTERS } as unknown as Response;
  });
  vi.stubGlobal("fetch", fetchFn);
  return fetchFn;
}

function liveLeaf() {
  seedProjection({
    enclosures: [
      enclosure({
        enclosure: "/contracts/l16",
        lifecycleId: "L16",
        leafId: LEAF,
        repoName: REPO,
        taskName: MASTER,
        worktreeGroup: "/worktrees/l16-ar",
      }),
    ],
    activeWorktreeGroups: ["l16-ar"],
  });
}

function mount() {
  const onOpen = vi.fn();
  const view = render(
    <DocChangeSetBar kind="leaf" repo={REPO} master={MASTER} leaf={LEAF} onOpen={onOpen} />,
  );
  return { view, onOpen };
}

const intentButton = (view: ReturnType<typeof mount>["view"]) =>
  view
    .getAllByTestId("open-changeset")
    .find((button) => (button.textContent ?? "").includes("Intent review"));

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the review entry's own read state", () => {
  it("shows a never-initialized refusal beside the entry and still offers the entry", async () => {
    liveLeaf();
    serveEntry(404, entryRefusal, "Not Found");

    const { view, onOpen } = mount();

    const state = await view.findByTestId("review-entry-state");
    expect(state.dataset.reviewState).toBe("not-initialized");
    expect(state.dataset.reviewCode).toBe("candidate_dataset_absent");
    expect(state.textContent).toContain(ENTRY_DETAIL);
    expect(state.textContent).toContain(ENTRY_NEXT);
    // F2 (fix round): the owner named an offending input, so the entry states it too -- the
    // Required Behavior names reason, offending input and next action, and a condensed rendering is
    // still a rendering of the refusal.
    expect(state.textContent).toContain("offending input: knowledge-candidate.sqlite");

    // The refusal is a reason, never a gate: the entry is still there and still opens the task
    // context, because that review needs no dataset.
    const button = intentButton(view);
    expect(button).toBeDefined();
    fireEvent.click(button as HTMLElement);
    expect(onOpen).toHaveBeenCalledWith({ repo: REPO, master: MASTER, leaf: LEAF, review: {} });
  });

  it("says known empty when the pair offers no subject, without calling it a failure", async () => {
    liveLeaf();
    serveEntry(200, {
      state: "entries",
      operation: "list_knowledge_review_entries",
      repository_id: REPO,
      master: MASTER,
      leaf_id: LEAF,
      entries: [],
    });

    const { view } = mount();

    const state = await view.findByTestId("review-entry-state");
    expect(state.dataset.reviewState).toBe("known-empty");
    expect(state.textContent).toContain("no subject is recorded for this pair");
    expect(intentButton(view)).toBeDefined();
  });

  it("shows a transport failure with its reason, and raises no refusal body it does not have", async () => {
    liveLeaf();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.startsWith("/api/review/intent/entries")) throw new TypeError("fetch failed");
        return { ok: true, status: 200, json: async () => COUNTERS } as unknown as Response;
      }),
    );

    const { view, onOpen } = mount();

    const state = await view.findByTestId("review-entry-state");
    expect(state.dataset.reviewState).toBe("network");
    expect(state.textContent).toContain("could not reach the server");
    const button = intentButton(view);
    expect(button).toBeDefined();
    fireEvent.click(button as HTMLElement);
    expect(onOpen).toHaveBeenCalledWith({ repo: REPO, master: MASTER, leaf: LEAF, review: {} });
  });

  it("carries the server's recorded subject into the entry, and prints no state for an answer", async () => {
    liveLeaf();
    serveEntry(200, {
      state: "entries",
      operation: "list_knowledge_review_entries",
      repository_id: REPO,
      master: MASTER,
      leaf_id: LEAF,
      entries: [
        {
          selector_kind: "invariant",
          selector_id: "inv-1",
          label: "Retries share one budget",
          presence: "both",
        },
      ],
      total_subjects: 1,
      invariant_total: 1,
      family_total: 0,
    });

    const { view, onOpen } = mount();
    const button = intentButton(view) as HTMLElement;

    await waitFor(() => expect(view.queryByTestId("review-entry-state")).toBeNull());
    fireEvent.click(button);
    expect(onOpen).toHaveBeenLastCalledWith({
      repo: REPO,
      master: MASTER,
      leaf: LEAF,
      review: { selectorKind: "invariant", selectorId: "inv-1" },
    });
  });
});

// ── ICR-R17: the entry notices candidate generations, and no superseded read wins ──────────────
//
// WHAT THIS EXERCISES. The real `DocChangeSetBar` over the real review client; only `fetch` is
// stubbed, and the store is driven through its OWN public channel (`applySnapshot`, the same call the
// `/api/state` snapshot and its delta channel reach the store through). Every assertion reads the
// rendered DOM or the query string the real client built.
//
// THE DEFECT THESE CASES CATCH. The entry read depended on its props alone, so a subject published
// after the panel opened was invisible until the panel was closed and reopened -- and a response that
// answered a PREVIOUS leaf could still write the read state after the props had moved on. The first
// case below fails against that hook (it never asks again when the workspace projection is
// republished); the second fails against it too, because the late answer for the first leaf is the
// last write and it replaces the second leaf's catalogue.

// The workspace publishing something new, through the store's OWN delta channel -- the same call the
// SSE `analytics` frame makes (`serving/delta.py` sends the whole projection under this event name,
// because it replaces wholesale). It is deliberately not a re-render of the same props: the entry has
// to notice a fact that moved, and this is the fact channel it already has.
function workspacePublished(revision: number) {
  act(() => {
    dashboardStore.getState().applyDelta("analytics", {
      driftSnapshots: [],
      stalestSidecars: [],
      setupSummaries: [],
      setupProgress: [],
      routeCoverage: [],
      toolReports: [],
      ledgers: [],
      taskDocuments: [],
      series: [],
      attentionQueue: [],
      engineProcesses: [],
      agentPickups: [],
      expectationRows: [],
      publishedRevision: revision,
    });
  });
}

const ENTRY_ONE = {
  state: "entries",
  operation: "list_knowledge_review_entries",
  repository_id: REPO,
  master: MASTER,
  leaf_id: LEAF,
  entries: [
    { selector_kind: "invariant", selector_id: "inv-1", label: "First", presence: "both" },
  ],
  total_subjects: 1,
  invariant_total: 1,
  family_total: 0,
};

// The FIRST leaf's own answer, with a subject the second leaf's catalogue does not hold. If the late
// response were allowed to win, this is the row the picker would show under the second leaf's header.
const ENTRY_EARLIER_LEAF = {
  state: "entries",
  operation: "list_knowledge_review_entries",
  repository_id: REPO,
  master: MASTER,
  leaf_id: "260921-ICR-L17-a",
  entries: [
    {
      selector_kind: "invariant",
      selector_id: "inv-earlier-leaf",
      label: "Only the earlier leaf records this",
      presence: "both",
    },
  ],
  total_subjects: 1,
  invariant_total: 1,
  family_total: 0,
};

const ENTRY_TWO = {
  state: "entries",
  operation: "list_knowledge_review_entries",
  repository_id: REPO,
  master: MASTER,
  leaf_id: LEAF,
  entries: [
    { selector_kind: "invariant", selector_id: "inv-2", label: "Second", presence: "after_only" },
  ],
  total_subjects: 1,
  invariant_total: 1,
  family_total: 0,
};

function serveAnswers(answers: unknown[]) {
  let call = 0;
  const fetchFn = vi.fn(async (url: string) => {
    if (url.startsWith("/api/review/intent/entries")) {
      const body = answers[Math.min(call, answers.length - 1)];
      call += 1;
      return { ok: true, status: 200, json: async () => body } as unknown as Response;
    }
    return { ok: true, status: 200, json: async () => COUNTERS } as unknown as Response;
  });
  vi.stubGlobal("fetch", fetchFn);
  return fetchFn;
}

const entryUrls = (fetchFn: ReturnType<typeof vi.fn>) =>
  fetchFn.mock.calls.map((call) => String(call[0])).filter((url) => url.includes("/intent/entries"));

describe("the review entry notices a new candidate generation (ICR-R17)", () => {
  it("re-reads the pair's subjects when the workspace republishes, and keeps the reader's subject", async () => {
    liveLeaf();
    const fetchFn = serveAnswers([ENTRY_ONE, ENTRY_TWO]);

    const { view, onOpen } = mount();

    const picker = (await view.findByTestId("review-subject-picker")) as HTMLSelectElement;
    expect(picker.value).toBe("inv-1");
    expect(entryUrls(fetchFn)).toHaveLength(1);

    // The publication: the same panel, the same props, and a projection the workspace republished.
    workspacePublished(2);

    // The entry asked again by itself -- no remount, no prop change, no polling loop.
    await waitFor(() => expect(entryUrls(fetchFn)).toHaveLength(2));
    await waitFor(() =>
      expect(
        (view.getByTestId("review-subject-picker") as HTMLSelectElement).value,
      ).toBe("inv-2"),
    );
    // The selected identity was `inv-1`, which the newer catalogue does not list, so the entry falls
    // back to the recorded row the answer does carry rather than opening a stale id.
    fireEvent.click(intentButton(view) as HTMLElement);
    expect(onOpen).toHaveBeenLastCalledWith({
      repo: REPO,
      master: MASTER,
      leaf: LEAF,
      review: { selectorKind: "invariant", selectorId: "inv-2" },
    });
  });

  it("offers an explicit refresh control that re-reads the pair on the reader's own click", async () => {
    liveLeaf();
    const fetchFn = serveAnswers([ENTRY_ONE, ENTRY_TWO]);

    const { view } = mount();

    const control = await view.findByTestId("review-catalogue-refresh");
    // Nothing has moved since the read, so the control reports no movement and still offers the
    // reader their own way to ask again -- an explicit control, not a timer.
    expect(control.dataset.catalogueStale).toBe("false");
    await waitFor(() => expect(entryUrls(fetchFn)).toHaveLength(1));

    fireEvent.click(view.getByTestId("review-catalogue-refresh"));

    await waitFor(() => expect(entryUrls(fetchFn)).toHaveLength(2));
    await waitFor(() =>
      expect((view.getByTestId("review-subject-picker") as HTMLSelectElement).value).toBe("inv-2"),
    );
    expect(view.getByTestId("review-subject-picker").textContent).toContain("Second");
    expect(view.getByTestId("review-catalogue-refresh").dataset.catalogueStale).toBe("false");
  });

  it("never lets an answer for a previous leaf overwrite the leaf on screen now", async () => {
    // THE ROUTED DEBT (recorded when L16 landed): a prop/target change while a request is in flight
    // must not let the older response win. The first leaf's answer is held open until after the bar
    // has moved to the second leaf and that leaf's answer has already rendered.
    // Both leaves are live, so the entry is addressed to the LIVE candidate on each side of the
    // switch and the assertion is about the race rather than about liveness.
    seedProjection({
      enclosures: [
        enclosure({
          enclosure: "/contracts/l17-a",
          lifecycleId: "L17",
          leafId: "260921-ICR-L17-a",
          repoName: REPO,
          taskName: MASTER,
          worktreeGroup: "/worktrees/l17-ar",
        }),
        enclosure({
          enclosure: "/contracts/l17-b",
          lifecycleId: "L17",
          leafId: "260921-ICR-L17-b",
          repoName: REPO,
          taskName: MASTER,
          worktreeGroup: "/worktrees/l17-ar",
        }),
      ],
      activeWorktreeGroups: ["l17-ar"],
    });
    let releaseFirst: ((value: unknown) => void) | undefined;
    const fetchFn = vi.fn(async (url: string) => {
      if (url.startsWith("/api/review/intent/entries")) {
        const params = new URLSearchParams(url.split("?", 2)[1] ?? "");
        const asked = params.get("leaf") ?? "";
        if (asked === "260921-ICR-L17-a") {
          return await new Promise<Response>((resolve) => {
            releaseFirst = () => {
              resolve({
                ok: true,
                status: 200,
                json: async () => ({ ...ENTRY_EARLIER_LEAF, leaf_id: "260921-ICR-L17-a" }),
              } as unknown as Response);
            };
          });
        }
        return { ok: true, status: 200, json: async () => ENTRY_TWO } as unknown as Response;
      }
      return { ok: true, status: 200, json: async () => COUNTERS } as unknown as Response;
    });
    vi.stubGlobal("fetch", fetchFn);

    const onOpen = vi.fn();
    const view = render(
      <DocChangeSetBar
        kind="leaf"
        repo={REPO}
        master={MASTER}
        leaf="260921-ICR-L17-a"
        onOpen={onOpen}
      />,
    );
    await waitFor(() => expect(entryUrls(fetchFn)).toHaveLength(1));

    view.rerender(
      <DocChangeSetBar
        kind="leaf"
        repo={REPO}
        master={MASTER}
        leaf="260921-ICR-L17-b"
        onOpen={onOpen}
      />,
    );
    await waitFor(() =>
      expect((view.getByTestId("review-subject-picker") as HTMLSelectElement).value).toBe("inv-2"),
    );

    // The older leaf's answer arrives last. It must not win.
    await act(async () => {
      releaseFirst?.(undefined);
      await Promise.resolve();
    });

    expect((view.getByTestId("review-subject-picker") as HTMLSelectElement).value).toBe("inv-2");
    expect(view.getByTestId("review-subject-picker").textContent).toContain("Second");
    fireEvent.click(intentButton(view) as HTMLElement);
    expect(onOpen).toHaveBeenLastCalledWith({
      repo: REPO,
      master: MASTER,
      leaf: "260921-ICR-L17-b",
      review: { selectorKind: "invariant", selectorId: "inv-2" },
    });
  });
});

// ── ICR-R17 (fix round 1, L17-F4): the pre-click marker is observable ───────────────────────────
//
// THE DEFECT THESE CASES CATCH. The mark was derived as `!loading && read.facts !== facts`, and the
// same effect that observes the projection move immediately sets `loading: true` and re-reads -- so the
// mark was committed once and withdrawn in the same flush and never appeared in a settled DOM. The
// comment claimed the reader was told before they clicked; the reader never was.
//
// WHAT IS ASSERTED. The mark is present in the settled state it describes -- the list on screen was read
// from facts the workspace has moved past -- and absent once the answer for those facts is filed. The
// answer is held open here, which is the real shape of the window (a read crosses the network); the
// assertion is on a settled DOM, not on a transient commit.
describe("the entry's pre-click freshness marker (ICR-R17 / L17-F4)", () => {
  it("marks the list while the projection has moved past the answer on screen, and clears it on the answer", async () => {
    seedProjection({
      enclosures: [
        enclosure({
          enclosure: "/contracts/l17-marker",
          lifecycleId: "L17",
          leafId: LEAF,
          repoName: REPO,
          taskName: MASTER,
          worktreeGroup: "/worktrees/l17-marker-ar",
        }),
      ],
      activeWorktreeGroups: ["l17-marker-ar"],
    });
    // The FIRST entry read answers at once; the read the publication triggers is held open, so the
    // window the mark describes is a settled state the test can observe rather than a transient commit.
    let release: (() => void) | undefined;
    let entryReads = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.startsWith("/api/review/intent/entries")) {
          entryReads += 1;
          if (entryReads === 1) {
            return { ok: true, status: 200, json: async () => ENTRY_TWO } as unknown as Response;
          }
          return await new Promise<Response>((resolve) => {
            release = () =>
              resolve({ ok: true, status: 200, json: async () => ENTRY_ONE } as unknown as Response);
          });
        }
        return { ok: true, status: 200, json: async () => COUNTERS } as unknown as Response;
      }),
    );

    const { view } = mount();
    await view.findByTestId("review-subject-picker");
    expect(view.getByTestId("review-catalogue-refresh").dataset.catalogueStale).toBe("false");

    // The publication: the projection moves, and the answer for it is in flight.
    workspacePublished(3);

    // The reader is told, in a settled DOM, that the list beside the control is behind the workspace.
    const marker = await view.findByTestId("review-catalogue-stale");
    expect(marker.textContent).toContain("workspace facts changed");
    expect(view.getByTestId("review-catalogue-refresh").dataset.catalogueStale).toBe("true");
    expect(entryReads).toBe(2);

    // The answer is filed: the list on screen is the one the answer named, so the mark is gone.
    await act(async () => {
      release?.();
      await Promise.resolve();
    });
    await waitFor(() =>
      expect(view.getByTestId("review-catalogue-refresh").dataset.catalogueStale).toBe("false"),
    );
    expect(view.queryByTestId("review-catalogue-stale")).toBeNull();
  });
});

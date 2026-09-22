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

import { fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

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
          selected_item_count: 3,
        },
      ],
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

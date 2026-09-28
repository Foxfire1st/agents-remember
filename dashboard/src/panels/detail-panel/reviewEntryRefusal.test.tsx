// The task document's compact entry: one `Intent review +N −N` control, brief states with their
// explanation one click away (ICR-R16), and an economical set of reads.
//
// WHAT THIS EXERCISES. The real `DocChangeSetBar` over the real review and change-set clients; only
// `fetch` is stubbed. The Intent review control reads `/api/review/intent/summary`, which answers every
// typed state (counted, partial, unavailable with the owner's refusal) as a 200 with the state in the
// body, so a leaf with no knowledge yet puts no console error on the page.
//
// THE DEFECTS THESE CASES CATCH (measured on the installed dashboard before this change): the Intent
// review reused the change-set button, so it printed the change set's code+memory LINE totals
// (+3775 −1056 on L41) and made a second, identical committed change-set request; the entry read the
// whole subject catalogue before the reviewer was opened and re-read it whenever the serialized global
// analytics document moved; and the entry printed whole backend explanations inside and beside itself.
// A reproduction against that code recorded two committed requests, one catalogue read before entry,
// and `⇄ Intent review+3775 −1056`.

import { act, fireEvent, render, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { dashboardStore } from "../../data/store";
import { DocChangeSetBar } from "./changeSetBar";
import { enclosure, seedProjection } from "./test-utils";

const REPO = "agents-remember";
const MASTER = "260921_complete-code-and-intent-review";
const LEAF = "260921-ICR-L16";

// The real route's refusal for a never-initialized task (the owner's own sentences, verbatim).
const ABSENT_DETAIL =
  "the resolved baseline dataset is absent, so the pair has nothing to compare; the review reads " +
  "neither of its two halves out of the live coordination tree and substitutes no other dataset";
const ABSENT_NEXT =
  "author the candidate's knowledge in the leaf's disposable knowledge root, and place the dataset " +
  "it forks from in the baseline half if this leaf has one; the surface substitutes no other dataset";
const UNAVAILABLE = {
  state: "unavailable",
  operation: "read_review_intent_summary",
  repository_id: REPO,
  master: MASTER,
  leaf_id: LEAF,
  refusal: {
    code: "candidate_dataset_absent",
    detail: ABSENT_DETAIL,
    next_action: ABSENT_NEXT,
    offending_input: "knowledge-candidate.sqlite",
  },
};

const counted = (added: number, removed: number, extra: Record<string, number> = {}) => ({
  state: extra.unresolved ? "partial" : "counted",
  operation: "read_review_intent_summary",
  repository_id: REPO,
  master: MASTER,
  leaf_id: LEAF,
  counts: {
    added,
    removed,
    invariants: { after_only: added, before_only: removed },
    guarantees: { after_only: 0, before_only: 0 },
    realization_only: 0,
    membership_only: 0,
    unresolved: 0,
    ...extra,
  },
});

// Line totals that must never appear on the Intent review control.
const COUNTERS = {
  counters: {
    code: { files: 31, insertions: 3000, deletions: 1000 },
    memory: { files: 5, insertions: 775, deletions: 56 },
  },
};

const respond = (status: number, body: unknown) =>
  ({ ok: status >= 200 && status < 300, status, statusText: "", json: async () => body }) as unknown as Response;

// Answer the summary route with `summary` (or with each of `summaries` in turn); every other route is
// the change-set counters.
function serve(summaries: Array<{ status: number; body: unknown }>) {
  let call = 0;
  const fetchFn = vi.fn(async (url: string) => {
    if (url.startsWith("/api/review/intent/summary")) {
      const answer = summaries[Math.min(call, summaries.length - 1)];
      call += 1;
      return respond(answer.status, answer.body);
    }
    return respond(200, COUNTERS);
  });
  vi.stubGlobal("fetch", fetchFn);
  return fetchFn;
}

const urlsOf = (fetchFn: ReturnType<typeof vi.fn>, fragment: string) =>
  fetchFn.mock.calls.map((call) => String(call[0])).filter((url) => url.includes(fragment));

function liveLeaf(over: Partial<ReturnType<typeof enclosure>> = {}) {
  seedProjection({
    enclosures: [
      enclosure({
        enclosure: "/contracts/l16",
        lifecycleId: "L16",
        leafId: LEAF,
        repoName: REPO,
        taskName: MASTER,
        worktreeGroup: "/worktrees/l16-ar",
        ...over,
      }),
    ],
    activeWorktreeGroups: ["l16-ar"],
  });
}

function mount(leaf = LEAF) {
  const onOpen = vi.fn();
  const view = render(
    <DocChangeSetBar kind="leaf" repo={REPO} master={MASTER} leaf={leaf} onOpen={onOpen} />,
  );
  return { view, onOpen };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the compact Intent review entry", () => {
  it("is one control with the comparison's changed-intent counts and nothing beside it", async () => {
    liveLeaf();
    serve([{ status: 200, body: counted(4, 2) }]);
    const { view, onOpen } = mount();

    const entry = await view.findByTestId("open-intent-review");
    await waitFor(() => expect(entry.dataset.intentState).toBe("counted"));
    const counts = within(entry).getByTestId("intent-review-counts");
    expect(counts.textContent).toBe("+4 −2");
    expect(entry.textContent).toBe("⇄ Intent review+4 −2");
    // The change set's line totals belong to the change-set controls, never to this one.
    expect(entry.textContent).not.toContain("3775");
    // No task-level subject picker, no separate refresh control, no entry paragraph, no disclosure.
    for (const gone of [
      "review-subject-picker",
      "review-catalogue-refresh",
      "review-entry-state",
      "review-catalogue-totals",
      "intent-review-details",
    ])
      expect(view.queryByTestId(gone)).toBeNull();
    fireEvent.click(entry);
    expect(onOpen).toHaveBeenCalledWith({ repo: REPO, master: MASTER, leaf: LEAF, review: {} });
  });

  it("makes one summary read, no catalogue read and one committed change-set read", async () => {
    liveLeaf();
    const fetchFn = serve([{ status: 200, body: counted(1, 0) }]);
    const { view } = mount();

    const entry = await view.findByTestId("open-intent-review");
    await waitFor(() => expect(entry.dataset.intentState).toBe("counted"));
    await waitFor(() => expect(urlsOf(fetchFn, "mode=working")).toHaveLength(1));

    expect(urlsOf(fetchFn, "/api/review/intent/summary")).toHaveLength(1);
    expect(urlsOf(fetchFn, "/api/review/intent/entries")).toHaveLength(0);
    expect(urlsOf(fetchFn, "mode=committed")).toHaveLength(1);
    // Clicking the entry hands the target to the reviewer; the entry itself reads nothing more.
    fireEvent.click(entry);
    expect(fetchFn).toHaveBeenCalledTimes(3);
  });
});

describe("the Intent review entry's own states (ICR-R16: brief, never swallowed)", () => {
  it("states missing knowledge briefly, never as +0 −0, with the owner's refusal in the disclosure", async () => {
    liveLeaf();
    serve([{ status: 200, body: UNAVAILABLE }]);
    const { view, onOpen } = mount();

    const entry = await view.findByTestId("open-intent-review");
    const state = await waitFor(() => {
      const found = within(entry).getByTestId("intent-review-state");
      expect(found.dataset.reviewState).toBe("not-initialized");
      return found;
    });
    expect(state.dataset.reviewCode).toBe("candidate_dataset_absent");
    expect(state.textContent).toBe("no knowledge yet");
    expect(entry.textContent).not.toMatch(/[+−]\d/);
    expect(entry.textContent).not.toContain(ABSENT_DETAIL);

    const details = view.getByTestId("intent-review-details");
    expect(details.textContent).toContain("candidate_dataset_absent");
    expect(details.textContent).toContain(ABSENT_DETAIL);
    expect(details.textContent).toContain("knowledge-candidate.sqlite");
    expect(details.textContent).toContain(ABSENT_NEXT);
    // The disclosure is closed by default: the page shows one control and a small marker.
    expect(details.hasAttribute("open")).toBe(false);

    // Still the entry: the task-context review opens on the source inventory.
    fireEvent.click(entry);
    expect(onOpen).toHaveBeenCalledWith({ repo: REPO, master: MASTER, leaf: LEAF, review: {} });
  });

  it("says a response the route did not produce is unreadable, and invents no refusal for it", async () => {
    liveLeaf();
    serve([{ status: 502, body: null }]);
    const { view } = mount();

    const entry = await view.findByTestId("open-intent-review");
    const state = await waitFor(() => {
      const found = within(entry).getByTestId("intent-review-state");
      expect(found.dataset.reviewState).toBe("unreadable");
      return found;
    });
    expect(state.textContent).toBe("unreadable");
    expect(view.getByTestId("intent-review-details").textContent).toContain("502");
  });

  it("marks partial counts as partial and explains what they leave out", async () => {
    liveLeaf();
    serve([{ status: 200, body: counted(3, 1, { unresolved: 2 }) }]);
    const { view } = mount();

    const entry = await view.findByTestId("open-intent-review");
    await waitFor(() => expect(entry.dataset.intentState).toBe("partial"));
    expect(within(entry).getByTestId("intent-review-counts").textContent).toBe("+3 −1");
    expect(within(entry).getByTestId("intent-review-state").textContent).toBe("partial");
    expect(view.getByTestId("intent-review-details").textContent).toContain("2 subject(s)");
  });
});

describe("the entry re-reads only for its own comparison (ICR-R17)", () => {
  it("ignores unrelated workspace publications and re-reads when this leaf's lifecycle moves", async () => {
    liveLeaf();
    const fetchFn = serve([
      { status: 200, body: counted(1, 0) },
      { status: 200, body: counted(2, 1) },
    ]);
    const { view } = mount();
    const entry = await view.findByTestId("open-intent-review");
    await waitFor(() => expect(entry.textContent).toContain("+1 −0"));

    // Another task's publication moves the global analytics document: no re-read.
    act(() => {
      dashboardStore.getState().applyDelta("analytics", {
        ...dashboardStore.getState().analytics,
        publishedRevision: 2,
      });
    });
    await act(async () => {});
    expect(urlsOf(fetchFn, "/api/review/intent/summary")).toHaveLength(1);

    // This leaf's own closeout moves: the comparison the entry opens may have changed, so it asks.
    act(() => {
      dashboardStore.getState().applyDelta(
        "enclosure",
        enclosure({
          enclosure: "/contracts/l16",
          lifecycleId: "L16",
          leafId: LEAF,
          repoName: REPO,
          taskName: MASTER,
          worktreeGroup: "/worktrees/l16-ar",
          closeoutStatus: "completed",
        }),
      );
    });
    await waitFor(() => expect(urlsOf(fetchFn, "/api/review/intent/summary")).toHaveLength(2));
    await waitFor(() => expect(entry.textContent).toContain("+2 −1"));
  });

  it("never lets an answer for a previous leaf overwrite the leaf on screen now", async () => {
    seedProjection({
      enclosures: ["a", "b"].map((suffix) =>
        enclosure({
          enclosure: `/contracts/l17-${suffix}`,
          lifecycleId: "L17",
          leafId: `260921-ICR-L17-${suffix}`,
          repoName: REPO,
          taskName: MASTER,
          worktreeGroup: "/worktrees/l17-ar",
        }),
      ),
      activeWorktreeGroups: ["l17-ar"],
    });
    let releaseFirst: (() => void) | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (!url.startsWith("/api/review/intent/summary")) return respond(200, COUNTERS);
        const asked = new URLSearchParams(url.split("?", 2)[1] ?? "").get("leaf");
        if (asked === "260921-ICR-L17-a")
          return await new Promise<Response>((resolve) => {
            releaseFirst = () => resolve(respond(200, counted(9, 9)));
          });
        return respond(200, counted(1, 1));
      }),
    );
    const { view, onOpen } = mount("260921-ICR-L17-a");
    view.rerender(
      <DocChangeSetBar kind="leaf" repo={REPO} master={MASTER} leaf="260921-ICR-L17-b" onOpen={onOpen} />,
    );
    const entry = await view.findByTestId("open-intent-review");
    await waitFor(() => expect(entry.textContent).toContain("+1 −1"));

    await act(async () => {
      releaseFirst?.();
      await Promise.resolve();
    });
    expect(view.getByTestId("open-intent-review").textContent).toContain("+1 −1");
    expect(view.getByTestId("open-intent-review").textContent).not.toContain("+9");
  });
});

// B6: a `committed` read of a live leaf has no landed commit yet. The route answers that state in the
// body; the control keeps an unrecorded range apart from a measured empty one and from a refusal, and
// now says so in one word with the route's own sentence in the disclosure beside it.
const UNRECORDED_DETAIL =
  "the contract records no code landed commit for leaf 260921-ICR-L16, so this leaf has no committed " +
  "code range yet: the committed view reads the two recorded commits and substitutes no HEAD, branch " +
  "or working tree for either. Read the uncommitted view (mode=working) while the task is live, or " +
  "reopen this view after closeout records the range";
const EMPTY = {
  counters: {
    code: { files: 0, insertions: 0, deletions: 0 },
    memory: { files: 0, insertions: 0, deletions: 0 },
  },
};
const UNRECORDED_BODY = { ...EMPTY, scope: LEAF, mode: "committed", state: "unrecorded", stateDetail: UNRECORDED_DETAIL };

const committedButton = (view: ReturnType<typeof mount>["view"]) =>
  view
    .getAllByTestId("open-changeset")
    .find((button) => (button.textContent ?? "").includes("committed"));

function serveCommitted(body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) =>
      respond(200, url.includes("/api/changeset/task") && url.includes("mode=committed") ? body : EMPTY),
    ),
  );
}

describe("the committed counter read of a leaf whose range nothing has recorded (B6)", () => {
  it("shows the unrecorded state briefly, the route's sentence on demand, and never its zero as a total", async () => {
    liveLeaf();
    serveCommitted(UNRECORDED_BODY);
    const { view } = mount();
    const button = await waitFor(() => {
      const found = committedButton(view);
      expect(found).toBeDefined();
      return found as HTMLElement;
    });

    const state = await waitFor(() => {
      const found = within(button).getByTestId("changeset-state");
      expect(found.dataset.reviewState).toBe("unrecorded");
      return found;
    });
    expect(state.textContent).toBe("unrecorded");
    expect(state.dataset.reviewCode).toBeUndefined();
    expect(button.textContent).not.toContain("+0 −0");
    expect(button.textContent).not.toContain("no committed code range yet");

    const details = view.getByTestId("changeset-state-details");
    expect(details.textContent).toContain("not measured empty");
    expect(details.textContent).toContain("no committed code range yet");
    expect(details.textContent).toContain("mode=working");
  });

  it("keeps an unrecorded range apart from a measured empty one and from a refusal", async () => {
    liveLeaf();
    serveCommitted({ ...EMPTY, state: "recorded", stateDetail: "" });
    const { view } = mount();
    const button = await waitFor(() => {
      const found = committedButton(view);
      expect(found).toBeDefined();
      return found as HTMLElement;
    });
    const empty = await waitFor(() => {
      const found = within(button).getByTestId("changeset-state");
      expect(found.dataset.reviewState).toBe("known-empty");
      return found;
    });
    expect(empty.textContent).toBe("empty");
    expect(empty.title).toContain("no changed file in either half");
    await waitFor(() => expect(button.textContent).toContain("+0 −0"));
    // A measured empty range needs no explanation beyond its own word.
    expect(view.queryByTestId("changeset-state-details")).toBeNull();
  });
});

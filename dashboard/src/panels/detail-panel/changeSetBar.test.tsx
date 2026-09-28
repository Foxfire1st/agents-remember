import { act, fireEvent, render, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { dashboardStore } from "../../data/store";
import { DocChangeSetBar } from "./changeSetBar";
import { DetailPanel } from "./DetailPanel";
import {
  enclosure,
  seedProjection,
  seedTaskDocuments,
  stubCounters,
  taskDoc,
} from "./test-utils";

describe("DetailPanel doc-reader change-set bar (L4a)", () => {
  const leafPath = "/tasks/agents-remember/260628_operations-integration/04a_changeset-everywhere.json";

  // The closed leaf's own entry set: committed (its landed delta) plus the Intent review, which is
  // read from the leaf's recorded comparison because there is no live candidate to read (ICR-R12).
  // The WORKING change-set is absent -- there is no uncommitted delta once the enclosure is closed --
  // so liveness still selects one of the three buttons and is no longer the gate on the review entry.
  const closedLeaf = () => {
    const doc = taskDoc({
      id: "260628-L4a",
      lifecycleId: undefined,
      kind: "subTask",
      title: "Change-set everywhere",
      repository: "agents-remember",
      docPath: leafPath,
      objective: "Leaf objective.",
    });
    seedTaskDocuments([doc]);
    return doc;
  };

  it("shows a committed button on a leaf doc reader (no live enclosure) and opens the leaf target", async () => {
    stubCounters();
    closedLeaf();
    const onOpenChangeSet = vi.fn();
    const { findAllByTestId } = render(
      <DetailPanel selectedId={`taskdoc:${leafPath}`} onOpenChangeSet={onOpenChangeSet} />,
    );
    // identity comes from the doc node, so the bar shows with NO active enclosure (the L4 gap);
    // committed is always present, working only when live.
    const buttons = await findAllByTestId("open-changeset");
    const committed = buttons.find((b) => (b.textContent ?? "").includes("committed"));
    expect(committed).toBeDefined();
    fireEvent.click(committed as HTMLElement);
    expect(onOpenChangeSet).toHaveBeenCalledWith({
      repo: "agents-remember",
      master: "260628_operations-integration",
      leaf: "260628-L4a",
      mode: "committed",
    });
  });

  // The changed-intent summary answers in the route's own shape; `counts` is what the control prints.
  const summary = (added: number, removed: number) => ({
    state: "counted",
    operation: "read_review_intent_summary",
    repository_id: "agents-remember",
    master: "260628_operations-integration",
    leaf_id: "260628-L4a",
    counts: {
      added,
      removed,
      invariants: { after_only: added, before_only: removed },
      guarantees: { after_only: 0, before_only: 0 },
      realization_only: 0,
      membership_only: 0,
      unresolved: 0,
    },
  });

  it("offers the Intent review for a closed leaf, bound to its recorded comparison", async () => {
    // The packet's defect: a cleaned leaf's Intent Review answered candidate_not_live. The entry that
    // opens it must exist for a leaf with no live enclosure, and it must name the record it is
    // addressed to -- the leaf's own durable generation -- rather than the live candidate. It is one
    // compact control with the comparison's changed-intent counts and no subject picker beside it.
    stubCounters(summary(2, 1));
    closedLeaf();
    const onOpenChangeSet = vi.fn();
    const { findAllByTestId, findByTestId, queryByTestId } = render(
      <DetailPanel selectedId={`taskdoc:${leafPath}`} onOpenChangeSet={onOpenChangeSet} />,
    );
    const labels = (await findAllByTestId("open-changeset")).map((b) => b.textContent ?? "");
    expect(labels.some((t) => t.includes("working"))).toBe(false);
    const entry = await findByTestId("open-intent-review");
    await waitFor(() => expect(entry.textContent).toContain("+2 −1"));
    expect(entry.textContent).toContain("Intent review (recorded)");
    expect(queryByTestId("review-subject-picker")).toBeNull();
    fireEvent.click(entry);
    expect(onOpenChangeSet).toHaveBeenCalledWith({
      repo: "agents-remember",
      master: "260628_operations-integration",
      leaf: "260628-L4a",
      review: { historical: true },
    });
  });

  it("keeps the closed leaf's Intent review when its knowledge is unavailable", async () => {
    // The summary is a label and never the gate: a closed leaf whose record holds no knowledge still
    // opens the review on its recorded comparison (the task-context entry).
    stubCounters({
      state: "unavailable",
      operation: "read_review_intent_summary",
      repository_id: "agents-remember",
      master: "260628_operations-integration",
      leaf_id: "260628-L4a",
      refusal: {
        code: "candidate_dataset_absent",
        detail: "the resolved candidate dataset is absent",
        next_action: "author the candidate's knowledge",
      },
    });
    closedLeaf();
    const onOpenChangeSet = vi.fn();
    const { findByTestId } = render(
      <DetailPanel selectedId={`taskdoc:${leafPath}`} onOpenChangeSet={onOpenChangeSet} />,
    );
    const entry = await findByTestId("open-intent-review");
    await waitFor(() => expect(entry.dataset.intentState).toBe("unavailable"));
    fireEvent.click(entry);
    expect(onOpenChangeSet).toHaveBeenCalledWith({
      repo: "agents-remember",
      master: "260628_operations-integration",
      leaf: "260628-L4a",
      review: { historical: true },
    });
  });

  it("shows a series button on a master doc reader", async () => {
    stubCounters();
    const master = taskDoc({
      lifecycleId: undefined,
      kind: "master",
      title: "Operations Integration",
      repository: "agents-remember",
      docPath: "/tasks/agents-remember/260628_operations-integration/task.json",
      objective: "Master objective.",
    });
    seedTaskDocuments([master]);
    const onOpenChangeSet = vi.fn();
    const { findAllByTestId } = render(
      <DetailPanel
        selectedId="taskdoc:/tasks/agents-remember/260628_operations-integration/task.json"
        onOpenChangeSet={onOpenChangeSet}
      />,
    );
    const buttons = await findAllByTestId("open-changeset");
    expect(buttons).toHaveLength(1);
    expect(buttons[0].textContent).toContain("series");
    fireEvent.click(buttons[0]);
    expect(onOpenChangeSet).toHaveBeenCalledWith({
      repo: "agents-remember",
      master: "260628_operations-integration",
    });
  });

  it("carries the master net's leaf attribution beside its total (R33.2)", async () => {
    // The delivered defect: this read asked for `includeLeaves: false`, so the master's net total
    // stood alone — a number with no leaf to attribute it to. The read now asks for the breakdown and
    // the control prints it beside the total, which is where the reviewer decides what to open.
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.startsWith("/api/task-document")) {
          const params = new URLSearchParams(url.split("?", 2)[1] ?? "");
          const docPath = params.get("path") ?? "";
          const doc =
            dashboardStore.getState().analytics?.taskDocuments.find((item) => item.docPath === docPath) ??
            taskDoc({ kind: docPath.endsWith("/task.json") ? "master" : "subTask", docPath });
          return { ok: true, status: 200, json: async () => doc } as unknown as Response;
        }
        return {
          ok: true,
          status: 200,
          json: async () => ({
            leaves: [
              {
                leafId: "260921-ICR-L1",
                state: "committed",
                counters: {
                  code: { files: 8, insertions: 1258, deletions: 235 },
                  memory: { files: 20, insertions: 1326, deletions: 406 },
                },
              },
              {
                leafId: "260921-ICR-L25",
                state: "working",
                counters: {
                  code: { files: 2, insertions: 10, deletions: 3 },
                  memory: { files: 1, insertions: 4, deletions: 0 },
                },
              },
            ],
            counters: {
              code: { files: 10, insertions: 1268, deletions: 238 },
              memory: { files: 21, insertions: 1330, deletions: 406 },
            },
          }),
        } as unknown as Response;
      }),
    );
    const master = taskDoc({
      lifecycleId: undefined,
      kind: "master",
      title: "Complete code and intent review",
      repository: "agents-remember",
      docPath: "/tasks/agents-remember/260921_complete-code-and-intent-review/task.json",
      objective: "Master objective.",
    });
    seedTaskDocuments([master]);
    const { findAllByTestId } = render(
      <DetailPanel
        selectedId="taskdoc:/tasks/agents-remember/260921_complete-code-and-intent-review/task.json"
        onOpenChangeSet={vi.fn()}
      />,
    );

    const attribution = await findAllByTestId("changeset-leaf-attribution");
    expect(attribution).toHaveLength(1);
    expect(attribution[0].textContent).toBe("2 leaf/leaves · 1 committed · 1 working");
    // Beside the net total, not instead of it: the button still carries the summed counters.
    const button = (await findAllByTestId("open-changeset"))[0];
    expect(button.textContent).toContain("+2598 −644");
    expect(
      (vi.mocked(fetch).mock.calls as unknown as string[][]).some((call) =>
        String(call[0]).includes("includeLeaves=true"),
      ),
    ).toBe(true);
  });

  it("opens the series view bound to the generation the net published", async () => {
    // The master net names its exact endpoints; the entry carries those pins into the viewer so
    // the view -- and each file expansion inside it -- reads the listed generation rather than
    // re-resolving the live tip.
    const generation = {
      codeBase: "b0",
      codeTip: "t2",
      memoryBase: "",
      memoryTip: "",
      digest: "ab".repeat(32),
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.startsWith("/api/task-document")) {
          const params = new URLSearchParams(url.split("?", 2)[1] ?? "");
          const docPath = params.get("path") ?? "";
          const doc =
            dashboardStore.getState().analytics?.taskDocuments.find((item) => item.docPath === docPath) ??
            taskDoc({ kind: docPath.endsWith("/task.json") ? "master" : "subTask", docPath });
          return { ok: true, status: 200, json: async () => doc } as unknown as Response;
        }
        return {
          ok: true,
          status: 200,
          json: async () => ({
            counters: {
              code: { files: 0, insertions: 0, deletions: 0 },
              memory: { files: 0, insertions: 0, deletions: 0 },
            },
            generation,
            currentness: "current",
            scope: "integrated",
          }),
        } as unknown as Response;
      }),
    );
    const master = taskDoc({
      lifecycleId: undefined,
      kind: "master",
      title: "Operations Integration",
      repository: "agents-remember",
      docPath: "/tasks/agents-remember/260628_operations-integration/task.json",
      objective: "Master objective.",
    });
    seedTaskDocuments([master]);
    const onOpenChangeSet = vi.fn();
    const { findAllByTestId } = render(
      <DetailPanel
        selectedId="taskdoc:/tasks/agents-remember/260628_operations-integration/task.json"
        onOpenChangeSet={onOpenChangeSet}
      />,
    );
    const buttons = await findAllByTestId("open-changeset");
    expect(buttons).toHaveLength(1);
    await act(async () => {});
    fireEvent.click(buttons[0]);
    expect(onOpenChangeSet).toHaveBeenCalledWith({
      repo: "agents-remember",
      master: "260628_operations-integration",
      generation: {
        codeBase: "b0",
        codeTip: "t2",
        memoryBase: "",
        memoryTip: "",
      },
    });
  });

  it("adds a working button when the leaf's enclosure is live", async () => {
    stubCounters();
    const doc = taskDoc({
      id: "260628-l4a",
      lifecycleId: undefined,
      kind: "subTask",
      title: "Change-set everywhere",
      repository: "agents-remember",
      docPath: leafPath,
    });
    seedProjection({
      enclosures: [
        enclosure({
          enclosure: "/contracts/l4a",
          lifecycleId: "X",
          leafId: "260628-l4a",
          repoName: "agents-remember",
          taskName: "260628_operations-integration",
          worktreeGroup: "/worktrees/changeset-everywhere-ar",
        }),
      ],
      activeWorktreeGroups: ["changeset-everywhere-ar"],
      analytics: {
        driftSnapshots: [],
        stalestSidecars: [],
        setupSummaries: [],
        setupProgress: [],
        routeCoverage: [],
        toolReports: [],
        ledgers: [],
        taskDocuments: [doc],
        series: [],
        attentionQueue: [],
        engineProcesses: [],
        agentPickups: [],
        expectationRows: [],
      },
    });
    const onOpenChangeSet = vi.fn();
    const { findAllByTestId } = render(
      <DetailPanel selectedId={`taskdoc:${leafPath}`} onOpenChangeSet={onOpenChangeSet} />,
    );
    const labels = (await findAllByTestId("open-changeset")).map((b) => b.textContent ?? "");
    expect(labels).toHaveLength(2);
    expect(labels.some((t) => t.includes("committed"))).toBe(true);
    expect(labels.some((t) => t.includes("working"))).toBe(true);
    expect((await findAllByTestId("open-intent-review"))[0].textContent).toContain("Intent review");
  });

  // The entry is the task context: a live leaf opens its source review whatever the summary answers,
  // and the reviewer -- not the entry -- chooses the subject once it has read its own catalogue.
  const liveLeaf = () => {
    const doc = taskDoc({
      id: "260628-l4a",
      lifecycleId: undefined,
      kind: "subTask",
      title: "Change-set everywhere",
      repository: "agents-remember",
      docPath: leafPath,
    });
    seedProjection({
      enclosures: [
        enclosure({
          enclosure: "/contracts/l4a",
          lifecycleId: "X",
          leafId: "260628-l4a",
          repoName: "agents-remember",
          taskName: "260628_operations-integration",
          worktreeGroup: "/worktrees/changeset-everywhere-ar",
        }),
      ],
      activeWorktreeGroups: ["changeset-everywhere-ar"],
      analytics: {
        driftSnapshots: [],
        stalestSidecars: [],
        setupSummaries: [],
        setupProgress: [],
        routeCoverage: [],
        toolReports: [],
        ledgers: [],
        taskDocuments: [doc],
        series: [],
        attentionQueue: [],
        engineProcesses: [],
        agentPickups: [],
        expectationRows: [],
      },
    });
  };

  it("opens the task-context review; the reviewer chooses the subject", async () => {
    stubCounters(summary(1, 1));
    liveLeaf();
    const onOpenChangeSet = vi.fn();
    const { findByTestId } = render(
      <DetailPanel selectedId={`taskdoc:${leafPath}`} onOpenChangeSet={onOpenChangeSet} />,
    );
    const button = await findByTestId("open-intent-review");
    await waitFor(() => expect(button.textContent).toContain("+1 −1"));
    expect(button.textContent).toContain("Intent review");
    expect(button.textContent).not.toContain("(recorded)");
    fireEvent.click(button);
    expect(onOpenChangeSet).toHaveBeenCalledWith({
      repo: "agents-remember",
      master: "260628_operations-integration",
      leaf: "260628-l4a",
      review: {},
    });
  });

  it("keeps the entry when the summary read itself fails", async () => {
    // An unreachable summary is not a reason to hide the source review: the task context is the
    // entry, and the control states that it could not count rather than printing a zero.
    const failing = vi.fn(async () => {
      throw new Error("socket closed");
    });
    vi.stubGlobal("fetch", failing);
    liveLeaf();
    const onOpenChangeSet = vi.fn();
    const { findByTestId } = render(
      <DetailPanel selectedId={`taskdoc:${leafPath}`} onOpenChangeSet={onOpenChangeSet} />,
    );
    const button = await findByTestId("open-intent-review");
    await waitFor(() => expect(button.dataset.intentState).toBe("unavailable"));
    expect(button.textContent).not.toMatch(/\+\d/);
    // Brief in the control, and the reason reaches the disclosure beside it (R16: never swallowed).
    const state = within(button).getByTestId("intent-review-state");
    expect(state.dataset.reviewState).toBe("network");
    expect(state.textContent).toBe("offline");
    const details = await findByTestId("intent-review-details");
    fireEvent.click(within(details).getByText("?"));
    expect(details.hasAttribute("open")).toBe(true);
    expect(details.textContent).toContain("network");
    expect(details.textContent).toContain("socket closed");
    fireEvent.click(button);
    expect(onOpenChangeSet).toHaveBeenCalledWith({
      repo: "agents-remember",
      master: "260628_operations-integration",
      leaf: "260628-l4a",
      review: {},
    });
  });

  it("omits the bar entirely when no onOpenChangeSet handler is wired", () => {
    stubCounters();
    const doc = taskDoc({
      id: "260628-L4a",
      lifecycleId: undefined,
      kind: "subTask",
      title: "Change-set everywhere",
      repository: "agents-remember",
      docPath: leafPath,
    });
    seedTaskDocuments([doc]);
    const { queryAllByTestId } = render(<DetailPanel selectedId={`taskdoc:${leafPath}`} />);
    expect(queryAllByTestId("open-changeset")).toHaveLength(0);
  });
});

// ── L32/D01: the counter read's refusal is carried, not swallowed ────────────────────────────────
//
// THE DEFECT. `ChangeSetButton` read its counters through `leafChangeset`/`masterChangeset`/
// `taskChangeset` and its rejection handler took NO error parameter: it set the counters to null and
// dropped the cause. A REFUSED read therefore rendered byte-identically to a read that had NOT
// ANSWERED -- `⇄ committed` either way, naming neither the refusal's code nor its reason -- while
// three lines below it in the same file the catalogue read carried "the reason, in the owner's own
// words" and rendered it.
//
// WHAT THESE CASES DRIVE. The real `DocChangeSetBar` over the real change-set client
// (`data/changeset.ts`); only the ROUTE is stubbed, so every answer below travels the production
// decode and the production control. A MASTER bar is used because it is exactly one control over
// exactly one read: pending, refused and answered are then three answers of the SAME question, and
// nothing else on screen can answer for them.
//
// THE REFUSAL IS THE ROUTE'S OWN. The change-set family publishes a refusal's code and its reason in
// the body of the non-2xx response, as `{status, detail}` -- and as `{status, path}` where the 404's
// path message IS the reason (`serving/changeset.py::_master_json`). The sentence below is verbatim
// from the refusal `serving/master_net_generation.py` raises when the net's declared base is not
// recorded yet, so the case asserts the owner's own words rather than a string this test invented.
const MASTER_NET = "260921_complete-code-and-intent-review";
const MASTER_NET_REASON =
  "master '260921_complete-code-and-intent-review' records no code base commit, so its net code " +
  "range does not exist yet: the net comparison reads the declared base and selected result and " +
  "substitutes no branch tip for either";
const OTHER_MASTER = "260921_other-master";
const MEASURED_COUNTERS = {
  counters: {
    code: { files: 3, insertions: 7, deletions: 2 },
    memory: { files: 1, insertions: 0, deletions: 0 },
  },
};

// One stubbed response in the shape the route answers with.
const routeAnswer = (ok: boolean, status: number, statusText: string, body: unknown) =>
  ({ ok, status, statusText, json: async () => body }) as unknown as Response;

const masterBar = (master: string) => {
  const onOpen = vi.fn();
  const view = render(
    <DocChangeSetBar kind="master" repo="agents-remember" master={master} onOpen={onOpen} />,
  );
  return { view, onOpen };
};

describe("the counter read's refusal (L32/D01)", () => {
  it("renders a refused counter read's own code and reason, never as a read that has not answered", async () => {
    seedProjection({});

    // PENDING: the route does not answer.
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise<Response>(() => {})),
    );
    const pending = masterBar(MASTER_NET);
    const pendingText = (await pending.view.findByTestId("open-changeset")).textContent ?? "";
    pending.view.unmount();

    // REFUSED: the route's own 404 idiom, with the reason in the body of the response.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        routeAnswer(false, 404, "Not Found", { status: "not-found", path: MASTER_NET_REASON }),
      ),
    );
    const refused = masterBar(MASTER_NET);
    const refusedButton = await refused.view.findByTestId("open-changeset");
    await act(async () => {});
    const refusedText = refusedButton.textContent ?? "";

    // THE DEFECT, FIRST: a refusal is not the absence of an answer. Before this case existed these
    // two renderings were the same string -- `⇄ series`, with no code and no reason anywhere on
    // screen -- so the reader could not tell a refused delta from one that had not loaded.
    expect(refusedText).not.toBe(pendingText);

    // The refusal's OWN code, and its reason in the owner's own words.
    // The control carries a brief state; the owner's reason is in the disclosure beside it (brief,
    // never swallowed).
    const state = refused.view.getByTestId("changeset-state");
    expect(state.dataset.reviewState).toBe("not-found");
    expect(state.dataset.reviewCode).toBe("not-found");
    expect(state.textContent).toBe("not found");
    expect(refusedText).not.toContain(MASTER_NET_REASON);
    const details = refused.view.getByTestId("changeset-state-details");
    expect(details.textContent).toContain("not-found");
    expect(details.textContent).toContain(MASTER_NET_REASON);

    // The refusal is a reason and never a gate: the control still opens the change-set it names.
    fireEvent.click(refusedButton);
    expect(refused.onOpen).toHaveBeenCalledWith({ repo: "agents-remember", master: MASTER_NET });
    refused.view.unmount();

    // ANSWERED: the counters ARE the answer, so no state is printed at all -- and the refusal the
    // previous read earned is gone rather than left standing over a delta that was measured.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => routeAnswer(true, 200, "OK", MEASURED_COUNTERS)),
    );
    const answered = masterBar(MASTER_NET);
    const answeredButton = await answered.view.findByTestId("open-changeset");
    await waitFor(() => expect(answeredButton.textContent).toContain("+7 −2"));
    expect(answered.view.queryByTestId("changeset-state")).toBeNull();
    expect(answered.view.queryByTestId("changeset-state-details")).toBeNull();
    expect(answeredButton.textContent).not.toContain(MASTER_NET_REASON);
  });

  it("keeps a measured empty answer apart from a refusal and from a read that has not answered", async () => {
    seedProjection({});
    // The answer the shared helper serves (and every other case in this file already drives): a
    // measured delta with no changed file in either half. A measured zero is a MEASUREMENT.
    stubCounters();
    const { view } = masterBar(MASTER_NET);

    const button = await view.findByTestId("open-changeset");
    const state = await waitFor(() => {
      const found = view.getByTestId("changeset-state");
      expect(found.dataset.reviewState).toBe("known-empty");
      return found;
    });
    expect(button.textContent).toContain("+0 −0");
    expect(state.textContent).toBe("empty");
    expect(state.title).toContain("no changed file in either half");
    // It names no refusal code: an empty delta is not a failure, and it is not a pending read.
    expect(state.dataset.reviewCode).toBeUndefined();
    expect(state.dataset.reviewState).not.toBe("loading");
  });

  it("does not let a superseded read's refusal land on the control that replaced it", async () => {
    // The `live` guard is kept and is load-bearing: the first master's read is held open until AFTER
    // the bar has moved to a second master and that master's answer has rendered. Removing
    // `if (!live) return;` from the rejection handler makes the late refusal land on the control for
    // the OTHER master, and this case then finds a refusal rendered over a measured delta.
    seedProjection({});
    let releaseFirst: (() => void) | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        const asked = new URLSearchParams(url.split("?", 2)[1] ?? "").get("master") ?? "";
        if (asked === MASTER_NET) {
          return await new Promise<Response>((resolve) => {
            releaseFirst = () =>
              resolve(
                routeAnswer(false, 404, "Not Found", {
                  status: "not-found",
                  path: MASTER_NET_REASON,
                }),
              );
          });
        }
        return routeAnswer(true, 200, "OK", MEASURED_COUNTERS);
      }),
    );

    const { view, onOpen } = masterBar(MASTER_NET);
    view.rerender(
      <DocChangeSetBar kind="master" repo="agents-remember" master={OTHER_MASTER} onOpen={onOpen} />,
    );
    await waitFor(() => expect(view.getByTestId("open-changeset").textContent).toContain("+7 −2"));

    // The superseded read's refusal arrives last. It must not win.
    await act(async () => {
      releaseFirst?.();
      await Promise.resolve();
    });
    expect(view.queryByTestId("changeset-state")).toBeNull();
    expect(view.getByTestId("open-changeset").textContent).not.toContain(MASTER_NET_REASON);
  });
});

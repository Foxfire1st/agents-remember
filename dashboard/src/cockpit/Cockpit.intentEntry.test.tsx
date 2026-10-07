// The task entry's Intent review counts are re-validated where the developer looks at them again.
//
// No dashboard signal moves when a leaf's knowledge is first written, so the entry keeps showing
// "no knowledge yet" until one of the ruled re-validation points:
//   (a) the task detail is opened or shown again (a view switch back, or selecting the task again);
//   (b) the developer leaves the reviewer back to the entry;
//   (c) the reviewer's own refresh runs.
// Each case reproduces the first-ingest sequence against the real cockpit shell -- stale entry, the
// store gains knowledge, then one trigger -- and counts requests: each trigger adds exactly one
// summary read, and no trigger reads the subject catalogue before the reviewer is opened.
import { act, cleanup, fireEvent, render, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { dashboardStore } from "../data/store";
import { taskDoc as wireTaskDoc } from "../test/fixtures/wire";
import { metricsFor } from "../types/projection";
import type { EnclosureNode, LifecycleProjection, TaskDocNode, WorkspaceProjection } from "../types/projection";
import { CockpitShell } from "./Cockpit";

vi.mock("../panels/Terminal", () => ({
  Terminal: ({ sessionId }: { sessionId: string }) => <div data-testid={`term-${sessionId}`} />,
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  dashboardStore.getState().reset();
  window.localStorage.clear();
});

const LEAF = "direct-leaf";
const OTHER = "other-leaf";

function leafDoc(id: string, title: string, lifecycleId: string): TaskDocNode {
  return wireTaskDoc({
    id,
    kind: "subTask",
    lifecycleId,
    repository: "repo-a",
    title,
    status: "inProgress",
    createdAt: "2026-06-20T09:00:00+00:00",
    docPath: `/tasks/repo-a/ops/${id}.json`,
    stepsDone: 0,
    stepsTotal: 0,
    steps: [],
    objective: `${title} objective.`,
    requirements: [],
    codeExamples: [],
    decisions: [],
    openQuestions: [],
    references: [],
    subTasks: [],
    sections: [],
  });
}

function lifecycle(id: string, enclosure: string): LifecycleProjection {
  return {
    id,
    state: "running",
    phase: "build",
    fleeting: false,
    repoId: "repo-a",
    enclosure: `/contracts/${enclosure}`,
    tokens: 0,
    startedAt: "2026-07-12T10:00:00+00:00",
    lastEventTs: "2026-07-12T10:00:30+00:00",
    stateEnteredAt: "2026-07-12T10:00:00+00:00",
    inferred: false,
    actions: [],
    tokenSeries: [],
  };
}

function liveEnclosure(leafId: string, lifecycleId: string): EnclosureNode {
  return {
    enclosure: `/contracts/${leafId}`,
    enclosureId: leafId,
    leafId,
    taskRoot: "/tasks/repo-a/ops",
    taskId: "ops-master",
    taskName: "ops",
    repoName: "repo-a",
    lifecycleId,
    worktreeGroup: `/worktrees/${leafId}`,
    humanReviewStatus: "pending-review",
    closeoutStatus: "not-started",
    integrationStatus: "not-started",
    cleanup: "pending",
    codeWorktreeExists: true,
    memoryWorktreeExists: true,
    actions: [],
  };
}

function seedTwoLiveLeaves(): TaskDocNode[] {
  const docs = [leafDoc(LEAF, "Direct Leaf Reader", "LC-A"), leafDoc(OTHER, "Other Leaf Reader", "LC-B")];
  const lifecycles = [lifecycle("LC-A", LEAF), lifecycle("LC-B", OTHER)];
  const projection: WorkspaceProjection = {
    version: 2,
    generatedAt: "2026-07-12T10:01:00+00:00",
    lifecycles,
    enclosures: [liveEnclosure(LEAF, "LC-A"), liveEnclosure(OTHER, "LC-B")],
    providers: [],
    activeWorktreeGroups: [LEAF, OTHER],
    metrics: metricsFor(lifecycles),
    analytics: {
      driftSnapshots: [],
      stalestSidecars: [],
      setupSummaries: [],
      setupProgress: [],
      routeCoverage: [],
      toolReports: [],
      ledgers: [],
      taskDocuments: docs,
      series: [],
      attentionQueue: [],
      engineProcesses: [],
      agentPickups: [],
      expectationRows: [],
    },
  };
  dashboardStore.getState().applySnapshot(projection);
  return docs;
}

const json = (body: unknown) => ({ ok: true, status: 200, json: async () => body }) as Response;

// The routes this journey touches. `knowledge.present` is the store: false until the first ingest.
function serve(docs: TaskDocNode[]) {
  const knowledge = { present: false };
  const summary = (leaf: string) =>
    knowledge.present
      ? {
          state: "counted",
          operation: "read_review_intent_summary",
          repository_id: "repo-a",
          master: "ops",
          leaf_id: leaf,
          counts: {
            added: 2,
            removed: 0,
            invariants: { after_only: 2, before_only: 0 },
            guarantees: { after_only: 0, before_only: 0 },
            realization_only: 0,
            membership_only: 0,
            unresolved: 0,
          },
        }
      : {
          state: "unavailable",
          operation: "read_review_intent_summary",
          repository_id: "repo-a",
          master: "ops",
          leaf_id: leaf,
          refusal: {
            code: "candidate_dataset_absent",
            detail: "the resolved candidate dataset is absent",
            next_action: "author the candidate's knowledge",
            offending_input: "knowledge-candidate.sqlite",
          },
        };
  const refused = {
    state: "refused",
    refusal: { code: "candidate_dataset_absent", detail: "no dataset", next_action: "author one" },
  };
  const fetchMock = vi.fn(async (address: string) => {
    const url = new URL(address, "http://dashboard.test");
    if (url.pathname === "/api/task-document")
      return json(docs.find((doc) => doc.docPath === url.searchParams.get("path")) ?? {});
    if (url.pathname === "/api/review/intent/summary")
      return json(summary(url.searchParams.get("leaf") ?? ""));
    if (url.pathname.startsWith("/api/review/intent")) return json(refused);
    if (url.pathname.startsWith("/api/changeset/"))
      return json({
        counters: {
          code: { files: 0, insertions: 0, deletions: 0 },
          memory: { files: 0, insertions: 0, deletions: 0 },
        },
      });
    if (url.pathname === "/api/terminal/sessions") return json({ sessions: [] });
    if (url.pathname === "/api/harnesses") return json({ harnesses: [] });
    if (url.pathname === "/api/files/repos") return json({ repos: [] });
    return json({});
  });
  vi.stubGlobal("fetch", fetchMock);
  const paths = () => fetchMock.mock.calls.map(([address]) => new URL(String(address), "http://dashboard.test"));
  return {
    knowledge,
    summaryReads: (leaf = LEAF) =>
      paths().filter(
        (url) => url.pathname === "/api/review/intent/summary" && url.searchParams.get("leaf") === leaf,
      ).length,
    catalogueReads: () => paths().filter((url) => url.pathname === "/api/review/intent/entries").length,
  };
}

// Open the leaf from the rail and wait for its stale entry: the first-ingest starting point.
async function openStaleEntry(view: ReturnType<typeof render>, routes: ReturnType<typeof serve>) {
  fireEvent.click(view.getByText("Direct Leaf Reader"));
  const entry = await view.findByTestId("open-intent-review");
  await waitFor(() => expect(entry.dataset.intentState).toBe("unavailable"));
  expect(entry.textContent).toContain("no knowledge yet");
  expect(routes.summaryReads()).toBe(1);
  // The first ingest: the store gains knowledge, and nothing the dashboard receives says so.
  routes.knowledge.present = true;
  await act(async () => {});
  expect(routes.summaryReads()).toBe(1);
  return entry;
}

it("(a) re-validates when the task detail is shown again, once per showing, with no catalogue read", async () => {
  const routes = serve(seedTwoLiveLeaves());
  const view = render(<CockpitShell initialView="operations" />);
  await openStaleEntry(view, routes);

  fireEvent.click(view.getByRole("radio", { name: "File Viewer" }));
  fireEvent.click(view.getByRole("radio", { name: "Operations" }));
  await waitFor(() => expect(view.getByTestId("open-intent-review").textContent).toContain("+2 −0"));
  expect(routes.summaryReads()).toBe(2);

  // Switching to another task and back to this one: one read for each leaf's entry, no duplicate.
  fireEvent.click(view.getByText("Other Leaf Reader"));
  await waitFor(() => expect(routes.summaryReads(OTHER)).toBe(1));
  fireEvent.click(view.getByText("Direct Leaf Reader"));
  await waitFor(() => expect(routes.summaryReads()).toBe(3));
  await act(async () => {});
  expect(routes.summaryReads()).toBe(3);
  expect(routes.summaryReads(OTHER)).toBe(1);
  expect(routes.catalogueReads()).toBe(0);
});

it("(a) opening a task from another view reads its entry exactly once", async () => {
  const routes = serve(seedTwoLiveLeaves());
  const view = render(<CockpitShell initialView="operations" />);
  fireEvent.click(view.getByRole("radio", { name: "File Viewer" }));
  // The rails stay on Memory; selecting a task changes the task and shows the detail in one step.
  fireEvent.click(view.getByText("Direct Leaf Reader"));
  const entry = await view.findByTestId("open-intent-review");
  await waitFor(() => expect(entry.dataset.intentState).toBe("unavailable"));
  await act(async () => {});
  expect(routes.summaryReads()).toBe(1);
  expect(routes.catalogueReads()).toBe(0);
});

it("(b) re-validates when the developer leaves the reviewer back to the entry", async () => {
  const routes = serve(seedTwoLiveLeaves());
  const view = render(<CockpitShell initialView="operations" />);
  await openStaleEntry(view, routes);
  expect(routes.catalogueReads()).toBe(0);

  fireEvent.click(view.getByTestId("open-intent-review"));
  await view.findByTestId("review-surface");
  await waitFor(() => expect(routes.catalogueReads()).toBe(1));
  // Opening the reviewer is not a re-validation of the (now hidden) entry.
  expect(routes.summaryReads()).toBe(1);

  fireEvent.click(view.getByTestId("review-back"));
  await waitFor(() => expect(view.getByTestId("open-intent-review").textContent).toContain("+2 −0"));
  await act(async () => {});
  expect(routes.summaryReads()).toBe(2);
  expect(routes.catalogueReads()).toBe(1);
});

it("(c) re-validates when the reviewer's own refresh runs", async () => {
  const routes = serve(seedTwoLiveLeaves());
  const view = render(<CockpitShell initialView="operations" />);
  const entry = await openStaleEntry(view, routes);

  fireEvent.click(entry);
  const surface = await view.findByTestId("review-surface");
  fireEvent.click(within(surface).getByTestId("review-refresh"));
  // The refresh itself re-reads the entry's summary once, while the reviewer is still open.
  await waitFor(() => expect(routes.summaryReads()).toBe(2));
  await waitFor(() => expect(view.getByTestId("open-intent-review").textContent).toContain("+2 −0"));
  await act(async () => {});
  expect(routes.summaryReads()).toBe(2);
  expect(routes.catalogueReads()).toBe(1);
});

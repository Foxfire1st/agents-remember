// The chat pane is never empty, and a document the dashboard already knows switches
// with the click.
import { act, cleanup, render, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { chatLeaf as leaf, chatMaster as master, chatSeries, chatSprint as sprint } from "../../test/fixtures/documentChat";
import { taskDoc } from "../../test/fixtures/wire";
import type { SeriesNode, TaskDocNode } from "../../types/projection";
import { DocumentChat } from "./DocumentChat";
import { KNOWN_CHATS_LIMIT, KnownChats, TREE_SELECTION_LIMIT, treeSelections, useKnownChats } from "./knownChats";
import { documentBindings, type DocumentAgent } from "./model";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const HOST = "https://host.example";
const ref = (path: string) => ({ repository: "repo", path });
const manager: DocumentAgent = { agentId: "manager", workspaceId: "projects", archivedAt: null, createdAt: "2026-01-01", labels: { "ar.role": "manager", "ar.master-ref": "repo/master/task.json" } };
const worker: DocumentAgent = { agentId: "worker", workspaceId: "leaf-workspace", archivedAt: null, createdAt: "2026-01-02", labels: { "ar.role": "worker", "ar.task-ref": "repo/master/leaf.json" } };
const DOCS = { taskDocuments: [sprint, master, leaf], series: chatSeries };
type Selection = { role: string; taskDocumentRef?: unknown; masterDocumentRef?: unknown };
const agentsOf = (selection: Selection) => (selection.taskDocumentRef ? [worker] : selection.masterDocumentRef ? [manager] : []);

/** The backend. `single` answers the read of one document, `tree` the read of the whole tree. */
function stubBackend(single: (selection: Selection) => Promise<object> | object, tree: (selections: Selection[]) => object) {
  const treeRequests: Selection[][] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(String(init.body)) : {};
    if (url.endsWith("/document-chats/tree")) treeRequests.push(body.selections);
    const value = url.endsWith("/frame") ? { available: true, frameBaseUrl: HOST, serverId: "server", projectsWorkspaceId: "projects" }
      : url.endsWith("/document-chats/tree") ? tree(body.selections)
      : url.endsWith("/document-chats") ? await single(body)
      : { roleDefaults: {}, agents: [], executions: [] };
    return new Response(JSON.stringify(value));
  }));
  return treeRequests;
}

/** The mounted frame after its plug-in reported ready and the Manager's chat was shown. */
async function onManagersChat(container: HTMLElement) {
  await waitFor(() => expect(container.querySelector("iframe")).not.toBeNull());
  const frame = container.querySelector("iframe")!;
  const posts = vi.spyOn(frame.contentWindow!, "postMessage");
  const says = (data: object) => act(() => {
    window.dispatchEvent(new MessageEvent("message", { origin: HOST, source: frame.contentWindow, data: { source: "ar-plugin", ...data } }));
  });
  says({ type: "ready" });
  await waitFor(() => expect(posts).toHaveBeenCalledWith({ type: "ar.open", agentId: "manager", workspaceId: "projects" }, HOST));
  says({ type: "shown", agentId: "manager" });
  return { frame, posts };
}
const frameHidden = (frame: HTMLIFrameElement) => frame.closest("[aria-hidden='true']") !== null;

describe("the chat pane while the selection changes", () => {
  it("keeps the earlier chat shown and out of reach while it asks, and hides the frame only on the answer no chat", async () => {
    let answerLeaf: (value: object) => void = () => {};
    stubBackend(
      (selection) => (selection.taskDocumentRef ? new Promise<object>((resolve) => { answerLeaf = resolve; }) : { agents: agentsOf(selection) }),
      () => ({}),
    );
    const { container, rerender, getByTestId, queryByTestId, findByTestId } = render(<DocumentChat active taskDocumentRef={ref("master/task.json")} {...DOCS} />);
    const { frame, posts } = await onManagersChat(container);
    expect(frame.hasAttribute("inert")).toBe(false);

    rerender(<DocumentChat active taskDocumentRef={ref("master/leaf.json")} {...DOCS} />);
    // Nothing is known about the task's chat yet: the Manager's chat stays, under one line.
    expect(frameHidden(frame)).toBe(false);
    expect(frame.hasAttribute("inert")).toBe(true);
    expect(getByTestId("paseo-frame-waiting").textContent).toContain("dashboard to name this document's chat");
    expect(queryByTestId("role-launcher")).toBeNull();
    expect(posts.mock.calls.filter(([message]) => message.type === "ar.open")).toHaveLength(1);

    await act(async () => { answerLeaf({ agents: [] }); });
    await findByTestId("role-launcher");
    expect(frameHidden(frame)).toBe(true);
    expect(container.querySelector("iframe")).toBe(frame);
  });

  it("switches with the click to a chat the tree's one request already named, and takes the fresh answer after it", async () => {
    let answerLeaf: (value: object) => void = () => {};
    const treeRequests = stubBackend(
      (selection) => (selection.taskDocumentRef ? new Promise<object>((resolve) => { answerLeaf = resolve; }) : { agents: agentsOf(selection) }),
      (selections) => ({ chats: selections.map((selection) => ({ agents: agentsOf(selection) })) }),
    );
    const { container, rerender, findByTestId } = render(<DocumentChat active taskDocumentRef={ref("master/task.json")} {...DOCS} />);
    const { frame, posts } = await onManagersChat(container);
    await waitFor(() => expect(treeRequests).toHaveLength(1));
    // One request names every document the tree lists.
    expect(treeRequests[0].map((selection) => selection.role)).toEqual(["orchestrator", "manager", "worker"]);
    await act(async () => {});

    rerender(<DocumentChat active taskDocumentRef={ref("master/leaf.json")} {...DOCS} />);
    // No answer for the task has come and none is waited for: the switch is out already.
    expect(posts).toHaveBeenLastCalledWith({ type: "ar.open", agentId: "worker", workspaceId: "leaf-workspace" }, HOST);
    expect(frameHidden(frame)).toBe(false);
    expect(treeRequests).toHaveLength(1);

    // The fresh answer corrects what was known: the Worker's agent is gone.
    await act(async () => { answerLeaf({ agents: [] }); });
    await findByTestId("role-launcher");
    expect(frameHidden(frame)).toBe(true);
  });
});

describe("what the dashboard keeps of the tree's chats", () => {
  it.each([KNOWN_CHATS_LIMIT + 1, KNOWN_CHATS_LIMIT * 3])("holds at most its limit of %i answers and drops the oldest", (count) => {
    const known = new KnownChats();
    for (let index = 0; index < count; index += 1) known.set("selection-" + index, [worker]);
    expect(known.size).toBe(KNOWN_CHATS_LIMIT);
    expect(known.get("selection-0")).toBeUndefined();
    expect(known.get("selection-" + (count - 1))).toEqual([worker]);
    expect(known.get("selection-" + (count - KNOWN_CHATS_LIMIT))).toEqual([worker]);
  });

  it.each([3, 150])("asks for at most its limit of a tree of %i masters, the viewed master's documents first", (masters) => {
    const docs: TaskDocNode[] = [taskDoc({ repository: "repo", docPath: "/tasks/repo/sprint/task.json", kind: "master", orchestrates: Array.from({ length: masters }, (_, index) => "m" + index) })];
    const series: SeriesNode[] = [];
    for (let index = 0; index < masters; index += 1) {
      const folder = "/tasks/repo/m" + index;
      docs.push(taskDoc({ repository: "repo", docPath: folder + "/task.json", kind: "master", orchestrates: [] }));
      docs.push(taskDoc({ repository: "repo", docPath: folder + "/leaf.json", kind: "subTask", orchestrates: [] }));
      series.push({ ...chatSeries[0], docPath: folder + "/task.json", seriesId: "m" + index });
    }
    const viewed = ref("m" + (masters - 1) + "/leaf.json");
    const selections = treeSelections(documentBindings(docs, series), viewed);
    const fits = 1 + masters * 2 <= TREE_SELECTION_LIMIT;
    expect(selections).toHaveLength(fits ? 1 + masters * 2 : TREE_SELECTION_LIMIT);
    // A tree within the limit is asked for whole, in its own order. A larger one names the
    // sprint, then the viewed document's master and its tasks, before any other master.
    const first = fits ? 0 : masters - 1;
    expect(selections.slice(0, 3).map((selection) => [selection.role, (selection.taskDocumentRef ?? selection.masterDocumentRef ?? selection.sprintDocumentRef)?.path])).toEqual([
      ["orchestrator", "sprint/task.json"],
      ["manager", "m" + first + "/task.json"],
      ["worker", "m" + first + "/leaf.json"],
    ]);
    expect(selections.some((selection) => selection.taskDocumentRef?.path === viewed.path)).toBe(true);
  });
});

describe("when the tree's chats are asked for", () => {
  const tree = documentBindings([sprint, master, leaf], chatSeries);
  const HALF_MINUTE = 30_000;
  const advance = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
  /** The browser's tab, hidden or shown, as the page learns of it. */
  const showTab = (shown: boolean) => act(() => {
    Object.defineProperty(document, "hidden", { configurable: true, get: () => !shown });
    document.dispatchEvent(new Event("visibilitychange"));
  });

  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => {
    Reflect.deleteProperty(document, "hidden");
    vi.useRealTimers();
  });

  it("asks nothing while the browser's tab or the chat pane is hidden, and once when it is shown again", async () => {
    const requests: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      requests.push(url);
      return new Response(JSON.stringify({ chats: [] }));
    }));
    const { rerender, unmount } = renderHook(({ active }) => useKnownChats(tree, undefined, active), { initialProps: { active: true } });
    await advance(0);
    expect(requests).toEqual(["/api/role-launch/document-chats/tree"]);
    await advance(HALF_MINUTE);
    expect(requests).toHaveLength(2);

    // The tab is hidden: the half minutes pass without a request.
    showTab(false);
    await advance(HALF_MINUTE * 3);
    expect(requests).toHaveLength(2);
    // Shown again, the last answer older than a half minute: one request at once, then the
    // half minutes again.
    showTab(true);
    await advance(0);
    expect(requests).toHaveLength(3);
    await advance(HALF_MINUTE - 1);
    expect(requests).toHaveLength(3);
    await advance(1);
    expect(requests).toHaveLength(4);

    // Hidden and shown inside a half minute: no request of its own, the next one at its time.
    await advance(10_000);
    showTab(false);
    await advance(5_000);
    showTab(true);
    await advance(14_999);
    expect(requests).toHaveLength(4);
    await advance(1);
    expect(requests).toHaveLength(5);

    // The chat pane hidden and shown again: the same.
    rerender({ active: false });
    await advance(HALF_MINUTE * 3);
    expect(requests).toHaveLength(5);
    rerender({ active: true });
    await advance(0);
    expect(requests).toHaveLength(6);

    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("gives up a request that is under way when the tab is hidden, and leaves no timer behind", async () => {
    let answer: (response: Response) => void = () => {};
    const signals: AbortSignal[] = [];
    vi.stubGlobal("fetch", vi.fn((_url: string, init?: RequestInit) => {
      signals.push(init!.signal!);
      return new Promise<Response>((resolve) => { answer = resolve; });
    }));
    const { unmount } = renderHook(() => useKnownChats(tree, undefined, true));
    await advance(0);
    expect(signals).toHaveLength(1);

    showTab(false);
    expect(signals[0].aborted).toBe(true);
    await act(async () => { answer(new Response(JSON.stringify({ chats: [] }))); });
    await advance(HALF_MINUTE * 2);
    expect(signals).toHaveLength(1);
    expect(vi.getTimerCount()).toBe(0);

    // Shown again: the request that was given up counts for nothing, the tree is asked for at once.
    showTab(true);
    await advance(0);
    expect(signals).toHaveLength(2);
    unmount();
    expect(signals[1].aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });
});

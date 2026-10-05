import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { taskDoc } from "../test/fixtures/wire";
import { RoleChatsPane } from "./RoleChats";
import { PaseoChatFrame } from "./PaseoChatFrame";
import { PASEO_CONTROL_TIMEOUT_MS } from "./paseoFrameControl";
import { tasklessRequestStorageKey } from "./roleLaunchModel";
import {
  paseoAgentTarget,
  parseFrameDescriptor,
  parsePluginMessage,
  paseoFrameUrl,
} from "./paseoFrameModel";


const FRAME_ORIGIN = "http://127.0.0.1:6820";
const AVAILABLE = {
  available: true,
  frameBaseUrl: FRAME_ORIGIN,
  serverId: "srv_test",
  projectsWorkspaceId: "wks_projects",
  projectsWorkspaceDetail: null,
};
const ARCHITECT = { agentId: "agent-architect", workspaceId: "wks_projects" };
const WORKER = { agentId: "agent-worker", workspaceId: "wks_leaf" };
const PROJECTS_URL = FRAME_ORIGIN + "/h/srv_test/workspace/wks_projects";
const WORKER_URL = FRAME_ORIGIN + "/h/srv_test/workspace/wks_leaf?open=agent:agent-worker";

type Answer = Record<string, unknown>;
type Route = Answer | ((request: Answer) => Answer);

/** The dashboard backend: the frame route answers from a queue (its last answer repeats). */
function stubBackend(frameAnswers: Answer[], other: Record<string, Route> = {}) {
  const queue = [...frameAnswers];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const route =
      url === "/api/role-launch/frame" ? (queue.length > 1 ? queue.shift() : queue[0]) : other[url];
    if (!route) throw new Error("unexpected request " + url);
    const body =
      typeof route === "function" ? route(JSON.parse(String(init?.body ?? "{}")) as Answer) : route;
    const status = typeof body.httpStatus === "number" ? body.httpStatus : 200;
    return { ok: status < 400, status, json: async () => body } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function settle(ms = 0): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

function frameElement(container: HTMLElement): HTMLIFrameElement {
  const frame = container.querySelector("iframe");
  if (!frame) throw new Error("no frame is mounted");
  return frame;
}

/** A message as the browser would deliver it from the frame's own window, or from elsewhere. */
function deliver(
  frame: HTMLIFrameElement,
  data: unknown,
  from: { origin?: string; source?: MessageEventSource | null } = {},
): void {
  act(() => {
    window.dispatchEvent(
      new MessageEvent("message", {
        data,
        origin: from.origin ?? FRAME_ORIGIN,
        source: from.source === undefined ? frame.contentWindow : from.source,
      }),
    );
  });
}

const plugin = (message: Answer) => ({ source: "ar-plugin", ...message });

function watchPosts(frame: HTMLIFrameElement) {
  const posts = vi.fn();
  if (!frame.contentWindow) throw new Error("the frame has no window");
  frame.contentWindow.postMessage = posts as unknown as Window["postMessage"];
  return posts;
}

beforeEach(() => {
  vi.useFakeTimers();
  sessionStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("grouped chat navigation channel", () => {
  const agent = (agentId: string, name: string, workspaceId: string) => ({
    agentId,
    name,
    provider: "pi",
    status: "idle",
    pendingPermissionCount: 0,
    requiresAttention: false,
    attentionReason: null,
    providerUnavailable: false,
    workspaceId,
    parentAgentId: null,
    archivedAt: null,
    labels: {},
  });
  const hierarchyAgents = [
    agent(ARCHITECT.agentId, "Architect chat", ARCHITECT.workspaceId),
    agent(WORKER.agentId, "Worker chat", WORKER.workspaceId),
  ];
  const hierarchy = plugin({
    type: "hierarchy",
    agents: hierarchyAgents,
    projects: [],
    workspaces: [],
  });

  it("navigates explicit sidebar clicks without iframe reload or changing launcher intent, and highlights only native selection sets", async () => {
    stubBackend([AVAILABLE]);
    const { container, getByRole, rerender } = render(
      <PaseoChatFrame navigationOpen taskDocuments={[]} series={[]} active scope="architect" target={ARCHITECT} />,
    );
    await settle();
    const frame = frameElement(container);
    const posts = watchPosts(frame);
    deliver(frame, plugin({ type: "ready" }));
    deliver(frame, plugin({ type: "shown", agentId: ARCHITECT.agentId }));
    deliver(frame, hierarchy);
    deliver(frame, plugin({ type: "selection", agentIds: [ARCHITECT.agentId] }));
    const root = getByRole("button", { name: /Architect chat/ });
    const worker = getByRole("button", { name: /Worker chat/ });
    expect(root.getAttribute("aria-current")).toBe("true");
    fireEvent.click(worker);
    expect(posts).toHaveBeenLastCalledWith(
      { type: "ar.open", agentId: WORKER.agentId },
      FRAME_ORIGIN,
    );
    deliver(frame, plugin({ type: "shown", agentId: WORKER.agentId }));
    expect(worker.getAttribute("aria-current")).toBeNull();
    expect(root.getAttribute("aria-current")).toBe("true");
    deliver(frame, plugin({ type: "selection", agentIds: [WORKER.agentId, ARCHITECT.agentId] }));
    expect(worker.getAttribute("aria-current")).toBe("true");
    expect(root.getAttribute("aria-current")).toBe("true");
    const count = posts.mock.calls.length;
    rerender(
      <PaseoChatFrame navigationOpen taskDocuments={[]} series={[]} active scope="architect" target={ARCHITECT} />,
    );
    expect(posts).toHaveBeenCalledTimes(count);
    rerender(
      <PaseoChatFrame navigationOpen={false} taskDocuments={[]} series={[]} active scope="architect" target={ARCHITECT} />,
    );
    expect(container.querySelector('nav[aria-label="AR chat navigation"]')).toBeNull();
    expect(frameElement(container)).toBe(frame);
    rerender(
      <PaseoChatFrame navigationOpen taskDocuments={[]} series={[]} active scope="architect" target={ARCHITECT} />,
    );
    expect(getByRole("button", { name: /Worker chat/ }).getAttribute("aria-current")).toBe("true");
    expect(frameElement(container)).toBe(frame);
    expect(frame.getAttribute("src")).toBe(PROJECTS_URL);
  });

  it("rejects untrusted catalogs/selection and malformed snapshots, and distinguishes catalog failure from empty data", async () => {
    stubBackend([AVAILABLE]);
    const { container, queryByRole, getByRole, queryByText, getByTestId } = render(
      <PaseoChatFrame navigationOpen taskDocuments={[]} series={[]} active scope="s" target={null} />,
    );
    await settle();
    const frame = frameElement(container);
    deliver(frame, plugin({ type: "ready" }));
    deliver(frame, hierarchy, { origin: "https://elsewhere.test" });
    expect(queryByRole("button", { name: /Worker chat/ })).toBeNull();
    deliver(frame, hierarchy);
    deliver(frame, plugin({ type: "selection", agentIds: [WORKER.agentId] }), { source: window });
    const worker = getByRole("button", { name: /Worker chat/ });
    expect(worker.getAttribute("aria-current")).toBeNull();
    deliver(
      frame,
      plugin({
        type: "hierarchy",
        agents: [{ ...hierarchyAgents[0], labels: [] }],
        projects: [],
        workspaces: [],
      }),
    );
    expect(getByRole("button", { name: /Worker chat/ })).toBe(worker);
    deliver(frame, plugin({ type: "hierarchy-error", message: "SDK directory unavailable" }));
    expect(getByTestId("paseo-frame-hierarchy-problem").textContent).toContain(
      "SDK directory unavailable",
    );
    expect(worker.hasAttribute("disabled")).toBe(true);
    expect(queryByText("No chats")).toBeNull();
    deliver(frame, hierarchy);
    expect(worker.hasAttribute("disabled")).toBe(false);
  });

  it("reports Parent failure only for a current visible native tab, including split panes, with no pending dashboard request", async () => {
    stubBackend([AVAILABLE]);
    const { container, getByRole, getByTestId, queryByTestId } = render(
      <PaseoChatFrame navigationOpen taskDocuments={[]} series={[]} active scope="worker" target={WORKER} />,
    );
    await settle();
    const frame = frameElement(container);
    watchPosts(frame);
    deliver(frame, plugin({ type: "ready" }));
    deliver(frame, plugin({ type: "shown", agentId: WORKER.agentId }));
    deliver(frame, hierarchy);
    deliver(frame, plugin({ type: "selection", agentIds: [WORKER.agentId] }));
    const error = plugin({
      type: "navigation-error",
      context: "parent",
      sourceAgentId: WORKER.agentId,
      targetAgentId: ARCHITECT.agentId,
      code: "agent-archived",
    });
    deliver(frame, error, { origin: "https://elsewhere.test" });
    expect(queryByTestId("paseo-frame-agent-problem")).toBeNull();
    deliver(frame, plugin({ ...error, sourceAgentId: "reviewer" }));
    expect(queryByTestId("paseo-frame-agent-problem")).toBeNull();
    // Native tabs can change without a dashboard request or a new frame URL.
    deliver(frame, plugin({ type: "selection", agentIds: [ARCHITECT.agentId] }));
    deliver(frame, error);
    expect(queryByTestId("paseo-frame-agent-problem")).toBeNull();
    // The Worker can remain visible beside another current tab in a split pane.
    deliver(frame, plugin({ type: "selection", agentIds: [WORKER.agentId, ARCHITECT.agentId] }));
    deliver(frame, error);
    expect(getByTestId("paseo-frame-agent-problem").textContent).toContain(
      "parent chat is archived",
    );
    expect(getByRole("button", { name: /Worker chat/ }).getAttribute("aria-current")).toBe("true");
    expect(frameElement(container)).toBe(frame);
  });

  it("accepts only the exact normalized hierarchy and selection-set protocol", () => {
    expect(parsePluginMessage(hierarchy)?.type).toBe("hierarchy");
    for (const invalid of [
      { provider: "" }, { provider: 42 }, { status: "completed" }, { status: null },
      { pendingPermissionCount: -1 }, { pendingPermissionCount: 0.5 },
      { pendingPermissionCount: Infinity }, { requiresAttention: "true" },
      { attentionReason: "unknown" }, { providerUnavailable: null },
      { status: undefined },
    ]) {
      expect(parsePluginMessage(plugin({
        ...hierarchy,
        agents: [{ ...hierarchyAgents[0], ...invalid }],
      }))).toBeNull();
    }
    expect(parsePluginMessage(plugin({ type: "selection", agentIds: ["a", "a", "b"] }))).toEqual({
      type: "selection",
      agentIds: ["a", "b"],
    });
    for (const agentIds of [null, "a", [42], [""]]) {
      expect(parsePluginMessage(plugin({ type: "selection", agentIds }))).toBeNull();
    }
    expect(
      parsePluginMessage(
        plugin({ ...hierarchy, agents: [hierarchyAgents[0], hierarchyAgents[0]] }),
      ),
    ).toBeNull();
    expect(
      parsePluginMessage(
        plugin({
          type: "navigation-error",
          context: "parent",
          sourceAgentId: WORKER.agentId,
          code: "agent-archived",
        }),
      ),
    ).toBeNull();
  });
});

describe("frame route answers without a frame", () => {
  it.each([
    ["not-configured", "no Paseo runtime configured"],
    ["unreachable", "Paseo daemon unreachable"],
    ["origin-not-listed", "embedded chat is not configured for this address"],
  ])("names %s, mounts no frame, and Retry asks the backend again", async (reason, headline) => {
    const fetchMock = stubBackend([
      { available: false, reason, detail: "detail for " + reason },
      AVAILABLE,
    ]);
    const { container, getByRole, getByTestId, queryByTestId } = render(
      <PaseoChatFrame navigationOpen taskDocuments={[]} series={[]} active scope="s" target={null} />,
    );
    await settle();

    const block = getByTestId("paseo-frame-unavailable");
    expect(block.getAttribute("data-reason")).toBe(reason);
    // The state's own sentence, then what the backend adds, then the control: each once.
    expect([...block.children].map((child) => child.textContent)).toEqual([
      headline,
      "detail for " + reason,
      "Retry",
    ]);
    expect(container.querySelector("iframe")).toBeNull();

    fireEvent.click(getByRole("button", { name: "Retry" }));
    await settle();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(queryByTestId("paseo-frame-unavailable")).toBeNull();
    expect(frameElement(container).getAttribute("src")).toBe(PROJECTS_URL);
  });

  it("mounts no frame for an answer that is not available, whatever else it carries", async () => {
    stubBackend([
      {
        available: false,
        reason: "origin-not-listed",
        detail: "x",
        frameBaseUrl: FRAME_ORIGIN,
        serverId: "srv_test",
      },
    ]);
    const { container, getByTestId } = render(
      <PaseoChatFrame navigationOpen taskDocuments={[]} series={[]} active scope="s" target={null} />,
    );
    await settle();
    expect(getByTestId("paseo-frame-unavailable").getAttribute("data-reason")).toBe(
      "origin-not-listed",
    );
    expect(container.querySelector("iframe")).toBeNull();
  });

  it("asks nothing until the pane is first active, and treats an unreadable answer as the backend failing", async () => {
    const fetchMock = stubBackend([
      { available: true, frameBaseUrl: "javascript:alert(1)", serverId: "srv" },
    ]);
    const { container, getByTestId, rerender } = render(
      <PaseoChatFrame navigationOpen taskDocuments={[]} series={[]} active={false} scope="s" target={null} />,
    );
    await settle();
    expect(fetchMock).not.toHaveBeenCalled();

    rerender(<PaseoChatFrame navigationOpen taskDocuments={[]} series={[]} active scope="s" target={null} />);
    await settle();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(getByTestId("paseo-frame-unavailable").getAttribute("data-reason")).toBe("backend");
    expect(container.querySelector("iframe")).toBeNull();
  });
});

describe("embedded frame and its control channel", () => {
  it("frames the Projects workspace with clipboard access for the embedded origin only", async () => {
    stubBackend([AVAILABLE]);
    const { container, getByTestId, queryByTestId } = render(
      <PaseoChatFrame navigationOpen taskDocuments={[]} series={[]} active scope="s" target={null} />,
    );
    await settle();

    const frame = frameElement(container);
    expect(frame.getAttribute("src")).toBe(PROJECTS_URL);
    expect(frame.getAttribute("allow")).toBe(
      `clipboard-read ${FRAME_ORIGIN}; clipboard-write ${FRAME_ORIGIN}`,
    );
    expect(frame.getAttribute("allow")).not.toContain("*");
    expect(frame.hasAttribute("allowfullscreen")).toBe(false);
    // The embedded application learns the parent's origin, and nothing more, from the referrer.
    expect(frame.getAttribute("referrerpolicy")).toBe("origin");
    expect(getByTestId("paseo-frame").getAttribute("data-control")).toBe("waiting");
    expect(getByTestId("paseo-frame-connecting")).not.toBeNull();
    expect(queryByTestId("paseo-frame-workspace-problem")).toBeNull();
  });

  it("says why when the route could not name the Projects workspace, and frames the start page", async () => {
    stubBackend([
      {
        ...AVAILABLE,
        projectsWorkspaceId: null,
        projectsWorkspaceDetail: "Directory not found: /projects",
      },
    ]);
    const { container, getByTestId } = render(
      <PaseoChatFrame navigationOpen taskDocuments={[]} series={[]} active scope="s" target={null} />,
    );
    await settle();

    expect(frameElement(container).getAttribute("src")).toBe(FRAME_ORIGIN + "/");
    expect(getByTestId("paseo-frame-workspace-problem").textContent).toBe(
      "The Projects workspace could not be opened: Directory not found: /projects",
    );
  });

  it("stays ready with nothing to show: the 10-second deadline belongs to the load, not to the pane", async () => {
    expect(PASEO_CONTROL_TIMEOUT_MS).toBe(10_000);
    stubBackend([AVAILABLE]);
    const { container, getByTestId, queryByTestId } = render(
      <PaseoChatFrame navigationOpen taskDocuments={[]} series={[]} active scope="s" target={null} />,
    );
    await settle();
    await settle(9_000);
    deliver(frameElement(container), plugin({ type: "ready" }));
    await settle(60_000);

    expect(getByTestId("paseo-frame").getAttribute("data-control")).toBe("ready");
    expect(queryByTestId("paseo-frame-control-banner")).toBeNull();
  });

  it("shows the displayed execution's agent by message, without reloading the frame", async () => {
    stubBackend([AVAILABLE]);
    const { container, getByTestId, queryByTestId, rerender } = render(
      <PaseoChatFrame navigationOpen taskDocuments={[]} series={[]} active scope="architect" target={ARCHITECT} />,
    );
    await settle();
    const frame = frameElement(container);
    const posts = watchPosts(frame);

    // Nothing is posted before the embedded application reports ready.
    expect(posts).not.toHaveBeenCalled();
    deliver(frame, plugin({ type: "ready" }));
    expect(getByTestId("paseo-frame").getAttribute("data-control")).toBe("ready");
    expect(queryByTestId("paseo-frame-connecting")).toBeNull();
    expect(posts).toHaveBeenLastCalledWith(
      { type: "ar.open", agentId: ARCHITECT.agentId },
      FRAME_ORIGIN,
    );
    deliver(frame, plugin({ type: "shown", agentId: ARCHITECT.agentId }));

    // Another execution is selected: one message, the same frame element, the same document.
    rerender(
      <PaseoChatFrame navigationOpen taskDocuments={[]} series={[]} active scope="worker" target={WORKER} />,
    );
    expect(posts).toHaveBeenLastCalledWith(
      { type: "ar.open", agentId: WORKER.agentId },
      FRAME_ORIGIN,
    );
    expect(posts).toHaveBeenCalledTimes(2);
    deliver(frame, plugin({ type: "shown", agentId: WORKER.agentId }));
    await settle(PASEO_CONTROL_TIMEOUT_MS * 2);

    expect(frameElement(container)).toBe(frame);
    expect(frame.getAttribute("src")).toBe(PROJECTS_URL);
    expect(getByTestId("paseo-frame").getAttribute("data-control")).toBe("ready");
  });

  it("follows the selection, not the reloading of launch options", async () => {
    stubBackend([AVAILABLE]);
    const { container, rerender } = render(
      <PaseoChatFrame navigationOpen taskDocuments={[]} series={[]} active scope="architect" target={ARCHITECT} />,
    );
    await settle();
    const frame = frameElement(container);
    const posts = watchPosts(frame);
    deliver(frame, plugin({ type: "ready" }));
    deliver(frame, plugin({ type: "shown", agentId: ARCHITECT.agentId }));
    expect(posts).toHaveBeenCalledTimes(1);

    // The execution is briefly absent while options reload: no second request for the same agent.
    rerender(
      <PaseoChatFrame navigationOpen taskDocuments={[]} series={[]} active scope="architect" target={null} />,
    );
    rerender(
      <PaseoChatFrame navigationOpen taskDocuments={[]} series={[]} active scope="architect" target={ARCHITECT} />,
    );
    expect(posts).toHaveBeenCalledTimes(1);

    // Another selection without an execution leaves the frame alone; choosing the first again asks again.
    rerender(
      <PaseoChatFrame navigationOpen taskDocuments={[]} series={[]} active scope="curator" target={null} />,
    );
    expect(posts).toHaveBeenCalledTimes(1);
    rerender(
      <PaseoChatFrame navigationOpen taskDocuments={[]} series={[]} active scope="architect" target={ARCHITECT} />,
    );
    expect(posts).toHaveBeenCalledTimes(2);
    expect(posts).toHaveBeenLastCalledWith(
      { type: "ar.open", agentId: ARCHITECT.agentId },
      FRAME_ORIGIN,
    );

    // The plugin restarted inside the page before answering: its new ready gets the request again.
    deliver(frame, plugin({ type: "ready" }));
    expect(posts).toHaveBeenCalledTimes(3);
    deliver(frame, plugin({ type: "shown", agentId: ARCHITECT.agentId }));
    deliver(frame, plugin({ type: "ready" }));
    expect(posts).toHaveBeenCalledTimes(3);
  });

  it("ignores messages from another origin or another window", async () => {
    stubBackend([AVAILABLE]);
    const { container, getByTestId, queryByTestId } = render(
      <PaseoChatFrame navigationOpen taskDocuments={[]} series={[]} active scope="architect" target={ARCHITECT} />,
    );
    await settle();
    const frame = frameElement(container);
    const posts = watchPosts(frame);

    deliver(frame, plugin({ type: "ready" }), { origin: "http://evil.test" });
    deliver(frame, plugin({ type: "ready" }), { origin: "http://127.0.0.1:6821" });
    // Look-alikes that begin with the frame's origin.
    deliver(frame, plugin({ type: "ready" }), { origin: FRAME_ORIGIN + "0" });
    deliver(frame, plugin({ type: "ready" }), { origin: FRAME_ORIGIN + ".evil.test" });
    deliver(frame, plugin({ type: "ready" }), { source: window });
    deliver(frame, plugin({ type: "ready" }), { source: null });
    deliver(frame, { type: "ready" });
    expect(getByTestId("paseo-frame").getAttribute("data-control")).toBe("waiting");
    expect(posts).not.toHaveBeenCalled();

    deliver(frame, plugin({ type: "ready" }));
    expect(posts).toHaveBeenCalledTimes(1);
    deliver(frame, plugin({ type: "error", code: "agent-archived", agentId: ARCHITECT.agentId }), {
      origin: "http://evil.test",
    });
    deliver(frame, plugin({ type: "shown", agentId: ARCHITECT.agentId }), { source: window });
    expect(queryByTestId("paseo-frame-agent-problem")).toBeNull();
    // The request is still unanswered for the real frame: a forged "shown" did not settle it.
    deliver(frame, plugin({ type: "ready" }));
    expect(posts).toHaveBeenCalledTimes(2);
  });

  it("takes an answer only for the request that is pending", async () => {
    stubBackend([AVAILABLE]);
    const { container, queryByTestId, getByTestId } = render(
      <PaseoChatFrame navigationOpen taskDocuments={[]} series={[]} active scope="architect" target={ARCHITECT} />,
    );
    await settle();
    const frame = frameElement(container);
    const posts = watchPosts(frame);
    deliver(frame, plugin({ type: "ready" }));
    expect(posts).toHaveBeenCalledTimes(1);

    // Answers that name another agent neither settle the request nor blame its agent.
    deliver(frame, plugin({ type: "error", code: "agent-archived", agentId: WORKER.agentId }));
    expect(queryByTestId("paseo-frame-agent-problem")).toBeNull();
    deliver(frame, plugin({ type: "shown", agentId: WORKER.agentId }));
    // Still pending: the error for the requested agent is taken.
    deliver(frame, plugin({ type: "error", code: "agent-archived", agentId: ARCHITECT.agentId }));
    expect(getByTestId("paseo-frame-agent-problem").textContent).toContain("archived");
  });

  it("ignores an error when no request is pending", async () => {
    stubBackend([AVAILABLE]);
    const { container, queryByTestId } = render(
      <PaseoChatFrame navigationOpen taskDocuments={[]} series={[]} active scope="architect" target={ARCHITECT} />,
    );
    await settle();
    const frame = frameElement(container);
    watchPosts(frame);
    deliver(frame, plugin({ type: "ready" }));
    deliver(frame, plugin({ type: "shown", agentId: ARCHITECT.agentId }));

    deliver(frame, plugin({ type: "error", code: "agent-archived", agentId: ARCHITECT.agentId }));
    deliver(frame, plugin({ type: "error", code: "open-failed" }));
    expect(queryByTestId("paseo-frame-agent-problem")).toBeNull();
  });

  it.each([
    ["agent-archived", "archived"],
    ["agent-not-found", "gone"],
  ])("says the agent is %s and leaves the frame where it is", async (code, word) => {
    stubBackend([AVAILABLE]);
    const { container, getByTestId, queryByTestId, rerender } = render(
      <PaseoChatFrame navigationOpen taskDocuments={[]} series={[]} active scope="architect" target={ARCHITECT} />,
    );
    await settle();
    const frame = frameElement(container);
    const posts = watchPosts(frame);
    deliver(frame, plugin({ type: "ready" }));
    deliver(frame, plugin({ type: "error", code, agentId: ARCHITECT.agentId, message: "x" }));

    expect(getByTestId("paseo-frame-agent-problem").textContent).toContain(word);
    await settle(PASEO_CONTROL_TIMEOUT_MS * 2);
    expect(frameElement(container)).toBe(frame);
    expect(frame.getAttribute("src")).toBe(PROJECTS_URL);
    expect(getByTestId("paseo-frame").getAttribute("data-control")).toBe("ready");
    expect(posts).toHaveBeenCalledTimes(1);

    // The notice belongs to that execution: selecting a live one clears it.
    rerender(
      <PaseoChatFrame navigationOpen taskDocuments={[]} series={[]} active scope="worker" target={WORKER} />,
    );
    expect(queryByTestId("paseo-frame-agent-problem")).toBeNull();
  });
});

describe("control channel unavailable", () => {
  it("keeps the frame, shows the banner after 10 seconds, and shows executions by URL", async () => {
    const fetchMock = stubBackend([AVAILABLE]);
    const { container, getByTestId, queryByTestId, rerender } = render(
      <PaseoChatFrame navigationOpen taskDocuments={[]} series={[]} active scope="s" target={null} />,
    );
    await settle();
    await settle(PASEO_CONTROL_TIMEOUT_MS - 1);
    expect(queryByTestId("paseo-frame-control-banner")).toBeNull();
    await settle(1);

    expect(getByTestId("paseo-frame-control-banner").textContent).toContain(
      "embedded chat control unavailable",
    );
    expect(queryByTestId("paseo-frame-connecting")).toBeNull();
    expect(frameElement(container).getAttribute("src")).toBe(PROJECTS_URL);

    // An execution is displayed: its agent's URL is loaded in the frame; nothing is posted.
    const before = frameElement(container);
    const posts = watchPosts(before);
    rerender(
      <PaseoChatFrame navigationOpen taskDocuments={[]} series={[]} active scope="worker" target={WORKER} />,
    );
    expect(posts).not.toHaveBeenCalled();
    expect(frameElement(container).getAttribute("src")).toBe(WORKER_URL);
    expect(frameElement(container)).not.toBe(before);
    expect(getByTestId("paseo-frame-control-banner")).not.toBeNull();

    // The frame's URL already names the agent: another deadline does not load it again.
    const loaded = frameElement(container);
    await settle(PASEO_CONTROL_TIMEOUT_MS * 2);
    expect(frameElement(container)).toBe(loaded);

    // A late ready report ends the state without reloading the frame. The agent is then asked
    // for by message, because only that answer tells whether it still exists.
    const latePosts = watchPosts(loaded);
    deliver(loaded, plugin({ type: "ready" }));
    expect(queryByTestId("paseo-frame-control-banner")).toBeNull();
    expect(latePosts).toHaveBeenCalledTimes(1);
    expect(latePosts).toHaveBeenCalledWith(
      { type: "ar.open", agentId: WORKER.agentId },
      FRAME_ORIGIN,
    );
    deliver(loaded, plugin({ type: "shown", agentId: WORKER.agentId }));
    expect(frameElement(container)).toBe(loaded);

    await settle(PASEO_CONTROL_TIMEOUT_MS);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(getByTestId("paseo-frame").getAttribute("data-control")).toBe("ready");
  });

  it("falls back to the agent's URL when a request is never answered, and Retry reloads the frame", async () => {
    const fetchMock = stubBackend([AVAILABLE]);
    const { container, getByRole, getByTestId } = render(
      <PaseoChatFrame navigationOpen taskDocuments={[]} series={[]} active scope="worker" target={WORKER} />,
    );
    await settle();
    const frame = frameElement(container);
    const posts = watchPosts(frame);
    deliver(frame, plugin({ type: "ready" }));
    expect(posts).toHaveBeenCalledTimes(1);

    await settle(PASEO_CONTROL_TIMEOUT_MS);
    expect(getByTestId("paseo-frame-control-banner")).not.toBeNull();
    expect(frameElement(container).getAttribute("src")).toBe(WORKER_URL);

    fireEvent.click(getByRole("button", { name: "Retry" }));
    await settle();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const reloaded = frameElement(container);
    expect(reloaded).not.toBe(frame);
    // The reloaded frame names the agent itself, so it lands there with or without the channel.
    expect(reloaded.getAttribute("src")).toBe(WORKER_URL);
    expect(getByTestId("paseo-frame").getAttribute("data-control")).toBe("waiting");
    // Once the channel is ready the agent is still asked for, so that archived or gone is said.
    const reloadedPosts = watchPosts(reloaded);
    deliver(reloaded, plugin({ type: "ready" }));
    expect(reloadedPosts).toHaveBeenCalledTimes(1);
    expect(reloadedPosts).toHaveBeenCalledWith(
      { type: "ar.open", agentId: WORKER.agentId },
      FRAME_ORIGIN,
    );
    deliver(reloaded, plugin({ type: "error", code: "agent-archived", agentId: WORKER.agentId }));
    expect(getByTestId("paseo-frame-agent-problem").textContent).toContain("archived");
    expect(frameElement(container)).toBe(reloaded);
    expect(reloaded.getAttribute("src")).toBe(WORKER_URL);
  });

  it("after Retry without the channel, the frame that names the agent is not loaded a second time", async () => {
    stubBackend([AVAILABLE]);
    const { container, getByRole, getByTestId } = render(
      <PaseoChatFrame navigationOpen taskDocuments={[]} series={[]} active scope="worker" target={WORKER} />,
    );
    await settle();
    await settle(PASEO_CONTROL_TIMEOUT_MS);
    expect(frameElement(container).getAttribute("src")).toBe(WORKER_URL);

    fireEvent.click(getByRole("button", { name: "Retry" }));
    await settle();
    const reloaded = frameElement(container);
    await settle(PASEO_CONTROL_TIMEOUT_MS);
    expect(getByTestId("paseo-frame-control-banner")).not.toBeNull();
    expect(frameElement(container)).toBe(reloaded);
  });
});

describe("Chats pane wiring", () => {
  const execution = {
    status: "running",
    requestId: "request-architect",
    canStart: true,
    canRevive: false,
    execution: { kind: "paseo-agent", serverId: "srv_test", ...ARCHITECT },
  };
  const sprintExecution = {
    status: "running",
    requestId: "request-orchestrator",
    canStart: false,
    canRevive: false,
    execution: { kind: "paseo-agent", serverId: "srv_test", ...WORKER },
  };
  const sprint = taskDoc({
    id: "SPRINT-1",
    title: "Sprint one",
    kind: "master",
    repository: "demo",
    docPath: "/coordination/tasks/demo/sprint-one/task.json",
    orchestrates: ["some-master"],
  });
  const catalog = {
    roleDefaults: { agent: "codex" },
    agents: [{ id: "codex", label: "Codex", models: [] }],
    catalogOrigin: "test",
  };

  it("steers the mounted frame and keeps it across the launcher's controlled navigation toggle", async () => {
    sessionStorage.setItem(
      tasklessRequestStorageKey("architect"),
      JSON.stringify({ requestId: execution.requestId }),
    );
    stubBackend([AVAILABLE], {
      "/api/role-launch/options": (request) =>
        request.role === "orchestrator"
          ? { ...catalog, execution: sprintExecution }
          : { ...catalog, executions: request.role === "architect" ? [execution] : [] },
      // No result is served: the taskless execution is known from the options answer alone.
      "/api/role-launch/result": { httpStatus: 404, detail: "no result" },
    });
    const { container, getByLabelText, getByRole } = render(
      <RoleChatsPane active taskDocuments={[sprint]} series={[]} />,
    );
    await settle();
    const frame = frameElement(container);
    expect(frame.getAttribute("src")).toBe(PROJECTS_URL);
    const posts = watchPosts(frame);
    deliver(frame, plugin({ type: "ready" }));
    await settle();
    expect(posts).toHaveBeenCalledTimes(1);
    expect(posts).toHaveBeenCalledWith(
      { type: "ar.open", agentId: ARCHITECT.agentId },
      FRAME_ORIGIN,
    );
    deliver(frame, plugin({ type: "shown", agentId: ARCHITECT.agentId }));

    const launcher = container.querySelector('[data-testid="role-launcher"]')!;
    const toggle = getByRole("button", { name: "Hide chat navigation" });
    expect(launcher.querySelector("button,select,input")).toBe(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(container.querySelector('[role="tablist"]')).toBeNull();
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(container.querySelector('nav[aria-label="AR chat navigation"]')).toBeNull();
    expect(frameElement(container)).toBe(frame);
    fireEvent.click(getByRole("button", { name: "Show chat navigation" }));
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    await settle();
    expect(frameElement(container)).toBe(frame);
    expect(posts).toHaveBeenCalledTimes(1);

    // Another selection without an execution leaves the frame; choosing the first selection
    // again asks for its agent again, although it is the agent that was shown last.
    fireEvent.change(getByLabelText("Role"), { target: { value: "system-specialist" } });
    await settle();
    expect(posts).toHaveBeenCalledTimes(1);
    fireEvent.change(getByLabelText("Role"), { target: { value: "architect" } });
    await settle();
    expect(posts).toHaveBeenCalledTimes(2);
    expect(posts).toHaveBeenLastCalledWith(
      { type: "ar.open", agentId: ARCHITECT.agentId },
      FRAME_ORIGIN,
    );
    deliver(frame, plugin({ type: "shown", agentId: ARCHITECT.agentId }));

    // A task-bound selection: choosing it displays its execution, and the frame follows.
    fireEvent.change(getByLabelText("Role"), { target: { value: "orchestrator" } });
    await settle();
    expect(posts).toHaveBeenCalledTimes(2);
    fireEvent.change(getByLabelText("AR sprint"), { target: { value: "0" } });
    await settle();
    expect(posts).toHaveBeenCalledTimes(3);
    expect(posts).toHaveBeenLastCalledWith(
      { type: "ar.open", agentId: WORKER.agentId },
      FRAME_ORIGIN,
    );
    expect(frameElement(container)).toBe(frame);
    expect(frame.getAttribute("src")).toBe(PROJECTS_URL);
  });

  it("follows the receipt when it names another agent than the options answer still held", async () => {
    // The options answer of the selection names one agent. The result read afterwards says the
    // execution is now another agent (it was revived elsewhere): the receipt is the newer word.
    const revived = { agentId: "agent-revived", workspaceId: "wks_leaf" };
    stubBackend([AVAILABLE], {
      "/api/role-launch/options": (request) =>
        request.role === "orchestrator"
          ? { ...catalog, execution: sprintExecution }
          : { ...catalog, executions: [] },
      "/api/role-launch/result": {
        ...sprintExecution,
        execution: { kind: "paseo-agent", serverId: "srv_test", ...revived },
      },
    });
    const { container, getByLabelText, getByRole } = render(
      <RoleChatsPane active taskDocuments={[sprint]} series={[]} />,
    );
    await settle();
    const frame = frameElement(container);
    const posts = watchPosts(frame);
    deliver(frame, plugin({ type: "ready" }));
    fireEvent.change(getByLabelText("Role"), { target: { value: "orchestrator" } });
    await settle();
    fireEvent.change(getByLabelText("AR sprint"), { target: { value: "0" } });
    await settle();
    expect(posts).toHaveBeenCalledTimes(1);
    expect(posts).toHaveBeenLastCalledWith(
      { type: "ar.open", agentId: WORKER.agentId },
      FRAME_ORIGIN,
    );
    deliver(frame, plugin({ type: "shown", agentId: WORKER.agentId }));

    fireEvent.click(getByRole("button", { name: "Refresh role result" }));
    await settle();
    expect(posts).toHaveBeenCalledTimes(2);
    expect(posts).toHaveBeenLastCalledWith(
      { type: "ar.open", agentId: revived.agentId },
      FRAME_ORIGIN,
    );
    expect(frameElement(container)).toBe(frame);
  });
});

describe("paseoFrameModel", () => {
  it("reads an agent target only from a paseo-agent execution that has a host agent", () => {
    const host = { kind: "paseo-agent", serverId: "srv", workspaceId: "wks", agentId: "a1" };
    expect(paseoAgentTarget({ status: "running", execution: host })).toEqual({
      agentId: "a1",
      workspaceId: "wks",
    });
    expect(
      paseoAgentTarget({ status: "running", execution: { kind: "paseo-agent", agentId: "a1" } }),
    ).toEqual({
      agentId: "a1",
    });
    expect(paseoAgentTarget({ status: "rejected", execution: host })).toBeNull();
    expect(
      paseoAgentTarget({ status: "running", execution: { kind: "terminal", handle: "h" } }),
    ).toBeNull();
    expect(paseoAgentTarget({ status: "starting", execution: {} })).toBeNull();
    expect(paseoAgentTarget(null)).toBeNull();
  });

  it("builds frame URLs from the route's base URL and server id", () => {
    const descriptor = parseFrameDescriptor({
      ...AVAILABLE,
      frameBaseUrl: "https://box.ts.net:8443/",
    });
    if (!descriptor.available) throw new Error("expected an available frame");
    expect(descriptor.frameOrigin).toBe("https://box.ts.net:8443");
    expect(paseoFrameUrl(descriptor, WORKER)).toBe(
      "https://box.ts.net:8443/h/srv_test/workspace/wks_leaf?open=agent:agent-worker",
    );
    expect(paseoFrameUrl(descriptor, { agentId: "a b" })).toBe(
      "https://box.ts.net:8443/h/srv_test/agent/a%20b",
    );
    expect(paseoFrameUrl(descriptor, null)).toBe(
      "https://box.ts.net:8443/h/srv_test/workspace/wks_projects",
    );
    expect(paseoFrameUrl({ ...descriptor, projectsWorkspaceId: null }, null)).toBe(
      "https://box.ts.net:8443/",
    );
  });

  it("accepts only the plugin's own message shapes", () => {
    expect(parsePluginMessage({ source: "ar-plugin", type: "ready", href: "x" })).toEqual({
      type: "ready",
    });
    expect(parsePluginMessage({ source: "ar-plugin", type: "error", agentId: "a1" })).toEqual({
      type: "error",
      code: "open-failed",
      agentId: "a1",
    });
    expect(parsePluginMessage({ source: "other", type: "ready" })).toBeNull();
    expect(parsePluginMessage({ source: "ar-plugin", type: "navigate" })).toBeNull();
    expect(parsePluginMessage("ready")).toBeNull();
    expect(parseFrameDescriptor({ available: false, reason: "made-up" })).toMatchObject({
      reason: "backend",
    });
  });
});

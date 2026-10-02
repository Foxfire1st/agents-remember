import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { taskDoc } from "../test/fixtures/wire";
import { ChatsModePanels } from "./OrcaChats";
import { PaseoChatFrame } from "./PaseoChatFrame";
import { PASEO_CONTROL_TIMEOUT_MS } from "./paseoFrameControl";
import { tasklessRequestStorageKey } from "./orcaLaunchModel";
import { paseoAgentTarget, parseFrameDescriptor, parsePluginMessage, paseoFrameUrl } from "./paseoFrameModel";

vi.mock("../panels/session-cockpit/sessions-view/SessionsView", () => ({
  SessionsView: () => <div data-testid="sessions-view" />,
}));

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
    const route = url === "/api/orca/frame" ? (queue.length > 1 ? queue.shift() : queue[0]) : other[url];
    if (!route) throw new Error("unexpected request " + url);
    const body = typeof route === "function" ? route(JSON.parse(String(init?.body ?? "{}")) as Answer) : route;
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

describe("frame route answers without a frame", () => {
  it.each([
    ["not-configured", "no Paseo runtime configured"],
    ["unreachable", "Paseo daemon unreachable"],
    ["origin-not-listed", "embedded chat is not configured for this address"],
  ])("names %s, mounts no frame, and Retry asks the backend again", async (reason, headline) => {
    const fetchMock = stubBackend([{ available: false, reason, detail: "detail for " + reason }, AVAILABLE]);
    const { container, getByRole, getByTestId, queryByTestId } = render(
      <PaseoChatFrame active scope="s" target={null} />,
    );
    await settle();

    const block = getByTestId("paseo-frame-unavailable");
    expect(block.getAttribute("data-reason")).toBe(reason);
    // The state's own sentence, then what the backend adds, then the control: each once.
    expect([...block.children].map((child) => child.textContent)).toEqual([headline, "detail for " + reason, "Retry"]);
    expect(container.querySelector("iframe")).toBeNull();

    fireEvent.click(getByRole("button", { name: "Retry" }));
    await settle();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(queryByTestId("paseo-frame-unavailable")).toBeNull();
    expect(frameElement(container).getAttribute("src")).toBe(PROJECTS_URL);
  });

  it("mounts no frame for an answer that is not available, whatever else it carries", async () => {
    stubBackend([
      { available: false, reason: "origin-not-listed", detail: "x", frameBaseUrl: FRAME_ORIGIN, serverId: "srv_test" },
    ]);
    const { container, getByTestId } = render(<PaseoChatFrame active scope="s" target={null} />);
    await settle();
    expect(getByTestId("paseo-frame-unavailable").getAttribute("data-reason")).toBe("origin-not-listed");
    expect(container.querySelector("iframe")).toBeNull();
  });

  it("asks nothing until the pane is first active, and treats an unreadable answer as the backend failing", async () => {
    const fetchMock = stubBackend([{ available: true, frameBaseUrl: "javascript:alert(1)", serverId: "srv" }]);
    const { container, getByTestId, rerender } = render(<PaseoChatFrame active={false} scope="s" target={null} />);
    await settle();
    expect(fetchMock).not.toHaveBeenCalled();

    rerender(<PaseoChatFrame active scope="s" target={null} />);
    await settle();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(getByTestId("paseo-frame-unavailable").getAttribute("data-reason")).toBe("backend");
    expect(container.querySelector("iframe")).toBeNull();
  });
});

describe("embedded frame and its control channel", () => {
  it("frames the Projects workspace with clipboard access for the embedded origin only", async () => {
    stubBackend([AVAILABLE]);
    const { container, getByTestId, queryByTestId } = render(<PaseoChatFrame active scope="s" target={null} />);
    await settle();

    const frame = frameElement(container);
    expect(frame.getAttribute("src")).toBe(PROJECTS_URL);
    expect(frame.getAttribute("allow")).toBe(`clipboard-read ${FRAME_ORIGIN}; clipboard-write ${FRAME_ORIGIN}`);
    expect(frame.getAttribute("allow")).not.toContain("*");
    expect(frame.hasAttribute("allowfullscreen")).toBe(false);
    // The embedded application learns the parent's origin, and nothing more, from the referrer.
    expect(frame.getAttribute("referrerpolicy")).toBe("origin");
    expect(getByTestId("paseo-frame").getAttribute("data-control")).toBe("waiting");
    expect(getByTestId("paseo-frame-connecting")).not.toBeNull();
    expect(queryByTestId("paseo-frame-workspace-problem")).toBeNull();
  });

  it("says why when the route could not name the Projects workspace, and frames the start page", async () => {
    stubBackend([{ ...AVAILABLE, projectsWorkspaceId: null, projectsWorkspaceDetail: "Directory not found: /projects" }]);
    const { container, getByTestId } = render(<PaseoChatFrame active scope="s" target={null} />);
    await settle();

    expect(frameElement(container).getAttribute("src")).toBe(FRAME_ORIGIN + "/");
    expect(getByTestId("paseo-frame-workspace-problem").textContent).toBe(
      "The Projects workspace could not be opened: Directory not found: /projects",
    );
  });

  it("stays ready with nothing to show: the 10-second deadline belongs to the load, not to the pane", async () => {
    expect(PASEO_CONTROL_TIMEOUT_MS).toBe(10_000);
    stubBackend([AVAILABLE]);
    const { container, getByTestId, queryByTestId } = render(<PaseoChatFrame active scope="s" target={null} />);
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
      <PaseoChatFrame active scope="architect" target={ARCHITECT} />,
    );
    await settle();
    const frame = frameElement(container);
    const posts = watchPosts(frame);

    // Nothing is posted before the embedded application reports ready.
    expect(posts).not.toHaveBeenCalled();
    deliver(frame, plugin({ type: "ready" }));
    expect(getByTestId("paseo-frame").getAttribute("data-control")).toBe("ready");
    expect(queryByTestId("paseo-frame-connecting")).toBeNull();
    expect(posts).toHaveBeenLastCalledWith({ type: "ar.open", agentId: ARCHITECT.agentId }, FRAME_ORIGIN);
    deliver(frame, plugin({ type: "shown", agentId: ARCHITECT.agentId }));

    // Another execution is selected: one message, the same frame element, the same document.
    rerender(<PaseoChatFrame active scope="worker" target={WORKER} />);
    expect(posts).toHaveBeenLastCalledWith({ type: "ar.open", agentId: WORKER.agentId }, FRAME_ORIGIN);
    expect(posts).toHaveBeenCalledTimes(2);
    deliver(frame, plugin({ type: "shown", agentId: WORKER.agentId }));
    await settle(PASEO_CONTROL_TIMEOUT_MS * 2);

    expect(frameElement(container)).toBe(frame);
    expect(frame.getAttribute("src")).toBe(PROJECTS_URL);
    expect(getByTestId("paseo-frame").getAttribute("data-control")).toBe("ready");
  });

  it("follows the selection, not the reloading of launch options", async () => {
    stubBackend([AVAILABLE]);
    const { container, rerender } = render(<PaseoChatFrame active scope="architect" target={ARCHITECT} />);
    await settle();
    const frame = frameElement(container);
    const posts = watchPosts(frame);
    deliver(frame, plugin({ type: "ready" }));
    deliver(frame, plugin({ type: "shown", agentId: ARCHITECT.agentId }));
    expect(posts).toHaveBeenCalledTimes(1);

    // The execution is briefly absent while options reload: no second request for the same agent.
    rerender(<PaseoChatFrame active scope="architect" target={null} />);
    rerender(<PaseoChatFrame active scope="architect" target={ARCHITECT} />);
    expect(posts).toHaveBeenCalledTimes(1);

    // Another selection without an execution leaves the frame alone; choosing the first again asks again.
    rerender(<PaseoChatFrame active scope="curator" target={null} />);
    expect(posts).toHaveBeenCalledTimes(1);
    rerender(<PaseoChatFrame active scope="architect" target={ARCHITECT} />);
    expect(posts).toHaveBeenCalledTimes(2);
    expect(posts).toHaveBeenLastCalledWith({ type: "ar.open", agentId: ARCHITECT.agentId }, FRAME_ORIGIN);

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
      <PaseoChatFrame active scope="architect" target={ARCHITECT} />,
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
      <PaseoChatFrame active scope="architect" target={ARCHITECT} />,
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
    const { container, queryByTestId } = render(<PaseoChatFrame active scope="architect" target={ARCHITECT} />);
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
      <PaseoChatFrame active scope="architect" target={ARCHITECT} />,
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
    rerender(<PaseoChatFrame active scope="worker" target={WORKER} />);
    expect(queryByTestId("paseo-frame-agent-problem")).toBeNull();
  });
});

describe("control channel unavailable", () => {
  it("keeps the frame, shows the banner after 10 seconds, and shows executions by URL", async () => {
    const fetchMock = stubBackend([AVAILABLE]);
    const { container, getByTestId, queryByTestId, rerender } = render(
      <PaseoChatFrame active scope="s" target={null} />,
    );
    await settle();
    await settle(PASEO_CONTROL_TIMEOUT_MS - 1);
    expect(queryByTestId("paseo-frame-control-banner")).toBeNull();
    await settle(1);

    expect(getByTestId("paseo-frame-control-banner").textContent).toContain("embedded chat control unavailable");
    expect(queryByTestId("paseo-frame-connecting")).toBeNull();
    expect(frameElement(container).getAttribute("src")).toBe(PROJECTS_URL);

    // An execution is displayed: its agent's URL is loaded in the frame; nothing is posted.
    const before = frameElement(container);
    const posts = watchPosts(before);
    rerender(<PaseoChatFrame active scope="worker" target={WORKER} />);
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
    expect(latePosts).toHaveBeenCalledWith({ type: "ar.open", agentId: WORKER.agentId }, FRAME_ORIGIN);
    deliver(loaded, plugin({ type: "shown", agentId: WORKER.agentId }));
    expect(frameElement(container)).toBe(loaded);

    await settle(PASEO_CONTROL_TIMEOUT_MS);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(getByTestId("paseo-frame").getAttribute("data-control")).toBe("ready");
  });

  it("falls back to the agent's URL when a request is never answered, and Retry reloads the frame", async () => {
    const fetchMock = stubBackend([AVAILABLE]);
    const { container, getByRole, getByTestId } = render(<PaseoChatFrame active scope="worker" target={WORKER} />);
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
    expect(reloadedPosts).toHaveBeenCalledWith({ type: "ar.open", agentId: WORKER.agentId }, FRAME_ORIGIN);
    deliver(reloaded, plugin({ type: "error", code: "agent-archived", agentId: WORKER.agentId }));
    expect(getByTestId("paseo-frame-agent-problem").textContent).toContain("archived");
    expect(frameElement(container)).toBe(reloaded);
    expect(reloaded.getAttribute("src")).toBe(WORKER_URL);
  });

  it("after Retry without the channel, the frame that names the agent is not loaded a second time", async () => {
    stubBackend([AVAILABLE]);
    const { container, getByRole, getByTestId } = render(<PaseoChatFrame active scope="worker" target={WORKER} />);
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

  it("steers the mounted frame to the launcher's execution and keeps it across mode switches", async () => {
    sessionStorage.setItem(tasklessRequestStorageKey("architect"), JSON.stringify({ requestId: execution.requestId }));
    stubBackend([AVAILABLE], {
      "/api/orca/launcher/options": (request) =>
        request.role === "orchestrator"
          ? { ...catalog, execution: sprintExecution }
          : { ...catalog, executions: request.role === "architect" ? [execution] : [] },
      // No result is served: the taskless execution is known from the options answer alone.
      "/api/orca/result": { httpStatus: 404, detail: "no result" },
    });
    const { container, getByLabelText, getByRole } = render(
      <ChatsModePanels
        active
        selectedLifecycleId={undefined}
        selectedLeafKey={undefined}
        taskDocuments={[sprint]}
        series={[]}
        contextMaster={undefined}
      />,
    );
    await settle();
    const frame = frameElement(container);
    expect(frame.getAttribute("src")).toBe(PROJECTS_URL);
    const posts = watchPosts(frame);
    deliver(frame, plugin({ type: "ready" }));
    await settle();
    expect(posts).toHaveBeenCalledTimes(1);
    expect(posts).toHaveBeenCalledWith({ type: "ar.open", agentId: ARCHITECT.agentId }, FRAME_ORIGIN);
    deliver(frame, plugin({ type: "shown", agentId: ARCHITECT.agentId }));

    // Both chat modes stay mounted: the frame is the same element after a round trip.
    fireEvent.click(getByRole("tab", { name: "AR Sessions" }));
    fireEvent.click(getByRole("tab", { name: "Orca" }));
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
    expect(posts).toHaveBeenLastCalledWith({ type: "ar.open", agentId: ARCHITECT.agentId }, FRAME_ORIGIN);
    deliver(frame, plugin({ type: "shown", agentId: ARCHITECT.agentId }));

    // A task-bound selection: choosing it displays its execution, and the frame follows.
    fireEvent.change(getByLabelText("Role"), { target: { value: "orchestrator" } });
    await settle();
    expect(posts).toHaveBeenCalledTimes(2);
    fireEvent.change(getByLabelText("AR sprint"), { target: { value: "0" } });
    await settle();
    expect(posts).toHaveBeenCalledTimes(3);
    expect(posts).toHaveBeenLastCalledWith({ type: "ar.open", agentId: WORKER.agentId }, FRAME_ORIGIN);
    expect(frameElement(container)).toBe(frame);
    expect(frame.getAttribute("src")).toBe(PROJECTS_URL);
  });
});

describe("paseoFrameModel", () => {
  it("reads an agent target only from a paseo-agent execution that has a host agent", () => {
    const host = { kind: "paseo-agent", serverId: "srv", workspaceId: "wks", agentId: "a1" };
    expect(paseoAgentTarget({ status: "running", execution: host })).toEqual({ agentId: "a1", workspaceId: "wks" });
    expect(paseoAgentTarget({ status: "running", execution: { kind: "paseo-agent", agentId: "a1" } })).toEqual({
      agentId: "a1",
    });
    expect(paseoAgentTarget({ status: "rejected", execution: host })).toBeNull();
    expect(paseoAgentTarget({ status: "running", execution: { kind: "terminal", handle: "h" } })).toBeNull();
    expect(paseoAgentTarget({ status: "starting", execution: {} })).toBeNull();
    expect(paseoAgentTarget(null)).toBeNull();
  });

  it("builds frame URLs from the route's base URL and server id", () => {
    const descriptor = parseFrameDescriptor({ ...AVAILABLE, frameBaseUrl: "https://box.ts.net:8443/" });
    if (!descriptor.available) throw new Error("expected an available frame");
    expect(descriptor.frameOrigin).toBe("https://box.ts.net:8443");
    expect(paseoFrameUrl(descriptor, WORKER)).toBe(
      "https://box.ts.net:8443/h/srv_test/workspace/wks_leaf?open=agent:agent-worker",
    );
    expect(paseoFrameUrl(descriptor, { agentId: "a b" })).toBe("https://box.ts.net:8443/h/srv_test/agent/a%20b");
    expect(paseoFrameUrl(descriptor, null)).toBe("https://box.ts.net:8443/h/srv_test/workspace/wks_projects");
    expect(paseoFrameUrl({ ...descriptor, projectsWorkspaceId: null }, null)).toBe("https://box.ts.net:8443/");
  });

  it("accepts only the plugin's own message shapes", () => {
    expect(parsePluginMessage({ source: "ar-plugin", type: "ready", href: "x" })).toEqual({ type: "ready" });
    expect(parsePluginMessage({ source: "ar-plugin", type: "error", agentId: "a1" })).toEqual({
      type: "error",
      code: "open-failed",
      agentId: "a1",
    });
    expect(parsePluginMessage({ source: "other", type: "ready" })).toBeNull();
    expect(parsePluginMessage({ source: "ar-plugin", type: "navigate" })).toBeNull();
    expect(parsePluginMessage("ready")).toBeNull();
    expect(parseFrameDescriptor({ available: false, reason: "made-up" })).toMatchObject({ reason: "backend" });
  });
});

// A change of the selection never unloads the chat. A late answer is waited for, the
// frame loads again only when the application answers nothing at all, and an older plug-in is
// named and still switches.
import { act, cleanup, render, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { installBridge, type BridgeClient } from "../../../mcp/src/agents_remember/package_data/paseo_plugin/client/bridge";
import { DASHBOARD, emptyHierarchyClient, newTab, pageLoad } from "../test/paseoPluginPage";
import { PaseoChatFrame } from "./PaseoChatFrame";
import { PASEO_CONTROL_TIMEOUT_MS, PASEO_UNAVAILABLE_QUIET_MS } from "./paseoFrameControl";

const FRAME_ORIGIN = "http://127.0.0.1:6820";
const COMMAND = "agents-remember paseo provision --config /home/u/.config/agents-remember/settings.json";
const AVAILABLE = {
  available: true,
  frameBaseUrl: FRAME_ORIGIN,
  serverId: "srv_test",
  projectsWorkspaceId: "wks_projects",
  projectsWorkspaceDetail: null,
  provisionCommand: COMMAND,
};
const MANAGER = { agentId: "agent-manager", workspaceId: "wks_projects" };
const WORKER = { agentId: "agent-worker", workspaceId: "wks_leaf" };
const OTHER = { agentId: "agent-other", workspaceId: "wks_other" };
const WORKER_URL = FRAME_ORIGIN + "/h/srv_test/workspace/wks_leaf?open=agent:agent-worker&arPage=document";
/** What the plug-in of this build says it can do. */
const CURRENT = ["open-agent", "ping", "page", "shown-on-display", "navigated", "on-chat", "load-failed", "pick"];
const openOf = (target: { agentId: string; workspaceId: string }) => [{ type: "ar.open", ...target }, FRAME_ORIGIN];
const PING = [{ type: "ar.ping" }, FRAME_ORIGIN];

async function settle(ms = 0): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

/** The frame as it is mounted now, with every message the dashboard posts into it recorded. */
function mountedFrame(container: HTMLElement) {
  const frame = container.querySelector("iframe");
  if (!frame?.contentWindow) throw new Error("no frame is mounted");
  const posts = vi.fn();
  frame.contentWindow.postMessage = posts as unknown as Window["postMessage"];
  const says = (message: Record<string, unknown>) =>
    act(() => {
      window.dispatchEvent(
        new MessageEvent("message", {
          data: { source: "ar-plugin", ...message },
          origin: FRAME_ORIGIN,
          source: frame.contentWindow,
        }),
      );
    });
  return { frame, posts, says };
}

/** The rail's chat on the Manager's chat, shown and settled, with a plug-in that said `capabilities`. */
async function shownOnManager(capabilities?: string[]) {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, json: async () => AVAILABLE }) as Response));
  const view = render(<PaseoChatFrame active scope="master" target={MANAGER} page="document" />);
  await settle();
  const mounted = mountedFrame(view.container);
  mounted.says({ type: "ready", ...(capabilities ? { capabilities } : {}) });
  mounted.says({ type: "shown", agentId: MANAGER.agentId });
  const show = (scope: string, target: typeof MANAGER) =>
    view.rerender(<PaseoChatFrame active scope={scope} target={target} page="document" />);
  const loads = () => view.getByTestId("paseo-frame").getAttribute("data-generation");
  return { ...view, ...mounted, show, loads };
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("a change of the selection while the host is slow", () => {
  it("waits for a late answer without loading the frame, and the next change goes out by message", async () => {
    const { frame, posts, says, show, loads, getByTestId, queryByTestId, container } = await shownOnManager(CURRENT);
    // The plug-in said what it can do: no notice, and the dashboard said what it can do in turn.
    expect(queryByTestId("paseo-frame-plugin-notice")).toBeNull();
    expect(posts).toHaveBeenCalledWith({ type: "ar.hello", capabilities: ["late-answer", "navigated", "on-chat", "load-failed", "pick", "showing"] }, FRAME_ORIGIN);
    expect(queryByTestId("paseo-frame-waiting")).toBeNull();
    expect(frame.hasAttribute("inert")).toBe(false);

    show("leaf", WORKER);
    expect(posts).toHaveBeenLastCalledWith(...openOf(WORKER));
    // Until the other chat is shown the earlier one stays, out of reach, under one line that
    // names what is waited for: first the host's answer, then the application's drawing.
    expect(frame.hasAttribute("inert")).toBe(true);
    expect(getByTestId("paseo-frame-waiting").textContent).toContain("host to answer");
    says({ type: "navigated", agentId: WORKER.agentId });
    expect(getByTestId("paseo-frame-waiting").textContent).toContain("application to draw");

    // The deadline passes: the application is asked whether it is there, and it is.
    await settle(PASEO_CONTROL_TIMEOUT_MS);
    expect(posts).toHaveBeenLastCalledWith(...PING);
    says({ type: "pong", capabilities: CURRENT });
    const waiting = getByTestId("paseo-frame-waiting");
    expect(waiting.textContent).toContain("Still waiting");
    expect(within(waiting).getByRole("button", { name: "Retry" })).not.toBeNull();
    expect(getByTestId("paseo-frame").getAttribute("data-control")).toBe("ready");
    expect(queryByTestId("paseo-frame-control-banner")).toBeNull();
    expect(queryByTestId("paseo-frame-agent-problem")).toBeNull();
    // The host answers after 30 seconds; a ping went out at each deadline in between.
    await settle(PASEO_CONTROL_TIMEOUT_MS);
    says({ type: "pong", capabilities: CURRENT });
    await settle(PASEO_CONTROL_TIMEOUT_MS);
    expect(posts.mock.calls.filter(([message]) => message.type === "ar.ping")).toHaveLength(3);
    says({ type: "pong", capabilities: CURRENT });
    // The request was sent once: a pong never asks for the agent again.
    expect(posts.mock.calls.filter(([message]) => message.type === "ar.open")).toHaveLength(2);
    says({ type: "shown", agentId: WORKER.agentId });
    expect(queryByTestId("paseo-frame-waiting")).toBeNull();
    expect(frame.hasAttribute("inert")).toBe(false);
    expect(loads()).toBe("1");

    show("other", OTHER);
    expect(posts).toHaveBeenLastCalledWith(...openOf(OTHER));
    expect(container.querySelector("iframe")).toBe(frame);
    expect(loads()).toBe("1");
  });

  it("loads the frame once when the application answers nothing at all, and switches by message again", async () => {
    const { frame, posts, show, loads, getByTestId, queryByTestId, container } = await shownOnManager(CURRENT);
    show("leaf", WORKER);
    await settle(PASEO_CONTROL_TIMEOUT_MS);
    expect(posts).toHaveBeenLastCalledWith(...PING);
    // The ping has the same deadline; until it passes nothing is loaded.
    await settle(PASEO_CONTROL_TIMEOUT_MS - 1);
    expect(loads()).toBe("1");
    await settle(1);

    expect(loads()).toBe("2");
    expect(getByTestId("paseo-frame-control-banner")).not.toBeNull();
    expect(getByTestId("paseo-frame-agent-problem").textContent).toContain("answered nothing");
    const reloaded = mountedFrame(container);
    expect(reloaded.frame).not.toBe(frame);
    expect(reloaded.frame.getAttribute("src")).toBe(WORKER_URL);
    // The loaded document holds no earlier chat: it is in reach while it is asked to confirm.
    reloaded.says({ type: "ready", capabilities: CURRENT });
    expect(queryByTestId("paseo-frame-control-banner")).toBeNull();
    expect(reloaded.posts).toHaveBeenLastCalledWith(...openOf(WORKER));
    expect(reloaded.frame.hasAttribute("inert")).toBe(false);
    reloaded.says({ type: "shown", agentId: WORKER.agentId });

    show("other", OTHER);
    expect(reloaded.posts).toHaveBeenLastCalledWith(...openOf(OTHER));
    reloaded.says({ type: "shown", agentId: OTHER.agentId });
    await settle(PASEO_CONTROL_TIMEOUT_MS * 3);
    expect(container.querySelector("iframe")).toBe(reloaded.frame);
    expect(loads()).toBe("2");
  });

  it("holds a change made right after that load for the loaded document's ready report, and loads again only on another unanswered ping", async () => {
    const { show, loads, getByTestId, container } = await shownOnManager(CURRENT);
    // The schedule: a request at 1 s, its ping at 11 s, the one load at 21 s, another
    // selection one millisecond later, before the loaded document said anything.
    await settle(1000);
    show("leaf", WORKER);
    await settle(PASEO_CONTROL_TIMEOUT_MS * 2);
    expect(loads()).toBe("2");
    const reloaded = mountedFrame(container);
    await settle(1);
    show("other", OTHER);
    await settle(PASEO_UNAVAILABLE_QUIET_MS * 2);
    // Held: no second load, the frame out of reach, and nothing asked by message yet.
    expect(loads()).toBe("2");
    expect(container.querySelector("iframe")).toBe(reloaded.frame);
    expect(reloaded.frame.hasAttribute("inert")).toBe(true);
    expect(getByTestId("paseo-frame-waiting")).not.toBeNull();
    expect(reloaded.posts).not.toHaveBeenCalled();

    // The loaded document reports ready: the held selection goes out by message.
    reloaded.says({ type: "ready", capabilities: CURRENT });
    expect(reloaded.posts).toHaveBeenLastCalledWith(...openOf(OTHER));
    reloaded.says({ type: "shown", agentId: OTHER.agentId });
    expect(loads()).toBe("2");

    // The same road with a loaded document that stays silent: its deadline asks by a ping,
    // and only that ping's silence loads the held selection, once.
    show("leaf", WORKER);
    await settle(PASEO_CONTROL_TIMEOUT_MS * 2);
    expect(loads()).toBe("3");
    const silent = mountedFrame(container);
    show("other", OTHER);
    await settle(PASEO_CONTROL_TIMEOUT_MS - 2);
    expect(silent.posts).not.toHaveBeenCalled();
    await settle(2);
    expect(silent.posts.mock.calls).toEqual([PING]);
    await settle(PASEO_CONTROL_TIMEOUT_MS - 1);
    expect(loads()).toBe("3");
    await settle(1);
    expect(loads()).toBe("4");
    expect(container.querySelector("iframe")?.getAttribute("src")).toContain("open=agent:" + OTHER.agentId);
    await settle(PASEO_CONTROL_TIMEOUT_MS * 4);
    expect(loads()).toBe("4");
  });
});

describe("a load whose ready report does not come", () => {
  /** A document page for the Worker, mounted, with nothing said by the application yet. */
  async function mountedSilent() {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, json: async () => AVAILABLE }) as Response));
    const view = render(<PaseoChatFrame active scope="leaf" target={WORKER} page="document" />);
    await settle();
    const loads = () => view.getByTestId("paseo-frame").getAttribute("data-generation");
    return { ...view, ...mountedFrame(view.container), loads };
  }

  it.each([
    ["its late ready report", { type: "ready", capabilities: CURRENT }],
    ["its pong", { type: "pong", capabilities: CURRENT }],
  ])("asks by a ping at the deadline and loads nothing: the application answers with %s", async (_case, answer) => {
    const { frame, posts, says, loads, getByTestId, queryByTestId, container } = await mountedSilent();
    await settle(PASEO_CONTROL_TIMEOUT_MS - 1);
    expect(posts).not.toHaveBeenCalled();
    await settle(1);
    expect(posts.mock.calls).toEqual([PING]);
    expect(queryByTestId("paseo-frame-control-banner")).toBeNull();
    expect(queryByTestId("paseo-frame-agent-problem")).toBeNull();
    expect(loads()).toBe("1");

    // The application is there, four seconds after the ping: no load, and the agent is asked for.
    await settle(4000);
    says(answer);
    expect(getByTestId("paseo-frame").getAttribute("data-control")).toBe("ready");
    expect(posts).toHaveBeenLastCalledWith(...openOf(WORKER));
    says({ type: "shown", agentId: WORKER.agentId });
    await settle(PASEO_CONTROL_TIMEOUT_MS * 3);
    expect(container.querySelector("iframe")).toBe(frame);
    expect(loads()).toBe("1");
  });

  it("counts the channel as unavailable only when that ping stays unanswered too, and then loads nothing more", async () => {
    const { frame, posts, loads, getByTestId, queryByTestId, container } = await mountedSilent();
    await settle(PASEO_CONTROL_TIMEOUT_MS * 2 - 1);
    expect(queryByTestId("paseo-frame-control-banner")).toBeNull();
    await settle(1);
    expect(getByTestId("paseo-frame-control-banner")).not.toBeNull();
    // The frame was loaded on the agent's address: nothing is loaded again, and nothing runs on.
    expect(getByTestId("paseo-frame-agent-problem").textContent).toContain("already shows the agent's page");
    await settle(PASEO_CONTROL_TIMEOUT_MS * 20);
    expect(container.querySelector("iframe")).toBe(frame);
    expect(loads()).toBe("1");
    expect(posts.mock.calls).toEqual([PING]);
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe("input while a conversation still loads", () => {
  it("is let through when the application says it is on the chat, while the line stays until the chat is shown", async () => {
    const { frame, says, show, getByTestId, queryByTestId } = await shownOnManager(CURRENT);
    show("leaf", WORKER);
    says({ type: "navigated", agentId: WORKER.agentId });
    // On another chat than the asked one: nothing changes.
    says({ type: "on-chat", agentId: OTHER.agentId });
    expect(frame.hasAttribute("inert")).toBe(true);

    says({ type: "on-chat", agentId: WORKER.agentId });
    expect(frame.hasAttribute("inert")).toBe(false);
    expect(frame.style.pointerEvents).toBe("");
    expect(getByTestId("paseo-frame-waiting").textContent).toContain("application to draw");

    says({ type: "shown", agentId: WORKER.agentId });
    expect(queryByTestId("paseo-frame-waiting")).toBeNull();
    expect(frame.hasAttribute("inert")).toBe(false);

    // The next change is held again from its click on.
    show("other", OTHER);
    expect(frame.hasAttribute("inert")).toBe(true);
  });

  it("ends the wait when the application shows that the host could not load the agent: the line goes and its own Retry is in reach", async () => {
    const { frame, posts, says, show, loads, getByTestId, queryByTestId } = await shownOnManager(CURRENT);
    show("leaf", WORKER);
    says({ type: "on-chat", agentId: WORKER.agentId });
    expect(getByTestId("paseo-frame-waiting")).not.toBeNull();
    // For another agent than the asked one it means nothing.
    says({ type: "load-failed", agentId: OTHER.agentId });
    expect(getByTestId("paseo-frame-waiting")).not.toBeNull();

    says({ type: "load-failed", agentId: WORKER.agentId });
    // The application's pane and its Retry stand alone: no line, no notice of the dashboard's
    // own, and the frame takes pointer and keyboard.
    expect(queryByTestId("paseo-frame-waiting")).toBeNull();
    expect(queryByTestId("paseo-frame-agent-problem")).toBeNull();
    expect(queryByTestId("paseo-frame-control-banner")).toBeNull();
    expect(frame.hasAttribute("inert")).toBe(false);
    expect(frame.style.pointerEvents).toBe("");
    // Nothing is waited for any more: no ping at the deadline, no load.
    const asked = posts.mock.calls.length;
    await settle(PASEO_CONTROL_TIMEOUT_MS * 3);
    expect(posts.mock.calls).toHaveLength(asked);
    expect(queryByTestId("paseo-frame-waiting")).toBeNull();
    expect(loads()).toBe("1");
  });
});

describe("a plug-in that is older than the dashboard", () => {
  it.each([
    ["says nothing about itself", undefined, "does not say what it can do", true],
    ["says less than the dashboard uses", ["open-agent", "page"], "ping, shown-on-display, navigated, on-chat, load-failed, pick.", true],
    ["cannot pick chat text", CURRENT.filter((ability) => ability !== "pick"), "it lacks pick.", false],
    ["only cannot tell when it is on a chat", CURRENT.filter((ability) => ability !== "on-chat"), "it lacks on-chat.", false],
  ])("is named with the provisioning command and still switches by message when it %s", async (_case, capabilities, lack, answersEarly) => {
    const { frame, posts, show, loads, getByTestId, container } = await shownOnManager(capabilities);
    const notice = getByTestId("paseo-frame-plugin-notice").textContent ?? "";
    expect(notice).toContain("older than this dashboard");
    expect(notice).toContain(lack);
    expect(notice).toContain(COMMAND);
    // A plug-in that answers when the screen is opened: the notice says what that does to the line.
    expect(notice.includes("With this plug-in the waiting line ends before the conversation is drawn.")).toBe(answersEarly);

    show("leaf", WORKER);
    expect(posts).toHaveBeenLastCalledWith(...openOf(WORKER));
    expect(container.querySelector("iframe")).toBe(frame);
    expect(loads()).toBe("1");
  });

  it("is named when it cannot show a chat on request, and each chat is then loaded by its address", async () => {
    const { posts, show, loads, getByTestId } = await shownOnManager(["ping", "page"]);
    const notice = getByTestId("paseo-frame-plugin-notice").textContent ?? "";
    expect(notice).toContain("cannot show a chat on request");
    expect(notice).toContain(COMMAND);
    expect(getByTestId("paseo-frame").getAttribute("data-control")).toBe("unavailable");
    expect(posts.mock.calls.filter(([message]) => message.type === "ar.open")).toHaveLength(0);

    const before = Number(loads());
    show("leaf", WORKER);
    await settle(PASEO_UNAVAILABLE_QUIET_MS);
    expect(Number(loads())).toBe(before + 1);
    expect(getByTestId("paseo-frame").querySelector("iframe")?.getAttribute("src")).toBe(WORKER_URL);
  });
});

describe("what the plug-in sends a dashboard", () => {
  it("tells that it asked the application to navigate only to a dashboard that said it reads that", async () => {
    const catalog = emptyHierarchyClient();
    const client: BridgeClient = {
      ...catalog,
      openScreen: () => {},
      paseo: {
        ...catalog.paseo,
        agents: { ...catalog.paseo.agents, ref: () => ({ archivedAt: null, workspaceId: "wks_leaf", refresh: async () => ({}) }) },
        workspaces: { ...catalog.paseo.workspaces, ref: () => ({ refresh: async () => ({}) }) },
      },
    };
    const load = pageLoad(newTab());
    installBridge(client, load.page, DASHBOARD);
    await settle();
    const sent = () => load.posted.map((entry) => entry.data).filter((data) => !["hierarchy", "selection"].includes(String(data.type)));
    load.posted.length = 0;

    // A dashboard that says nothing about itself is an older build: it gets what it always got.
    load.receive({ type: "ar.open", agentId: "live" });
    await settle();
    expect(sent()).toEqual([]);

    load.receive({ type: "ar.hello", capabilities: ["late-answer", "navigated"] });
    load.receive({ type: "ar.open", agentId: "live" });
    await settle();
    expect(sent()).toEqual([{ source: "ar-plugin", type: "navigated", agentId: "live" }]);
  });
});

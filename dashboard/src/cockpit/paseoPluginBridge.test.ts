// The control channel of the AR plugin's client part: what the bridge answers the dashboard that
// frames the application, and when it says an agent's chat is shown. Its source lives with the
// plugin, under mcp/src/agents_remember/package_data/paseo_plugin/client/; the other parts of
// the client are covered in paseoPluginClient.test.ts and paseoPluginLook.test.ts.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  installBridge,
  PLUGIN_ABILITIES,
  SHOWING_SETTLE_MS,
  type BridgeClient,
} from "../../../mcp/src/agents_remember/package_data/paseo_plugin/client/bridge";
import {
  DAEMON,
  DASHBOARD,
  emptyHierarchyClient,
  newTab,
  pageLoad,
  type Posted,
  type Tab,
} from "../test/paseoPluginPage";

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  document.head.innerHTML = "";
  document.body.innerHTML = "";
  localStorage.clear();
});

describe("the control channel inside the frame", () => {
  function fakeClient() {
    const catalog = emptyHierarchyClient();
    const opened: Array<Record<string, string> | undefined> = [];
    const agents: Record<string, { archivedAt: string | null; workspaceId: string } | Error> = {
      live: { archivedAt: null, workspaceId: "wks_leaf" },
      archived: { archivedAt: "2026-10-02T01:00:00.000Z", workspaceId: "wks_leaf" },
      broken: new Error("socket closed"),
    };
    const client: BridgeClient = {
      ...catalog,
      openScreen: (input) => opened.push(input.params),
      paseo: {
        ...catalog.paseo,
        agents: {
          ...catalog.paseo.agents,
          ref: (agentId) => {
            const known = agents[agentId];
            const handle = { archivedAt: null as unknown, workspaceId: null as string | null, refresh: async () => {
              if (known instanceof Error) throw known;
              if (!known) throw new Error("Agent not found: " + agentId);
              handle.archivedAt = known.archivedAt;
              handle.workspaceId = known.workspaceId;
              return { agent: known };
            } };
            return handle;
          },
        },
        workspaces: {
          ...catalog.paseo.workspaces,
          ref: (workspaceId) => ({
            refresh: async () => {
              if (workspaceId === "wks_broken") throw new Error("socket closed");
              return workspaceId === "wks_leaf" ? { id: workspaceId } : null;
            },
          }),
        },
      },
    };
    return { client, opened };
  }
  const answers = (posted: Posted[]) => posted.map((entry) => entry.data).filter((data) => !["hierarchy", "selection"].includes(String(data.type)));
  const flush = () => vi.advanceTimersByTimeAsync(0);
  /** Visible native tabs, as the app shows them on wide layouts, with the mounted agent chat. */
  const showTabs = (ids: string[]) => {
    document.body.innerHTML = ids.map((id) => `<button data-testid="workspace-tab-agent_${id}" aria-selected="true"></button>`).join("") +
      (ids.length > 0 ? `<div id="agent-chat-scroll-web-dom-scroll">Chat</div>` : "");
    for (const node of document.querySelectorAll("button, div")) {
      Object.assign(node, { checkVisibility: () => true, getBoundingClientRect: () => ({ width: 160, height: 28 }) });
    }
  };
  /**
   * The compact layout below the desktop breakpoint: a switcher trigger instead of tabs, with
   * the mounted agent chat scroll node (live web id `agent-chat-scroll-web-dom-scroll`; the
   * bundle also carries the native literal), and optionally the loading pane instead of the
   * chat. Pinned @getpaseo/server 0.11.1.
   */
  const showCompact = (mode: "chat" | "loading" = "chat") => {
    document.body.innerHTML = `<button data-testid="workspace-tab-switcher-trigger" aria-label="tabs"></button>` +
      (mode === "chat" ? `<div id="agent-chat-scroll-web-dom-scroll">Chat</div>` : `<div data-testid="agent-loading"></div>`);
    for (const node of document.querySelectorAll("button, div")) {
      Object.assign(node, { checkVisibility: () => true, getBoundingClientRect: () => ({ width: 160, height: 28 }) });
    }
  };
  /** A later DOM mutation batch: the application's answer to the open, observed after it. */
  const touchDom = () => {
    // `class` is on both observers' attribute filter; the value itself changes no selection.
    const target = document.querySelector("button") ?? document.documentElement;
    target.setAttribute("class", `${target.getAttribute("class") ?? ""} touched`.trim());
  };
  /** The frame's location after the application resolved the agent to its workspace. */
  const LEAF_URL = DAEMON + "/h/srv_test/workspace/wks_leaf";
  /**
   * The application's stored tab layout for the leaf workspace, as the pinned web build
   * persists it: `{ state: { layoutByWorkspace: { "<server>:<workspace>": ... } } }`.
   */
  const seedLayout = (tab: Tab, focusedTabId: string | null, workspaceKey = "srv_test:wks_leaf") => {
    tab.local.put("workspace-layout-state", {
      state: {
        layoutByWorkspace: {
          [workspaceKey]: {
            root: {
              kind: "group",
              group: {
                id: "workspace-root",
                children: [
                  { kind: "pane", pane: { id: "main", tabIds: focusedTabId ? [focusedTabId] : [], focusedTabId, tabs: [] } },
                ],
              },
            },
            focusedPaneId: "main",
          },
        },
      },
      version: 2,
    });
  };

  it("reports ready to the listed parent only, leaves native navigation visible, and stops cleanly", () => {
    document.body.innerHTML = '<div data-testid="composer-dock-header" style="display:flex"><button data-testid="menu-button"></button></div>';
    const header = document.querySelector('[data-testid="composer-dock-header"]') as HTMLElement;
    const load = pageLoad(newTab());
    const stop = installBridge(fakeClient().client, load.page, DASHBOARD);

    expect(answers(load.posted)).toEqual([{ source: "ar-plugin", type: "ready", capabilities: PLUGIN_ABILITIES }]);
    expect(document.getElementById("ar-plugin-embed-style")).toBeNull();
    expect(getComputedStyle(header).display).toBe("flex");
    expect(header.querySelector('[data-testid="menu-button"]')).not.toBeNull();
    expect(load.listening()).toBe(1);

    stop();
    expect(load.listening()).toBe(0);
    expect(document.getElementById("ar-plugin-embed-style")).toBeNull();
  });

  it("answers the parent and nobody else", async () => {
    const load = pageLoad(newTab());
    const { client, opened } = fakeClient();
    installBridge(client, load.page, DASHBOARD);
    await flush();
    load.posted.length = 0;

    load.receive({ type: "ar.ping" }, { origin: "http://evil.test" });
    load.receive({ type: "ar.ping" }, { origin: DASHBOARD + "0" });
    load.receive({ type: "ar.ping" }, { origin: "http://localhost:9797" });
    load.receive({ type: "ar.ping" }, { source: {} });
    load.receive({ type: "ar.open", agentId: "live" }, { source: load.page.window });
    load.receive({ type: "ar.open", agentId: "live" }, { origin: "null" });
    await flush();
    expect(load.posted).toEqual([]);
    expect(opened).toEqual([]);

    load.receive({ type: "ar.ping" });
    load.receive("ar.ping");
    load.receive({ type: "something-else" });
    expect(answers(load.posted)).toEqual([{ source: "ar-plugin", type: "pong", capabilities: PLUGIN_ABILITIES }]);
    expect(load.posted.every((entry) => entry.targetOrigin === DASHBOARD)).toBe(true);
  });

  it("shows a live agent and says so when route, store and chat all name it", async () => {
    showTabs(["live"]);
    const tab = newTab();
    const load = pageLoad(tab, { href: LEAF_URL });
    const { client, opened } = fakeClient();
    installBridge(client, load.page, DASHBOARD);
    load.posted.length = 0;

    // The tab names the agent but the stored layout does not yet: no evidence. The open
    // itself is navigation alone, with the agent only — the application resolves its workspace.
    load.receive({ type: "ar.open", agentId: "live" });
    await flush();
    expect(opened).toHaveLength(1);
    expect(opened[0]).toEqual({ agentId: "live", at: expect.any(String) });
    touchDom();
    await flush();
    expect(answers(load.posted)).toEqual([]);

    // The stored layout names the requested agent as the shown pane's focused tab, with the
    // frame's route naming its workspace and the chat mounted: the screen reports it.
    seedLayout(tab, "agent_live");
    touchDom();
    await flush();
    expect(answers(load.posted)).toEqual([{ source: "ar-plugin", type: "shown", agentId: "live", workspaceId: "wks_leaf" }]);

    load.posted.length = 0;
    for (const agentId of ["archived", "gone", "broken"]) {
      load.receive({ type: "ar.open", agentId });
      await flush();
    }
    load.receive({ type: "ar.open" });
    await flush();
    expect(opened).toHaveLength(1);
    expect(answers(load.posted).map((answer) => [answer.type, answer.code, answer.agentId])).toEqual([
      ["error", "agent-archived", "archived"],
      ["error", "agent-not-found", "gone"],
      ["error", "open-failed", "broken"],
      ["error", "open-failed", undefined],
    ]);
  });

  it("confirms at once when the display already satisfies the request", async () => {
    showTabs(["live"]);
    const tab = newTab();
    seedLayout(tab, "agent_live");
    const load = pageLoad(tab, { href: LEAF_URL });
    const { client, opened } = fakeClient();
    installBridge(client, load.page, DASHBOARD);
    load.posted.length = 0;

    // Route, stored focus and mounted chat already name the agent: no later observation
    // is needed for the screen to report it.
    load.receive({ type: "ar.open", agentId: "live" });
    await flush();
    expect(opened).toHaveLength(1);
    expect(answers(load.posted)).toEqual([{ source: "ar-plugin", type: "shown", agentId: "live", workspaceId: "wks_leaf" }]);
  });

  it("never settles an Opening screen on a stale cached tab", async () => {
    showTabs(["live"]);
    const tab = newTab();
    const load = pageLoad(tab, { href: LEAF_URL });
    const { client, opened } = fakeClient();
    installBridge(client, load.page, DASHBOARD);
    load.posted.length = 0;

    load.receive({ type: "ar.open", agentId: "live", workspaceId: "wks_leaf" });
    await flush();
    expect(opened).toHaveLength(1);
    // The dashboard's workspace rides along for the record; navigation carries the agent alone.
    expect(opened[0]).toEqual({ agentId: "live", at: expect.any(String) });
    // The stored layout does not name the agent: the tab alone never reports.
    touchDom();
    await flush();
    expect(answers(load.posted)).toEqual([]);

    // The open replaces the screen with Opening: no chat mounted anymore, nothing reported.
    showTabs([]);
    touchDom();
    await flush();
    expect(answers(load.posted)).toEqual([]);

    // Only the agent's screen, with route, stored focus and mounted chat, reports it.
    showTabs(["live"]);
    seedLayout(tab, "agent_live");
    touchDom();
    await flush();
    expect(answers(load.posted)).toEqual([{ source: "ar-plugin", type: "shown", agentId: "live", workspaceId: "wks_leaf" }]);
  });

  it("opens the agent the host keeps and reports its actual workspace", async () => {
    showTabs([]);
    const tab = newTab();
    const load = pageLoad(tab, { href: LEAF_URL });
    const { client, opened } = fakeClient();
    installBridge(client, load.page, DASHBOARD);
    load.posted.length = 0;

    // The dashboard names a workspace the host record disagrees with: the open still goes to
    // the agent — the application resolves its location — and the report names the actual one.
    load.receive({ type: "ar.open", agentId: "live", workspaceId: "wks_elsewhere" });
    await flush();
    expect(opened).toHaveLength(1);
    expect(opened[0]).toEqual({ agentId: "live", at: expect.any(String) });
    touchDom();
    await flush();
    expect(answers(load.posted)).toEqual([]);

    showTabs(["live"]);
    seedLayout(tab, "agent_live");
    touchDom();
    await flush();
    expect(answers(load.posted)).toEqual([{ source: "ar-plugin", type: "shown", agentId: "live", workspaceId: "wks_leaf" }]);
  });

  it("confirms a compact mounted chat the stored layout names, never a loading pane", async () => {
    showCompact("loading");
    const tab = newTab();
    const load = pageLoad(tab, { href: LEAF_URL });
    const { client, opened } = fakeClient();
    installBridge(client, load.page, DASHBOARD);
    load.posted.length = 0;

    load.receive({ type: "ar.open", agentId: "live" });
    await flush();
    expect(opened).toHaveLength(1);
    // The loading pane is observed after the open and names no displayed chat.
    touchDom();
    await flush();
    expect(answers(load.posted)).toEqual([]);

    // The mounted chat, observed after the open, with the stored layout naming the
    // requested agent as the focused tab of the workspace's shown pane, reports it.
    seedLayout(tab, "agent_live");
    showCompact("chat");
    await flush();
    expect(answers(load.posted)).toEqual([{ source: "ar-plugin", type: "shown", agentId: "live", workspaceId: "wks_leaf" }]);
  });

  it("tells a dashboard that reads it once when the application is on the agent's chat, before the chat is drawn", async () => {
    showCompact("loading");
    const tab = newTab();
    seedLayout(tab, "agent_live");
    const load = pageLoad(tab, { href: LEAF_URL });
    const { client } = fakeClient();
    installBridge(client, load.page, DASHBOARD);
    load.posted.length = 0;
    const navigated = { source: "ar-plugin", type: "navigated", agentId: "live" };

    // A dashboard that did not say it reads it is told nothing while the conversation loads.
    load.receive({ type: "ar.open", agentId: "live" });
    await flush();
    touchDom();
    await flush();
    expect(answers(load.posted)).toEqual([]);

    // The stored layout names another agent of the workspace: the application is not on the
    // asked chat, whatever the route says.
    load.receive({ type: "ar.hello", capabilities: ["late-answer", "navigated", "on-chat", "load-failed"] });
    seedLayout(tab, "agent_other");
    load.receive({ type: "ar.open", agentId: "live" });
    await flush();
    touchDom();
    await flush();
    expect(answers(load.posted)).toEqual([navigated]);

    // Route and stored layout name the agent while its loading pane still shows: said once,
    // however often the display is observed after it.
    seedLayout(tab, "agent_live");
    touchDom();
    await flush();
    await vi.advanceTimersByTimeAsync(600);
    expect(answers(load.posted)).toEqual([navigated, { source: "ar-plugin", type: "on-chat", agentId: "live" }]);

    showCompact("chat");
    await flush();
    expect(answers(load.posted).slice(2)).toEqual([{ source: "ar-plugin", type: "shown", agentId: "live", workspaceId: "wks_leaf" }]);
  });

  it("tells such a dashboard that the host could not load the agent when the application shows that pane, and then waits for nothing", async () => {
    const showFailure = () => {
      document.body.innerHTML = '<button data-testid="workspace-tab-switcher-trigger"></button><div data-testid="agent-load-error"><button data-testid="agent-load-error-retry">Retry</button></div>';
      for (const node of document.querySelectorAll("button, div")) {
        Object.assign(node, { checkVisibility: () => true, getBoundingClientRect: () => ({ width: 160, height: 28 }) });
      }
    };
    showFailure();
    const tab = newTab();
    seedLayout(tab, "agent_live");
    const load = pageLoad(tab, { href: LEAF_URL });
    const { client } = fakeClient();
    installBridge(client, load.page, DASHBOARD);
    load.posted.length = 0;

    // A dashboard that did not say it reads it is told nothing, as before.
    load.receive({ type: "ar.open", agentId: "live" });
    await flush();
    touchDom();
    await flush();
    expect(answers(load.posted)).toEqual([]);

    load.receive({ type: "ar.hello", capabilities: ["late-answer", "navigated", "on-chat", "load-failed"] });
    load.receive({ type: "ar.open", agentId: "live" });
    await flush();
    expect(answers(load.posted)).toEqual([
      { source: "ar-plugin", type: "navigated", agentId: "live" },
      { source: "ar-plugin", type: "on-chat", agentId: "live" },
      { source: "ar-plugin", type: "load-failed", agentId: "live" },
    ]);

    // The request has ended: the display is observed no more, and a chat drawn later (the
    // application's own Retry) is no answer to it.
    expect(vi.getTimerCount()).toBe(0);
    showCompact("chat");
    await flush();
    await vi.advanceTimersByTimeAsync(600);
    expect(answers(load.posted)).toHaveLength(3);
  });

  it("ends a compact open without shown when the stored layout names another agent", async () => {
    // F02's sealed control: requested A, B's chat mounted, an unrelated mutation.
    showCompact("chat");
    const tab = newTab();
    seedLayout(tab, "agent_elsewhere");
    const load = pageLoad(tab, { href: LEAF_URL });
    const { client, opened } = fakeClient();
    installBridge(client, load.page, DASHBOARD);
    load.posted.length = 0;

    load.receive({ type: "ar.open", agentId: "live" });
    await flush();
    expect(opened).toHaveLength(1);
    // The unrelated mutation and the timer's re-observation both run: the mounted chat is
    // B's, so nothing is reported and the dashboard's deadline owns the end.
    touchDom();
    await flush();
    await vi.advanceTimersByTimeAsync(1000);
    expect(answers(load.posted)).toEqual([]);
  });

  it("never confirms when the workspace is only in the query, never in the route", async () => {
    showCompact("chat");
    const tab = newTab();
    seedLayout(tab, "agent_live");
    // The route names another workspace; the requested one appears only in `?next=`.
    // Under a whole-address search this would satisfy; as path segments it never does.
    const load = pageLoad(tab, { href: DAEMON + "/h/srv_test/workspace/wks_other?next=/workspace/wks_leaf" });
    const { client, opened } = fakeClient();
    installBridge(client, load.page, DASHBOARD);
    load.posted.length = 0;

    load.receive({ type: "ar.open", agentId: "live" });
    await flush();
    expect(opened).toHaveLength(1);
    touchDom();
    await flush();
    await vi.advanceTimersByTimeAsync(1000);
    expect(answers(load.posted)).toEqual([]);
  });

  it("never confirms on another server's stored key for the same workspace", async () => {
    showCompact("chat");
    const tab = newTab();
    seedLayout(tab, "agent_live", "other-srv:wks_leaf");
    const load = pageLoad(tab, { href: LEAF_URL });
    const { client, opened } = fakeClient();
    installBridge(client, load.page, DASHBOARD);
    load.posted.length = 0;

    load.receive({ type: "ar.open", agentId: "live" });
    await flush();
    expect(opened).toHaveLength(1);
    // A suffix match would take `other-srv:wks_leaf`; the whole key never does.
    touchDom();
    await flush();
    await vi.advanceTimersByTimeAsync(1000);
    expect(answers(load.posted)).toEqual([]);
  });

  it("never confirms a compact chat on a missing, foreign, unparsable, or hidden layout", async () => {
    showCompact("chat");
    const cases: Array<{ name: string; seed: (tab: Tab) => void }> = [
      { name: "absent", seed: () => {} },
      { name: "foreign workspace", seed: (tab) => seedLayout(tab, "agent_live", "srv_test:wks_other") },
      {
        name: "unparsable",
        seed: (tab) => tab.local.setItem("workspace-layout-state", "{nope"),
      },
      {
        name: "hidden shown pane",
        seed: (tab) => tab.local.put("workspace-layout-state", {
          state: {
            layoutByWorkspace: {
              "srv_test:wks_leaf": {
                root: {
                  kind: "group",
                  group: {
                    id: "workspace-root",
                    children: [
                      { kind: "pane", pane: { id: "main", tabIds: ["agent_live"], focusedTabId: "agent_live", hidden: true } },
                    ],
                  },
                },
                focusedPaneId: "main",
              },
            },
          },
          version: 2,
        }),
      },
    ];
    for (const { seed } of cases) {
      const tab = newTab();
      seed(tab);
      const load = pageLoad(tab, { href: LEAF_URL });
      const { client } = fakeClient();
      installBridge(client, load.page, DASHBOARD);
      load.posted.length = 0;
      load.receive({ type: "ar.open", agentId: "live" });
      await flush();
      touchDom();
      await flush();
      await vi.advanceTimersByTimeAsync(1000);
      expect(answers(load.posted)).toEqual([]);
    }
  });

  it("never confirms when a visible selected tab names another agent than the store", async () => {
    // Route, stored focus and mounted chat all name the request — but the on-screen
    // selected strip tab names a different agent (render lag or stale strip): the wait
    // stays open instead of confirming against the contradiction.
    document.body.innerHTML =
      `<button data-testid="workspace-tab-agent_other" aria-selected="true"></button>` +
      `<div id="agent-chat-scroll-web-dom-scroll">Chat</div>`;
    for (const node of document.querySelectorAll("button, div")) {
      Object.assign(node, { checkVisibility: () => true, getBoundingClientRect: () => ({ width: 160, height: 28 }) });
    }
    const tab = newTab();
    seedLayout(tab, "agent_live");
    const load = pageLoad(tab, { href: LEAF_URL });
    const { client } = fakeClient();
    installBridge(client, load.page, DASHBOARD);
    load.posted.length = 0;

    load.receive({ type: "ar.open", agentId: "live" });
    await flush();
    touchDom();
    await flush();
    await vi.advanceTimersByTimeAsync(1000);
    expect(answers(load.posted)).toEqual([]);
  });

  it("accepts the bundle's native scroll literal as a mounted chat", async () => {
    // The pinned bundle carries `agent-chat-scroll-native-virtualized`; the probe matches
    // the shared prefix, so either variant mounts the chat.
    document.body.innerHTML =
      `<button data-testid="workspace-tab-agent_live" aria-selected="true"></button>` +
      `<div id="agent-chat-scroll-native-virtualized">Chat</div>`;
    for (const node of document.querySelectorAll("button, div")) {
      Object.assign(node, { checkVisibility: () => true, getBoundingClientRect: () => ({ width: 160, height: 28 }) });
    }
    const tab = newTab();
    seedLayout(tab, "agent_live");
    const load = pageLoad(tab, { href: LEAF_URL });
    const { client } = fakeClient();
    installBridge(client, load.page, DASHBOARD);
    load.posted.length = 0;

    load.receive({ type: "ar.open", agentId: "live" });
    await flush();
    expect(answers(load.posted)).toEqual([{ source: "ar-plugin", type: "shown", agentId: "live", workspaceId: "wks_leaf" }]);
  });

  it("confirms on the timer when the stored layout lands after the last DOM change", async () => {
    showCompact("chat");
    const tab = newTab();
    const load = pageLoad(tab, { href: LEAF_URL });
    const { client, opened } = fakeClient();
    installBridge(client, load.page, DASHBOARD);
    load.posted.length = 0;

    load.receive({ type: "ar.open", agentId: "live" });
    await flush();
    expect(opened).toHaveLength(1);
    // The DOM answers, but the store does not name the agent yet: nothing is reported.
    touchDom();
    await flush();
    expect(answers(load.posted)).toEqual([]);

    // The application writes its layout after the last DOM change; the timer's
    // re-observation reports the screen with no further mutation.
    seedLayout(tab, "agent_live");
    await vi.advanceTimersByTimeAsync(300);
    expect(answers(load.posted)).toEqual([{ source: "ar-plugin", type: "shown", agentId: "live", workspaceId: "wks_leaf" }]);
  });

  it("tells a dashboard that reads it which chat the compact layout shows as Paseo's own list moves it, and none on a screen without one", async () => {
    showCompact("chat");
    const tab = newTab();
    seedLayout(tab, "agent_live");
    const load = pageLoad(tab, { href: LEAF_URL });
    // The page's timers are the test's fake clock.
    Object.assign(load.page.window, { setTimeout: window.setTimeout.bind(window), clearTimeout: window.clearTimeout.bind(window) });
    const { client, opened } = fakeClient();
    installBridge(client, load.page, DASHBOARD);
    const showing = () => load.posted.map((entry) => entry.data).filter((data) => data.type === "showing");
    const told = (agentId: string | null) => ({ source: "ar-plugin", type: "showing", agentId });

    // A dashboard that did not say it reads it is told nothing, and nothing is watched for it.
    touchDom();
    await vi.advanceTimersByTimeAsync(SHOWING_SETTLE_MS);
    expect(showing()).toEqual([]);
    expect(vi.getTimerCount()).toBe(0);

    // Below the 720 px breakpoint no tab is shown as selected, so no selection is published;
    // the stored focus of the routed workspace names the chat, as it does for `shown`.
    load.receive({ type: "ar.hello", capabilities: ["late-answer", "navigated", "on-chat", "load-failed", "pick", "showing"] });
    expect(showing()).toEqual([told("live")]);
    expect(load.posted.map((entry) => entry.data).filter((data) => data.type === "selection")).toEqual([{ source: "ar-plugin", type: "selection", agentIds: [] }]);

    // He picks another agent in Paseo's own list: the application stores the new focus and
    // draws that chat. The plug-in reads it; it navigates nothing itself.
    seedLayout(tab, "agent_other");
    touchDom();
    await vi.advanceTimersByTimeAsync(SHOWING_SETTLE_MS);
    expect(showing()).toEqual([told("live"), told("other")]);
    touchDom();
    await vi.advanceTimersByTimeAsync(SHOWING_SETTLE_MS);
    expect(showing()).toHaveLength(2);
    expect(opened).toEqual([]);

    // The agent's screen shows the application's not-found pane: no chat is shown.
    document.body.innerHTML = '<button data-testid="workspace-tab-switcher-trigger"></button><div data-testid="agent-not-found"></div>';
    for (const node of document.querySelectorAll("button, div")) {
      Object.assign(node, { checkVisibility: () => true, getBoundingClientRect: () => ({ width: 160, height: 28 }) });
    }
    await vi.advanceTimersByTimeAsync(SHOWING_SETTLE_MS);
    expect(showing().at(-1)).toEqual(told(null));
    // The focused tab is no agent's (a terminal): no chat either.
    showCompact("chat");
    seedLayout(tab, "terminal_7");
    touchDom();
    await vi.advanceTimersByTimeAsync(SHOWING_SETTLE_MS);
    expect(showing()).toEqual([told("live"), told("other"), told(null)]);
  });

  it("tells the chat the wide strip shows, and the focused one of split panes", async () => {
    showTabs(["live"]);
    const tab = newTab();
    seedLayout(tab, "agent_live");
    const load = pageLoad(tab, { href: LEAF_URL });
    Object.assign(load.page.window, { setTimeout: window.setTimeout.bind(window), clearTimeout: window.clearTimeout.bind(window) });
    installBridge(fakeClient().client, load.page, DASHBOARD);
    const showing = () => load.posted.map((entry) => entry.data).filter((data) => data.type === "showing").map((data) => data.agentId);
    load.receive({ type: "ar.hello", capabilities: ["late-answer", "navigated", "on-chat", "load-failed", "pick", "showing"] });
    expect(showing()).toEqual(["live"]);

    // Two panes side by side: the stored focus names which of the two visible chats is shown.
    seedLayout(tab, "agent_other");
    showTabs(["live", "other"]);
    await vi.advanceTimersByTimeAsync(SHOWING_SETTLE_MS);
    expect(showing()).toEqual(["live", "other"]);
  });

  it("opens a workspace the runtime knows, and answers workspace-not-found for any other", async () => {
    const load = pageLoad(newTab());
    const { client, opened } = fakeClient();
    installBridge(client, load.page, DASHBOARD);
    load.posted.length = 0;

    load.receive({ type: "ar.open", workspaceId: "wks_leaf" });
    await flush();
    expect(opened).toHaveLength(1);
    expect(opened[0]).toMatchObject({ workspaceId: "wks_leaf" });
    expect(answers(load.posted)).toEqual([{ source: "ar-plugin", type: "shown", workspaceId: "wks_leaf" }]);

    load.posted.length = 0;
    load.receive({ type: "ar.open", workspaceId: "wks_doesnotexist0000" });
    await flush();
    load.receive({ type: "ar.open", workspaceId: "wks_broken" });
    await flush();
    expect(opened).toHaveLength(1);
    expect(answers(load.posted).map((answer) => [answer.type, answer.code, answer.workspaceId, answer.message])).toEqual([
      ["error", "workspace-not-found", "wks_doesnotexist0000", "the runtime has no such workspace"],
      ["error", "workspace-not-found", "wks_broken", "socket closed"],
    ]);
  });

  it("only the newest native open can navigate or answer after refresh, including teardown", async () => {
    const tab = newTab();
    const load = pageLoad(tab, { href: DAEMON + "/h/srv_test/workspace/workspace-B" });
    const { client, opened } = fakeClient();
    const pending = new Map<string, { resolve: (value: unknown) => void; reject: (error: Error) => void }>();
    const refresh = (id: string) => new Promise<unknown>((resolve, reject) => pending.set(id, { resolve, reject }));
    client.paseo.agents.ref = (id) => ({ archivedAt: null, workspaceId: "workspace-" + id, refresh: () => refresh(id) });
    client.paseo.workspaces.ref = (id) => ({ refresh: () => refresh(id) });
    const stop = installBridge(client, load.page, DASHBOARD);
    load.posted.length = 0;
    load.receive({ type: "ar.open", agentId: "A" });
    load.receive({ type: "ar.open", agentId: "B" });
    pending.get("B")!.resolve({});
    await flush();
    // B is opened but nothing is observed after its request yet: nothing is reported.
    expect(opened.map((params) => params?.agentId)).toEqual(["B"]);
    expect(answers(load.posted)).toEqual([]);
    showTabs(["B"]);
    seedLayout(tab, "agent_B", "srv_test:workspace-B");
    touchDom();
    await flush();
    pending.get("A")!.resolve({});
    await flush();
    expect(opened.map((params) => params?.agentId)).toEqual(["B"]);
    expect(answers(load.posted)).toEqual([{ source: "ar-plugin", type: "shown", agentId: "B", workspaceId: "workspace-B" }]);
    load.receive({ type: "ar.open", agentId: "stale-error" });
    load.receive({ type: "ar.open", workspaceId: "workspace-C" });
    pending.get("workspace-C")!.resolve({});
    pending.get("stale-error")!.reject(new Error("not found"));
    await flush();
    expect(answers(load.posted).map((entry) => entry.type)).toEqual(["shown", "shown"]);
    expect(opened.at(-1)?.workspaceId).toBe("workspace-C");
    load.receive({ type: "ar.open", agentId: "after-stop" });
    stop();
    pending.get("after-stop")!.resolve({});
    await flush();
    expect(opened).toHaveLength(2);
    expect(answers(load.posted)).toHaveLength(2);
  });
});

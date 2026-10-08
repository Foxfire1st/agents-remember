// The client part of the AR plugin for the Paseo runtime (PNT-R05). Its source lives with the
// plugin, under mcp/src/agents_remember/package_data/paseo_plugin/client/, and imports nothing
// from Paseo: the browser objects and the plugin's client context are passed in. This file and
// paseoPluginLook.test.ts are where the repository's gates run and type-check it.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  framingOrigin,
  installBridge,
  isListedParent,
  isParentMessage,
  type BridgeClient,
} from "../../../mcp/src/agents_remember/package_data/paseo_plugin/client/bridge";
import {
  RELOAD_FLAG,
  bootstrapEmbed,
  loadState,
  reloadOnce,
  requestedUrl,
  takeOwnLookBack,
  watchAppWrites,
} from "../../../mcp/src/agents_remember/package_data/paseo_plugin/client/load";
import {
  APP_SETTINGS_KEY,
  EMBED_LOOK,
  PANEL_STATE_KEY,
  STANDALONE_LOOK_KEY,
  SIDEBAR_OPENED_KEY,
  applyEmbedLook,
  restoreStandaloneLook,
} from "../../../mcp/src/agents_remember/package_data/paseo_plugin/client/look";
import { currentPage } from "../../../mcp/src/agents_remember/package_data/paseo_plugin/client/page";
import { clearEmbedPageMode, setEmbedPageMode } from "../../../mcp/src/agents_remember/package_data/paseo_plugin/client/sidebar";
import { startClientPart } from "../../../mcp/src/agents_remember/package_data/paseo_plugin/client/start";
import {
  CYCLED_IN_FRAME,
  DAEMON,
  DASHBOARD,
  DEFAULT_LOOK,
  EMBED,
  MemoryStorage,
  OWN_LOOK,
  WORKSPACE_URL,
  emptyHierarchyClient,
  memoryOf,
  newTab,
  pageLoad,
  panelState,
  settingsOf,
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

describe("one reload per page load", () => {
  it("replaces the page once, and the page that results never replaces it again", () => {
    const tab = newTab();
    const first = pageLoad(tab);

    expect(reloadOnce(first.page, WORKSPACE_URL, DASHBOARD)).toBe(true);
    expect(first.replace).toHaveBeenCalledExactlyOnceWith(WORKSPACE_URL);
    expect(tab.session.getItem(RELOAD_FLAG)).not.toBeNull();
    // The same page, evaluated again before it is gone, does not ask twice.
    expect(reloadOnce(first.page, WORKSPACE_URL, DASHBOARD)).toBe(false);
    expect(first.replace).toHaveBeenCalledTimes(1);

    // The reload: it takes the flag out of the session and refuses to reload.
    const reloaded = pageLoad(tab);
    expect(loadState(reloaded.page).reloaded).toBe(true);
    expect(tab.session.getItem(RELOAD_FLAG)).toBeNull();
    expect(reloadOnce(reloaded.page, WORKSPACE_URL, DASHBOARD)).toBe(false);
    expect(reloaded.replace).not.toHaveBeenCalled();
  });

  it("takes any flag for the reload, also one it cannot read", () => {
    for (const flag of ["{not json", "null", "[]", JSON.stringify({ parent: 42 })]) {
      const tab = newTab();
      tab.session.setItem(RELOAD_FLAG, flag);
      const load = pageLoad(tab);
      expect(loadState(load.page)).toMatchObject({ reloaded: true, carriedParent: null });
      expect(tab.session.getItem(RELOAD_FLAG)).toBeNull();
      expect(reloadOnce(load.page, WORKSPACE_URL, DASHBOARD)).toBe(false);
      expect(load.replace).not.toHaveBeenCalled();
    }
  });

  it("never refuses a later, separate load its own reload, however soon it comes", () => {
    const tab = newTab(OWN_LOOK);
    // Frame load: the look is stored and the page reloaded.
    const frame = pageLoad(tab);
    expect(bootstrapEmbed(frame.page, DASHBOARD)).toBe(true);
    expect(bootstrapEmbed(pageLoad(tab).page, DASHBOARD)).toBe(false);

    // A standalone tab of the same profile (its own session) takes the user's look back.
    const standalone: Tab = { local: tab.local, session: new MemoryStorage() };
    const solo = pageLoad(standalone, { framedBy: null });
    takeOwnLookBack(solo.page);
    expect(solo.replace).toHaveBeenCalledExactlyOnceWith(WORKSPACE_URL);
    takeOwnLookBack(pageLoad(standalone, { framedBy: null }).page);
    expect(settingsOf(tab)).toEqual({ ...OWN_LOOK, language: "system" });

    // No time has passed. The dashboard page is loaded again: the frame must get the AR look,
    // which needs a reload, and gets it.
    const frameAgain = pageLoad(tab);
    expect(bootstrapEmbed(frameAgain.page, DASHBOARD)).toBe(true);
    expect(frameAgain.replace).toHaveBeenCalledTimes(1);
    expect(settingsOf(tab)).toEqual({ ...EMBED_LOOK, language: "system" });
    pageLoad(tab); // (its reload)

    // And the standalone tab, reloaded by the user right away, takes its look back again.
    const soloAgain = pageLoad(standalone, { framedBy: null });
    takeOwnLookBack(soloAgain.page);
    expect(soloAgain.replace).toHaveBeenCalledTimes(1);
    expect(settingsOf(tab)).toEqual({ ...OWN_LOOK, language: "system" });
  });

  it("does not replace the page when the session cannot hold the flag", () => {
    const tab = newTab();
    tab.session.setItem = () => {
      throw new Error("storage is disabled");
    };
    const load = pageLoad(tab);
    expect(reloadOnce(load.page, WORKSPACE_URL, DASHBOARD)).toBe(false);
    expect(load.replace).not.toHaveBeenCalled();
  });

  it("repairs a first-visit deep link with the same single reload", () => {
    const deepLink = WORKSPACE_URL + "?open=agent:a1";
    const tab = newTab(DEFAULT_LOOK);
    // First visit: the app bounced the deep link to its project picker; the look is not stored yet.
    const bounced = pageLoad(tab, { href: DAEMON + "/open-project", requested: deepLink });
    expect(requestedUrl(bounced.page)).toBe(deepLink);
    expect(bootstrapEmbed(bounced.page, DASHBOARD)).toBe(true);
    expect(bounced.replace).toHaveBeenCalledExactlyOnceWith(deepLink);
    // A page that is leaving does not start waiting for the app's sidebar toggle.
    expect(vi.getTimerCount()).toBe(0);

    // The look is stored already but the link bounced: still one reload to the link.
    const warmTab = newTab(DEFAULT_LOOK);
    applyEmbedLook(warmTab.local);
    const welcome = pageLoad(warmTab, { href: DAEMON + "/welcome", requested: deepLink });
    expect(bootstrapEmbed(welcome.page, DASHBOARD)).toBe(true);
    expect(welcome.replace).toHaveBeenCalledExactlyOnceWith(deepLink);
    expect(vi.getTimerCount()).toBe(0);

    // The app is somewhere else than the link for another reason (not one of the two bounce
    // paths): that is not a first visit, and the page is left where it is.
    for (const elsewhere of ["/settings/appearance", "/h/srv_test/workspace/wks_other", "/"]) {
      const movedTab = newTab(DEFAULT_LOOK);
      applyEmbedLook(movedTab.local);
      const moved = pageLoad(movedTab, { href: DAEMON + elsewhere, requested: deepLink });
      expect(bootstrapEmbed(moved.page, DASHBOARD)).toBe(false);
      expect(moved.replace).not.toHaveBeenCalled();
    }
    vi.advanceTimersByTime(10_000); // (their wait for the app's sidebar toggle ends)

  });

  it.each([false, true, null])("opens only an old closed sidebar once, then honors user changes across reloads (stored %s)", (initiallyOpen) => {
    const tab = newTab(EMBED_LOOK, initiallyOpen ?? true);
    if (initiallyOpen === null) tab.local.removeItem(PANEL_STATE_KEY);
    document.body.innerHTML = `<button data-testid="menu-button" aria-expanded="${initiallyOpen ?? true}"></button>`;
    const toggle = document.querySelector('[data-testid="menu-button"]') as HTMLElement;
    let load = pageLoad(tab);
    let stop = watchAppWrites(load.page);
    const writes = vi.spyOn(tab.local, "setItem");
    const toggled = vi.fn(() => {
      const open = toggle.getAttribute("aria-expanded") !== "true";
      toggle.setAttribute("aria-expanded", String(open));
      load.appWrites(PANEL_STATE_KEY, panelState(open));
    });
    toggle.addEventListener("click", toggled);
    const nativeBefore = tab.local.getItem(PANEL_STATE_KEY);
    expect(bootstrapEmbed(load.page, DASHBOARD)).toBe(false);
    vi.advanceTimersByTime(10_000);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(toggled).toHaveBeenCalledTimes(initiallyOpen === false ? 1 : 0);
    expect(writes.mock.calls.filter(([key]) => key === PANEL_STATE_KEY)).toHaveLength(initiallyOpen === false ? 1 : 0);
    expect(writes.mock.calls.filter(([key]) => key === APP_SETTINGS_KEY)).toHaveLength(0);
    if (initiallyOpen !== false) expect(tab.local.getItem(PANEL_STATE_KEY)).toBe(nativeBefore);
    expect(tab.local.getItem(SIDEBAR_OPENED_KEY)).toBe("done");
    stop();

    // The native toggle owns both choices. Separate page globals model actual reloads.
    for (const open of [false, true, false]) {
      toggle.click();
      expect(toggle.getAttribute("aria-expanded")).toBe(String(open));
      const chosen = tab.local.getItem(PANEL_STATE_KEY);
      toggled.mockClear();
      writes.mockClear();
      load = pageLoad(tab);
      stop = watchAppWrites(load.page);
      expect(bootstrapEmbed(load.page, DASHBOARD)).toBe(false);
      vi.advanceTimersByTime(10_000);
      expect(toggle.getAttribute("aria-expanded")).toBe(String(open));
      expect(toggled).not.toHaveBeenCalled();
      expect(tab.local.getItem(PANEL_STATE_KEY)).toBe(chosen);
      expect(writes.mock.calls.filter(([key]) => key === PANEL_STATE_KEY)).toHaveLength(0);
      stop();
    }
  });

  it("leaves a missing host panel state untouched after migration and refuses an automatic click without durable storage", () => {
    const tab = newTab(EMBED_LOOK, false);
    tab.local.setItem(SIDEBAR_OPENED_KEY, "done");
    tab.local.removeItem(PANEL_STATE_KEY);
    const missing = pageLoad(tab);
    const writes = vi.spyOn(tab.local, "setItem");
    expect(bootstrapEmbed(missing.page, DASHBOARD)).toBe(false);
    vi.advanceTimersByTime(10_000);
    expect(tab.local.getItem(PANEL_STATE_KEY)).toBeNull();
    expect(writes.mock.calls.filter(([key]) => key === PANEL_STATE_KEY)).toHaveLength(0);

    document.body.innerHTML = '<button data-testid="menu-button" aria-expanded="false"></button>';
    const toggle = document.querySelector('[data-testid="menu-button"]') as HTMLElement;
    const clicks = vi.fn();
    toggle.addEventListener("click", clicks);
    const blocked = newTab(EMBED_LOOK, false);
    const set = blocked.local.setItem.bind(blocked.local);
    blocked.local.setItem = (key, value) => {
      if (key === SIDEBAR_OPENED_KEY) throw new Error("storage disabled");
      set(key, value);
    };
    expect(bootstrapEmbed(pageLoad(blocked).page, DASHBOARD)).toBe(false);
    vi.advanceTimersByTime(10_000);
    expect(clicks).not.toHaveBeenCalled();
    expect(blocked.local.getItem(SIDEBAR_OPENED_KEY)).toBeNull();
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
  });

  it("keeps a user's native menu click before the first startup poll, including a descendant icon", () => {
    document.body.innerHTML = '<button data-testid="menu-button" aria-expanded="false"><span data-testid="menu-icon"></span></button>';
    const toggle = document.querySelector('[data-testid="menu-button"]') as HTMLElement;
    const icon = document.querySelector('[data-testid="menu-icon"]') as HTMLElement;
    const tab = newTab(DEFAULT_LOOK, false);
    applyEmbedLook(tab.local);
    const load = pageLoad(tab);
    const toggled = vi.fn(() => {
      toggle.setAttribute("aria-expanded", String(toggle.getAttribute("aria-expanded") !== "true"));
    });
    toggle.addEventListener("click", toggled);

    expect(bootstrapEmbed(load.page, DASHBOARD)).toBe(false);
    icon.click();
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(vi.getTimerCount()).toBe(0);
    vi.advanceTimersByTime(10_000);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(toggled).toHaveBeenCalledTimes(1);
  });

  it("asks for a URL of another origin, or outside the host routes, as the page stands", () => {
    const tab = newTab();
    expect(requestedUrl(pageLoad(tab, { requested: "http://evil.test/h/srv/workspace/x" }).page)).toBe(WORKSPACE_URL);
    expect(requestedUrl(pageLoad(tab, { href: DAEMON + "/open-project", requested: DAEMON + "/" }).page)).toBe(
      DAEMON + "/open-project",
    );
  });
});

describe("who frames the page", () => {
  it("names the parent from the browser, and never trusts anything at top level", () => {
    const tab = newTab();
    expect(framingOrigin(pageLoad(tab).page, null)).toBe(DASHBOARD);
    expect(framingOrigin(pageLoad(tab, { ancestorOrigins: false, referrer: DASHBOARD + "/" }).page, null)).toBe(DASHBOARD);
    // Framed, and nothing says by whom.
    expect(framingOrigin(pageLoad(tab, { ancestorOrigins: false }).page, null)).toBeUndefined();
    // A standalone tab is nobody's frame, whatever a session may carry.
    expect(framingOrigin(pageLoad(tab, { framedBy: null }).page, null)).toBeNull();
    expect(framingOrigin(pageLoad(tab, { framedBy: null, referrer: DASHBOARD + "/" }).page, DASHBOARD)).toBeNull();
  });

  it("carries the verified parent across the plugin's own reload where the browser forgets it", () => {
    const tab = newTab(DEFAULT_LOOK);
    // A browser without ancestorOrigins: the first load knows the parent from the referrer.
    const first = pageLoad(tab, { ancestorOrigins: false, referrer: DASHBOARD + "/" });
    const parent = framingOrigin(first.page, loadState(first.page).carriedParent);
    expect(parent).toBe(DASHBOARD);
    expect(bootstrapEmbed(first.page, DASHBOARD)).toBe(true);

    // After the plugin's reload the referrer is the page's own origin.
    const reloaded = pageLoad(tab, { ancestorOrigins: false, referrer: WORKSPACE_URL });
    expect(loadState(reloaded.page).carriedParent).toBe(DASHBOARD);
    expect(framingOrigin(reloaded.page, loadState(reloaded.page).carriedParent)).toBe(DASHBOARD);
    // It is a candidate, checked against the embed list like any other.
    expect(isListedParent(EMBED, DASHBOARD, DAEMON)).toBe(true);
    expect(isListedParent([], DASHBOARD, DAEMON)).toBe(false);

    // What the browser says now always goes before what was carried: a carried origin is never
    // used under another parent.
    const other = "http://other.test";
    expect(framingOrigin(pageLoad(tab, { framedBy: other }).page, DASHBOARD)).toBe(other);
    expect(framingOrigin(pageLoad(tab, { ancestorOrigins: false, referrer: other + "/page" }).page, DASHBOARD)).toBe(other);
    // ancestorOrigins goes before the referrer, which a page can be sent with any value of.
    expect(framingOrigin(pageLoad(tab, { framedBy: other, referrer: DASHBOARD + "/" }).page, null)).toBe(other);

    // The carried value lasts for that one page load: a later load without it knows no parent.
    const later = pageLoad(tab, { ancestorOrigins: false, referrer: WORKSPACE_URL });
    expect(framingOrigin(later.page, loadState(later.page).carriedParent)).toBeUndefined();
  });

  it("lists a parent only for the exact pair of origins in the embed list", () => {
    expect(isListedParent(EMBED, DASHBOARD, DAEMON)).toBe(true);
    expect(isListedParent([{ dashboardOrigin: DASHBOARD + "/", frameBaseUrl: DAEMON + "/paseo" }], DASHBOARD, DAEMON)).toBe(true);
    for (const lookAlike of [DASHBOARD + "0", DASHBOARD + ".evil.test", "http://evil.test", "http://localhost:9797", "null"]) {
      expect(isListedParent(EMBED, lookAlike, DAEMON)).toBe(false);
    }
    // Listed for another daemon address than the one this page is served from.
    expect(isListedParent(EMBED, DASHBOARD, "http://localhost:6820")).toBe(false);
    expect(isListedParent(EMBED, DASHBOARD, DAEMON + "0")).toBe(false);
  });

  it("takes a message only from the parent's origin and the parent's own window", () => {
    const parent = {};
    expect(isParentMessage({ origin: DASHBOARD, source: parent }, DASHBOARD, parent)).toBe(true);
    expect(isParentMessage({ origin: DASHBOARD + "0", source: parent }, DASHBOARD, parent)).toBe(false);
    expect(isParentMessage({ origin: "null", source: parent }, DASHBOARD, parent)).toBe(false);
    expect(isParentMessage({ origin: DASHBOARD, source: {} }, DASHBOARD, parent)).toBe(false);
    expect(isParentMessage({ origin: DASHBOARD, source: null }, DASHBOARD, parent)).toBe(false);
  });
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

  it("reports ready to the listed parent only, leaves native navigation visible, and stops cleanly", () => {
    document.body.innerHTML = '<div data-testid="composer-dock-header" style="display:flex"><button data-testid="menu-button"></button></div>';
    const header = document.querySelector('[data-testid="composer-dock-header"]') as HTMLElement;
    const load = pageLoad(newTab());
    const stop = installBridge(fakeClient().client, load.page, DASHBOARD);

    expect(answers(load.posted)).toEqual([{ source: "ar-plugin", type: "ready" }]);
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
    expect(answers(load.posted)).toEqual([{ source: "ar-plugin", type: "pong" }]);
    expect(load.posted.every((entry) => entry.targetOrigin === DASHBOARD)).toBe(true);
  });

  it("shows a live agent and says so; refuses an archived, a missing and an unreadable one", async () => {
    const load = pageLoad(newTab());
    const { client, opened } = fakeClient();
    installBridge(client, load.page, DASHBOARD);
    load.posted.length = 0;

    load.receive({ type: "ar.open", agentId: "live" });
    await flush();
    expect(opened).toHaveLength(1);
    expect(opened[0]).toMatchObject({ agentId: "live" });
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
    const load = pageLoad(newTab());
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

describe("which look a page runs, and recording the app's writes in it", () => {
  const FRAME_FONT = { ...EMBED_LOOK, uiFontFamily: '"Courier New", monospace' };

  it("records a write inside a listed frame as the frame's", () => {
    const tab = newTab(OWN_LOOK);
    applyEmbedLook(tab.local);
    restoreStandaloneLook(tab.local);
    const frame = pageLoad(tab);
    const stop = watchAppWrites(frame.page);
    // Until the page is known to be a listed frame, nothing is recorded for it.
    frame.appWrites(APP_SETTINGS_KEY, FRAME_FONT);
    expect(memoryOf(tab)).toMatchObject({ appSettings: OWN_LOOK, stored: "user", seen: OWN_LOOK });

    bootstrapEmbed(frame.page, DASHBOARD);
    expect(loadState(frame.page).usersLook).toBe(false);
    frame.appWrites(APP_SETTINGS_KEY, FRAME_FONT);
    expect(memoryOf(tab)).toMatchObject({ appSettings: OWN_LOOK, stored: "frame", seen: FRAME_FONT });
    frame.appWrites(PANEL_STATE_KEY, panelState(true));
    expect(memoryOf(tab)).toMatchObject({ agentListOpen: true, sidebar: { stored: "frame", seen: true } });

    stop();
    expect(frame.watched()).toBe(false);
  });

  it("records a write in a standalone page that started with the user's look as the user's", () => {
    const tab = newTab(OWN_LOOK);
    // The tab loads before any frame: nothing is remembered yet, the look is the user's.
    const solo = pageLoad(tab, { framedBy: null });
    watchAppWrites(solo.page);
    takeOwnLookBack(solo.page);
    expect(loadState(solo.page).usersLook).toBe(true);
    expect(solo.replace).not.toHaveBeenCalled();
    // A frame stores the AR look in the shared storage; then the user changes the theme in the tab.
    applyEmbedLook(tab.local);
    const chosen = { ...OWN_LOOK, theme: "light" };
    solo.appWrites(APP_SETTINGS_KEY, chosen);
    expect(memoryOf(tab)).toMatchObject({ appSettings: chosen, stored: "user" });
    solo.appWrites(PANEL_STATE_KEY, panelState(false));
    expect(memoryOf(tab)).toMatchObject({ agentListOpen: false, sidebar: { stored: "user", seen: false } });

    // The plugin evaluated again in the same page (the storage is the frame's once more): the
    // look is put back, and the page still counts as running the user's look.
    applyEmbedLook(tab.local);
    takeOwnLookBack(solo.page);
    expect(solo.replace).toHaveBeenCalledTimes(1);
    expect(loadState(solo.page).usersLook).toBe(true);
  });

  it("does not take a standalone page that is left in the AR look for the user's", () => {
    const tab = newTab(OWN_LOOK);
    applyEmbedLook(tab.local);
    // The page started with the AR look and is not allowed its reload (it is one already).
    tab.session.setItem(RELOAD_FLAG, JSON.stringify({ parent: null }));
    const solo = pageLoad(tab, { framedBy: null });
    watchAppWrites(solo.page);
    takeOwnLookBack(solo.page);
    expect(solo.replace).not.toHaveBeenCalled();
    expect(loadState(solo.page).usersLook).toBe(false);
    expect(settingsOf(tab)).toEqual({ ...OWN_LOOK, language: "system" });
    // What the app writes from that page is the AR look with one change: never the user's.
    solo.appWrites(APP_SETTINGS_KEY, CYCLED_IN_FRAME);
    expect(memoryOf(tab)).toMatchObject({ appSettings: OWN_LOOK, stored: "frame" });
    // A frame page that later falls back to "take the look back" stays a page of the AR look too.
    const frame = pageLoad(newTab(OWN_LOOK));
    bootstrapEmbed(frame.page, DASHBOARD);
    takeOwnLookBack(frame.page);
    expect(loadState(frame.page).usersLook).toBe(false);
  });

  it("does not take a page for the user's look when the store was written after the page began to load", () => {
    const tab = newTab(OWN_LOOK);
    applyEmbedLook(tab.local);
    vi.advanceTimersByTime(10_000);
    // Two standalone tabs begin to load at the same moment. The store holds the AR look, and the
    // app of each tab reads it. Each tab has its own session.
    const began = Date.now();
    const tabOf = (): Tab => ({ local: tab.local, session: new MemoryStorage() });
    const [firstTab, secondTab] = [tabOf(), tabOf()];
    const first = pageLoad(firstTab, { framedBy: null, beganAt: began });
    const second = pageLoad(secondTab, { framedBy: null, beganAt: began });
    // The first tab's plugin runs, puts the user's look back and reloads its page.
    vi.advanceTimersByTime(400);
    takeOwnLookBack(first.page);
    expect(first.replace).toHaveBeenCalledTimes(1);
    expect(settingsOf(tab)).toEqual({ ...OWN_LOOK, language: "system" });
    // The second tab's plugin runs after that. Nothing has to be put back any more, but the
    // page started with the AR look: it loads once more and is not a page of the user's look.
    vi.advanceTimersByTime(30);
    watchAppWrites(second.page);
    takeOwnLookBack(second.page);
    expect(second.replace).toHaveBeenCalledExactlyOnceWith(WORKSPACE_URL);
    expect(loadState(second.page).usersLook).toBe(false);
    // The load that follows began after the write: the user's look, and no further reload.
    vi.advanceTimersByTime(20);
    const reloaded = pageLoad(secondTab, { framedBy: null });
    takeOwnLookBack(reloaded.page);
    expect(reloaded.replace).not.toHaveBeenCalled();
    expect(loadState(reloaded.page).usersLook).toBe(true);
    // Until the stale page is gone, what its app writes (the AR look with one change) is not
    // remembered as the user's.
    second.appWrites(APP_SETTINGS_KEY, CYCLED_IN_FRAME);
    expect(memoryOf(tab)).toMatchObject({ appSettings: OWN_LOOK, stored: "frame" });
  });

  it("where a stale page is refused its reload, it stays a page that does not run the user's look", () => {
    const tab = newTab(OWN_LOOK);
    applyEmbedLook(tab.local);
    vi.advanceTimersByTime(10_000);
    const began = Date.now();
    vi.advanceTimersByTime(400);
    restoreStandaloneLook(tab.local); // (another tab put the user's look back)
    vi.advanceTimersByTime(30);
    // This load is the plugin's own reload already, and it began before that write.
    const session = new MemoryStorage();
    session.setItem(RELOAD_FLAG, JSON.stringify({ parent: null }));
    const stale = pageLoad({ local: tab.local, session }, { framedBy: null, beganAt: began });
    watchAppWrites(stale.page);
    takeOwnLookBack(stale.page);
    expect(stale.replace).not.toHaveBeenCalled();
    expect(loadState(stale.page).usersLook).toBe(false);
    stale.appWrites(APP_SETTINGS_KEY, CYCLED_IN_FRAME);
    expect(memoryOf(tab)).toMatchObject({ appSettings: OWN_LOOK, stored: "frame" });
    // The plugin evaluated again in that page: still not the user's look, still no reload.
    takeOwnLookBack(stale.page);
    expect(loadState(stale.page).usersLook).toBe(false);
  });

  it("does not reload a page for a write that came before the app of that page read the store", () => {
    const loadAfterWrite = (msBeforeWrite: number) => {
      const tab = newTab(OWN_LOOK);
      applyEmbedLook(tab.local);
      vi.advanceTimersByTime(10_000);
      const began = Date.now();
      vi.advanceTimersByTime(msBeforeWrite);
      restoreStandaloneLook(tab.local);
      vi.advanceTimersByTime(400);
      const load = pageLoad({ local: tab.local, session: new MemoryStorage() }, { framedBy: null, beganAt: began });
      takeOwnLookBack(load.page);
      return { reloads: load.replace.mock.calls.length, usersLook: loadState(load.page).usersLook };
    };
    // Up to 50 ms after the page began, the app has not read the store yet.
    expect(loadAfterWrite(0)).toEqual({ reloads: 0, usersLook: true });
    expect(loadAfterWrite(50)).toEqual({ reloads: 0, usersLook: true });
    expect(loadAfterWrite(51)).toEqual({ reloads: 1, usersLook: false });
  });

  it("a later evaluation in the same page does not reload it for the page's own writes", () => {
    const tab = newTab(OWN_LOOK);
    applyEmbedLook(tab.local);
    restoreStandaloneLook(tab.local);
    vi.advanceTimersByTime(10_000);
    const solo = pageLoad(tab, { framedBy: null });
    watchAppWrites(solo.page);
    takeOwnLookBack(solo.page);
    expect(loadState(solo.page).usersLook).toBe(true);
    // The user changes a setting in this page: the store is written long after the page began.
    vi.advanceTimersByTime(5_000);
    const chosen = { ...OWN_LOOK, theme: "light" };
    solo.appWrites(APP_SETTINGS_KEY, chosen);
    expect(memoryOf(tab)).toMatchObject({ appSettings: chosen, stored: "user", at: Date.now() });
    // The plugin is evaluated again in the same page.
    vi.advanceTimersByTime(1_000);
    takeOwnLookBack(solo.page);
    expect(solo.replace).not.toHaveBeenCalled();
    expect(loadState(solo.page).usersLook).toBe(true);
  });

  it("treats a record without the time, or a page without a clock, as before", () => {
    const withoutTime = newTab(OWN_LOOK);
    applyEmbedLook(withoutTime.local);
    restoreStandaloneLook(withoutTime.local);
    const record = { ...memoryOf(withoutTime) };
    delete record.at;
    withoutTime.local.put(STANDALONE_LOOK_KEY, record);
    const old = pageLoad(withoutTime, { framedBy: null, beganAt: Date.now() - 60_000 });
    takeOwnLookBack(old.page);
    expect(old.replace).not.toHaveBeenCalled();
    expect(loadState(old.page).usersLook).toBe(true);

    const noClock = newTab(OWN_LOOK);
    applyEmbedLook(noClock.local);
    restoreStandaloneLook(noClock.local);
    const load = pageLoad(noClock, { framedBy: null, beganAt: Date.now() - 60_000 });
    load.page.performance.now = () => {
      throw new Error("no clock");
    };
    takeOwnLookBack(load.page);
    expect(load.replace).not.toHaveBeenCalled();
    expect(loadState(load.page).usersLook).toBe(true);
  });

  it("works without watching where the write cannot be wrapped", () => {
    const load = pageLoad(newTab());
    load.page.watchWrites = () => {
      throw new Error("setItem is not writable");
    };
    expect(() => watchAppWrites(load.page)()).not.toThrow();
  });
});

describe("seeing the app's own storage writes (the real page)", () => {
  const original = Storage.prototype.setItem;

  afterEach(() => {
    Storage.prototype.setItem = original;
    delete (globalThis as Record<string, unknown>).__arPluginWrites;
    sessionStorage.clear();
  });

  it("tells the listener what the app stored, after it is stored, and nothing else", () => {
    const page = currentPage();
    if (!page) throw new Error("the test environment has no page");
    const seen: Array<[string, string, string | null]> = [];
    const stop = page.watchWrites((key, value) => seen.push([key, value, localStorage.getItem(key)]));

    localStorage.setItem(APP_SETTINGS_KEY, '{"theme":"light"}');
    expect(seen).toEqual([[APP_SETTINGS_KEY, '{"theme":"light"}', '{"theme":"light"}']]);
    // Not the session's writes, and not what the plugin itself writes through its page object.
    sessionStorage.setItem(RELOAD_FLAG, "{}");
    expect(sessionStorage.getItem(RELOAD_FLAG)).toBe("{}");
    expect(localStorage.getItem(RELOAD_FLAG)).toBeNull();
    sessionStorage.setItem(APP_SETTINGS_KEY, '{"theme":"dark"}');
    expect(localStorage.getItem(APP_SETTINGS_KEY)).toBe('{"theme":"light"}');
    page.localStorage.setItem(STANDALONE_LOOK_KEY, "{}");
    expect(localStorage.getItem(STANDALONE_LOOK_KEY)).toBe("{}");
    expect(sessionStorage.getItem(STANDALONE_LOOK_KEY)).toBeNull();
    expect(seen).toHaveLength(1);

    stop();
    localStorage.setItem(APP_SETTINGS_KEY, '{"theme":"dark"}');
    expect(seen).toHaveLength(1);
    expect(Storage.prototype.setItem).toBe(original);
    // Without a listener the plugin's own writes still work.
    page.localStorage.setItem(STANDALONE_LOOK_KEY, "[]");
    expect(localStorage.getItem(STANDALONE_LOOK_KEY)).toBe("[]");
  });

  it("never lets a failing listener break the app's write, and wraps the function once", () => {
    const page = currentPage();
    if (!page) throw new Error("the test environment has no page");
    const stopFirst = page.watchWrites(() => {
      throw new Error("listener failed");
    });
    const wrapped = Storage.prototype.setItem;
    expect(wrapped).not.toBe(original);
    expect(() => localStorage.setItem(PANEL_STATE_KEY, "{}")).not.toThrow();
    expect(localStorage.getItem(PANEL_STATE_KEY)).toBe("{}");

    // A later evaluation of the plugin in the same page replaces the listener, not the wrapper.
    const seen: string[] = [];
    const stopSecond = currentPage()?.watchWrites((key) => seen.push(key)) ?? (() => {});
    expect(Storage.prototype.setItem).toBe(wrapped);
    // The earlier evaluation stopping afterwards does not take the newer listener away.
    stopFirst();
    localStorage.setItem(PANEL_STATE_KEY, "{}");
    expect(seen).toEqual([PANEL_STATE_KEY]);
    stopSecond();
    expect(Storage.prototype.setItem).toBe(original);
  });

  it("passes a call on as it came: what the original refuses stays refused", () => {
    const page = currentPage();
    if (!page) throw new Error("the test environment has no page");
    // What the storage does with a one-argument call and with a foreign receiver, unwrapped.
    const refusal = (call: () => void): string | null => {
      try {
        call();
        return null;
      } catch (error) {
        return (error as Error).name;
      }
    };
    const oneArgument = () => (localStorage.setItem as (key: string) => void)("ar-one-argument");
    const foreignReceiver = () => Storage.prototype.setItem.call({}, "ar-foreign", "1");
    const symbolValue = () => (localStorage.setItem as (key: string, value: unknown) => void)("ar-symbol", Symbol("x"));
    expect(refusal(oneArgument)).toBe("TypeError");
    expect(refusal(foreignReceiver)).toBe("TypeError");
    expect(refusal(symbolValue)).toBe("TypeError");

    const seen: string[] = [];
    const stop = page.watchWrites((key) => seen.push(key));
    expect(refusal(oneArgument)).toBe("TypeError");
    expect(localStorage.getItem("ar-one-argument")).toBeNull();
    expect(refusal(foreignReceiver)).toBe("TypeError");
    expect(refusal(symbolValue)).toBe("TypeError");
    expect(localStorage.getItem("ar-symbol")).toBeNull();
    expect(seen).toEqual([]);
    // Values go on unconverted too: the storage itself makes the strings.
    (localStorage.setItem as (key: string, value: unknown) => void)("ar-number", 7);
    expect(localStorage.getItem("ar-number")).toBe("7");
    expect(seen).toEqual(["ar-number"]);
    stop();
  });

  it("the production watcher preserves native arity, receiver and coercion with its transform installed", () => {
    const page = currentPage();
    if (!page) throw new Error("the test environment has no page");
    localStorage.setItem(PANEL_STATE_KEY, '{"state":{"desktop":{"agentListOpen":true}},"version":16}');
    const stop = watchAppWrites(page);
    const refused = () => {
      for (const [receiver, args] of [[localStorage, ["ar-one-argument"]], [localStorage, ["ar-symbol", Symbol("x")]], [{}, ["ar-foreign", "1"]]] as const) {
        let error: unknown;
        try { Reflect.apply(Storage.prototype.setItem, receiver, args); } catch (reason) { error = reason; }
        expect(error).toMatchObject({ name: "TypeError" });
      }
      expect(localStorage.getItem("ar-one-argument")).toBeNull();
      expect(localStorage.getItem("ar-symbol")).toBeNull();
    };
    try {
      refused();
      let conversions = 0;
      const value = { toString: () => { conversions++; return "stored"; } };
      Reflect.apply(localStorage.setItem, localStorage, ["ar-coercion", value]);
      expect(conversions).toBe(1);
      expect(localStorage.getItem("ar-coercion")).toBe("stored");
      sessionStorage.setItem("ar-session", "unchanged");
      expect(sessionStorage.getItem("ar-session")).toBe("unchanged");
      setEmbedPageMode(page, "document");
      refused();
      localStorage.setItem(PANEL_STATE_KEY, '{"state":{"desktop":{"agentListOpen":false}},"version":16}');
      expect(JSON.parse(localStorage.getItem(PANEL_STATE_KEY)!).state.desktop.agentListOpen).toBe(true);
      page.localStorage.setItem("ar-quiet", "bypassed");
      expect(localStorage.getItem("ar-quiet")).toBe("bypassed");
      clearEmbedPageMode(page);
      localStorage.setItem(PANEL_STATE_KEY, '{"state":{"desktop":{"agentListOpen":false}},"version":16}');
      expect(JSON.parse(localStorage.getItem(PANEL_STATE_KEY)!).state.desktop.agentListOpen).toBe(false);
    } finally {
      clearEmbedPageMode(page);
      stop();
    }
    expect(Storage.prototype.setItem).toBe(original);
  });

  it("leaves the function alone on stopping when something else has wrapped it since", () => {
    const page = currentPage();
    if (!page) throw new Error("the test environment has no page");
    const seen: string[] = [];
    const stop = page.watchWrites((key) => seen.push(key));
    const ours = Storage.prototype.setItem;
    const outer = function (this: Storage, key: string, value: string) {
      ours.call(this, key, value);
    };
    Storage.prototype.setItem = outer;
    stop();
    expect(Storage.prototype.setItem).toBe(outer);
    localStorage.setItem(PANEL_STATE_KEY, "{}");
    expect(localStorage.getItem(PANEL_STATE_KEY)).toBe("{}");
    expect(seen).toEqual([]);
  });
});

describe("starting the client part: which parent is trusted and what follows", () => {
  const catalog = emptyHierarchyClient();
  const client: BridgeClient = {
    ...catalog,
    openScreen: () => {},
    paseo: {
      ...catalog.paseo,
      agents: { ...catalog.paseo.agents, ref: () => ({ archivedAt: null, workspaceId: null, refresh: async () => ({}) }) },
      workspaces: { ...catalog.paseo.workspaces, ref: () => ({ refresh: async () => ({}) }) },
    },
  };
  const flush = () => vi.advanceTimersByTimeAsync(0);
  const listed = () => vi.fn(async () => EMBED);
  /** A tab whose storage holds the AR look already: a frame load there needs no reload. */
  function framedTab(look: Record<string, unknown> = OWN_LOOK): Tab {
    const tab = newTab(look);
    applyEmbedLook(tab.local);
    return tab;
  }
  const ready = [
    { data: { source: "ar-plugin", type: "ready" }, targetOrigin: DASHBOARD },
    { data: { source: "ar-plugin", type: "selection", agentIds: [] }, targetOrigin: DASHBOARD },
    { data: { source: "ar-plugin", type: "hierarchy", projects: [], workspaces: [], agents: [] }, targetOrigin: DASHBOARD },
  ];

  it("standalone tab: takes the user's look back and never reads the list", async () => {
    const tab = framedTab();
    const solo = pageLoad(tab, { framedBy: null });
    const readList = listed();
    startClientPart(solo.page, client, readList);
    await flush();

    expect(readList).not.toHaveBeenCalled();
    expect(settingsOf(tab)).toEqual({ ...OWN_LOOK, language: "system" });
    expect(solo.replace).toHaveBeenCalledExactlyOnceWith(WORKSPACE_URL);
    expect(solo.posted).toEqual([]);
    expect(solo.listening()).toBe(0);
    expect(solo.watched()).toBe(true);
  });

  it("listed frame: the AR look and the channel, once the list has confirmed the parent", async () => {
    // A first load: the look is stored and the page replaced; no channel in a page that is leaving.
    const fresh = newTab(OWN_LOOK);
    const first = pageLoad(fresh);
    startClientPart(first.page, client, listed());
    expect(settingsOf(fresh)).toEqual({ ...OWN_LOOK, language: "system" });
    await flush();
    expect(settingsOf(fresh)).toEqual({ ...EMBED_LOOK, language: "system" });
    expect(first.replace).toHaveBeenCalledExactlyOnceWith(WORKSPACE_URL);
    expect(first.posted).toEqual([]);

    // The load that follows: nothing before the list answers (the app's writes are not watched
    // either), then ready to that parent.
    const frame = pageLoad(fresh);
    const stop = startClientPart(frame.page, client, listed());
    expect(frame.posted).toEqual([]);
    expect(frame.listening()).toBe(0);
    expect(frame.watched()).toBe(false);
    await flush();
    expect(frame.posted).toEqual(ready);
    expect(frame.listening()).toBe(1);
    expect(frame.watched()).toBe(true);
    expect(loadState(frame.page).trustedParent).toBe(DASHBOARD);
    // What the app writes in that page is recorded as the frame's.
    frame.appWrites(APP_SETTINGS_KEY, CYCLED_IN_FRAME);
    expect(memoryOf(fresh)).toMatchObject({ appSettings: OWN_LOOK, stored: "frame" });

    stop();
    expect(frame.listening()).toBe(0);
    expect(frame.watched()).toBe(false);
    expect(document.getElementById("ar-plugin-embed-style")).toBeNull();
  });

  it("frame under a parent the list does not pair with this page: no channel, the user's look", async () => {
    const lists = [
      [],
      [{ dashboardOrigin: "http://other.test", frameBaseUrl: DAEMON }],
      // The parent is listed, but for another daemon address than the one this page is served from.
      [{ dashboardOrigin: DASHBOARD, frameBaseUrl: "http://localhost:6820" }],
    ];
    for (const list of lists) {
      const tab = framedTab();
      const frame = pageLoad(tab);
      startClientPart(frame.page, client, async () => list);
      await flush();
      expect(frame.posted).toEqual([]);
      expect(frame.listening()).toBe(0);
      expect(document.getElementById("ar-plugin-embed-style")).toBeNull();
      expect(loadState(frame.page).trustedParent).toBeNull();
      expect(settingsOf(tab)).toEqual({ ...OWN_LOOK, language: "system" });
      expect(frame.replace).toHaveBeenCalledTimes(1);
      // As in a standalone tab, the app's writes are watched from here on.
      expect(frame.watched()).toBe(true);
    }
    // The same for a listed dashboard origin that frames the page from an unlisted look-alike.
    const tab = framedTab();
    const frame = pageLoad(tab, { framedBy: DASHBOARD + ".evil.test" });
    startClientPart(frame.page, client, listed());
    await flush();
    expect(frame.posted).toEqual([]);
    expect(settingsOf(tab)).toEqual({ ...OWN_LOOK, language: "system" });
  });

  it("a list that cannot be read: no parent is trusted and nothing changes", async () => {
    const failures = [
      () => Promise.reject(new Error("rpc failed")),
      () => {
        throw new Error("rpc unavailable");
      },
      async () => null as unknown as typeof EMBED,
    ];
    for (const readList of failures) {
      const tab = newTab(OWN_LOOK);
      const frame = pageLoad(tab);
      startClientPart(frame.page, client, readList);
      await flush();
      expect(frame.posted).toEqual([]);
      expect(frame.listening()).toBe(0);
      expect(frame.replace).not.toHaveBeenCalled();
      expect(settingsOf(tab)).toEqual({ ...OWN_LOOK, language: "system" });
      expect(memoryOf(tab)).toBeNull();
      expect(loadState(frame.page).usersLook).toBeNull();
      expect(frame.watched()).toBe(false);
    }
  });

  it("a frame whose parent nothing names: nothing at all, and the list is not read", async () => {
    const tab = framedTab();
    const frame = pageLoad(tab, { ancestorOrigins: false });
    const readList = listed();
    startClientPart(frame.page, client, readList);
    await flush();
    expect(readList).not.toHaveBeenCalled();
    expect(frame.posted).toEqual([]);
    expect(frame.replace).not.toHaveBeenCalled();
    expect(settingsOf(tab)).toEqual({ ...EMBED_LOOK, language: "system" });
    expect(frame.watched()).toBe(false);
  });

  it("evaluated again in the same page: no gap in the channel, and the list has the last word", async () => {
    const tab = framedTab();
    const frame = pageLoad(tab);
    const stopFirst = startClientPart(frame.page, client, listed());
    await flush();
    stopFirst();
    frame.posted.length = 0;

    // The second evaluation answers at once, on the first one's verified parent ...
    let answer: (list: typeof EMBED) => void = () => {};
    startClientPart(frame.page, client, () => new Promise((resolve) => (answer = resolve)));
    expect(frame.posted).toEqual(ready.slice(0, 2));
    expect(frame.listening()).toBe(1);
    expect(frame.watched()).toBe(true);
    // ... and the list, which no longer has the parent, takes the channel and the look away.
    await flush();
    answer([]);
    await flush();
    expect(frame.listening()).toBe(0);
    expect(loadState(frame.page).trustedParent).toBeNull();
    expect(settingsOf(tab)).toEqual({ ...OWN_LOOK, language: "system" });
    expect(frame.posted).toEqual(ready);
  });

  it("evaluated again under a parent the page did not verify: waits for the list", async () => {
    const frame = pageLoad(framedTab());
    loadState(frame.page).trustedParent = "http://other.test";
    startClientPart(frame.page, client, () => new Promise(() => {}));
    await flush();
    expect(frame.posted).toEqual([]);
    expect(frame.listening()).toBe(0);
    expect(frame.watched()).toBe(false);
  });

  it("evaluated again where the list cannot be read any more: what the page is stays known, its writes stay watched", async () => {
    // A standalone-like page (an unlisted parent) and a listed frame, each settled by a first evaluation.
    for (const first of [async () => [], listed()]) {
      const frame = pageLoad(framedTab());
      const stopFirst = startClientPart(frame.page, client, first);
      await flush();
      stopFirst();
      const usersLook = loadState(frame.page).usersLook;
      expect(usersLook).not.toBeNull();
      loadState(frame.page).trustedParent = null;
      const stop = startClientPart(frame.page, client, () => Promise.reject(new Error("rpc failed")));
      await flush();
      expect(frame.watched()).toBe(true);
      expect(loadState(frame.page).usersLook).toBe(usersLook);
      stop();
      expect(frame.watched()).toBe(false);
    }
  });

  it("stopped before the list answers: the late answer does nothing", async () => {
    const tab = framedTab();
    const frame = pageLoad(tab);
    let answer: (list: typeof EMBED) => void = () => {};
    const stop = startClientPart(frame.page, client, () => new Promise((resolve) => (answer = resolve)));
    await flush();
    stop();
    answer(EMBED);
    await flush();
    expect(frame.posted).toEqual([]);
    expect(frame.listening()).toBe(0);
    expect(loadState(frame.page).trustedParent).toBeNull();
  });
});

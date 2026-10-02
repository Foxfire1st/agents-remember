// The client part of the AR plugin for the Paseo runtime (PNT-R05). Its source lives with the
// plugin, under mcp/src/agents_remember/package_data/paseo_plugin/client/, and imports nothing
// from Paseo: the browser objects and the plugin's client context are passed in. This file is
// where the repository's gates run and type-check it.
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
} from "../../../mcp/src/agents_remember/package_data/paseo_plugin/client/load";
import {
  APP_SETTINGS_KEY,
  EMBED_LOOK,
  PANEL_STATE_KEY,
  STANDALONE_LOOK_KEY,
  applyEmbedLook,
  restoreStandaloneLook,
} from "../../../mcp/src/agents_remember/package_data/paseo_plugin/client/look";
import type { PluginPage, StorageLike } from "../../../mcp/src/agents_remember/package_data/paseo_plugin/client/page";

const DASHBOARD = "http://127.0.0.1:9797";
const DAEMON = "http://127.0.0.1:6820";
const WORKSPACE_URL = DAEMON + "/h/srv_test/workspace/wks_projects";
const EMBED = [{ dashboardOrigin: DASHBOARD, frameBaseUrl: DAEMON }];
// What a browser profile holds before the dashboard was ever used, and two looks a user may set.
const DEFAULT_LOOK = { theme: "auto", pluginThemeId: null, uiFontFamily: "", monoFontFamily: "" };
const OWN_LOOK = { theme: "dark", pluginThemeId: null, uiFontFamily: "Georgia, serif", monoFontFamily: "" };

class MemoryStorage implements StorageLike {
  readonly items = new Map<string, string>();
  getItem(key: string): string | null {
    return this.items.get(key) ?? null;
  }
  setItem(key: string, value: string): void {
    this.items.set(key, value);
  }
  removeItem(key: string): void {
    this.items.delete(key);
  }
  json(key: string): unknown {
    const raw = this.getItem(key);
    return raw === null ? null : JSON.parse(raw);
  }
  put(key: string, value: unknown): void {
    this.setItem(key, JSON.stringify(value));
  }
}

interface Posted {
  data: Record<string, unknown>;
  targetOrigin: string;
}

interface Tab {
  local: MemoryStorage;
  session: MemoryStorage;
}

/** One page load: a fresh page global, the tab's storages, and what the browser tells the page. */
function pageLoad(
  tab: Tab,
  options: {
    framedBy?: string | null;
    ancestorOrigins?: boolean;
    referrer?: string;
    href?: string;
    requested?: string;
  } = {},
) {
  const framedBy = options.framedBy === undefined ? DASHBOARD : options.framedBy;
  const href = options.href ?? WORKSPACE_URL;
  const posted: Posted[] = [];
  const listeners = new Set<(event: { origin: string; source: unknown; data: unknown }) => void>();
  const parent = {
    postMessage: (data: Record<string, unknown>, targetOrigin: string) => posted.push({ data, targetOrigin }),
  };
  const pageWindow: Record<string, unknown> = {
    addEventListener: (_type: string, listener: (typeof listeners extends Set<infer L> ? L : never)) => listeners.add(listener),
    removeEventListener: (_type: string, listener: (typeof listeners extends Set<infer L> ? L : never)) => listeners.delete(listener),
    setInterval: (handler: () => void, ms: number) => window.setInterval(handler, ms),
    clearInterval: (timer: number) => window.clearInterval(timer),
  };
  pageWindow.parent = framedBy === null ? pageWindow : parent;
  const replace = vi.fn();
  const url = new URL(href);
  const page: PluginPage = {
    window: pageWindow,
    document: {
      referrer: options.referrer ?? "",
      head: document.head,
      getElementById: (id: string) => document.getElementById(id),
      createElement: (tag: string) => document.createElement(tag),
      querySelector: (selector: string) => document.querySelector(selector),
    },
    location: {
      href,
      origin: url.origin,
      pathname: url.pathname,
      replace,
      ...(framedBy !== null && options.ancestorOrigins !== false ? { ancestorOrigins: [framedBy] } : {}),
    },
    performance: { getEntriesByType: () => [{ name: options.requested ?? href }] },
    localStorage: tab.local,
    sessionStorage: tab.session,
    state: {},
  };
  return {
    page,
    replace,
    posted,
    parent,
    /** Deliver a message event to the page, by default from the parent window and origin. */
    receive(data: unknown, from: { origin?: string; source?: unknown } = {}) {
      for (const listener of listeners) {
        listener({ origin: from.origin ?? DASHBOARD, source: "source" in from ? from.source : parent, data });
      }
    },
    listening: () => listeners.size,
  };
}

function newTab(look: Record<string, unknown> | null = DEFAULT_LOOK, sidebarOpen = true): Tab {
  const local = new MemoryStorage();
  if (look) local.put(APP_SETTINGS_KEY, { ...look, language: "system" });
  local.put(PANEL_STATE_KEY, { state: { desktop: { agentListOpen: sidebarOpen, focusModeEnabled: false } }, version: 16 });
  return { local, session: new MemoryStorage() };
}

const settingsOf = (tab: Tab) => tab.local.json(APP_SETTINGS_KEY) as Record<string, unknown>;
const memoryOf = (tab: Tab) =>
  tab.local.json(STANDALONE_LOOK_KEY) as { appSettings: Record<string, unknown>; agentListOpen: boolean | null; stored: string } | null;
const sidebarOf = (tab: Tab) =>
  (tab.local.json(PANEL_STATE_KEY) as { state: { desktop: { agentListOpen: boolean } } }).state.desktop.agentListOpen;
/** What Paseo does on any settings change: it writes all of the page's in-memory settings. */
const paseoWrites = (tab: Tab, look: Record<string, unknown>) => tab.local.put(APP_SETTINGS_KEY, { ...look, language: "system" });

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  document.head.innerHTML = "";
  document.body.innerHTML = "";
});

describe("the look stored for the frame and the look remembered for a standalone tab", () => {
  it("remembers the user's look before it stores the AR look, and stores it once", () => {
    const tab = newTab(OWN_LOOK);

    expect(applyEmbedLook(tab.local)).toEqual(["theme", "pluginThemeId", "uiFontFamily", "monoFontFamily"]);
    expect(settingsOf(tab)).toEqual({ ...EMBED_LOOK, language: "system" });
    expect(sidebarOf(tab)).toBe(false);
    expect(memoryOf(tab)).toEqual({ appSettings: OWN_LOOK, agentListOpen: true, stored: "frame" });

    // The next frame load finds the AR look: nothing to change, nothing to remember.
    expect(applyEmbedLook(tab.local)).toEqual([]);
    expect(memoryOf(tab)).toEqual({ appSettings: OWN_LOOK, agentListOpen: true, stored: "frame" });
  });

  it("gives a standalone tab the user's look back, keys the user never had included", () => {
    const tab = newTab({ theme: "dark" });
    applyEmbedLook(tab.local);

    expect(restoreStandaloneLook(tab.local)).toBe(true);
    expect(settingsOf(tab)).toEqual({ theme: "dark", language: "system" });
    expect(sidebarOf(tab)).toBe(true);
    expect(memoryOf(tab)?.stored).toBe("user");
    // Nothing left to put back.
    expect(restoreStandaloneLook(tab.local)).toBe(false);
    // A tab that never shared storage with a frame has nothing remembered and is left alone.
    const untouched = newTab(OWN_LOOK);
    expect(restoreStandaloneLook(untouched.local)).toBe(false);
    expect(memoryOf(untouched)).toBeNull();
    expect(settingsOf(untouched)).toEqual({ ...OWN_LOOK, language: "system" });
  });

  it("puts the default sidebar back when the app had stored none before the frame closed it", () => {
    // A first visit: the frame's look is stored before the app has written any sidebar state.
    const tab = newTab(DEFAULT_LOOK);
    tab.local.removeItem(PANEL_STATE_KEY);
    applyEmbedLook(tab.local);
    expect(memoryOf(tab)?.agentListOpen).toBeNull();
    // The frame then closes the sidebar through the app's toggle, and the app stores that.
    tab.local.put(PANEL_STATE_KEY, { state: { desktop: { agentListOpen: false } }, version: 16 });

    expect(restoreStandaloneLook(tab.local)).toBe(true);
    expect(sidebarOf(tab)).toBe(true);
    expect(settingsOf(tab)).toEqual({ ...DEFAULT_LOOK, language: "system" });
  });

  it("does not take a font changed inside the frame for the user's standalone look", () => {
    const tab = newTab(OWN_LOOK);
    applyEmbedLook(tab.local);
    // Inside the frame the page runs the AR look, so its write keeps the other three AR values.
    paseoWrites(tab, { ...EMBED_LOOK, uiFontFamily: '"Courier New", monospace' });

    // The next frame load puts the AR font back and keeps the remembered standalone look.
    expect(applyEmbedLook(tab.local)).toEqual(["uiFontFamily"]);
    expect(memoryOf(tab)).toEqual({ appSettings: OWN_LOOK, agentListOpen: true, stored: "frame" });

    // A standalone tab that loads in that state gets the user's look, not the frame's font.
    paseoWrites(tab, { ...EMBED_LOOK, uiFontFamily: '"Courier New", monospace' });
    expect(restoreStandaloneLook(tab.local)).toBe(true);
    expect(settingsOf(tab)).toEqual({ ...OWN_LOOK, language: "system" });
  });

  it("keeps a look the user set in a standalone tab while the frame's mark was on the storage", () => {
    const tab = newTab(DEFAULT_LOOK);
    applyEmbedLook(tab.local);
    expect(memoryOf(tab)?.stored).toBe("frame");
    // A standalone tab loaded before the frame runs the user's look; the user types a font and
    // Paseo writes that page's whole look over the storage. The mark still says "frame".
    const usersNewLook = { ...DEFAULT_LOOK, uiFontFamily: "Verdana" };
    paseoWrites(tab, usersNewLook);

    // The standalone tab's next load adopts it: the settings are not touched. Only the sidebar,
    // which the frame closed when it stored its look, is put back (that is the one change).
    expect(restoreStandaloneLook(tab.local)).toBe(true);
    expect(settingsOf(tab)).toEqual({ ...usersNewLook, language: "system" });
    expect(sidebarOf(tab)).toBe(true);
    expect(memoryOf(tab)).toEqual({ appSettings: usersNewLook, agentListOpen: true, stored: "user" });
    // With the sidebar as the user had it, the adoption changes nothing at all.
    const closed = newTab(DEFAULT_LOOK, false);
    applyEmbedLook(closed.local);
    paseoWrites(closed, usersNewLook);
    expect(restoreStandaloneLook(closed.local)).toBe(false);
    expect(settingsOf(closed)).toEqual({ ...usersNewLook, language: "system" });
    expect(memoryOf(closed)).toEqual({ appSettings: usersNewLook, agentListOpen: false, stored: "user" });
    // Adopted once: a further load of the tab finds nothing to do.
    expect(restoreStandaloneLook(tab.local)).toBe(false);

    // The frame's next load stores the AR look again and still remembers the user's new look.
    expect(applyEmbedLook(tab.local)).toHaveLength(4);
    expect(memoryOf(tab)).toEqual({ appSettings: usersNewLook, agentListOpen: true, stored: "frame" });
    expect(restoreStandaloneLook(tab.local)).toBe(true);
    expect(settingsOf(tab)).toEqual({ ...usersNewLook, language: "system" });
  });

  it("remembers a look set in a standalone tab when the frame loads before that tab does", () => {
    const tab = newTab(DEFAULT_LOOK);
    applyEmbedLook(tab.local);
    const usersNewLook = { ...DEFAULT_LOOK, theme: "light", uiFontFamily: "Verdana" };
    paseoWrites(tab, usersNewLook);

    // The other order: the frame loads first. It must not treat the mark as proof either.
    expect(applyEmbedLook(tab.local)).toHaveLength(4);
    expect(memoryOf(tab)).toEqual({ appSettings: usersNewLook, agentListOpen: true, stored: "frame" });
    expect(settingsOf(tab)).toEqual({ ...EMBED_LOOK, language: "system" });
  });

  it("restores again when a frame that stayed open stored the AR look after the tab took its own back", () => {
    const tab = newTab(OWN_LOOK);
    applyEmbedLook(tab.local);
    restoreStandaloneLook(tab.local);
    paseoWrites(tab, EMBED_LOOK);

    expect(restoreStandaloneLook(tab.local)).toBe(true);
    expect(settingsOf(tab)).toEqual({ ...OWN_LOOK, language: "system" });
    // And the frame's next load knows the storage is its own again without forgetting the user's look.
    paseoWrites(tab, EMBED_LOOK);
    expect(applyEmbedLook(tab.local)).toEqual([]);
    expect(memoryOf(tab)).toEqual({ appSettings: OWN_LOOK, agentListOpen: true, stored: "frame" });
  });
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

  it("repairs a first-visit deep link with the same single reload, and closes the sidebar otherwise", () => {
    const deepLink = WORKSPACE_URL + "?open=agent:a1";
    const tab = newTab(DEFAULT_LOOK);
    // First visit: the app bounced the deep link to its project picker; the look is not stored yet.
    const bounced = pageLoad(tab, { href: DAEMON + "/open-project", requested: deepLink });
    expect(requestedUrl(bounced.page)).toBe(deepLink);
    expect(bootstrapEmbed(bounced.page, DASHBOARD)).toBe(true);
    expect(bounced.replace).toHaveBeenCalledExactlyOnceWith(deepLink);

    // The look is stored already but the link bounced: still one reload to the link.
    const warmTab = newTab(DEFAULT_LOOK);
    applyEmbedLook(warmTab.local);
    const welcome = pageLoad(warmTab, { href: DAEMON + "/welcome", requested: deepLink });
    expect(bootstrapEmbed(welcome.page, DASHBOARD)).toBe(true);
    expect(welcome.replace).toHaveBeenCalledExactlyOnceWith(deepLink);

    // Nothing to store and nothing to repair: no reload; an open sidebar is closed by the app's toggle.
    const toggled = vi.fn();
    document.body.innerHTML = '<button data-testid="menu-button"></button><div data-testid="sidebar-footer"></div>';
    (document.querySelector('[data-testid="menu-button"]') as HTMLElement).addEventListener("click", toggled);
    (document.querySelector('[data-testid="sidebar-footer"]') as HTMLElement).getBoundingClientRect = () =>
      ({ width: 320 }) as DOMRect;
    const settledTab = newTab(DEFAULT_LOOK);
    applyEmbedLook(settledTab.local);
    const warm = pageLoad(settledTab);
    expect(bootstrapEmbed(warm.page, DASHBOARD)).toBe(false);
    expect(warm.replace).not.toHaveBeenCalled();
    vi.advanceTimersByTime(300);
    expect(toggled).toHaveBeenCalledTimes(1);
    // Once per page load: a second evaluation in the same page does not do it again.
    expect(bootstrapEmbed(warm.page, DASHBOARD)).toBe(false);
    vi.advanceTimersByTime(10_000);
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
    const opened: Array<Record<string, string> | undefined> = [];
    const agents: Record<string, { archivedAt: string | null; workspaceId: string } | Error> = {
      live: { archivedAt: null, workspaceId: "wks_leaf" },
      archived: { archivedAt: "2026-10-02T01:00:00.000Z", workspaceId: "wks_leaf" },
      broken: new Error("socket closed"),
    };
    const client: BridgeClient = {
      openScreen: (input) => opened.push(input.params),
      paseo: {
        agents: {
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
  const answers = (posted: Posted[]) => posted.map((entry) => entry.data);
  const flush = () => vi.advanceTimersByTimeAsync(0);

  it("reports ready to the listed parent only, hides the header row, and stops cleanly", () => {
    const load = pageLoad(newTab());
    const stop = installBridge(fakeClient().client, load.page, DASHBOARD);

    expect(load.posted).toEqual([{ data: { source: "ar-plugin", type: "ready" }, targetOrigin: DASHBOARD }]);
    expect(document.getElementById("ar-plugin-embed-style")?.textContent).toContain('[data-testid="composer-dock-header"]');
    expect(load.listening()).toBe(1);

    stop();
    expect(load.listening()).toBe(0);
    expect(document.getElementById("ar-plugin-embed-style")).toBeNull();
  });

  it("answers the parent and nobody else", async () => {
    const load = pageLoad(newTab());
    const { client, opened } = fakeClient();
    installBridge(client, load.page, DASHBOARD);
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
    for (const agentId of ["archived", "gone", "broken"]) load.receive({ type: "ar.open", agentId });
    load.receive({ type: "ar.open" });
    await flush();
    expect(opened).toHaveLength(1);
    expect(answers(load.posted).map((answer) => [answer.type, answer.code, answer.agentId])).toEqual([
      ["error", "open-failed", undefined],
      ["error", "agent-archived", "archived"],
      ["error", "agent-not-found", "gone"],
      ["error", "open-failed", "broken"],
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
    load.receive({ type: "ar.open", workspaceId: "wks_broken" });
    await flush();
    expect(opened).toHaveLength(1);
    expect(answers(load.posted).map((answer) => [answer.type, answer.code, answer.workspaceId, answer.message])).toEqual([
      ["error", "workspace-not-found", "wks_doesnotexist0000", "the runtime has no such workspace"],
      ["error", "workspace-not-found", "wks_broken", "socket closed"],
    ]);
  });
});

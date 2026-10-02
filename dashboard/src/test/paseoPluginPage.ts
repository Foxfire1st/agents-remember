// Shared by the cases on the AR plugin's client part (paseoPluginClient.test.ts and
// paseoPluginLook.test.ts): storages, tabs and page loads made of plain objects, and the writes
// the Paseo app makes. Test support, not product code.
import { vi } from "vitest";

import {
  APP_SETTINGS_KEY,
  EMBED_LOOK,
  PANEL_STATE_KEY,
  STANDALONE_LOOK_KEY,
  recordAppWrite,
} from "../../../mcp/src/agents_remember/package_data/paseo_plugin/client/look";
import type { PluginPage, StorageLike, WriteListener } from "../../../mcp/src/agents_remember/package_data/paseo_plugin/client/page";

export const DASHBOARD = "http://127.0.0.1:9797";
export const DAEMON = "http://127.0.0.1:6820";
export const WORKSPACE_URL = DAEMON + "/h/srv_test/workspace/wks_projects";
export const EMBED = [{ dashboardOrigin: DASHBOARD, frameBaseUrl: DAEMON }];
// What a browser profile holds before the dashboard was ever used, and two looks a user may set.
export const DEFAULT_LOOK = { theme: "auto", pluginThemeId: null, uiFontFamily: "", monoFontFamily: "" };
export const OWN_LOOK = { theme: "dark", pluginThemeId: null, uiFontFamily: "Georgia, serif", monoFontFamily: "" };

export class MemoryStorage implements StorageLike {
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

export interface Posted {
  data: Record<string, unknown>;
  targetOrigin: string;
}

export interface Tab {
  local: MemoryStorage;
  session: MemoryStorage;
}

/** One page load: a fresh page global, the tab's storages, and what the browser tells the page. */
export function pageLoad(
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
  let watching: WriteListener | null = null;
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
    watchWrites: (listener) => {
      watching = listener;
      return () => {
        if (watching === listener) watching = null;
      };
    },
  };
  return {
    page,
    replace,
    posted,
    parent,
    /** The app of THIS page stores a value: the page's plugin, if it watches, is told. */
    appWrites(key: string, value: unknown) {
      tab.local.put(key, value);
      watching?.(key, JSON.stringify(value));
    },
    watched: () => watching !== null,
    /** Deliver a message event to the page, by default from the parent window and origin. */
    receive(data: unknown, from: { origin?: string; source?: unknown } = {}) {
      for (const listener of listeners) {
        listener({ origin: from.origin ?? DASHBOARD, source: "source" in from ? from.source : parent, data });
      }
    },
    listening: () => listeners.size,
  };
}

export function newTab(look: Record<string, unknown> | null = DEFAULT_LOOK, sidebarOpen = true): Tab {
  const local = new MemoryStorage();
  if (look) local.put(APP_SETTINGS_KEY, { ...look, language: "system" });
  local.put(PANEL_STATE_KEY, { state: { desktop: { agentListOpen: sidebarOpen, focusModeEnabled: false } }, version: 16 });
  return { local, session: new MemoryStorage() };
}

export const settingsOf = (tab: Tab) => tab.local.json(APP_SETTINGS_KEY) as Record<string, unknown>;
export interface Memory {
  appSettings: Record<string, unknown>;
  agentListOpen: boolean | null;
  stored: string;
  seen?: Record<string, unknown>;
  sidebar?: { stored: string; seen: boolean | null };
}
export const memoryOf = (tab: Tab) => tab.local.json(STANDALONE_LOOK_KEY) as Memory | null;
export const sidebarOf = (tab: Tab) =>
  (tab.local.json(PANEL_STATE_KEY) as { state: { desktop: { agentListOpen: boolean } } }).state.desktop.agentListOpen;
export const panelState = (open: boolean) => ({ state: { desktop: { agentListOpen: open, focusModeEnabled: false } }, version: 16 });
/**
 * What Paseo does on any settings change: it writes all of the page's in-memory settings. Here
 * nobody records the write (the plugin was off, or an earlier version of it ran).
 */
export const paseoWrites = (tab: Tab, look: Record<string, unknown>) => tab.local.put(APP_SETTINGS_KEY, { ...look, language: "system" });
/** The same write in a page whose plugin records it. `usersLook`: which look that page runs. */
export function recordedWrite(tab: Tab, usersLook: boolean, look: Record<string, unknown>): void {
  paseoWrites(tab, look);
  recordAppWrite(tab.local, usersLook, APP_SETTINGS_KEY, tab.local.getItem(APP_SETTINGS_KEY) ?? "");
}
/** The app stores its panel state (the sidebar toggled) in a page whose plugin records it. */
export function recordedSidebar(tab: Tab, usersLook: boolean, open: boolean): void {
  tab.local.put(PANEL_STATE_KEY, panelState(open));
  recordAppWrite(tab.local, usersLook, PANEL_STATE_KEY, tab.local.getItem(PANEL_STATE_KEY) ?? "");
}
// What a page that runs the AR look writes after "Cycle theme": the other three AR values stay.
export const CYCLED_IN_FRAME = { ...EMBED_LOOK, theme: "light" };
// The user chose the "Agents Remember" theme by hand in a standalone tab, with a font of their own.
export const HAND_PICKED = { theme: "plugin", pluginThemeId: EMBED_LOOK.pluginThemeId, uiFontFamily: "Georgia", monoFontFamily: "" };

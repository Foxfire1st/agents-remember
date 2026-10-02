import type { PluginPage, StorageLike } from "./page";

// The look of the app inside the AR dashboard's frame, and giving a standalone tab its own back.
//
// UNSUPPORTED Paseo behaviour this file relies on. Paseo has no plugin interface for selecting a
// theme, setting fonts or hiding chrome; a Paseo release can change any of these:
//   - localStorage["@paseo:app-settings"]: theme, pluginThemeId, uiFontFamily, monoFontFamily;
//   - the app reading that store once, when its page loads, and writing its WHOLE in-memory
//     settings back on any settings change (which is how a tab that runs one look can overwrite
//     the other look in a storage both share);
//   - localStorage["panel-state"].state.desktop.agentListOpen (the left sidebar), which the app
//     may not have written yet on a first visit and whose default is "open";
//   - the test ids "composer-dock-header" (workspace header row), "menu-button" (the app's own
//     sidebar toggle) and "sidebar-footer" (present while the sidebar is shown).

export const PLUGIN_ID = "ar-plugin";
export const THEME_ID = "agents-remember";
// The dashboard's own stack (dashboard/src/styles/tokens.css, --font-mono).
export const AR_FONT_STACK = 'ui-monospace, "JetBrains Mono", "SFMono-Regular", Menlo, monospace';

export const APP_SETTINGS_KEY = "@paseo:app-settings";
export const PANEL_STATE_KEY = "panel-state";
// The look the user had outside the frame, kept so a standalone tab can take it back.
export const STANDALONE_LOOK_KEY = "ar-plugin:standalone-look";
const HEADER_STYLE_ID = "ar-plugin-embed-style";
const HIDE_HEADER_CSS = '[data-testid="composer-dock-header"]{display:none !important}';
// What the app shows while it has stored no sidebar state yet.
const SIDEBAR_OPEN_BY_DEFAULT = true;
const SIDEBAR_SETTLE_MS = 250;
const SIDEBAR_ATTEMPTS = 24;

export const EMBED_LOOK: Readonly<Record<string, string>> = {
  theme: "plugin",
  pluginThemeId: `${PLUGIN_ID}/theme/${THEME_ID}`,
  uiFontFamily: AR_FONT_STACK,
  monoFontFamily: AR_FONT_STACK,
};
const LOOK_KEYS = Object.keys(EMBED_LOOK);

export interface StandaloneLook {
  // Only the keys the settings held; a key missing here was missing there.
  appSettings: Record<string, unknown>;
  // `null`: the app had stored no sidebar state yet, so the user's sidebar was the default.
  agentListOpen: boolean | null;
  // Which page last stored a look: the frame (this plugin stored the AR look) or a standalone
  // tab (it took the user's look back). It is a hint, not proof: see `isUsersLook`.
  stored: "frame" | "user";
}

function readJson(storage: StorageLike, key: string): any {
  try {
    const raw = storage.getItem(key);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function writeJson(storage: StorageLike, key: string, value: unknown): void {
  storage.setItem(key, JSON.stringify(value));
}

function sidebarFlag(storage: StorageLike): boolean | null {
  const open = readJson(storage, PANEL_STATE_KEY)?.state?.desktop?.agentListOpen;
  return typeof open === "boolean" ? open : null;
}

function setSidebarFlag(storage: StorageLike, open: boolean): void {
  const panel = readJson(storage, PANEL_STATE_KEY);
  if (panel?.state?.desktop && panel.state.desktop.agentListOpen !== open) {
    panel.state.desktop.agentListOpen = open;
    writeJson(storage, PANEL_STATE_KEY, panel);
  }
}

function readStandaloneLook(storage: StorageLike): StandaloneLook | null {
  const own = readJson(storage, STANDALONE_LOOK_KEY);
  return own && typeof own.appSettings === "object" && own.appSettings !== null ? own : null;
}

function lookOf(settings: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(
    LOOK_KEYS.filter((key) => key in settings).map((key) => [key, settings[key]]),
  );
}

/**
 * Whether the stored settings were written by a page that runs the user's own look. A page
 * inside the frame runs the AR look, and the app writes all of its in-memory settings at once,
 * so whatever such a page writes leaves at least one of the four look settings at its AR value.
 * Settings with none of them at the AR value therefore come from a standalone tab, whatever the
 * mark says: the tab may have been loaded before the frame stored the AR look.
 */
function isUsersLook(settings: Record<string, unknown>): boolean {
  return LOOK_KEYS.every((key) => settings[key] !== EMBED_LOOK[key]);
}

/**
 * Inside a listed frame: store the AR look for the next page load and report which settings had
 * to change. The user's own look is remembered before it is replaced: when no frame has stored
 * a look yet, when a standalone tab took its look back since, and when the stored settings are
 * a standalone tab's (see `isUsersLook`). A change made inside the frame is not remembered.
 */
export function applyEmbedLook(storage: StorageLike): string[] {
  try {
    const settings = readJson(storage, APP_SETTINGS_KEY) ?? {};
    const changed = LOOK_KEYS.filter((key) => settings[key] !== EMBED_LOOK[key]);
    const own = readStandaloneLook(storage);
    if (changed.length > 0 && (own?.stored !== "frame" || isUsersLook(settings))) {
      writeJson(storage, STANDALONE_LOOK_KEY, {
        appSettings: lookOf(settings),
        // The sidebar's stored state cannot be attributed while the mark says "frame": keep
        // what was remembered for the standalone tab.
        agentListOpen: own?.stored === "frame" ? own.agentListOpen : sidebarFlag(storage),
        stored: "frame",
      } satisfies StandaloneLook);
    } else if (own && own.stored !== "frame") {
      // A frame that was still open stored the AR look again after a standalone tab took its
      // own back: the storage is the frame's again, the remembered look stays the user's.
      writeJson(storage, STANDALONE_LOOK_KEY, { ...own, stored: "frame" } satisfies StandaloneLook);
    }
    if (changed.length === 0) return changed;
    writeJson(storage, APP_SETTINGS_KEY, { ...settings, ...EMBED_LOOK });
    // The page is about to load again, so the sidebar can start closed instead of closing late.
    setSidebarFlag(storage, false);
    return changed;
  } catch {
    return [];
  }
}

/**
 * Outside a frame of a listed dashboard: put the user's own look back when the storage holds
 * the frame's. Returns whether anything stored changed (the page then has to load again).
 *
 * Settings that are the user's already (see `isUsersLook`) are never touched: they are adopted
 * as the remembered look instead, so a look the user set in a standalone tab while a frame's
 * mark was on the shared storage survives. The sidebar's stored state is put back whenever the
 * mark says the frame stored a look, because the frame closed it then. The memory itself is
 * kept, because a frame that is still open may store the AR look again.
 */
export function restoreStandaloneLook(storage: StorageLike): boolean {
  try {
    const own = readStandaloneLook(storage);
    if (!own) return false;
    const settings = readJson(storage, APP_SETTINGS_KEY) ?? {};
    const usersLook = isUsersLook(settings);
    const framesLook = LOOK_KEYS.every((key) => settings[key] === EMBED_LOOK[key]);
    if (own.stored !== "frame" && (usersLook || !framesLook)) return false;
    let changed = false;
    if (!usersLook) {
      for (const key of LOOK_KEYS) {
        if (key in own.appSettings ? settings[key] === own.appSettings[key] : !(key in settings)) continue;
        if (key in own.appSettings) settings[key] = own.appSettings[key];
        else delete settings[key];
        changed = true;
      }
      if (changed) writeJson(storage, APP_SETTINGS_KEY, settings);
    }
    if (own.stored === "frame") {
      const open = own.agentListOpen ?? SIDEBAR_OPEN_BY_DEFAULT;
      const stored = sidebarFlag(storage);
      // Nothing stored still means the default: there is nothing to put back.
      if (stored !== null && stored !== open) {
        setSidebarFlag(storage, open);
        changed = true;
      }
    }
    writeJson(storage, STANDALONE_LOOK_KEY, {
      appSettings: usersLook ? lookOf(settings) : own.appSettings,
      agentListOpen: own.agentListOpen,
      stored: "user",
    } satisfies StandaloneLook);
    return changed;
  } catch {
    return false;
  }
}

export function setWorkspaceHeaderHidden(document: PluginPage["document"], hidden: boolean): void {
  const existing = document.getElementById(HEADER_STYLE_ID);
  if (!hidden) {
    existing?.remove();
    return;
  }
  if (existing) return;
  const style = document.createElement("style");
  style.id = HEADER_STYLE_ID;
  style.textContent = HIDE_HEADER_CSS;
  document.head.appendChild(style);
}

/**
 * Close the left sidebar once the workspace is on screen, through the app's own toggle, when the
 * page loaded with it open. It runs once per page load: afterwards the sidebar is the user's
 * (Paseo's shortcut reopens it).
 */
export function closeSidebarAtLoad(page: PluginPage): void {
  let attempts = 0;
  const timer = page.window.setInterval(() => {
    attempts += 1;
    const toggle = page.document.querySelector('[data-testid="menu-button"]');
    if (toggle) {
      const footer = page.document.querySelector('[data-testid="sidebar-footer"]');
      if (footer && footer.getBoundingClientRect().width > 0) toggle.click();
      page.window.clearInterval(timer);
    } else if (attempts >= SIDEBAR_ATTEMPTS) {
      page.window.clearInterval(timer);
    }
  }, SIDEBAR_SETTLE_MS);
}

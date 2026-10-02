import type { PluginPage, StorageLike } from "./page";

// The look of the app inside the AR dashboard's frame, and giving a standalone tab its own back.
//
// A frame and a standalone tab of the same browser can share one localStorage, and the app keeps
// one look in it. This file keeps the user's own look beside it (STANDALONE_LOOK_KEY) and records
// who wrote the app's stores last: a page that runs the AR look or a page that runs the user's.
// The record is made when the write happens (the plugin's own writes here, the app's writes
// through `recordAppWrite`), so at the next page load it is proof and not a guess.
//
// UNSUPPORTED Paseo behaviour this file relies on. Paseo has no plugin interface for selecting a
// theme, setting fonts or hiding chrome; a Paseo release can change any of these:
//   - localStorage["@paseo:app-settings"]: theme, pluginThemeId, uiFontFamily, monoFontFamily;
//   - the app reading that store once, when its page loads, and writing its WHOLE in-memory
//     settings back on any settings change (which is how a tab that runs one look can overwrite
//     the other look in a storage both share);
//   - the app's "Cycle theme" shortcut and its settings screen working inside the frame, and
//     writing through the same store (a look changed there is recorded as the frame's);
//   - the app keeping `pluginThemeId` when another theme is chosen: the id counts as the AR
//     look only together with `theme: "plugin"` (content rule, used only when no record holds);
//   - localStorage["panel-state"].state.desktop.agentListOpen (the left sidebar), which the app
//     may not have written yet on a first visit and whose default is "open", and the app
//     writing its whole panel state on any panel change;
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

/** Which kind of page wrote a store: one that runs the AR look, or one that runs the user's. */
export type Writer = "frame" | "user";

export interface StandaloneLook {
  // The user's own look. Only the keys the settings held; a key missing here was missing there.
  appSettings: Record<string, unknown>;
  // The user's own sidebar. `null`: the app had stored no sidebar state yet (the default).
  agentListOpen: boolean | null;
  // Who wrote the stored settings last, and the four look settings as that write left them.
  // While the store still holds exactly those, `stored` is proof. A record without `seen`
  // (an earlier plugin version) or a store that has changed since (written while no plugin
  // observed it) proves nothing: see `lookWriter`.
  stored: Writer;
  seen?: Record<string, unknown>;
  // The same for the sidebar's stored state.
  sidebar?: { stored: Writer; seen: boolean | null };
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

function flagOf(panel: any): boolean | null {
  const open = panel?.state?.desktop?.agentListOpen;
  return typeof open === "boolean" ? open : null;
}

function sidebarFlag(storage: StorageLike): boolean | null {
  return flagOf(readJson(storage, PANEL_STATE_KEY));
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

function isWriter(value: unknown): value is Writer {
  return value === "frame" || value === "user";
}

/**
 * The content rule, a fallback for a store nobody recorded the writer of. A page that runs the
 * AR look writes all of its in-memory settings at once, so what it writes keeps the AR theme or
 * an AR font. Settings with neither come from a page that runs the user's look. The theme id
 * alone says nothing: the app keeps it when the user chooses another theme.
 */
function looksLikeUsers(settings: Record<string, unknown>): boolean {
  const arTheme =
    settings.theme === EMBED_LOOK.theme && settings.pluginThemeId === EMBED_LOOK.pluginThemeId;
  return !arTheme && settings.uiFontFamily !== AR_FONT_STACK && settings.monoFontFamily !== AR_FONT_STACK;
}

/** Who wrote the stored look settings last: by the record when it holds, else by content. */
function lookWriter(own: StandaloneLook, settings: Record<string, unknown>): Writer {
  const seen = own.seen;
  if (isWriter(own.stored) && seen && LOOK_KEYS.every((key) => seen[key] === settings[key])) {
    return own.stored;
  }
  return looksLikeUsers(settings) ? "user" : "frame";
}

/**
 * Who wrote the sidebar's stored state last. Its content says nothing, so without a record that
 * holds it goes with the settings' mark: a frame that stored its look also closed the sidebar.
 */
function sidebarWriter(own: StandaloneLook, flag: boolean | null): Writer {
  const mark = own.sidebar;
  if (mark && isWriter(mark.stored) && mark.seen === flag) return mark.stored;
  return own.stored === "frame" ? "frame" : "user";
}

/**
 * Inside a listed frame: store the AR look for the next page load and report which settings had
 * to change. Before that the user's own look and sidebar are remembered, but only from a store
 * that a page running the user's look wrote (or that nothing was remembered for yet). What a
 * page running the AR look wrote, a change made inside the frame included, is never remembered.
 */
export function applyEmbedLook(storage: StorageLike): string[] {
  try {
    const settings = readJson(storage, APP_SETTINGS_KEY) ?? {};
    const changed = LOOK_KEYS.filter((key) => settings[key] !== EMBED_LOOK[key]);
    const own = readStandaloneLook(storage);
    const flag = sidebarFlag(storage);
    const usersLook = !own || lookWriter(own, settings) === "user";
    const usersSidebar = !own || sidebarWriter(own, flag) === "user";
    // When the settings change the page is about to load again, so the sidebar is closed in the
    // store and starts closed. Unchanged settings leave it to the app's own toggle, whose write
    // is recorded when it happens.
    const closes = changed.length > 0 && Boolean(readJson(storage, PANEL_STATE_KEY)?.state?.desktop);
    // The memory first: the user's look is never overwritten before it is kept.
    writeJson(storage, STANDALONE_LOOK_KEY, {
      appSettings: usersLook ? lookOf(settings) : own.appSettings,
      agentListOpen: usersSidebar ? flag : own.agentListOpen,
      stored: "frame",
      seen: { ...EMBED_LOOK },
      sidebar: { stored: changed.length > 0 || !usersSidebar ? "frame" : "user", seen: closes ? false : flag },
    } satisfies StandaloneLook);
    if (changed.length > 0) {
      writeJson(storage, APP_SETTINGS_KEY, { ...settings, ...EMBED_LOOK });
      setSidebarFlag(storage, false);
    }
    return changed;
  } catch {
    return [];
  }
}

/**
 * Outside a frame of a listed dashboard: put the user's own look and sidebar back wherever a
 * page running the AR look wrote last, whatever it wrote. A store a page running the user's look
 * wrote is the user's: it is left alone and remembered. The memory itself is kept, because a
 * frame that is still open may store the AR look again.
 *
 * `changed`: something stored changed, so the page has to load again. `usersLook`: the look this
 * page started with was the user's already (its settings were not touched).
 */
export function restoreStandaloneLook(storage: StorageLike): { changed: boolean; usersLook: boolean } {
  try {
    const own = readStandaloneLook(storage);
    // Nothing remembered: no frame shared this storage, the look is the user's.
    if (!own) return { changed: false, usersLook: true };
    const settings = readJson(storage, APP_SETTINGS_KEY) ?? {};
    let lookChanged = false;
    if (lookWriter(own, settings) === "frame") {
      for (const key of LOOK_KEYS) {
        if (key in own.appSettings ? settings[key] === own.appSettings[key] : !(key in settings)) continue;
        if (key in own.appSettings) settings[key] = own.appSettings[key];
        else delete settings[key];
        lookChanged = true;
      }
      if (lookChanged) writeJson(storage, APP_SETTINGS_KEY, settings);
    }
    const flag = sidebarFlag(storage);
    const framesSidebar = sidebarWriter(own, flag) === "frame";
    const open = own.agentListOpen ?? SIDEBAR_OPEN_BY_DEFAULT;
    // Nothing stored still means the default: there is nothing to put back.
    const sidebarChanged = framesSidebar && flag !== null && flag !== open;
    if (sidebarChanged) setSidebarFlag(storage, open);
    writeJson(storage, STANDALONE_LOOK_KEY, {
      appSettings: lookOf(settings),
      agentListOpen: framesSidebar ? own.agentListOpen : flag,
      stored: "user",
      seen: lookOf(settings),
      sidebar: { stored: "user", seen: sidebarFlag(storage) },
    } satisfies StandaloneLook);
    return { changed: lookChanged || sidebarChanged, usersLook: !lookChanged };
  } catch {
    return { changed: false, usersLook: false };
  }
}

/**
 * The app wrote one of its stores in this page: record who that was. `usersLook` says which look
 * the page runs. A page that runs the user's look wrote the user's look, which is remembered. A
 * page that runs the AR look (inside a listed frame) did not, and the memory stays as it is.
 * Nothing is recorded where no frame ever shared the storage.
 */
export function recordAppWrite(storage: StorageLike, usersLook: boolean, key: string, value: string): void {
  if (key !== APP_SETTINGS_KEY && key !== PANEL_STATE_KEY) return;
  try {
    const own = readStandaloneLook(storage);
    if (!own) return;
    const stored: Writer = usersLook ? "user" : "frame";
    const written = JSON.parse(value);
    if (key === APP_SETTINGS_KEY) {
      const look = lookOf(written ?? {});
      const appSettings = usersLook ? look : own.appSettings;
      writeJson(storage, STANDALONE_LOOK_KEY, { ...own, appSettings, stored, seen: look } satisfies StandaloneLook);
    } else {
      const flag = flagOf(written);
      const agentListOpen = usersLook ? flag : own.agentListOpen;
      writeJson(storage, STANDALONE_LOOK_KEY, { ...own, agentListOpen, sidebar: { stored, seen: flag } } satisfies StandaloneLook);
    }
  } catch {
    // An unreadable value proves nothing: the record no longer matches the store, and the next
    // page load falls back to the content rule.
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

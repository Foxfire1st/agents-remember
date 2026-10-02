// The look of the app inside the AR dashboard's frame, and giving a standalone tab its own back.
//
// UNSUPPORTED Paseo behaviour, all of it in this file: Paseo has no plugin interface for selecting
// a theme, setting fonts or hiding chrome. The client bundle is evaluated inside the app page, so
// on web it can reach the page's storage and DOM. What this file relies on:
//   - localStorage["@paseo:app-settings"]: theme, pluginThemeId, uiFontFamily, monoFontFamily;
//   - localStorage["panel-state"].state.desktop.agentListOpen (the left sidebar);
//   - the test ids "composer-dock-header" (workspace header row), "menu-button" (the app's own
//     sidebar toggle) and "sidebar-footer" (present while the sidebar is shown);
//   - the app reading both stores once, when its page loads.
// A Paseo release can change any of them. The supported parts (the theme contribution, the screen
// and its navigation) are in index.client.tsx.

export const PLUGIN_ID = "ar-plugin";
export const THEME_ID = "agents-remember";
// The dashboard's own stack (dashboard/src/styles/tokens.css, --font-mono).
export const AR_FONT_STACK = 'ui-monospace, "JetBrains Mono", "SFMono-Regular", Menlo, monospace';

const APP_SETTINGS_KEY = "@paseo:app-settings";
const PANEL_STATE_KEY = "panel-state";
// The look the user had outside the frame, kept so a standalone tab can take it back.
const STANDALONE_LOOK_KEY = "ar-plugin:standalone-look";
const RELOAD_GUARD_KEY = "ar-plugin:reloaded-at";
const RELOAD_GUARD_MS = 15000;
const HEADER_STYLE_ID = "ar-plugin-embed-style";
const HIDE_HEADER_CSS = '[data-testid="composer-dock-header"]{display:none !important}';
const SIDEBAR_SETTLE_MS = 250;
const SIDEBAR_ATTEMPTS = 24;

const EMBED_LOOK: Record<string, string> = {
  theme: "plugin",
  pluginThemeId: `${PLUGIN_ID}/theme/${THEME_ID}`,
  uiFontFamily: AR_FONT_STACK,
  monoFontFamily: AR_FONT_STACK,
};
const LOOK_KEYS = Object.keys(EMBED_LOOK);

interface StandaloneLook {
  // Only the keys the settings held; a key missing here was missing there.
  appSettings: Record<string, unknown>;
  agentListOpen: boolean | null;
  // Whose look the storage holds now: the frame's after this plugin stored the AR look, the
  // user's after a standalone tab took its own back. While it is the frame's, whatever the
  // settings hold (also a change made inside the frame) is never mistaken for the user's look.
  stored: "frame" | "user";
}

// The plugin tsconfig deliberately has no DOM lib; reach the browser globals untyped.
const web = globalThis as any;

function readJson(key: string): any {
  try {
    const raw = web.localStorage.getItem(key);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function writeJson(key: string, value: unknown): void {
  web.localStorage.setItem(key, JSON.stringify(value));
}

function sidebarFlag(panel: any): boolean | null {
  const open = panel?.state?.desktop?.agentListOpen;
  return typeof open === "boolean" ? open : null;
}

function setSidebarFlag(open: boolean): void {
  const panel = readJson(PANEL_STATE_KEY);
  if (panel?.state?.desktop && panel.state.desktop.agentListOpen !== open) {
    panel.state.desktop.agentListOpen = open;
    writeJson(PANEL_STATE_KEY, panel);
  }
}

function readStandaloneLook(): StandaloneLook | null {
  const own = readJson(STANDALONE_LOOK_KEY);
  return own && typeof own.appSettings === "object" && own.appSettings !== null ? own : null;
}

/**
 * Store the AR look for the next page load and report which settings had to change. When the
 * storage holds the user's own look (a first use in a frame, or a standalone tab took it back
 * since), that look is remembered before it is replaced.
 */
export function applyEmbedLook(): string[] {
  try {
    const settings = readJson(APP_SETTINGS_KEY) ?? {};
    const changed = LOOK_KEYS.filter((key) => settings[key] !== EMBED_LOOK[key]);
    const own = readStandaloneLook();
    if (own?.stored !== "frame" && changed.length > 0) {
      writeJson(STANDALONE_LOOK_KEY, {
        appSettings: Object.fromEntries(
          LOOK_KEYS.filter((key) => key in settings).map((key) => [key, settings[key]]),
        ),
        agentListOpen: sidebarFlag(readJson(PANEL_STATE_KEY)),
        stored: "frame",
      } satisfies StandaloneLook);
    } else if (own && own.stored !== "frame") {
      // A frame that was still open stored the AR look again after a standalone tab took its
      // own back: the storage is the frame's again, the remembered look stays the user's.
      writeJson(STANDALONE_LOOK_KEY, { ...own, stored: "frame" } satisfies StandaloneLook);
    }
    if (changed.length === 0) return changed;
    writeJson(APP_SETTINGS_KEY, { ...settings, ...EMBED_LOOK });
    // The page is about to load again, so the sidebar can start closed instead of closing late.
    setSidebarFlag(false);
    return changed;
  } catch {
    return [];
  }
}

/**
 * Outside a frame of a listed dashboard: when the storage holds the frame's look and the user's
 * own is remembered, put the user's own back. Returns whether a stored setting changed. The
 * memory itself is kept, because a frame that is still open may store the AR look again.
 */
export function restoreStandaloneLook(): boolean {
  try {
    const own = readStandaloneLook();
    if (!own) return false;
    const settings = readJson(APP_SETTINGS_KEY) ?? {};
    const framesLook = LOOK_KEYS.every((key) => settings[key] === EMBED_LOOK[key]);
    if (own.stored !== "frame" && !framesLook) return false;
    let changed = false;
    for (const key of LOOK_KEYS) {
      if (key in own.appSettings ? settings[key] === own.appSettings[key] : !(key in settings)) continue;
      if (key in own.appSettings) settings[key] = own.appSettings[key];
      else delete settings[key];
      changed = true;
    }
    if (changed) writeJson(APP_SETTINGS_KEY, settings);
    // The sidebar is put back only when the frame itself last wrote the storage; otherwise its
    // stored state is already the user's.
    if (own.stored === "frame" && typeof own.agentListOpen === "boolean") {
      changed = sidebarFlag(readJson(PANEL_STATE_KEY)) !== own.agentListOpen || changed;
      setSidebarFlag(own.agentListOpen);
    }
    writeJson(STANDALONE_LOOK_KEY, { ...own, stored: "user" } satisfies StandaloneLook);
    return changed;
  } catch {
    return false;
  }
}

/**
 * Load `url` in place of this page, at most once per 15 seconds per tab, so a look or a route
 * that cannot be established never becomes a reload loop. Returns whether the page is leaving.
 */
export function reloadOnce(url: string): boolean {
  try {
    const last = Number(web.sessionStorage.getItem(RELOAD_GUARD_KEY) ?? 0);
    if (Date.now() - last <= RELOAD_GUARD_MS) return false;
    web.sessionStorage.setItem(RELOAD_GUARD_KEY, String(Date.now()));
  } catch {
    return false;
  }
  web.location.replace(url);
  return true;
}

export function setWorkspaceHeaderHidden(hidden: boolean): void {
  const existing = web.document.getElementById(HEADER_STYLE_ID);
  if (!hidden) {
    existing?.remove();
    return;
  }
  if (existing) return;
  const style = web.document.createElement("style");
  style.id = HEADER_STYLE_ID;
  style.textContent = HIDE_HEADER_CSS;
  web.document.head.appendChild(style);
}

/**
 * Close the left sidebar once the workspace is on screen, through the app's own toggle, when the
 * page loaded with it open. It runs once per page load: afterwards the sidebar is the user's
 * (Paseo's shortcut reopens it). Returns a function that stops waiting.
 */
export function closeSidebarAtLoad(): () => void {
  let attempts = 0;
  const timer = web.setInterval(() => {
    attempts += 1;
    const toggle = web.document.querySelector('[data-testid="menu-button"]');
    if (toggle) {
      const footer = web.document.querySelector('[data-testid="sidebar-footer"]');
      if (footer && footer.getBoundingClientRect().width > 0) toggle.click();
      web.clearInterval(timer);
    } else if (attempts >= SIDEBAR_ATTEMPTS) {
      web.clearInterval(timer);
    }
  }, SIDEBAR_SETTLE_MS);
  return () => web.clearInterval(timer);
}

import { applyEmbedLook, collapseNativeSidebarAtLoad, recordAppWrite, restoreStandaloneLook } from "./look";
import type { PluginPage } from "./page";

// What the client part does once per page load: store or take back a look, repair a first-visit
// deep link, and replace the page at most once so that either takes effect. It also knows which
// look the page runs, which is what a later write of the app in this page is recorded as.
//
// UNSUPPORTED Paseo behaviour this file relies on:
//   - the app redirecting a deep link under /h/ to "/welcome" and then "/open-project" while it
//     does not know the serving daemon yet (a browser's first visit), and accepting the same
//     link once it does;
//   - `performance.getEntriesByType("navigation")` naming the URL the page was asked to load;
//   - `location.replace` and `sessionStorage` being available to plugin code;
//   - one page global shared by every evaluation of the entry in the same page (see page.ts);
//   - the look a page runs being the one its storage held when the page started: a page inside
//     a listed frame runs the AR look, a page whose settings did not have to be put back runs
//     the user's (look.ts lists what the look itself relies on);
//   - `performance.now()` counting from the moment the page began to load, the wall clock
//     (`Date.now()`) being one clock for every tab, and the app reading its stored settings no
//     earlier than 50 ms after that moment: together they say whether the stored settings were
//     written after this page began to load.

// On the page global: one record per page load.
const LOAD_STATE = "__arPluginLoad";
// In sessionStorage: written just before this plugin replaces the page, removed by the load that
// finds it. That load is the reload; it never replaces the page again.
export const RELOAD_FLAG = "ar-plugin:reloading";
// Where the app sends a deep link while it does not know the serving daemon yet.
const BOUNCE_PATHS = ["/welcome", "/open-project"];
// A write this soon after a page began to load came before the app of that page read the store
// (the app reads it 140 ms and more after the navigation starts).
const LOAD_BEGIN_MARGIN_MS = 50;

export interface LoadState {
  /** This page is the plugin's own reload, or has already asked for one: no further reload. */
  reloaded: boolean;
  /** The parent origin the reloading page had verified; a browser may not name it after a reload. */
  carriedParent: string | null;
  bootstrapped: boolean;
  /** The parent origin this page verified against the embed list. */
  trustedParent: string | null;
  /**
   * Which look this page runs: the user's (`true`) or the AR look (`false`). `null` until the
   * page is known to be a listed frame or not one; nothing is recorded for its writes till then.
   */
  usersLook: boolean | null;
}

/** This page load's record; the first call of a load takes the reload flag out of the session. */
export function loadState(page: PluginPage): LoadState {
  const existing = page.state[LOAD_STATE] as LoadState | undefined;
  if (existing) return existing;
  const state: LoadState = {
    reloaded: false,
    carriedParent: null,
    bootstrapped: false,
    trustedParent: null,
    usersLook: null,
  };
  try {
    const flag = page.sessionStorage.getItem(RELOAD_FLAG);
    if (flag !== null) {
      state.reloaded = true;
      page.sessionStorage.removeItem(RELOAD_FLAG);
      const parent = JSON.parse(flag)?.parent;
      state.carriedParent = typeof parent === "string" ? parent : null;
    }
  } catch {
    // An unreadable flag still counts as "this is the reload".
  }
  page.state[LOAD_STATE] = state;
  return state;
}

/**
 * Load `url` in place of this page, once per page load: the page that results from it finds the
 * flag and does not reload again, so a look or a route that cannot be established never becomes
 * a loop, and a later, separate load is never refused its own reload. Returns whether the page
 * is leaving.
 */
export function reloadOnce(page: PluginPage, url: string, parentOrigin: string | null): boolean {
  const state = loadState(page);
  if (state.reloaded) return false;
  try {
    page.sessionStorage.setItem(RELOAD_FLAG, JSON.stringify({ parent: parentOrigin }));
  } catch {
    // Without the flag the next load could not tell that it is the reload.
    return false;
  }
  state.reloaded = true;
  page.location.replace(url);
  return true;
}

/** The URL this page was asked to load, even if the app has since redirected away from it. */
export function requestedUrl(page: PluginPage): string {
  try {
    const entry = page.performance.getEntriesByType("navigation")[0];
    const url = new URL(entry?.name ?? page.location.href);
    if (url.origin === page.location.origin && url.pathname.startsWith("/h/")) return url.href;
  } catch {
    // fall through
  }
  return page.location.href;
}

/**
 * Once per page load inside a listed frame: store the AR look and repair a first-visit deep
 * link. Both take effect only when the page loads, so this may replace the page. Returns
 * whether the page is leaving.
 */
export function bootstrapEmbed(page: PluginPage, parentOrigin: string): boolean {
  const state = loadState(page);
  if (state.bootstrapped) return false;
  state.bootstrapped = true;
  state.usersLook = false;
  const changed = applyEmbedLook(page.localStorage);
  const requested = requestedUrl(page);
  const bounced = requested !== page.location.href && BOUNCE_PATHS.includes(page.location.pathname);
  if ((changed.length > 0 || bounced) && reloadOnce(page, requested, parentOrigin)) return true;
  collapseNativeSidebarAtLoad(page);
  return false;
}

/** At top level, or under a parent that is not listed: give the page the user's own look back. */
export function takeOwnLookBack(page: PluginPage): void {
  const state = loadState(page);
  const first = state.usersLook === null;
  const restored = restoreStandaloneLook(page.localStorage);
  // The first answer of a page load stands: what the page runs does not change while it lives.
  const stale = first && restored.usersLook && writtenSinceLoadBegan(page, restored.at);
  if (first) state.usersLook = restored.usersLook && !stale;
  // A stale page loads once more, like one whose look had to be put back. Where that is refused
  // it stays what it is: a page that does not count as running the user's look.
  if (restored.changed || stale) reloadOnce(page, page.location.href, null);
}

/**
 * Whether the stored settings were written after this page began to load. The app reads them
 * early in the load and the plugin runs later, so a store that holds the user's look now does
 * not prove the page started with it: another tab may have put that look back in between (two
 * standalone tabs opened at the same moment while the store held the AR look). A wrong "yes"
 * costs one more load. Where that load is refused, the page stays counted as not running the
 * user's look, and a theme or font the user chooses in it is put back at the next standalone
 * load. It never makes a page count as running the user's look.
 */
function writtenSinceLoadBegan(page: PluginPage, at: number | null): boolean {
  if (at === null) return false;
  try {
    // Both sides from the wall clock; a page's own time origin can lag behind it after a sleep.
    const began = Date.now() - page.performance.now();
    return at > began + LOAD_BEGIN_MARGIN_MS;
  } catch {
    return false;
  }
}

/**
 * Record, for as long as the returned function is not called, who writes the app's stored
 * settings and sidebar state in this page (see `recordAppWrite`). Without this the next page
 * load can only guess the writer from what the store holds.
 */
export function watchAppWrites(page: PluginPage): () => void {
  try {
    return page.watchWrites((key, value) => {
      const usersLook = loadState(page).usersLook;
      if (usersLook !== null) recordAppWrite(page.localStorage, usersLook, key, value);
    });
  } catch {
    // A browser that does not let the write be wrapped: the content rule remains.
    return () => {};
  }
}

// The browser objects the client part touches, gathered once and passed in. Nothing else in the
// client part reads a global, so its logic runs in a test against plain objects.
//
// UNSUPPORTED: Paseo documents these globals as unavailable to plugin code (they do not exist on
// iOS and Android). The client bundle is evaluated inside the app page, so on web they are there.

export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export interface PluginPage {
  /** The page's own window: `parent`, message events, timers. */
  window: any;
  document: any;
  location: any;
  performance: any;
  localStorage: StorageLike;
  /** Per tab; survives the plugin's own reload of the page and nothing else that matters here. */
  sessionStorage: StorageLike;
  /**
   * The page's global object. The entry is evaluated again on a plugin reload (and, in some Paseo
   * versions, on a workspace switch) in the same page, with the same global: what must happen
   * once per page load is recorded on it.
   */
  state: Record<string, any>;
}

/** The page this bundle runs in, or `null` where there is none (the native apps). */
export function currentPage(): PluginPage | null {
  const web = globalThis as any;
  if (typeof web.window === "undefined" || typeof web.document === "undefined") return null;
  try {
    return {
      window: web.window,
      document: web.document,
      location: web.location,
      performance: web.performance,
      localStorage: web.localStorage,
      sessionStorage: web.sessionStorage,
      state: web,
    };
  } catch {
    // A browser that refuses storage to this page: the client part then does nothing.
    return null;
  }
}

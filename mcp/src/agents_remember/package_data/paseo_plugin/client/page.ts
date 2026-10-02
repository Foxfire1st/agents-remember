// The browser objects the client part touches, gathered once and passed in. Nothing else in the
// client part reads a global, so its logic runs in a test against plain objects.
//
// UNSUPPORTED Paseo behaviour this file relies on:
//   - the browser globals themselves. Paseo documents them as unavailable to plugin code (they
//     do not exist on iOS and Android); the client bundle is evaluated inside the app page, so on
//     web they are there;
//   - the app writing its stores with `localStorage.setItem(key, value)`, looked up on the
//     storage object each time it writes. `watchWrites` wraps `setItem` on the storage's
//     prototype for as long as a listener is registered; a write made any other way (a kept
//     reference to the original function, a property assignment) is not seen.

export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

/** Told the key and the new value after the app stored it. */
export type WriteListener = (key: string, value: string) => void;

export interface PluginPage {
  /** The page's own window: `parent`, message events, timers. */
  window: any;
  document: any;
  location: any;
  performance: any;
  /** What the plugin itself writes through this object is not reported to `watchWrites`. */
  localStorage: StorageLike;
  /** Per tab; survives the plugin's own reload of the page and nothing else that matters here. */
  sessionStorage: StorageLike;
  /**
   * The page's global object. The entry is evaluated again on a plugin reload (and, in some Paseo
   * versions, on a workspace switch) in the same page, with the same global: what must happen
   * once per page load is recorded on it.
   */
  state: Record<string, any>;
  /**
   * Report every write the app makes to this page's localStorage from now on. One listener at a
   * time: a later call replaces it. Returns the function that stops this listener.
   */
  watchWrites(listener: WriteListener): () => void;
}

// On the page global: the wrapped `setItem` and who listens, shared by every evaluation.
const WRITE_WATCH = "__arPluginWrites";

interface WriteWatch {
  /** The function the storage's prototype had before it was wrapped. */
  setItem: (this: unknown, key: string, value: string) => void;
  wrapper: (this: unknown, key: string, value: string) => void;
  listener: WriteListener | null;
}

function watchWrites(web: any, listener: WriteListener): () => void {
  const proto = Object.getPrototypeOf(web.localStorage);
  let watch = web[WRITE_WATCH] as WriteWatch | undefined;
  if (!watch) {
    const created: WriteWatch = {
      setItem: proto.setItem,
      listener: null,
      wrapper(this: unknown, key: string, value: string): void {
        created.setItem.call(this, key, value);
        if (this !== web.localStorage || !created.listener) return;
        try {
          created.listener(String(key), String(value));
        } catch {
          // Observing must never break the app's own write.
        }
      },
    };
    proto.setItem = created.wrapper;
    web[WRITE_WATCH] = watch = created;
  }
  const active = watch;
  active.listener = listener;
  return () => {
    if (active.listener !== listener) return;
    active.listener = null;
    // Put the original back unless something else wrapped the function in the meantime.
    if (proto.setItem === active.wrapper) {
      proto.setItem = active.setItem;
      delete web[WRITE_WATCH];
    }
  };
}

/** The page's localStorage for the plugin's own use: its writes go past the listener. */
function quietStorage(web: any): StorageLike {
  const real = web.localStorage;
  return {
    getItem: (key) => real.getItem(key),
    setItem: (key, value) => {
      const watch = web[WRITE_WATCH] as WriteWatch | undefined;
      (watch ? watch.setItem : real.setItem).call(real, key, value);
    },
    removeItem: (key) => real.removeItem(key),
  };
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
      localStorage: quietStorage(web),
      sessionStorage: web.sessionStorage,
      state: web,
      watchWrites: (listener) => watchWrites(web, listener),
    };
  } catch {
    // A browser that refuses storage to this page: the client part then does nothing.
    return null;
  }
}

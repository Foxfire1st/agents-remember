import { describe, expect, it } from "vitest";

import { startScreenWakeLock } from "./screenWakeLock";

// The cockpit holds a screen wake lock while visible (monitoring counts as activity),
// releases on stop, reacquires after UA-initiated releases, and degrades silently when the
// API is missing or denied.

interface FakeSentinel {
  released: boolean;
  release: () => Promise<void>;
  addEventListener: (type: "release", listener: () => void) => void;
  fireRelease: () => void;
}

function makeSentinel(): FakeSentinel {
  const listeners: Array<() => void> = [];
  const sentinel: FakeSentinel = {
    released: false,
    release: async () => {
      sentinel.released = true;
    },
    addEventListener: (_type, listener) => listeners.push(listener),
    fireRelease: () => {
      sentinel.released = true;
      for (const listener of listeners) listener();
    },
  };
  return sentinel;
}

function makeEnv(visibility: () => DocumentVisibilityState) {
  const sentinels: FakeSentinel[] = [];
  let requests = 0;
  const visibilityListeners: Array<() => void> = [];
  const doc = {
    get visibilityState() {
      return visibility();
    },
    addEventListener: (type: string, listener: () => void) => {
      if (type === "visibilitychange") visibilityListeners.push(listener);
    },
    removeEventListener: () => {},
  } as unknown as Document;
  const nav = {
    wakeLock: {
      request: async () => {
        requests += 1;
        const sentinel = makeSentinel();
        sentinels.push(sentinel);
        return sentinel;
      },
    },
  } as unknown as Navigator;
  return {
    doc,
    nav,
    sentinels,
    requestCount: () => requests,
    fireVisibility: () => visibilityListeners.forEach((listener) => listener()),
  };
}

// Like makeEnv, but `request` resolves only when the test calls `resolveAll()`, so a request
// can be held IN FLIGHT while a second acquire is triggered — the overlap the in-flight guard must
// coalesce. Every sentinel handed out is recorded so an orphan (one never released) is visible.
function makeDeferredEnv(visibility: () => DocumentVisibilityState) {
  const sentinels: FakeSentinel[] = [];
  const resolvers: Array<() => void> = [];
  let requests = 0;
  const visibilityListeners: Array<() => void> = [];
  const doc = {
    get visibilityState() {
      return visibility();
    },
    addEventListener: (type: string, listener: () => void) => {
      if (type === "visibilitychange") visibilityListeners.push(listener);
    },
    removeEventListener: () => {},
  } as unknown as Document;
  const nav = {
    wakeLock: {
      request: () => {
        requests += 1;
        const sentinel = makeSentinel();
        sentinels.push(sentinel);
        return new Promise<FakeSentinel>((resolve) => {
          resolvers.push(() => resolve(sentinel));
        });
      },
    },
  } as unknown as Navigator;
  return {
    doc,
    nav,
    sentinels,
    requestCount: () => requests,
    resolveAll: () => {
      while (resolvers.length > 0) resolvers.shift()?.();
    },
    fireVisibility: () => visibilityListeners.forEach((listener) => listener()),
  };
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("screen wake lock (260718-CHATS-L5I F-aq)", () => {
  it("acquires while visible and releases on stop", async () => {
    const env = makeEnv(() => "visible");
    const stop = startScreenWakeLock(env.doc, env.nav);
    await flush();
    expect(env.requestCount()).toBe(1);
    stop();
    await flush();
    expect(env.sentinels[0]?.released).toBe(true);
  });

  it("reacquires after a UA-initiated release while still visible", async () => {
    const env = makeEnv(() => "visible");
    const stop = startScreenWakeLock(env.doc, env.nav);
    await flush();
    env.sentinels[0]?.fireRelease();
    await flush();
    expect(env.requestCount()).toBe(2);
    stop();
  });

  it("does not acquire while hidden; acquires when visibility returns", async () => {
    let state: DocumentVisibilityState = "hidden";
    const env = makeEnv(() => state);
    const stop = startScreenWakeLock(env.doc, env.nav);
    await flush();
    expect(env.requestCount()).toBe(0);
    state = "visible";
    env.fireVisibility();
    await flush();
    expect(env.requestCount()).toBe(1);
    stop();
  });

  it("coalesces overlapping acquires to one held sentinel that stop() fully releases (m13)", async () => {
    // Hold the initial acquire's request in flight, then fire a visibilitychange that triggers
    // a second acquire while the first is unresolved. The last-resolved-sentinel guard cannot
    // see the in-flight request, so without the in-flight guard both acquires request, the
    // second overwrites `sentinel`, and stop() orphans the first held wake-lock.
    const env = makeDeferredEnv(() => "visible");
    const stop = startScreenWakeLock(env.doc, env.nav);
    // Initial acquire is now suspended at its request await (one in-flight request).
    env.fireVisibility(); // overlapping acquire, before the first resolves
    env.resolveAll();
    await flush();
    // Exactly one request survived the coalesce — a second means an orphan is possible.
    expect(env.requestCount()).toBe(1);
    stop();
    await flush();
    // No orphan: every sentinel ever handed out is released.
    expect(env.sentinels.every((s) => s.released)).toBe(true);
    expect(env.sentinels.length).toBeGreaterThan(0);
  });

  it("is a silent no-op without the API and on denial", async () => {
    const bare = startScreenWakeLock(
      { visibilityState: "visible", addEventListener: () => {}, removeEventListener: () => {} } as unknown as Document,
      {} as Navigator,
    );
    bare();
    const denying = {
      wakeLock: {
        request: async () => {
          throw new Error("denied by permissions policy");
        },
      },
    } as unknown as Navigator;
    const env = makeEnv(() => "visible");
    const stop = startScreenWakeLock(env.doc, denying);
    await flush();
    stop(); // no throw is the assertion
  });
});

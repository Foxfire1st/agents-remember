// Screen wake lock for the monitoring cockpit.
//
// Watching agents work is the cockpit's core use, and monitoring produces no keyboard or
// mouse input — so OS idle timers and inactivity-lock policies blank the screen mid-watch.
// Video sites survive those timers because playback holds a "display required" OS power
// request; the Screen Wake Lock API is the same request exposed to web apps. Semantic
// mirror of "video is playing": while the dashboard is the VISIBLE tab, someone is
// monitoring, so the screen stays on. The browser releases the lock the moment the tab is
// hidden (API-native behavior) and we reacquire on return, so a backgrounded dashboard
// never pins the display.
//
// Honesty posture: silent when held (a healthy state is not chrome worth surfacing);
// unsupported browsers and denials degrade to a single console note, never a banner.

type WakeLockSentinelLike = {
  released: boolean;
  release: () => Promise<void>;
  addEventListener: (type: "release", listener: () => void) => void;
};

type WakeLockLike = {
  request: (type: "screen") => Promise<WakeLockSentinelLike>;
};

function wakeLockOf(nav: Navigator): WakeLockLike | null {
  const candidate = (nav as Navigator & { wakeLock?: WakeLockLike }).wakeLock;
  return candidate && typeof candidate.request === "function" ? candidate : null;
}

/**
 * Hold a screen wake lock while the document is visible. Returns a stop function.
 * Reacquires after visibility returns and after spurious sentinel releases.
 */
export function startScreenWakeLock(
  doc: Document = document,
  nav: Navigator = navigator,
): () => void {
  const wakeLock = wakeLockOf(nav);
  if (wakeLock === null) return () => {};
  let stopped = false;
  let sentinel: WakeLockSentinelLike | null = null;
  let acquiring = false;
  let noted = false;

  const acquire = async (): Promise<void> => {
    if (stopped || doc.visibilityState !== "visible") return;
    // Coalesce overlapping acquires onto a single request. `acquire` is async and suspends
    // at the `request` await, so the last-resolved-sentinel guard below cannot see a request
    // still IN FLIGHT: two acquires racing before the first resolves (e.g. the initial call
    // still pending when a visibilitychange fires another) would both request, and the second
    // `sentinel = next` would orphan the first held sentinel that `stop()` can never release —
    // a leaked OS wake-lock. The in-flight guard admits at most one owned sentinel; `stop()`
    // releases a held one here and the `if (stopped)` branch releases a still-pending one.
    if (acquiring) return;
    if (sentinel !== null && !sentinel.released) return;
    acquiring = true;
    try {
      const next = await wakeLock.request("screen");
      if (stopped) {
        void next.release();
        return;
      }
      sentinel = next;
      // The UA releases the sentinel on tab hide/system events; a visible document wants
      // it back (the UA-release path does not re-fire visibilitychange).
      next.addEventListener("release", () => {
        if (!stopped && doc.visibilityState === "visible") void acquire();
      });
    } catch (error) {
      if (!noted) {
        noted = true;
        console.info(
          "screen wake lock unavailable — the OS idle/lock policy applies while monitoring:",
          error instanceof Error ? error.message : String(error),
        );
      }
    } finally {
      acquiring = false;
    }
  };

  const onVisibility = (): void => {
    if (doc.visibilityState === "visible") void acquire();
  };
  doc.addEventListener("visibilitychange", onVisibility);
  void acquire();

  return () => {
    stopped = true;
    doc.removeEventListener("visibilitychange", onVisibility);
    if (sentinel !== null && !sentinel.released) void sentinel.release();
    sentinel = null;
  };
}

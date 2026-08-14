// Liveness watchdog for long-lived EventSource channels (sleep/wake + half-open wedge).
//
// The defect class: the laptop slept with a chat open; on wake the
// surface showed "connecting…" that NEVER cleared until a manual refresh. The browser suspends
// timers AND the SSE socket during sleep; on wake the EventSource is half-open dead — the
// connection still LOOKS open to the browser, so no `error` event ever fires and neither the
// native auto-reconnect nor the unopened-escalation engages. The same half-open corpse
// appears WITHOUT sleep through proxies that keep dead sockets (the live-wedge class).
//
// Two independent detectors, judged ONLY against an OPEN channel:
//
// 1. Wall-clock jump (sleep). A `setInterval` tick compares `Date.now()` between ticks: an
//    elapsed jump past `sleepJumpMs` means the OS suspended timers — i.e. the machine slept (or
//    the page froze hard) — and the open socket is suspect. This catches sleep precisely without
//    penalizing idle chats, which are silent by design (the conversation events stream has no
//    heartbeat; sse-starlette's ~15 s ping is a comment frame EventSource never surfaces).
// 2. Frame-timeout backstop. With no sleep, a wedged half-open socket is indistinguishable from
//    a quiet chat except by duration: no observable frame for `frameTimeoutMs` (generous — idle
//    chats are legitimately silent) judges the channel dead. A false positive costs exactly one
//    quiet resubscribe, which is honest; a missed positive is the reported permanent wedge.
//    That "exactly one" is a hard rule, not a hope — see the one-shot contract below.
//
// The one-shot contract. Neither channel emits a heartbeat an EventSource can
// surface: the conversation endpoint yields a `: connected` COMMENT and then blocks on its queue,
// sse-starlette's keepalive is a `: ping` comment, and a state tick where only ages advanced emits
// nothing at all. So on a quiet channel "no frame for 90 s" is not evidence of death — it is the
// normal, healthy state, and re-judging it every 90 s made every idle channel self-cycle forever
// (measured: 21 subscribes / 20 closed corpses in 30 minutes on a quiescent daemon, each state
// resubscribe re-shipping the full ~1.3 MB snapshot). A system with all external inputs at zero
// must reach a fixed point. The backstop therefore spends ONE quiet resubscribe per episode of
// observed life and then goes silent; it re-arms only when the channel proves it is delivering
// content again, because only then does subsequent silence carry information. The sleep detector
// is unaffected — a suspended timer is positive evidence, not an absence.
//
// On a judgment the caller performs a QUIET cycle (close + fresh resumable subscribe): no
// disconnect is reported, so the surface never flashes "connecting…" for a healthy resume — the
// conversation endpoint's priming comment (`: connected`, active/api.py) opens the fresh
// instance immediately, and the resume replays whatever the corpse missed from `after=<cursor>`.
// If the fresh subscribe itself fails, the caller's ordinary error path (disconnect signal,
// escalation) engages exactly as for a transport drop.
//
// Hidden tabs are exempt from judgment: background timer throttling makes both measures lie (a
// throttled tick cadence looks like a jump; an idle chat is silent anyway), so checking there
// would reconnect-storm healthy background tabs. The first tick after a refocus judges normally
// — a channel whose connection died in the background is cycled within one tick, at most once.

export const STREAM_LIVENESS_TICK_MS = 5_000;
/** Timer suspension beyond this reads as OS sleep: the open socket is treated as dead. */
export const STREAM_LIVENESS_SLEEP_JUMP_MS = 30_000;
/** Silence on an OPEN channel beyond this reads as a wedged half-open socket. */
export const STREAM_LIVENESS_FRAME_TIMEOUT_MS = 90_000;
/**
 * A resubscribe's OWN opening payload lands inside this window after a cycle: the state channel
 * re-ships its whole snapshot unconditionally, and a conversation resume replays whatever the
 * corpse missed. That traffic is caused by the cycle, so it must not count as the proof of life
 * that re-arms the backstop — otherwise the one-shot contract degenerates back into a 90 s loop.
 */
export const STREAM_LIVENESS_HANDSHAKE_SETTLE_MS = 10_000;

export interface StreamLivenessTuning {
  /** Backstop: judge an open, frameless channel dead after this (0 disables). */
  frameTimeoutMs?: number;
  /** Wall-clock jump between ticks that reads as OS sleep. */
  sleepJumpMs?: number;
  /** Frames within this long after a cycle are that cycle's handshake, not proof of life. */
  handshakeSettleMs?: number;
  /** Drift-check cadence. */
  tickMs?: number;
  now?: () => number;
  /** Background exemption (defaults to `document.hidden`); hidden tabs are never judged. */
  isHidden?: () => boolean;
  setIntervalImpl?: (fn: () => void, ms: number) => number;
  clearIntervalImpl?: (handle: number) => void;
  /** Registers a "became visible" callback; returns a disposer (no-op without a document). */
  listenVisibility?: (onVisible: () => void) => () => void;
}

export interface StreamLivenessOptions extends StreamLivenessTuning {
  /** True while the watched channel is OPEN — death is only ever judged on an open channel. */
  isOpen: () => boolean;
  /** The open channel is judged dead: the caller closes it and opens a fresh subscribe. */
  onDead: () => void;
}

export interface StreamLivenessWatchdog {
  /** Record a sign of life: the channel's open event, or any received frame. */
  markAlive: () => void;
  stop: () => void;
}

const defaultIsHidden = (): boolean =>
  typeof document !== "undefined" && document.hidden;

const defaultListenVisibility = (onVisible: () => void): (() => void) => {
  if (typeof document === "undefined") return () => {};
  const handler = (): void => {
    if (!document.hidden) onVisible();
  };
  document.addEventListener("visibilitychange", handler);
  return () => document.removeEventListener("visibilitychange", handler);
};

export function startStreamLivenessWatchdog(
  options: StreamLivenessOptions,
): StreamLivenessWatchdog {
  const {
    isOpen,
    onDead,
    frameTimeoutMs = STREAM_LIVENESS_FRAME_TIMEOUT_MS,
    sleepJumpMs = STREAM_LIVENESS_SLEEP_JUMP_MS,
    handshakeSettleMs = STREAM_LIVENESS_HANDSHAKE_SETTLE_MS,
    tickMs = STREAM_LIVENESS_TICK_MS,
    now = () => Date.now(),
    isHidden = defaultIsHidden,
    setIntervalImpl = (fn, ms) => globalThis.setInterval(fn, ms) as unknown as number,
    clearIntervalImpl = (handle) => globalThis.clearInterval(handle),
    listenVisibility = defaultListenVisibility,
  } = options;

  let lastTickAt = now();
  let lastFrameAt = lastTickAt;
  let stopped = false;
  // One-shot backstop. `backstopSpent` guards the frame-timeout so it fires at MOST once per
  // episode of observed life. Once spent it stays spent — an idle channel that emits no frame an
  // EventSource can surface is HEALTHY, not dead, so re-judging its silence every frameTimeoutMs
  // just self-cycles it forever (re-shipping the full snapshot each time). `lastCycleAt` records
  // when the backstop last cycled the channel; markAlive re-arms only for life that lands past the
  // handshake-settle window after it — i.e. the channel delivering content on its own again, the
  // only signal that makes subsequent silence informative once more.
  let backstopSpent = false;
  let lastCycleAt = Number.NEGATIVE_INFINITY;

  // One judgment per call: the jump is the stronger evidence and its cycle also settles the
  // frame clock (the caller's fresh subscribe marks alive on open).
  const judge = (): void => {
    const at = now();
    const elapsed = at - lastTickAt;
    lastTickAt = at;
    if (stopped || isHidden() || !isOpen()) return;
    if (elapsed >= sleepJumpMs) {
      // Sleep is POSITIVE evidence (the OS suspended timers) and self-limiting (lastTickAt just
      // advanced), so it is exempt from the one-shot guard — a genuine wake always earns its cycle.
      onDead();
      return;
    }
    if (frameTimeoutMs > 0 && !backstopSpent && at - lastFrameAt >= frameTimeoutMs) {
      // Spend the one-shot BEFORE cycling: the resubscribe's own handshake markAlive must not find
      // an un-spent backstop, or the idle channel loops every frameTimeoutMs (quiescence).
      backstopSpent = true;
      lastCycleAt = at;
      onDead();
    }
  };

  const interval = setIntervalImpl(judge, tickMs);
  const unlisten = listenVisibility(judge);

  return {
    markAlive: () => {
      const at = now();
      lastFrameAt = at;
      // Re-arm the one-shot backstop ONLY for life that is not this cycle's own handshake replay
      // (the state channel re-ships its whole snapshot on resubscribe; a conversation resume
      // replays the gap — both land inside the settle window and prove nothing about liveness).
      // A frame past the window is the channel delivering on its own again: silence is meaningful
      // once more, so the backstop may fire a fresh one-shot for the next idle episode.
      if (at - lastCycleAt > handshakeSettleMs) backstopSpent = false;
    },
    stop: () => {
      if (stopped) return;
      stopped = true;
      clearIntervalImpl(interval);
      unlisten();
    },
  };
}

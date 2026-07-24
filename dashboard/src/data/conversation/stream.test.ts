import { afterEach, describe, expect, it, vi } from "vitest";

import {
  openConversationStream,
  STREAM_OPEN_DEADLINE_MS,
  type ConversationStreamController,
  type EventSourceCtor,
} from "./stream";
import type { ActiveEventCursor } from "./types";

// The stream's reconnect backoff is boot-window aware. Before the FIRST
// successful open (the fresh-chat window where the bridge's events endpoint still 503s) the
// reconnect is fast (500 ms — a composing bridge is discovered within ~0.5 s instead of sitting
// blind through the established 2 s backoff). After the first successful open every drop keeps
// the measured 2 s backoff. The unopened-escalation semantics are untouched: EVERY error on
// an instance that never opened reports onOpenFailure, and an opened instance never does.

class ControlledSource {
  static instances: ControlledSource[] = [];
  listeners: Record<string, Array<(event: unknown) => void>> = {};
  closed = false;
  constructor(public url: string) {
    ControlledSource.instances.push(this);
  }
  addEventListener(type: string, fn: (event: unknown) => void): void {
    (this.listeners[type] ??= []).push(fn);
  }
  close(): void {
    this.closed = true;
  }
  fire(type: string, event: unknown = {}): void {
    for (const fn of this.listeners[type] ?? []) fn(event);
  }
}

function openStream(handlers: {
  onOpen?: () => void;
  onDisconnect?: () => void;
  onOpenFailure?: () => void;
}): ConversationStreamController {
  return openConversationStream({
    sessionId: "s1",
    epoch: "e1",
    getResumeCursor: () => null,
    handlers: { onEnvelope: () => {}, ...handlers },
    eventSourceCtor: ControlledSource as unknown as EventSourceCtor,
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("boot-window reconnect backoff (260718-CHATS-L5I A3)", () => {
  it("retries FAST before the first successful open — the boot-503 window costs ≤0.5 s", () => {
    vi.useFakeTimers();
    ControlledSource.instances = [];
    const controller = openStream({});
    expect(ControlledSource.instances).toHaveLength(1);

    // The first attempt errors unopened (the measured bridge-boot 503): the reopen is due at
    // the boot cadence, not the established 2 s backoff.
    ControlledSource.instances[0].fire("error");
    vi.advanceTimersByTime(499);
    expect(ControlledSource.instances).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(ControlledSource.instances).toHaveLength(2);
    controller.stop();
  });

  it("keeps the established 2 s backoff once the stream has opened", () => {
    vi.useFakeTimers();
    ControlledSource.instances = [];
    const controller = openStream({});
    ControlledSource.instances[0].fire("open");

    // A drop of an ESTABLISHED stream is a real transport drop: no boot haste.
    ControlledSource.instances[0].fire("error");
    vi.advanceTimersByTime(1999);
    expect(ControlledSource.instances).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(ControlledSource.instances).toHaveLength(2);
    controller.stop();
  });

  it("closes the boot window at the FIRST open even across retried instances", () => {
    vi.useFakeTimers();
    ControlledSource.instances = [];
    const controller = openStream({});

    // Boot race, then the retry opens: the window closes for the life of the controller.
    ControlledSource.instances[0].fire("error");
    vi.advanceTimersByTime(500);
    expect(ControlledSource.instances).toHaveLength(2);
    ControlledSource.instances[1].fire("open");

    ControlledSource.instances[1].fire("error");
    vi.advanceTimersByTime(500);
    expect(ControlledSource.instances).toHaveLength(2); // still the 2 s backoff
    vi.advanceTimersByTime(1500);
    expect(ControlledSource.instances).toHaveLength(3);
    controller.stop();
  });

  it("leaves the F-h unopened-escalation signal untouched in both windows", () => {
    vi.useFakeTimers();
    ControlledSource.instances = [];
    const onOpenFailure = vi.fn();
    const onDisconnect = vi.fn();
    const controller = openStream({ onOpenFailure, onDisconnect });

    // Every error on an instance that NEVER opened reports onOpenFailure (the store's
    // rejected-resume signal) — boot haste changes the delay, never the signal.
    ControlledSource.instances[0].fire("error");
    expect(onOpenFailure).toHaveBeenCalledTimes(1);
    expect(onDisconnect).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(500);
    ControlledSource.instances[1].fire("error");
    expect(onOpenFailure).toHaveBeenCalledTimes(2);
    vi.advanceTimersByTime(500);

    // An instance that DID open errors as a plain drop: disconnect, never open-failure.
    ControlledSource.instances[2].fire("open");
    ControlledSource.instances[2].fire("error");
    expect(onOpenFailure).toHaveBeenCalledTimes(2);
    expect(onDisconnect).toHaveBeenCalledTimes(3);
    controller.stop();
  });
});

describe("liveness watchdog (260723 sleep/wake + half-open wedge)", () => {
  // The reported defect: the laptop slept with a chat open; on wake the EventSource was a
  // half-open corpse — no `error` ever fired, so neither the reconnect backoff nor the dead-stream
  // escalation engaged and the surface waited forever. The watchdog judges an OPEN stream dead
  // on a wall-clock jump (OS sleep) or on silence past the generous frame backstop (wedged
  // half-open socket without sleep), and QUIETLY cycles it: close + fresh resumable subscribe
  // from the latest cursor — no onDisconnect, so a healthy resume never flashes "connecting…".
  // Sleep is injected the honest way: `vi.setSystemTime` jumps the wall clock WITHOUT firing
  // the suspended ticks; the next tick then sees the jump exactly as a post-wake tick would.
  function openWatched(handlers: {
    onOpen?: () => void;
    onDisconnect?: () => void;
    onOpenFailure?: () => void;
  }): ConversationStreamController {
    return openConversationStream({
      sessionId: "s1",
      epoch: "e1",
      getResumeCursor: () => "evt-7" as ActiveEventCursor,
      handlers: { onEnvelope: () => {}, ...handlers },
      eventSourceCtor: ControlledSource as unknown as EventSourceCtor,
    });
  }

  it("a wall-clock jump while OPEN quietly re-subscribes from the latest cursor (no disconnect)", () => {
    vi.useFakeTimers();
    ControlledSource.instances = [];
    const onOpen = vi.fn();
    const onDisconnect = vi.fn();
    const onOpenFailure = vi.fn();
    const controller = openWatched({ onOpen, onDisconnect, onOpenFailure });
    ControlledSource.instances[0].fire("open");
    expect(onOpen).toHaveBeenCalledTimes(1);

    // Ordinary ticking alone never cycles a quiet stream.
    vi.advanceTimersByTime(20_000);
    expect(ControlledSource.instances).toHaveLength(1);

    // OS sleep: the wall clock jumps ~1 h without a tick; the first post-wake tick judges.
    vi.setSystemTime(Date.now() + 3_600_000);
    vi.advanceTimersByTime(5_000);
    expect(ControlledSource.instances[0].closed).toBe(true); // the corpse is closed
    expect(ControlledSource.instances).toHaveLength(2); // fresh subscribe, SAME cursor — no re-page
    expect(ControlledSource.instances[1].url).toContain("after=evt-7");
    expect(onDisconnect).not.toHaveBeenCalled(); // quiet: the surface stays "live"
    expect(onOpenFailure).not.toHaveBeenCalled();

    // The fresh subscribe opens instantly (priming comment) — the resume completes invisibly.
    ControlledSource.instances[1].fire("open");
    expect(onOpen).toHaveBeenCalledTimes(2);

    // The open-failure escalation is untouched by the cycle: a plain drop on the cycled (opened) instance reports
    // disconnect only — never the unopened signal — and keeps the established backoff.
    ControlledSource.instances[1].fire("error");
    expect(onDisconnect).toHaveBeenCalledTimes(1);
    expect(onOpenFailure).not.toHaveBeenCalled(); // it HAD opened — a plain drop
    vi.advanceTimersByTime(2_000); // established backoff, not the boot cadence
    expect(ControlledSource.instances).toHaveLength(3);
    expect(ControlledSource.instances[2].url).toContain("after=evt-7");
    controller.stop();
  });

  it("keeps a quiet-but-healthy stream open under the backstop, and cycles a dead one past it", () => {
    vi.useFakeTimers();
    ControlledSource.instances = [];
    const onDisconnect = vi.fn();
    const controller = openWatched({ onDisconnect });
    ControlledSource.instances[0].fire("open");

    // Under the 90 s backstop an idle stream is legitimate: it stays open.
    vi.advanceTimersByTime(89_999);
    expect(ControlledSource.instances).toHaveLength(1);
    expect(ControlledSource.instances[0].closed).toBe(false);

    // A received frame is a sign of life and resets the backstop clock (it lands at t≈90 s,
    // just before that tick would have judged).
    ControlledSource.instances[0].fire("conversation", { data: "{}" });
    vi.advanceTimersByTime(89_999);
    expect(ControlledSource.instances).toHaveLength(1);

    // Past the backstop with no frame, the (wedged) stream is cycled to a fresh subscribe.
    vi.advanceTimersByTime(5_000);
    expect(ControlledSource.instances[0].closed).toBe(true);
    expect(ControlledSource.instances).toHaveLength(2);
    expect(ControlledSource.instances[1].url).toContain("after=evt-7");
    expect(onDisconnect).not.toHaveBeenCalled(); // still quiet
    controller.stop();
  });

  it("never reconnect-storms a hidden tab; one quiet cycle at most on refocus", () => {
    vi.useFakeTimers();
    ControlledSource.instances = [];
    const controller = openWatched({});
    ControlledSource.instances[0].fire("open");

    // Five minutes hidden with throttled/paused timers: judgments are exempt — zero cycles.
    vi.spyOn(document, "hidden", "get").mockReturnValue(true);
    vi.advanceTimersByTime(300_000);
    expect(ControlledSource.instances).toHaveLength(1);
    expect(ControlledSource.instances[0].closed).toBe(false);

    // Refocus after long background silence: at most ONE quiet cycle (a channel that died in
    // the background recovers within a tick; a healthy one pays a single silent resubscribe).
    vi.spyOn(document, "hidden", "get").mockReturnValue(false);
    document.dispatchEvent(new Event("visibilitychange"));
    expect(ControlledSource.instances).toHaveLength(2);
    expect(ControlledSource.instances[1].url).toContain("after=evt-7");
    ControlledSource.instances[1].fire("open");

    // Life resumes normally — the refocus cycle does not repeat.
    vi.advanceTimersByTime(89_999);
    expect(ControlledSource.instances).toHaveLength(2);
    controller.stop();
  });

  it("stops judging once stopped", () => {
    vi.useFakeTimers();
    ControlledSource.instances = [];
    const controller = openWatched({});
    ControlledSource.instances[0].fire("open");
    controller.stop();
    vi.setSystemTime(Date.now() + 3_600_000);
    vi.advanceTimersByTime(200_000);
    expect(ControlledSource.instances).toHaveLength(1);
  });

  // The watchdog's cycle re-opens QUIETLY (no phase write). If that fresh
  // subscribe is itself a half-open corpse it fires neither `open` NOR `error` (a CONNECTING
  // EventSource cannot surface the failure), so before the open-deadline the phase sat at a
  // fabricated "live" forever — a silent dead chat. The deadline drives the ordinary dead-instance
  // path so the wedge becomes an honest disconnect + open-failure.
  it("escalates HONESTLY when a cycled subscribe never opens — no silent fabricated 'live' (M4)", () => {
    vi.useFakeTimers();
    ControlledSource.instances = [];
    const onDisconnect = vi.fn();
    const onOpenFailure = vi.fn();
    const controller = openWatched({ onDisconnect, onOpenFailure });
    ControlledSource.instances[0].fire("open");

    // Wedge the live stream (OS sleep): the watchdog quietly cycles it to a fresh subscribe.
    vi.setSystemTime(Date.now() + 3_600_000);
    vi.advanceTimersByTime(5_000);
    expect(ControlledSource.instances).toHaveLength(2);
    expect(onDisconnect).not.toHaveBeenCalled(); // the cycle itself is quiet

    // The fresh subscribe never opens and never errors. WITHOUT the open-deadline nothing more
    // fires (the watchdog is a no-op on a non-open channel) and the phase stays "live"; WITH it the
    // deadline turns the silent wedge loud — exactly the signals the store's open-failure path consumes.
    vi.advanceTimersByTime(STREAM_OPEN_DEADLINE_MS);
    expect(onDisconnect).toHaveBeenCalledTimes(1);
    expect(onOpenFailure).toHaveBeenCalledTimes(1);
    controller.stop();
  });

  // Neither channel emits a heartbeat an EventSource can surface, so "no frame
  // for 90 s" on a quiet chat is the HEALTHY state, not death. Re-judging that silence every 90 s
  // self-cycled every idle channel forever (measured 20 corpses / 30 min, each re-shipping the full
  // snapshot). The backstop is now one-shot: it spends a single quiet resubscribe per episode of
  // observed life and then leaves an idle channel alone.
  it("cycles a persistently idle channel at MOST once — no 90 s self-cycle loop (M8)", () => {
    vi.useFakeTimers();
    ControlledSource.instances = [];
    const onDisconnect = vi.fn();
    const controller = openWatched({ onDisconnect });
    ControlledSource.instances[0].fire("open");

    // Silence past the backstop cycles the (possibly wedged) channel once — the honest one-shot.
    vi.advanceTimersByTime(90_000);
    expect(ControlledSource.instances).toHaveLength(2);
    // The resubscribe opens on its priming handshake. That handshake is NOT proof the channel is
    // delivering content, so it must not re-arm the backstop.
    ControlledSource.instances[1].fire("open");

    // A genuinely idle-but-healthy channel now stays silent. WITHOUT the one-shot it re-cycles
    // every 90 s (here five more windows → 5+ extra corpses); WITH it the channel is left alone.
    vi.advanceTimersByTime(5 * 90_000);
    expect(ControlledSource.instances).toHaveLength(2);
    expect(onDisconnect).not.toHaveBeenCalled();
    controller.stop();
  });

  it("re-arms the one-shot when genuine content proves the channel alive again (M8)", () => {
    vi.useFakeTimers();
    ControlledSource.instances = [];
    const controller = openWatched({});
    ControlledSource.instances[0].fire("open");

    // First idle episode: one cycle, then spent.
    vi.advanceTimersByTime(90_000);
    expect(ControlledSource.instances).toHaveLength(2);
    ControlledSource.instances[1].fire("open");

    // A real content frame lands WELL past the resubscribe handshake: the channel is delivering on
    // its own again, so its silence is informative once more and the backstop re-arms. (A frame
    // inside the handshake-settle window would be the resubscribe's own replay and must NOT re-arm
    // — that guard is what stops the loop, so the re-arm must require a later frame.)
    vi.advanceTimersByTime(20_000);
    ControlledSource.instances[1].fire("conversation", { data: "{}" });

    // It then wedges again: a fresh idle episode earns a second, honest one-shot cycle.
    vi.advanceTimersByTime(90_000);
    expect(ControlledSource.instances).toHaveLength(3);
    controller.stop();
  });
});

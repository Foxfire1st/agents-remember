/**
 * The fakes the declared-wait tests share: a sender that keeps what it sent, a
 * clock whose timers a case fires by hand, the host's state for one agent, and one
 * turn end driven through the pure decision with them, so no host is needed.
 */
import {
  noteTurnEnded,
  type HostAgentState,
  type TurnEndedAgent,
  type TurnEndedFacts,
  type TurnEndedSender,
  type TurnEndedTracker,
} from "./turn_ended.ts";

export interface SentMessage {
  agentId: string;
  text: string;
}

export function makeSender(): TurnEndedSender & { sent: SentMessage[] } {
  const sent: SentMessage[] = [];
  return {
    sent,
    sendMessage(agentId: string, text: string): Promise<void> {
      sent.push({ agentId, text });
      return Promise.resolve();
    },
  };
}

export interface FakeTimer {
  callback: () => void;
  delayMs: number;
  cleared: boolean;
}

/** A clock that stands at `at`, and the timers the decision arms, so a case can end a wait by hand. */
export function fakeClock(at: string) {
  const current = new Date(at);
  const timers: FakeTimer[] = [];
  return {
    timers,
    now: () => current,
    setTimeout: (callback: () => void, delayMs: number): unknown => {
      const timer: FakeTimer = { callback, delayMs, cleared: false };
      timers.push(timer);
      return timer;
    },
    clearTimeout: (handle: unknown): void => {
      (handle as FakeTimer).cleared = true;
    },
    async fire(): Promise<void> {
      for (const timer of timers.splice(0)) if (!timer.cleared) timer.callback();
      await new Promise<void>((resolve) => setImmediate(resolve));
    },
    pending(): FakeTimer[] {
      return timers.filter((timer) => !timer.cleared);
    },
  };
}

export type FakeClock = ReturnType<typeof fakeClock>;

/** The host's state for one agent, as a send reads it: idle unless a case says otherwise. */
export function hostState(overrides: Partial<HostAgentState> & { readonly id: string }): HostAgentState {
  return { parentAgentId: null, inTurn: false, permissionWait: false, archived: false, ...overrides };
}

/** One seat's turn end as a case drives it. */
export interface SeatEnd {
  readonly agent: TurnEndedAgent;
  readonly records: Readonly<Record<string, TurnEndedFacts>>;
  readonly timeline: readonly unknown[];
  /** The host's outcome for the turn; `completed` when absent. */
  readonly outcome?: string;
  /** The host's agents at the end and at the send; when absent, an idle seat and its idle parent. */
  readonly readHost?: () => Promise<readonly HostAgentState[] | null>;
}

/** The seat's turn end through the decision, on the fake clock's timers. */
export function endSeatTurn(
  tracker: TurnEndedTracker,
  sender: TurnEndedSender,
  clock: FakeClock,
  end: SeatEnd,
): ReturnType<typeof noteTurnEnded> {
  return noteTurnEnded(end.agent, end.timeline, end.records, tracker, sender, {
    outcome: end.outcome ?? "completed",
    now: clock.now,
    setTimeout: clock.setTimeout,
    clearTimeout: clock.clearTimeout,
    readHost: end.readHost ?? (async () => [hostState({ id: "seat" }), hostState({ id: "parent" })]),
  });
}

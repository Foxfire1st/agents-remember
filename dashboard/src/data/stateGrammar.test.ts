import { describe, expect, it } from "vitest";

import {
  PULSE_ANIMATION,
  PULSE_DURATION_S,
  PULSE_TIMING,
  seatVisualState,
} from "./stateGrammar";

// The dot-grammar contract: one mapping for every
// surface; blocked-on-human is STEADY, only working/failed pulse, and the pulse is the slow
// ease-in-out one — never steps().

describe("seatVisualState mapping (spec §2.4)", () => {
  it("working = cyan SLOW PULSE", () => {
    expect(seatVisualState({ turnState: "working" })).toMatchObject({
      key: "working",
      color: "cyan",
      pulse: true,
      chip: "working",
    });
  });

  it("awaiting-input = STEADY amber (doctrine: blocked-on-human never flickers)", () => {
    expect(seatVisualState({ turnState: "awaiting-input" })).toMatchObject({
      key: "awaiting-input",
      color: "amber",
      pulse: false,
      chip: "input?",
    });
    // A pending interaction is awaiting-input even when the sweep has not reclassified yet.
    expect(
      seatVisualState({ turnState: "working", controlPendingInteraction: { kind: "approval" } }),
    ).toMatchObject({ key: "awaiting-input", pulse: false });
  });

  it("a seat blocked SOLELY on a multiplexed sub-agent approval is awaiting-input", () => {
    // Singular slot null, plural non-empty: the attention grammar must not go dark.
    expect(
      seatVisualState({
        turnState: "working",
        controlPendingInteractions: [
          {
            interactionId: "ix_agent",
            kind: "permission",
            prompt: "Allow the sub-agent command?",
            raw: { threadId: "agent-thread-1", agentLabel: "agent agent-t" },
          },
        ],
      }),
    ).toMatchObject({ key: "awaiting-input", pulse: false });
    // Both slots present: the parent presentation is unchanged.
    expect(
      seatVisualState({
        turnState: "working",
        controlPendingInteraction: { kind: "approval" },
        controlPendingInteractions: [
          { interactionId: "ix_agent", kind: "permission", prompt: "agent asks" },
        ],
      }),
    ).toMatchObject({ key: "awaiting-input", pulse: false });
  });

  it("waiting(reason) = STEADY muted-amber, reason rendered into word and chip", () => {
    const visual = seatVisualState({ turnState: "working", waitingReason: "ci run 8323" });
    expect(visual).toMatchObject({ key: "waiting", color: "mutedAmber", pulse: false });
    expect(visual.word).toBe("waiting(ci run 8323)");
    expect(visual.chip).toBe("waiting: ci run 8323");
  });

  it("failed = alarm SLOW PULSE and outranks turn-state", () => {
    expect(seatVisualState({ controlState: "failed", turnState: "working" })).toMatchObject({
      key: "failed",
      color: "alarm",
      pulse: true,
    });
  });

  it("liveTurnWorking (R9) overrides a lagging catalog turn-ended with a working state", () => {
    // While the projection reports a live streaming turn, the
    // sweep-bounded catalog still reads turn-ended — the fresher projection signal must win so no
    // authority shows settled-green during a stream.
    expect(seatVisualState({ turnState: "turn-ended", liveTurnWorking: true })).toMatchObject({
      key: "working",
      color: "cyan",
      pulse: true,
    });
  });

  it("liveTurnWorking (R9) NEVER fakes liveness over a terminal / fault / blocked state", () => {
    // The projection signal slots BELOW the terminal/fault/blocked guards — it can never resurrect
    // a landed/retired/failed/awaiting-input seat.
    expect(seatVisualState({ status: "terminated", liveTurnWorking: true }).key).toBe("retired");
    expect(seatVisualState({ controlState: "failed", liveTurnWorking: true }).key).toBe("failed");
    expect(
      seatVisualState({ controlPendingInteraction: { kind: "approval" }, liveTurnWorking: true }).key,
    ).toBe("awaiting-input");
  });

  it("ready / turn-ended = steady mint", () => {
    expect(seatVisualState({ controlState: "ready" })).toMatchObject({
      key: "ready",
      color: "mint",
      pulse: false,
    });
    expect(seatVisualState({ turnState: "turn-ended" })).toMatchObject({
      key: "turn-ended",
      color: "mint",
      pulse: false,
      chip: "turn-ended",
    });
  });

  it("a fresh chat reads starting → ready and NEVER stale / turn-ended (260718-CHATS-L5I A2b)", () => {
    // The sweep makes NO seat claim until a fresh chat's first turn, so the dot follows the
    // control lifecycle: cyan starting during boot, then calm mint ready with NO chip — the
    // boot stale chip and the fake turn-ended chip are gone from the fresh-chat trajectory.
    expect(seatVisualState({ controlState: "starting" })).toMatchObject({
      key: "starting",
      chip: "starting",
    });
    expect(seatVisualState({ controlState: "ready" })).toMatchObject({
      key: "ready",
      word: "ready",
      color: "mint",
      pulse: false,
      chip: undefined,
    });
  });

  it("landed / retired = dormant and outrank every live signal", () => {
    expect(seatVisualState({ status: "landed", turnState: "working" })).toMatchObject({
      key: "landed",
      color: "dormant",
      pulse: false,
      chip: "landed",
    });
    expect(seatVisualState({ status: "terminated", controlState: "failed" })).toMatchObject({
      key: "retired",
      color: "dormant",
      pulse: false,
    });
  });

  it("mirrors, never invents: unclassified rows render as unclassified; stale stays stale", () => {
    expect(seatVisualState({})).toMatchObject({ key: "unclassified", chip: undefined });
    expect(seatVisualState({ turnState: "stale" })).toMatchObject({ key: "stale", pulse: false });
    expect(seatVisualState({ status: "exited" })).toMatchObject({ key: "exited", color: "dormant" });
  });

  it("starting = cyan steady", () => {
    expect(seatVisualState({ controlState: "starting" })).toMatchObject({
      key: "starting",
      color: "cyan",
      pulse: false,
    });
  });
});

describe("the pulse ruling (developer, 2026-07-16)", () => {
  it("is the 2.4 s ease-in-out pulse — NEVER steps()", () => {
    expect(PULSE_DURATION_S).toBe(2.4);
    expect(PULSE_TIMING).toBe("ease-in-out");
    expect(PULSE_ANIMATION).not.toContain("steps");
    // StateDot.tsx inlines this exact string for Panda's static extraction — pin the pair.
    expect(PULSE_ANIMATION).toBe("pulseSlow 2.4s ease-in-out infinite");
  });
});

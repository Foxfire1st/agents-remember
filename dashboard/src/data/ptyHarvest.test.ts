// Legacy-raw byte-stream harvesting (260715-FEUI-L6 R7): the pure OSC parsers + the harvest
// store — hints only, never grammar states; xterm itself stays out of jsdom.
import { beforeEach, describe, expect, it } from "vitest";

import { parseOsc133, parseOsc94, ptyHarvestStore, turnHintWord } from "./ptyHarvest";

beforeEach(() => {
  ptyHarvestStore.setState({ bySession: {} });
});

describe("parseOsc133 (shell-integration marks)", () => {
  it("maps A/B → prompt, C → running, D → finished", () => {
    expect(parseOsc133("A", 1)?.hint).toBe("prompt");
    expect(parseOsc133("B", 1)?.hint).toBe("prompt");
    expect(parseOsc133("C", 1)?.hint).toBe("command-running");
    expect(parseOsc133("D;0", 1)?.hint).toBe("command-finished");
  });

  it("unknown marks are never fabricated into hints", () => {
    expect(parseOsc133("Z;whatever", 1)).toBeNull();
    expect(parseOsc133("", 1)).toBeNull();
  });
});

describe("parseOsc94 (ConEmu progress)", () => {
  it("maps state 0 → done, active states → progress with a clamped percent", () => {
    expect(parseOsc94("4;0", 1)?.hint).toBe("progress-done");
    expect(parseOsc94("4;1;50", 1)).toEqual({ hint: "progress", progress: 50, at: 1 });
    expect(parseOsc94("4;1;250", 1)?.progress).toBe(100);
    expect(parseOsc94("4;3", 1)).toEqual({ hint: "progress", at: 1 });
  });

  it("non-progress OSC 9 payloads (e.g. notifications) are ignored", () => {
    expect(parseOsc94("some notification text", 1)).toBeNull();
    expect(parseOsc94("4;x", 1)).toBeNull();
  });
});

describe("turnHintWord", () => {
  it("renders clearly-labeled hint words", () => {
    expect(turnHintWord({ hint: "command-running", at: 1 })).toBe("command running");
    expect(turnHintWord({ hint: "progress", progress: 42, at: 1 })).toBe("progress 42%");
  });
});

describe("harvest store", () => {
  it("bell sets the pending marker; focusing the seat acknowledges it", () => {
    ptyHarvestStore.getState().recordBell("raw-1", 100);
    expect(ptyHarvestStore.getState().bySession["raw-1"]).toMatchObject({
      bellPending: true,
      lastBellAt: 100,
    });
    ptyHarvestStore.getState().acknowledgeBell("raw-1");
    expect(ptyHarvestStore.getState().bySession["raw-1"].bellPending).toBe(false);
  });

  it("acknowledge without a pending bell is a no-op (no state churn)", () => {
    const before = ptyHarvestStore.getState().bySession;
    ptyHarvestStore.getState().acknowledgeBell("never-seen");
    expect(ptyHarvestStore.getState().bySession).toBe(before);
  });

  it("title and turn hints are per-session and independent", () => {
    ptyHarvestStore.getState().recordTitle("raw-1", "vim · main.rs");
    ptyHarvestStore.getState().recordTurnHint("raw-2", { hint: "prompt", at: 5 });
    expect(ptyHarvestStore.getState().bySession["raw-1"].title).toBe("vim · main.rs");
    expect(ptyHarvestStore.getState().bySession["raw-1"].turnHint).toBeUndefined();
    expect(ptyHarvestStore.getState().bySession["raw-2"].turnHint?.hint).toBe("prompt");
  });
});

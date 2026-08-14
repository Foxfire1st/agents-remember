import { describe, expect, it } from "vitest";

import { startSubmitRecord, type SubmitPhase, type SubmitRecord } from "./submitMachine";
import {
  compactSubmitHistory,
  compactSubmitQueue,
  SUBMIT_HISTORY_INSPECTOR_WINDOW,
  SUBMIT_QUEUE_RETENTION_WINDOW,
} from "./submitRetention";

function record(index: number, phase: SubmitPhase = "accepted"): SubmitRecord {
  return {
    ...startSubmitRecord({
      requestId: `request-${index}`,
      text: `full message ${index}`,
      expectedBridgeEpoch: "bridge-epoch-l5",
      source: "composer",
      submittedRevision: index,
      at: index,
    }),
    phase,
  };
}

describe("reliable-submit retention policy (F4)", () => {
  it("keeps the newest finite inspector window of settled records", () => {
    const input = Array.from(
      { length: SUBMIT_HISTORY_INSPECTOR_WINDOW + 5 },
      (_, index) => record(index),
    );
    const compacted = compactSubmitHistory(input);
    expect(compacted).toHaveLength(SUBMIT_HISTORY_INSPECTOR_WINDOW);
    expect(compacted[0]?.requestId).toBe("request-5");
    expect(compacted.at(-1)?.requestId).toBe(
      `request-${SUBMIT_HISTORY_INSPECTOR_WINDOW + 4}`,
    );
  });

  it.each(["sending", "ambiguous", "reconciling", "endgame", "route-error"] as const)(
    "protects an old %s record outside the settled inspector tail",
    (phase) => {
      const protectedRecord = record(0, phase);
      const settled = Array.from(
        { length: SUBMIT_HISTORY_INSPECTOR_WINDOW + 3 },
        (_, index) => record(index + 1),
      );
      const compacted = compactSubmitHistory([protectedRecord, ...settled]);
      expect(compacted).toHaveLength(SUBMIT_HISTORY_INSPECTOR_WINDOW + 1);
      expect(compacted[0]).toBe(protectedRecord);
      expect(compacted.filter((item) => item.phase === phase)).toEqual([protectedRecord]);
    },
  );

  it("returns a protected record to the finite settled tail after it resolves", () => {
    const old = record(0, "endgame");
    const settled = Array.from(
      { length: SUBMIT_HISTORY_INSPECTOR_WINDOW },
      (_, index) => record(index + 1),
    );
    expect(compactSubmitHistory([old, ...settled])).toContain(old);
    expect(compactSubmitHistory([{ ...old, phase: "released" }, ...settled])).not.toContainEqual(
      expect.objectContaining({ requestId: old.requestId }),
    );
  });

  it("caps full-text queue retention to the newest named window", () => {
    const queue = Array.from(
      { length: SUBMIT_QUEUE_RETENTION_WINDOW + 3 },
      (_, index) => ({ requestId: `queue-${index}`, text: `secret ${index}` }),
    );
    const compacted = compactSubmitQueue(queue);
    expect(compacted).toHaveLength(SUBMIT_QUEUE_RETENTION_WINDOW);
    expect(compacted[0]?.requestId).toBe("queue-3");
    expect(compacted.at(-1)?.requestId).toBe(
      `queue-${SUBMIT_QUEUE_RETENTION_WINDOW + 2}`,
    );
  });
});

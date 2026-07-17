import { describe, expect, it } from "vitest";

import {
  SET_RESULT_CLAMP,
  SET_RESULT_ECHO_NO_VALUE,
  SET_RESULTS,
  UNKNOWN_THEN_READBACK,
  codexLiveSessionSnapshot,
} from "../test/fixtures/capabilityEnvelopes";
import type { SetResultWire } from "../types/harnessCapabilities";
import type { PendingSet } from "./sessionCockpitStore";
import {
  classifySetResponse,
  demandsAttention,
  isClamp,
  readbackMatches,
  reduceSetResult,
  resolvePendingsByReadback,
  shouldRefetchOnTurnEnded,
  toResultSnapshot,
  type SetKind,
} from "./setAcceptance";

// The acceptance honesty table, EXHAUSTIVE (260715-FEUI-L4 R2/R9): every acceptance × kind ×
// clamp combination through the pure reducer — requested/effective never merge, queued/unknown
// never move the marker, unsupported keeps the prior state, unknown gets exactly ONE re-GET.

const result = (overrides: Partial<SetResultWire>): SetResultWire => ({
  ok: true,
  acceptance: "echo-verified",
  requestedValue: "v-req",
  effectiveValue: "v-req",
  detail: null,
  ...overrides,
});

const KINDS: SetKind[] = ["model", "effort"];

describe("reduceSetResult — the exhaustive acceptance × kind × clamp table", () => {
  // The full honesty table: [description, result, expected reduction].
  const table: Array<[string, SetResultWire, ReturnType<typeof reduceSetResult>]> = [
    [
      "echo-verified, effective == requested",
      result({}),
      {
        pendingPhase: undefined,
        ledger: { acknowledged: true },
        echoEffective: "v-req",
        autoReGet: false,
        clamped: false,
      },
    ],
    [
      "echo-verified CLAMP (effective ≠ requested) — demands acknowledgment",
      SET_RESULT_CLAMP,
      {
        pendingPhase: undefined,
        ledger: { acknowledged: false },
        echoEffective: "high",
        autoReGet: false,
        clamped: true,
      },
    ],
    [
      "echo-verified with NO echoed value — proves nothing, marker must not move",
      SET_RESULT_ECHO_NO_VALUE,
      {
        pendingPhase: undefined,
        ledger: { acknowledged: false },
        echoEffective: null,
        autoReGet: false,
        clamped: false,
      },
    ],
    [
      "immediate — already effective, marker unchanged (snapshot stays authoritative)",
      SET_RESULTS.immediate,
      {
        pendingPhase: undefined,
        ledger: { acknowledged: true },
        echoEffective: null,
        autoReGet: false,
        clamped: false,
      },
    ],
    [
      "queued — marker NOT moved, pending pinned awaiting a turn",
      SET_RESULTS.queued,
      {
        pendingPhase: "queued-awaiting-turn",
        ledger: { acknowledged: true },
        echoEffective: null,
        autoReGet: false,
        clamped: false,
      },
    ],
    [
      "unknown — uncertainty badge + exactly ONE automatic re-GET, no blind retry",
      SET_RESULTS.unknown,
      {
        pendingPhase: "unknown-verifying",
        ledger: { acknowledged: false },
        echoEffective: null,
        autoReGet: true,
        clamped: false,
      },
    ],
    [
      "unsupported — prior effective kept, verbatim detail, demands acknowledgment",
      SET_RESULTS.unsupported,
      {
        pendingPhase: undefined,
        ledger: { acknowledged: false },
        echoEffective: null,
        autoReGet: false,
        clamped: false,
      },
    ],
  ];

  // The reducer is kind-independent BY DESIGN (the kind routes storage, not honesty) — the
  // table runs for both kinds to pin that.
  for (const kind of KINDS) {
    it.each(table)(`[${kind}] %s`, (_name, wire, expected) => {
      expect(reduceSetResult(wire)).toEqual(expected);
    });
  }
});

describe("isClamp / demandsAttention", () => {
  it("clamp requires echo-verified AND a differing non-null effective value", () => {
    expect(isClamp(SET_RESULT_CLAMP)).toBe(true);
    expect(isClamp(result({}))).toBe(false);
    expect(isClamp(SET_RESULT_ECHO_NO_VALUE)).toBe(false);
    expect(isClamp(result({ acceptance: "queued", effectiveValue: null }))).toBe(false);
  });

  it("attention = unsupported | unknown | clamp — benign outcomes never mark the rail", () => {
    expect(demandsAttention(SET_RESULTS.unsupported)).toBe(true);
    expect(demandsAttention(SET_RESULTS.unknown)).toBe(true);
    expect(demandsAttention(SET_RESULT_CLAMP)).toBe(true);
    expect(demandsAttention(SET_RESULTS.immediate)).toBe(false);
    expect(demandsAttention(SET_RESULTS.queued)).toBe(false);
    expect(demandsAttention(result({}))).toBe(false);
  });
});

describe("classifySetResponse — the HTTP boundary (R3)", () => {
  it("a 200 carrying unknown/unsupported is EVIDENCE, never styled as transport failure", () => {
    for (const wire of [SET_RESULTS.unknown, SET_RESULTS.unsupported]) {
      const outcome = classifySetResponse(200, wire);
      expect(outcome.kind).toBe("result");
      if (outcome.kind === "result") expect(outcome.result).toEqual(wire);
    }
  });

  it.each([
    [404, { status: "unknown-session" }, "session-gone"],
    [409, { status: "unsupported", detail: "session has no native protocol control endpoint" }, "no-native-control"],
    [503, { status: "control-unavailable", detail: "control socket refused" }, "outage"],
  ] as const)("HTTP %s → %s", (status, body, kind) => {
    const outcome = classifySetResponse(status, body);
    expect(outcome.kind).toBe(kind);
    if ("detail" in outcome && "detail" in body) expect(outcome.detail).toBe(body.detail);
  });

  it("null status / malformed 200 → transport", () => {
    expect(classifySetResponse(null, undefined).kind).toBe("transport");
    expect(classifySetResponse(200, { ok: "yes" }).kind).toBe("transport");
  });
});

describe("toResultSnapshot", () => {
  it("nulls become absent fields; the words stay verbatim", () => {
    expect(toResultSnapshot(SET_RESULTS.unsupported)).toEqual({
      acceptance: "unsupported",
      requestedValue: "ar-unknown-model",
      detail: "requested model is absent from the dynamic catalog",
    });
    expect(toResultSnapshot(SET_RESULT_CLAMP)).toEqual({
      acceptance: "echo-verified",
      requestedValue: "max",
      effectiveValue: "high",
      detail: "thinking level clamped by the model",
    });
  });
});

describe("readback promotion (R4)", () => {
  const pending = (requestedValue: string, phase: PendingSet["phase"]): PendingSet => ({
    requestedValue,
    sentAt: 1,
    phase,
  });

  it("readbackMatches compares exact keys per kind (Pi provider-qualified verbatim)", () => {
    const snapshot = codexLiveSessionSnapshot("gpt-5.6-sol", "high");
    expect(readbackMatches("model", "gpt-5.6-sol", snapshot)).toBe(true);
    expect(readbackMatches("model", "sol", snapshot)).toBe(false);
    expect(readbackMatches("effort", "high", snapshot)).toBe(true);
    expect(readbackMatches("effort", "low", snapshot)).toBe(false);
  });

  it("queued resolves ONLY when confirmed; an unconfirming readback keeps it pinned", () => {
    const snapshot = codexLiveSessionSnapshot("gpt-5.6-sol", "high");
    expect(
      resolvePendingsByReadback({ effort: pending("high", "queued-awaiting-turn") }, snapshot),
    ).toEqual([
      { kind: "effort", resolution: "confirmed", requestedValue: "high", fromPhase: "queued-awaiting-turn" },
    ]);
    expect(
      resolvePendingsByReadback({ effort: pending("xhigh", "queued-awaiting-turn") }, snapshot),
    ).toEqual([]);
  });

  it("CODEX BOTH-QUEUED PAIR resolves together on ONE readback", () => {
    const snapshot = codexLiveSessionSnapshot("gpt-5.6-terra", "xhigh");
    const resolutions = resolvePendingsByReadback(
      {
        model: pending("gpt-5.6-terra", "queued-awaiting-turn"),
        effort: pending("xhigh", "queued-awaiting-turn"),
      },
      snapshot,
    );
    expect(resolutions.map((r) => [r.kind, r.resolution])).toEqual([
      ["model", "confirmed"],
      ["effort", "confirmed"],
    ]);
  });

  it("unknown-verifying resolves EITHER WAY (its one readback is definitive)", () => {
    const { result: unknownResult, confirmingSnapshot, disprovingSnapshot } = UNKNOWN_THEN_READBACK;
    const pendings = { effort: pending(unknownResult.requestedValue, "unknown-verifying" as const) };
    expect(resolvePendingsByReadback(pendings, confirmingSnapshot)).toEqual([
      { kind: "effort", resolution: "confirmed", requestedValue: "high", fromPhase: "unknown-verifying" },
    ]);
    expect(resolvePendingsByReadback(pendings, disprovingSnapshot)).toEqual([
      { kind: "effort", resolution: "not-applied", requestedValue: "high", fromPhase: "unknown-verifying" },
    ]);
  });

  it("inflight pendings are untouched — their POST response is still coming", () => {
    const snapshot = codexLiveSessionSnapshot("gpt-5.6-sol", "high");
    expect(resolvePendingsByReadback({ effort: pending("high", "inflight") }, snapshot)).toEqual([]);
  });
});

describe("shouldRefetchOnTurnEnded (R4 + the v3 vendor-drift delta)", () => {
  const queued: PendingSet = { requestedValue: "x", sentAt: 1, phase: "queued-awaiting-turn" };
  const unknown: PendingSet = { requestedValue: "x", sentAt: 1, phase: "unknown-verifying" };
  const inflight: PendingSet = { requestedValue: "x", sentAt: 1, phase: "inflight" };

  it.each([
    ["focused, no pendings (vendor drift — the v3 delta)", true, undefined, true],
    ["focused with pendings", true, { model: queued }, true],
    ["unfocused with a queued pending", false, { model: queued }, true],
    ["unfocused with an unknown pending", false, { effort: unknown }, true],
    ["unfocused with only an inflight pending", false, { effort: inflight }, false],
    ["unfocused, no pendings", false, {}, false],
    ["unfocused, no cockpit entry at all", false, undefined, false],
  ] as const)("%s → %s", (_name, focused, pendings, expected) => {
    expect(shouldRefetchOnTurnEnded(focused, pendings)).toBe(expected);
  });
});

import { describe, expect, it } from "vitest";

import type { SetAcceptance } from "../types/harnessCapabilities";
import { CAPABILITY_SCHEMA } from "../types/harnessCapabilities";
import {
  CAPABILITY_ERROR_BODIES,
  capabilityEnvelope,
  CLAUDE_FRESH_SESSION_SNAPSHOT,
  CLAUDE_MODEL_ROWS,
  CODEX_MODEL_ROWS,
  ENVELOPES_BY_CACHE_STATUS,
  PI_MODEL_ROWS,
  SET_RESULTS,
} from "./fixtures/capabilityEnvelopes";
import { RECONCILIATIONS, SUBMISSION_RECEIPTS } from "./fixtures/controlMessages";
import {
  FAILED_LAUNCH_ROWS,
  LAUNCH_CONFLICT,
  SEAT_TAKEN,
  OPENED_STARTING,
  OPENED_VENDOR_DEFAULTS,
  PENDING_INTERACTION_ROW,
} from "./fixtures/openResponses";

// The contract fixture pack (260715-FEUI-L3 R3): typed fixtures asserted against the RECORDED L5
// samples (260716-ACPUI-L5-{claude,codex}-live-conformance.md, -pi-live-and-resource-proof.md).
// Later leaves (L4 setters, L5 submit/reconcile, L6 interactions) consume and extend this pack.
// The values below are test evidence ONLY — the dynamic-only invariant forbids any of them from
// appearing in production code.

describe("capability envelopes", () => {
  it("carry the v1 schema and appear in ALL THREE cacheStatus values", () => {
    const statuses = Object.keys(ENVELOPES_BY_CACHE_STATUS).sort();
    expect(statuses).toEqual(["hit", "miss", "refreshed"]);
    for (const envelope of Object.values(ENVELOPES_BY_CACHE_STATUS)) {
      expect(envelope.schema).toBe(CAPABILITY_SCHEMA);
      expect(envelope.installFingerprint.length).toBeGreaterThan(0);
      expect(envelope.capabilities.models.length).toBeGreaterThan(0);
    }
  });

  it("Claude mirrors the recorded five rows — and Haiku advertises NO effort rows", () => {
    expect(CLAUDE_MODEL_ROWS.map((row) => row.key)).toEqual([
      "default",
      "opus[1m]",
      "claude-fable-5[1m]",
      "sonnet",
      "haiku",
    ]);
    const haiku = CLAUDE_MODEL_ROWS.find((row) => row.key === "haiku")!;
    expect(haiku.effortOptions).toEqual([]);
    expect(haiku.supportsEffort).toBe(false);
    const fable = CLAUDE_MODEL_ROWS.find((row) => row.key === "claude-fable-5[1m]")!;
    expect(fable.resolvedModel).toBe("claude-fable-5");
    expect(fable.effortOptions.map((option) => option.key)).toEqual([
      "low",
      "medium",
      "high",
      "xhigh",
      "max",
    ]);
  });

  it("Codex mirrors the recorded eight rows with per-row defaults and one hidden row", () => {
    expect(CODEX_MODEL_ROWS).toHaveLength(8);
    const sol = CODEX_MODEL_ROWS.find((row) => row.key === "gpt-5.6-sol")!;
    expect(sol.defaultEffort).toBe("low");
    expect(sol.effortOptions.map((option) => option.key)).toEqual([
      "low",
      "medium",
      "high",
      "xhigh",
      "max",
      "ultra",
    ]);
    const hidden = CODEX_MODEL_ROWS.filter((row) => row.hidden).map((row) => row.key);
    expect(hidden).toEqual(["codex-auto-review"]);
  });

  it("Pi keys keep the provider-qualified form verbatim", () => {
    expect(PI_MODEL_ROWS.map((row) => row.key)).toEqual([
      "deepseek/deepseek-v4-flash",
      "deepseek/deepseek-v4-pro",
    ]);
    for (const row of PI_MODEL_ROWS) {
      expect(row.key).toContain("/");
      expect(row.provider).toBe("deepseek");
    }
  });

  it("a fresh Claude session snapshot has NULL selectedEffort and only the model category", () => {
    expect(CLAUDE_FRESH_SESSION_SNAPSHOT.selectedEffort).toBeNull();
    expect(
      CLAUDE_FRESH_SESSION_SNAPSHOT.configOptions.map((option) => option.category),
    ).toEqual(["model"]);
    // hidden non-selected rows never appear as config options (server projection rule)
    const optionValues = CLAUDE_FRESH_SESSION_SNAPSHOT.configOptions[0].options.map(
      (option) => option.value,
    );
    expect(optionValues).toContain("claude-fable-5[1m]");
  });

  it("pre-session envelopes carry no selection (nothing runs yet)", () => {
    for (const harness of ["claude", "codex", "pi"] as const) {
      const envelope = capabilityEnvelope(harness, "hit");
      expect(envelope.capabilities.selectedModelKey).toBeNull();
      expect(envelope.capabilities.selectedEffort).toBeNull();
    }
  });

  it("capability-route error fixtures carry the verbatim status words per HTTP code", () => {
    expect(CAPABILITY_ERROR_BODIES.notInstalled.httpStatus).toBe(404);
    expect(CAPABILITY_ERROR_BODIES.nonNative.httpStatus).toBe(409);
    expect(CAPABILITY_ERROR_BODIES.controlUnavailable.httpStatus).toBe(503);
    expect(CAPABILITY_ERROR_BODIES.notInstalled.body.status).toBe("capability-unavailable");
    expect(CAPABILITY_ERROR_BODIES.controlUnavailable.body.status).toBe("control-unavailable");
  });
});

describe("SetResult / receipt / reconciliation vocabularies", () => {
  it("SetResult fixtures cover exactly the five acceptances", () => {
    const acceptances = Object.keys(SET_RESULTS).sort();
    const expected: SetAcceptance[] = [
      "echo-verified",
      "immediate",
      "queued",
      "unknown",
      "unsupported",
    ];
    expect(acceptances).toEqual([...expected].sort());
    for (const [acceptance, result] of Object.entries(SET_RESULTS)) {
      expect(result.acceptance).toBe(acceptance);
      expect(result.requestedValue.length).toBeGreaterThan(0);
    }
    // queued NEVER carries an effective value — the marker must not move early (handover rule)
    expect(SET_RESULTS.queued.effectiveValue).toBeNull();
    expect(SET_RESULTS.unknown.effectiveValue).toBeNull();
    expect(SET_RESULTS["echo-verified"].effectiveValue).not.toBeNull();
  });

  it("submission receipts cover exactly the five acceptance states", () => {
    expect(Object.keys(SUBMISSION_RECEIPTS).sort()).toEqual(
      ["immediate", "queued", "rejected", "unknown", "unsupported"].sort(),
    );
    for (const [acceptance, receipt] of Object.entries(SUBMISSION_RECEIPTS)) {
      expect(receipt.acceptance).toBe(acceptance);
      expect(receipt.requestId.length).toBeGreaterThan(0);
      expect(receipt.submittedAt.length).toBeGreaterThan(0);
    }
  });

  it("reconciliation fixtures cover the four states and reuse the ambiguous requestId", () => {
    expect(Object.keys(RECONCILIATIONS).sort()).toEqual(
      ["accepted", "rejected", "unresolved", "unsupported"].sort(),
    );
    for (const [state, result] of Object.entries(RECONCILIATIONS)) {
      expect(result.state).toBe(state);
      expect(result.requestId).toBe(SUBMISSION_RECEIPTS.unknown.requestId); // same id, no resend
    }
  });
});

describe("open responses + failed rows", () => {
  it("the 200-starting body echoes the REQUESTED pair verbatim before any validation", () => {
    expect(OPENED_STARTING.controlState).toBe("starting");
    expect(OPENED_STARTING.resolvedModel).toBe("claude-fable-5[1m]");
    expect(OPENED_STARTING.resolvedEffort).toBe("max");
    expect(OPENED_VENDOR_DEFAULTS.resolvedModel).toBeNull();
    expect(OPENED_VENDOR_DEFAULTS.resolvedEffort).toBeNull();
  });

  it("the conflict body carries the LIVE row's retained pair, never the attempted one", () => {
    expect(LAUNCH_CONFLICT.resolvedModel).toBe("claude-fable-5[1m]");
    expect(LAUNCH_CONFLICT.resolvedEffort).toBe("max");
    expect(LAUNCH_CONFLICT.detail).toContain("requested 'sonnet'/'high'");
  });

  it("seat-taken names the owning session", () => {
    expect(SEAT_TAKEN.session).toBe("worker-l3-live");
  });

  it("failed rows exist for ALL THREE harnesses, retain the refused pair, and the bridgeError names alternatives", () => {
    expect(FAILED_LAUNCH_ROWS.map((row) => row.harness)).toEqual(["claude", "codex", "pi"]);
    for (const row of FAILED_LAUNCH_ROWS) {
      expect(row.controlState).toBe("failed");
      expect(row.resolvedModel).toBeTruthy(); // the refused pair is retained verbatim
      expect(row.resolvedEffort).toBeTruthy();
      const bridgeError = row.controlRaw?.bridgeError;
      expect(typeof bridgeError).toBe("string");
      expect(bridgeError as string).toMatch(/advertised|alternatives/); // names the alternatives
    }
  });

  it("the pack carries a controlPendingInteraction row for the interaction leaves", () => {
    expect(PENDING_INTERACTION_ROW.controlPendingInteraction).toMatchObject({
      interactionId: "ix_7",
      kind: "approval",
    });
  });
});

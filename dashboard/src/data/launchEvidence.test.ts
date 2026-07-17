import { describe, expect, it } from "vitest";

import { FLEET } from "../test/fixtures/catalogRows";
import {
  FAILED_CLAUDE_EFFORT_ROW,
  FAILED_LAUNCH_ROWS,
  OPENED_STARTING,
  OPENED_VENDOR_DEFAULTS,
  PENDING_INTERACTION_ROW,
} from "../test/fixtures/openResponses";
import { hasLaunchEcho, launchTier, TIER_SENSE, verbatimBridgeError } from "./launchEvidence";
import type { EvidenceTier } from "./sessionCockpitStore";

// The five-tier machine (260715-FEUI-L3 R7/R8): assignment gated on CONTROL STATE — the open
// response echoes the REQUESTED pair pre-validation, so no tier ever moves on it alone.

const PAIR = { resolvedModel: "some-model", resolvedEffort: "some-effort" };

describe("launchTier — exhaustive controlState × harness × pair table", () => {
  const harnesses = ["claude", "codex", "pi"] as const;
  const echoTier: Record<(typeof harnesses)[number], EvidenceTier> = {
    claude: "model-validated", // stream-json emits NO launch-effort echo — never readback
    codex: "readback",
    pi: "readback",
  };

  const table: Array<{ controlState: string | undefined; expected: (h: string) => EvidenceTier }> = [
    { controlState: "starting", expected: () => "pending" },
    { controlState: "failed", expected: () => "refused" },
    { controlState: "ready", expected: (h) => echoTier[h as (typeof harnesses)[number]] },
    { controlState: "disconnected", expected: () => "pending" }, // no promotion without proof
    { controlState: "unsupported", expected: () => "pending" },
    { controlState: undefined, expected: () => "pending" },
  ];

  for (const harness of harnesses) {
    for (const row of table) {
      it(`${harness} + pair + controlState=${row.controlState ?? "absent"} → ${row.expected(harness)}`, () => {
        expect(launchTier({ harness, ...PAIR, controlState: row.controlState })).toBe(
          row.expected(harness),
        );
      });
    }
  }

  it("a BOTH-NULL launch is 'defaults' regardless of control state (no pair was ever sent)", () => {
    for (const controlState of ["starting", "ready", "failed", "disconnected", undefined]) {
      expect(launchTier({ harness: "claude", controlState })).toBe("defaults");
    }
  });

  it("an unknown/settings-defined harness never promotes past model-validated on ready", () => {
    expect(launchTier({ harness: "hermes", ...PAIR, controlState: "ready" })).toBe(
      "model-validated",
    );
    expect(hasLaunchEcho("hermes")).toBe(false);
    expect(hasLaunchEcho(undefined)).toBe(false);
  });

  it("Claude launch evidence NEVER reaches readback (the review-killing invariant)", () => {
    for (const controlState of ["starting", "ready", "failed", "disconnected", "unsupported"]) {
      expect(launchTier({ harness: "claude", ...PAIR, controlState })).not.toBe("readback");
    }
  });
});

describe("launchTier over the R3 fixture pack (every fixture exercised)", () => {
  it("open 200-starting responses render the retained pair at 'pending'", () => {
    expect(
      launchTier({
        harness: OPENED_STARTING.harness ?? undefined,
        resolvedModel: OPENED_STARTING.resolvedModel,
        resolvedEffort: OPENED_STARTING.resolvedEffort,
        controlState: OPENED_STARTING.controlState,
      }),
    ).toBe("pending");
  });

  it("a vendor-defaults open renders 'defaults'", () => {
    expect(
      launchTier({
        harness: OPENED_VENDOR_DEFAULTS.harness ?? undefined,
        resolvedModel: OPENED_VENDOR_DEFAULTS.resolvedModel,
        resolvedEffort: OPENED_VENDOR_DEFAULTS.resolvedEffort,
        controlState: OPENED_VENDOR_DEFAULTS.controlState,
      }),
    ).toBe("defaults");
  });

  it("ALL THREE harnesses' failed rows render 'refused' — the refusal path is uniform", () => {
    for (const row of [...FAILED_LAUNCH_ROWS, FAILED_CLAUDE_EFFORT_ROW]) {
      expect(launchTier(row)).toBe("refused");
    }
  });

  it("the pending-interaction fixture (ready, claude pair) sits at model-validated", () => {
    expect(launchTier(PENDING_INTERACTION_ROW)).toBe("model-validated");
  });

  it("FLEET rows classify without fabrication (ready pairs promote; pairless rows are defaults)", () => {
    const workerL4 = FLEET.find((row) => row.id === "worker-l4");
    expect(launchTier(workerL4 ?? {})).toBe("model-validated"); // claude harness, ready, pair
    const scout = FLEET.find((row) => row.id === "scout");
    // L2's scout fixture carries NO pair — a failed launch without a selection stays 'defaults'
    // (nothing was requested); its bridgeError still renders in the failed banner.
    expect(launchTier(scout ?? {})).toBe("defaults");
  });
});

describe("verbatimBridgeError (R6)", () => {
  it("returns a string bridgeError VERBATIM", () => {
    for (const row of FAILED_LAUNCH_ROWS) {
      expect(verbatimBridgeError(row.controlRaw)).toBe(row.controlRaw?.bridgeError);
    }
  });

  it("serializes a non-string retained shape rather than rewording it", () => {
    expect(verbatimBridgeError({ bridgeError: { code: 7 } })).toBe('{"code":7}');
  });

  it("is null when nothing was retained — the banner states the absence, never invents", () => {
    expect(verbatimBridgeError(undefined)).toBeNull();
    expect(verbatimBridgeError({})).toBeNull();
  });
});

describe("TIER_SENSE", () => {
  it("carries an honest sentence for every tier (badge titles)", () => {
    const tiers: EvidenceTier[] = ["pending", "readback", "model-validated", "defaults", "refused"];
    for (const tier of tiers) {
      expect(TIER_SENSE[tier].length).toBeGreaterThan(10);
    }
  });
});

import { describe, expect, it } from "vitest";

import { startPairChange, applyPairStepResult } from "./pairChange";
import type { PerSessionCockpit, SetLedgerEntry } from "./sessionCockpitStore";
import { deriveSetChips, hasUnackedSetAttention, queuedComposerHint } from "./setChips";
import { setWaitingCopy } from "./setControlsCopy";

// The chip-row derivation (260715-FEUI-L4 R2/R5/R6): one pure function, every surface reads it —
// each chip's text carries its acceptance WORD (never color-only), requested/effective never
// merge, and only attention-worthy outcomes demand acknowledgment.

const cockpit = (overrides: Partial<PerSessionCockpit> = {}): PerSessionCockpit => ({
  snapshotLoading: false,
  echoEvidence: {},
  pendingSets: {},
  setLedger: [],
  launchEvidence: { tier: "pending" },
  composer: { draft: "", draftRevision: 0 },
  surfaceTab: "terminal",
  turnClock: { workingSince: null },
  freshness: { ptyWs: "none", lastOutputAt: null },
  queue: [],
  submitHistory: [],
  ...overrides,
});

const entry = (overrides: Partial<SetLedgerEntry>): SetLedgerEntry => ({
  at: 1,
  kind: "effort",
  requestedValue: "max",
  result: { acceptance: "echo-verified", requestedValue: "max", effectiveValue: "max" },
  acknowledged: true,
  ...overrides,
});

describe("deriveSetChips", () => {
  it("nothing pending, nothing unacked → no chips (quiet chrome)", () => {
    expect(deriveSetChips(undefined)).toEqual([]);
    expect(deriveSetChips(cockpit())).toEqual([]);
  });

  it("inflight → the honest ~35 s waiting chip with a spinner, saying WHAT it waits for", () => {
    const chips = deriveSetChips(
      cockpit({ pendingSets: { effort: { requestedValue: "max", sentAt: 1, phase: "inflight" } } }),
    );
    expect(chips).toEqual([
      expect.objectContaining({
        id: "pending-effort",
        spinner: true,
        text: setWaitingCopy("effort", "max"),
      }),
    ]);
    expect(chips[0].text).toContain("acceptance evidence");
    expect(chips[0].text).toContain("~35 s");
  });

  it("queued/unknown pendings pin their words; PER-KIND chips coexist without clobbering", () => {
    const chips = deriveSetChips(
      cockpit({
        pendingSets: {
          model: { requestedValue: "gpt-5.6-terra", sentAt: 1, phase: "queued-awaiting-turn" },
          effort: { requestedValue: "xhigh", sentAt: 2, phase: "unknown-verifying" },
        },
      }),
    );
    expect(chips.map((chip) => chip.id)).toEqual(["queued-model", "unknown-effort"]);
    expect(chips[0].text).toContain("queued");
    expect(chips[0].text).toContain("applies on next turn");
    expect(chips[1].text).toContain("unknown");
  });

  it("CLAMP: both values rendered persistently ('requested max → effective high'), demands ack", () => {
    const chips = deriveSetChips(
      cockpit({
        setLedger: [
          entry({
            acknowledged: false,
            result: {
              acceptance: "echo-verified",
              requestedValue: "max",
              effectiveValue: "high",
              detail: "thinking level clamped by the model",
            },
          }),
        ],
      }),
    );
    expect(chips).toEqual([
      expect.objectContaining({ id: "clamp-effort", demandsAck: true, acceptance: "echo-verified" }),
    ]);
    expect(chips[0].text).toBe("echo-verified (clamped): requested max → effective high");
  });

  it("unsupported: prior kept + the VERBATIM detail, demands ack", () => {
    const chips = deriveSetChips(
      cockpit({
        setLedger: [
          entry({
            kind: "model",
            requestedValue: "ghost",
            acknowledged: false,
            result: {
              acceptance: "unsupported",
              requestedValue: "ghost",
              detail: "requested model is absent from the dynamic catalog",
            },
          }),
        ],
      }),
    );
    expect(chips[0]).toMatchObject({ id: "unsupported-model", demandsAck: true, tone: "alarm" });
    expect(chips[0].text).toContain("unsupported");
    expect(chips[0].text).toContain("prior value kept");
    expect(chips[0].text).toContain("requested model is absent from the dynamic catalog");
  });

  it("an acknowledged entry renders NO chip; a pending's own queued entry is not duplicated", () => {
    expect(
      deriveSetChips(cockpit({ setLedger: [entry({ acknowledged: true })] })),
    ).toEqual([]);
    const chips = deriveSetChips(
      cockpit({
        pendingSets: { effort: { requestedValue: "xhigh", sentAt: 1, phase: "queued-awaiting-turn" } },
        setLedger: [
          entry({
            requestedValue: "xhigh",
            acknowledged: false,
            result: { acceptance: "queued", requestedValue: "xhigh" },
          }),
        ],
      }),
    );
    expect(chips.map((chip) => chip.id)).toEqual(["queued-effort"]);
  });

  it("pair progress renders the two-step chip; a partial outcome renders the designed sentence", () => {
    const progress = deriveSetChips(cockpit({ pairChange: startPairChange("gpt-5.6-terra", "xhigh") }));
    expect(progress[0]).toMatchObject({ id: "pair-progress", spinner: true });
    expect(progress[0].text).toBe("1/2 model gpt-5.6-terra…");

    const partial = applyPairStepResult(
      applyPairStepResult(startPairChange("gpt-5.6-terra", "xhigh"), "model", {
        ok: true,
        acceptance: "echo-verified",
        requestedValue: "gpt-5.6-terra",
        effectiveValue: "gpt-5.6-terra",
        detail: null,
      }).state,
      "effort",
      { ok: false, acceptance: "unsupported", requestedValue: "xhigh", effectiveValue: null, detail: "no" },
    ).state;
    const chips = deriveSetChips(cockpit({ pairChange: partial }));
    expect(chips[0].id).toBe("pair-partial");
    expect(chips[0].text).toContain("model switched, effort request refused — session now at gpt-5.6-terra /");
    expect(chips[0].demandsAck).toBe(true);
  });

  it("a 503 route error renders alarm with retry; other route errors carry no retry", () => {
    const outage = deriveSetChips(
      cockpit({
        setRouteError: {
          kind: "effort",
          requestedValue: "high",
          httpStatus: 503,
          status: "control-unavailable",
          detail: "socket refused",
          retryable: true,
          at: 1,
        },
      }),
    );
    expect(outage[0]).toMatchObject({ id: "route-effort", retryable: true, tone: "alarm" });
    expect(outage[0].text).toContain("control outage (503)");
    const gone = deriveSetChips(
      cockpit({
        setRouteError: {
          kind: "model",
          requestedValue: "m",
          httpStatus: 404,
          status: "unknown-session",
          detail: "unknown-session",
          retryable: false,
          at: 1,
        },
      }),
    );
    expect(gone[0]).toMatchObject({ retryable: false });
    expect(gone[0].text).toContain("session gone (404)");
  });
});

describe("queuedComposerHint (R2 — the composer-hint slot)", () => {
  it("present exactly while a set is queued-awaiting-turn, naming the queued kinds", () => {
    expect(queuedComposerHint(undefined)).toBeNull();
    expect(queuedComposerHint(cockpit())).toBeNull();
    expect(
      queuedComposerHint(
        cockpit({ pendingSets: { effort: { requestedValue: "x", sentAt: 1, phase: "inflight" } } }),
      ),
    ).toBeNull();
    expect(
      queuedComposerHint(
        cockpit({
          pendingSets: {
            model: { requestedValue: "m", sentAt: 1, phase: "queued-awaiting-turn" },
            effort: { requestedValue: "x", sentAt: 1, phase: "queued-awaiting-turn" },
          },
        }),
      ),
    ).toBe(
      "queued model + effort change applies when the next turn starts — sending a message starts one",
    );
  });
});

describe("hasUnackedSetAttention (R6 — the rail marker / toast gate)", () => {
  it("true for unacked ledger entries and finished failed pairs; false otherwise", () => {
    expect(hasUnackedSetAttention(undefined)).toBe(false);
    expect(hasUnackedSetAttention(cockpit())).toBe(false);
    expect(hasUnackedSetAttention(cockpit({ setLedger: [entry({ acknowledged: false })] }))).toBe(true);
    expect(hasUnackedSetAttention(cockpit({ setLedger: [entry({ acknowledged: true })] }))).toBe(false);
    const aborted = applyPairStepResult(startPairChange("m", "e"), "model", {
      ok: false,
      acceptance: "unsupported",
      requestedValue: "m",
      effectiveValue: null,
      detail: null,
    }).state;
    expect(hasUnackedSetAttention(cockpit({ pairChange: aborted }))).toBe(true);
    // An in-progress pair is not attention — it is progress.
    expect(hasUnackedSetAttention(cockpit({ pairChange: startPairChange("m", "e") }))).toBe(false);
  });
});

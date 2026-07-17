import { describe, expect, it } from "vitest";

import { SET_RESULT_CLAMP, SET_RESULTS } from "../test/fixtures/capabilityEnvelopes";
import type { SetResultWire } from "../types/harnessCapabilities";
import {
  applyPairReadback,
  applyPairRouteError,
  applyPairStepResult,
  pairPartialFailureCopy,
  pairProgressCopy,
  pairRouteTerminationCopy,
  startPairChange,
} from "./pairChange";

// The serialized pair-change machine, exhaustive (260715-FEUI-L4 R5/R9): set-model → evidence →
// set-effort; unsupported on step 1 aborts with nothing sent; on step 2 it is the DESIGNED
// partial failure; unknown holds for its readback; Codex both-queued flows through.

const wire = (overrides: Partial<SetResultWire>): SetResultWire => ({
  ok: true,
  acceptance: "echo-verified",
  requestedValue: "gpt-5.6-terra",
  effectiveValue: "gpt-5.6-terra",
  detail: null,
  ...overrides,
});

const start = () => startPairChange("gpt-5.6-terra", "xhigh");

describe("step 1 (model) — every acceptance", () => {
  it.each([
    ["echo-verified", wire({})],
    ["immediate", wire({ acceptance: "immediate" })],
    ["queued (Codex): evidence enough — the gate opens WITHOUT waiting for effectiveness", wire({ acceptance: "queued", effectiveValue: null })],
  ])("%s advances to the effort step and directs the effort POST", (_name, result) => {
    const directive = applyPairStepResult(start(), "model", result);
    expect(directive).toMatchObject({ sendEffort: true, done: false });
    expect(directive.state).toMatchObject({ step: "effort", phase: "inflight", modelResult: result });
  });

  it("unsupported ABORTS — the effort POST is never sent, the verbatim detail is kept", () => {
    const refusal = wire({
      acceptance: "unsupported",
      effectiveValue: null,
      detail: "requested model is absent from the dynamic catalog",
    });
    const directive = applyPairStepResult(start(), "model", refusal);
    expect(directive).toMatchObject({ sendEffort: false, done: true });
    expect(directive.state.outcome).toEqual({
      kind: "aborted",
      detail: "requested model is absent from the dynamic catalog",
    });
  });

  it("unknown HOLDS the step for its snapshot readback (no advance, no abort)", () => {
    const directive = applyPairStepResult(start(), "model", wire({ acceptance: "unknown", effectiveValue: null }));
    expect(directive).toMatchObject({ sendEffort: false, done: false });
    expect(directive.state.phase).toBe("awaiting-readback");
  });
});

describe("step 2 (effort) — every acceptance", () => {
  const atEffort = () => applyPairStepResult(start(), "model", wire({})).state;

  it.each([
    ["echo-verified", wire({ requestedValue: "xhigh", effectiveValue: "xhigh" })],
    ["immediate", wire({ acceptance: "immediate", requestedValue: "xhigh" })],
    ["queued (Codex both-queued: completes; the pendings carry effectiveness)", wire({ acceptance: "queued", requestedValue: "xhigh", effectiveValue: null })],
    ["clamp (echo-verified, effective ≠ requested — the pair completes; the clamp chip demands its own ack)", SET_RESULT_CLAMP],
  ])("%s completes the pair", (_name, result) => {
    const directive = applyPairStepResult(atEffort(), "effort", result);
    expect(directive).toMatchObject({ sendEffort: false, done: true });
    expect(directive.state.outcome).toEqual({ kind: "completed" });
  });

  it("unsupported is the DESIGNED PARTIAL FAILURE: model switched, effort refused", () => {
    const refusal = wire({
      acceptance: "unsupported",
      requestedValue: "xhigh",
      effectiveValue: null,
      detail: "effort token rejected",
    });
    const directive = applyPairStepResult(atEffort(), "effort", refusal);
    expect(directive.done).toBe(true);
    expect(directive.state.outcome).toEqual({
      kind: "partial",
      model: "gpt-5.6-terra",
      detail: "effort token rejected",
    });
  });

  it("unknown holds step 2 for its readback", () => {
    const directive = applyPairStepResult(
      atEffort(),
      "effort",
      wire({ acceptance: "unknown", requestedValue: "xhigh", effectiveValue: null }),
    );
    expect(directive.state.phase).toBe("awaiting-readback");
    expect(directive.done).toBe(false);
  });
});

describe("readback resolution of an unknown-held step", () => {
  it("model step: confirmed → effort goes out; not confirmed → aborted", () => {
    const held = applyPairStepResult(start(), "model", wire({ acceptance: "unknown", effectiveValue: null })).state;
    expect(applyPairReadback(held, true)).toMatchObject({ sendEffort: true, done: false });
    const aborted = applyPairReadback(held, false);
    expect(aborted.done).toBe(true);
    expect(aborted.state.outcome?.kind).toBe("aborted");
  });

  it("effort step: confirmed → completed; not confirmed → partial (model DID switch)", () => {
    const atEffort = applyPairStepResult(start(), "model", wire({})).state;
    const held = applyPairStepResult(
      atEffort,
      "effort",
      wire({ acceptance: "unknown", requestedValue: "xhigh", effectiveValue: null }),
    ).state;
    expect(applyPairReadback(held, true).state.outcome).toEqual({ kind: "completed" });
    const partial = applyPairReadback(held, false);
    expect(partial.state.outcome?.kind).toBe("partial");
  });
});

describe("route failures end the pair story", () => {
  it("a model-route failure aborts before effort, while an effort-route failure is partial", () => {
    const modelFailure = applyPairRouteError(
      start(),
      "model",
      "set model failed — session gone (404)",
    );
    expect(modelFailure).toMatchObject({ sendEffort: false, done: true });
    expect(modelFailure.state.outcome).toEqual({
      kind: "aborted",
      detail: "set model failed — session gone (404)",
      routeErrorStep: "model",
    });

    const atEffort = applyPairStepResult(start(), "model", wire({})).state;
    const effortFailure = applyPairRouteError(
      atEffort,
      "effort",
      "set effort failed — control outage (503): socket refused",
    );
    expect(effortFailure).toMatchObject({ sendEffort: false, done: true });
    expect(effortFailure.state.outcome).toEqual({
      kind: "partial",
      model: "gpt-5.6-terra",
      detail: "set effort failed — control outage (503): socket refused",
      routeErrorStep: "effort",
    });
  });
});

describe("machine guards", () => {
  it("results for the WRONG step are refused (per-kind pendings never clobber)", () => {
    const fresh = start();
    const directive = applyPairStepResult(fresh, "effort", SET_RESULTS.queued);
    expect(directive.state).toBe(fresh);
    expect(directive.sendEffort).toBe(false);
  });

  it("a finished pair ignores further results and readbacks", () => {
    const aborted = applyPairStepResult(
      start(),
      "model",
      wire({ acceptance: "unsupported", effectiveValue: null }),
    ).state;
    expect(applyPairStepResult(aborted, "model", wire({})).state).toBe(aborted);
    expect(applyPairReadback(aborted, true).state).toBe(aborted);
  });
});

describe("copy (R5 — one source, tests assert the words)", () => {
  it("two-step progress chip", () => {
    expect(pairProgressCopy(start())).toBe("1/2 model gpt-5.6-terra…");
    const atEffort = applyPairStepResult(start(), "model", wire({})).state;
    expect(pairProgressCopy(atEffort)).toBe("2/2 effort xhigh…");
  });

  it("partial-failure rendering — with the snapshot's known effort, and the honest placeholder without", () => {
    const outcome = { kind: "partial", model: "gpt-5.6-terra", detail: "effort token rejected" } as const;
    expect(pairPartialFailureCopy(outcome, "medium")).toBe(
      "model switched, effort request refused — session now at gpt-5.6-terra / medium (effort token rejected)",
    );
    expect(pairPartialFailureCopy(outcome, null)).toBe(
      "model switched, effort request refused — session now at gpt-5.6-terra / vendor default or prior (effort token rejected)",
    );
  });

  it("route termination names the stopped step and keeps effectiveness unknown", () => {
    expect(pairRouteTerminationCopy("model")).toBe(
      "pair change stopped at the model step — its effective outcome is unknown",
    );
    expect(pairRouteTerminationCopy("effort")).toBe(
      "pair change stopped at the effort step — its effective outcome is unknown",
    );
  });
});

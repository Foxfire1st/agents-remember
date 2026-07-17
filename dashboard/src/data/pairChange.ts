import type { SetResultWire } from "../types/harnessCapabilities";

// The serialized pair-change machine (260715-FEUI-L4 R5, design §6.5): set-model → evidence →
// set-effort, as a PURE state machine so vitest tables are exhaustive. Rules encoded here:
//   - SERIALIZED: the effort set is never sent before the model set produced acceptance
//     evidence. Acceptance evidence = echo-verified | immediate | queued ("accepted as desired");
//     Codex's queued model set therefore proceeds to the effort step immediately — both queued
//     sets then resolve together on one snapshot readback (R4).
//   - `unknown` HOLDS the step (awaiting its single automatic snapshot readback) — the machine
//     advances or aborts only on the readback's verdict, never on a guess.
//   - `unsupported` on step 1 aborts with nothing changed; on step 2 it is the designed PARTIAL
//     FAILURE: model switched, effort request refused — both SetResults stay in the ledger.
//   - a route error terminates the same control flow, but carries its step as provenance because
//     no SetResult exists from which to claim whether that step took effect.
// The machine holds no timers and performs no IO; data/setClient.ts drives it.

export type PairStep = "model" | "effort";

export type PairOutcome =
  | { kind: "completed" }
  | { kind: "aborted"; detail: string; routeErrorStep?: "model" }
  | {
      kind: "partial";
      /** The requested model from step 1; evidence-backed partial copy may call it switched. */
      model: string;
      /** Verbatim evidence or route detail; its provenance decides which copy is honest. */
      detail: string;
      routeErrorStep?: "effort";
    };

export interface PairChangeState {
  modelKey: string;
  effort: string;
  step: PairStep;
  /** inflight = the step's POST is out; awaiting-readback = unknown held for its snapshot. */
  phase: "inflight" | "awaiting-readback";
  modelResult?: SetResultWire;
  effortResult?: SetResultWire;
  outcome?: PairOutcome;
}

export interface PairDirective {
  state: PairChangeState;
  /** True ⇒ the driver sends the set-effort POST now (serialization gate passed). */
  sendEffort: boolean;
  /** True ⇒ the pair change is finished (outcome is set) — the driver clears/keeps per UX. */
  done: boolean;
}

export function startPairChange(modelKey: string, effort: string): PairChangeState {
  return { modelKey, effort, step: "model", phase: "inflight" };
}

const ACCEPTED: ReadonlySet<string> = new Set(["echo-verified", "immediate", "queued"]);

/** Apply one SetResult to the step that requested it. Results for the wrong step are ignored
 *  (per-kind pendingSets make that impossible in practice; the machine still refuses). */
export function applyPairStepResult(
  state: PairChangeState,
  kind: PairStep,
  result: SetResultWire,
): PairDirective {
  if (state.outcome || kind !== state.step) return { state, sendEffort: false, done: !!state.outcome };
  if (kind === "model") {
    const next: PairChangeState = { ...state, modelResult: result };
    if (ACCEPTED.has(result.acceptance)) {
      // Evidence in hand — the serialization gate opens; step 2 goes out.
      return {
        state: { ...next, step: "effort", phase: "inflight" },
        sendEffort: true,
        done: false,
      };
    }
    if (result.acceptance === "unknown") {
      return { state: { ...next, phase: "awaiting-readback" }, sendEffort: false, done: false };
    }
    // unsupported: nothing changed — abort with the verbatim detail.
    return {
      state: {
        ...next,
        outcome: {
          kind: "aborted",
          detail: result.detail ?? "model request refused",
        },
      },
      sendEffort: false,
      done: true,
    };
  }
  // kind === "effort"
  const next: PairChangeState = { ...state, effortResult: result };
  if (ACCEPTED.has(result.acceptance)) {
    return { state: { ...next, outcome: { kind: "completed" } }, sendEffort: false, done: true };
  }
  if (result.acceptance === "unknown") {
    return { state: { ...next, phase: "awaiting-readback" }, sendEffort: false, done: false };
  }
  // unsupported on step 2 — the designed partial failure (model DID switch).
  return {
    state: {
      ...next,
      outcome: {
        kind: "partial",
        model: state.modelKey,
        detail: result.detail ?? "effort request refused",
      },
    },
    sendEffort: false,
    done: true,
  };
}

/**
 * End the active pair when a step's POST produced no SetResult evidence. The route error remains
 * a separate transport chip; this outcome ends the pair story so the progress chip cannot claim
 * work is still in flight. The control flow is aborted at model or partial at effort, but the
 * affected step's effective outcome remains unknown because the route supplied no evidence.
 */
export function applyPairRouteError(
  state: PairChangeState,
  kind: PairStep,
  detail: string,
): PairDirective {
  if (state.outcome || kind !== state.step) {
    return { state, sendEffort: false, done: !!state.outcome };
  }
  if (kind === "model") {
    return {
      state: {
        ...state,
        outcome: { kind: "aborted", detail, routeErrorStep: "model" },
      },
      sendEffort: false,
      done: true,
    };
  }
  return {
    state: {
      ...state,
      outcome: {
        kind: "partial",
        model: state.modelKey,
        detail,
        routeErrorStep: "effort",
      },
    },
    sendEffort: false,
    done: true,
  };
}

/**
 * Resolve an `unknown`-held step by its snapshot readback verdict (the one automatic re-GET):
 * confirmed ⇒ the step's value proved effective; not confirmed ⇒ the step did not take effect.
 */
export function applyPairReadback(state: PairChangeState, confirmed: boolean): PairDirective {
  if (state.outcome || state.phase !== "awaiting-readback") {
    return { state, sendEffort: false, done: !!state.outcome };
  }
  if (state.step === "model") {
    if (confirmed) {
      return {
        state: { ...state, step: "effort", phase: "inflight" },
        sendEffort: true,
        done: false,
      };
    }
    return {
      state: {
        ...state,
        outcome: {
          kind: "aborted",
          detail: "model set outcome was unknown and the snapshot readback shows it did not take effect",
        },
      },
      sendEffort: false,
      done: true,
    };
  }
  if (confirmed) {
    return { state: { ...state, outcome: { kind: "completed" } }, sendEffort: false, done: true };
  }
  return {
    state: {
      ...state,
      outcome: {
        kind: "partial",
        model: state.modelKey,
        detail:
          "effort set outcome was unknown and the snapshot readback shows it did not take effect",
      },
    },
    sendEffort: false,
    done: true,
  };
}

/** The two-step progress chip text ('1/2 model…' / '2/2 effort…') — one source, tested. */
export function pairProgressCopy(state: PairChangeState): string {
  return state.step === "model"
    ? `1/2 model ${state.modelKey}…`
    : `2/2 effort ${state.effort}…`;
}

/** Route termination copy: the separate route chip owns the transport detail; the pair chip
 *  states only what that missing SetResult permits us to know about the serialized flow. */
export function pairRouteTerminationCopy(step: PairStep): string {
  return `pair change stopped at the ${step} step — its effective outcome is unknown`;
}

/**
 * The designed partial-failure rendering (R5): 'model switched, effort request refused — session
 * now at <model> / <vendor default or prior>'. `effortNow` is the snapshot's CURRENT effort when
 * known (never guessed): null renders the honest "vendor default or prior" placeholder.
 */
export function pairPartialFailureCopy(outcome: Extract<PairOutcome, { kind: "partial" }>, effortNow: string | null): string {
  const at = effortNow ?? "vendor default or prior";
  return `model switched, effort request refused — session now at ${outcome.model} / ${at} (${outcome.detail})`;
}

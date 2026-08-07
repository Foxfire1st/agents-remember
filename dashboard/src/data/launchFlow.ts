import type {
  CapabilitySnapshotWire,
  EffortOptionWire,
  ModelCapabilityWire,
} from "../types/harnessCapabilities";
import type { HarnessControlState, TerminalOpenKind } from "../types/terminalCatalog";
import { openTerminalSession, type OpenedTerminalSession } from "./terminal";

// The launch-flow state machines (260715-FEUI-L3 R4/R5), pure so vitest tables can be
// exhaustive. LAUNCH RULES encoded here:
//   - A selection is a COMPLETE pair or vendor defaults (both omitted) — a partial pair is
//     unrepresentable on the wire (`launchSelectionBody` refuses it), and no default is ever
//     invented client-side.
//   - Effort options are the selected model's advertised menu filtered `launchSettable`, in
//     advertised NATIVE order — never reordered, never emphasized (decoy-anchor discipline).
//   - Selecting a new model RE-GATES effort: that row's advertised `defaultEffort` when it is
//     launch-settable, otherwise `null` (the flow demands an explicit choice).
//   - Keys are used verbatim — Pi's provider-qualified `provider/id` form is never stripped.

export interface LaunchSelectionState {
  modelKey: string | null;
  effort: string | null;
  /** Explicit vendor-defaults choice: send NEITHER knob (an intentionally selectionless open). */
  vendorDefaults: boolean;
}

export const EMPTY_SELECTION: LaunchSelectionState = {
  modelKey: null,
  effort: null,
  vendorDefaults: false,
};

/** The selected model's launchable menu — advertised order preserved (filter never sorts). */
export function launchableEfforts(model: ModelCapabilityWire): EffortOptionWire[] {
  return model.effortOptions.filter((option) => option.launchSettable);
}

export function modelByKey(
  snapshot: CapabilitySnapshotWire,
  modelKey: string | null,
): ModelCapabilityWire | undefined {
  if (modelKey === null) return undefined;
  return snapshot.models.find((model) => model.key === modelKey);
}

/** Pick a model: effort re-gates to ITS advertised default, or demands an explicit choice. */
export function chooseModel(
  snapshot: CapabilitySnapshotWire,
  modelKey: string,
): LaunchSelectionState {
  const row = modelByKey(snapshot, modelKey);
  if (!row) return EMPTY_SELECTION; // not advertised ⇒ not selectable (dynamic-only)
  const efforts = launchableEfforts(row);
  const advertisedDefault =
    row.defaultEffort !== null && efforts.some((option) => option.key === row.defaultEffort)
      ? row.defaultEffort
      : null;
  return { modelKey: row.key, effort: advertisedDefault, vendorDefaults: false };
}

/** Pick an effort — valid only for the current model's advertised launchable menu. */
export function chooseEffort(
  snapshot: CapabilitySnapshotWire,
  selection: LaunchSelectionState,
  effortKey: string,
): LaunchSelectionState {
  const row = modelByKey(snapshot, selection.modelKey);
  if (!row) return selection;
  if (!launchableEfforts(row).some((option) => option.key === effortKey)) return selection;
  return { ...selection, effort: effortKey, vendorDefaults: false };
}

export function chooseVendorDefaults(): LaunchSelectionState {
  return { modelKey: null, effort: null, vendorDefaults: true };
}

export function selectionComplete(selection: LaunchSelectionState): boolean {
  return selection.vendorDefaults || (selection.modelKey !== null && selection.effort !== null);
}

/** The wire knobs: BOTH keys or NEITHER — a partial pair cannot be produced. */
export function launchSelectionBody(
  selection: LaunchSelectionState,
): { model: string; effort: string } | Record<string, never> {
  if (selection.vendorDefaults) return {};
  if (selection.modelKey !== null && selection.effort !== null) {
    return { model: selection.modelKey, effort: selection.effort };
  }
  throw new Error("launch selection is incomplete — complete the pair or choose vendor defaults");
}

// ── Open-response classification (R5) ──────────────────────────────────────────────────────────

/** One normalized open outcome per server path — the flow renders each verbatim. */
export type OpenOutcome =
  | {
      path: "opened";
      session: string;
      label: string | null;
      kind: TerminalOpenKind | null;
      harness: string | null;
      leafKey: string | null;
      controlState: HarnessControlState | null;
      /** The REQUESTED pair persisted verbatim before validation — tier 'pending', never proof. */
      resolvedModel: string | null;
      resolvedEffort: string | null;
    }
  | { path: "launch-selection-invalid"; detail: string }
  | { path: "open-refused"; status: string; detail: string }
  | { path: "leaf-taken"; leafKey: string | null; ownerSession: string | null }
  | {
      path: "launch-selection-conflict";
      session: string;
      detail: string | null;
      /** The LIVE row's retained pair (process truth) — provenance is never rewritten. */
      liveModel: string | null;
      liveEffort: string | null;
      controlState: HarnessControlState | null;
    }
  | {
      /** Transport loss / 5xx / unrecognized body (design §7.1 F9): the flow keeps the
       *  selection and resolves by the next catalog poll — the session id is caller-minted,
       *  so reconciliation = does the row exist. Never a blind re-POST with a fresh id. */
      path: "outcome-unknown";
      detail: string;
    };

const text = (value: unknown): string | null => (typeof value === "string" ? value : null);

function openedOutcome(record: Record<string, unknown>): OpenOutcome {
  return {
    path: "opened",
    session: record.session as string,
    label: text(record.label),
    kind: text(record.kind) as TerminalOpenKind | null,
    harness: text(record.harness),
    leafKey: text(record.leafKey),
    controlState: text(record.controlState) as HarnessControlState | null,
    resolvedModel: text(record.resolvedModel),
    resolvedEffort: text(record.resolvedEffort),
  };
}

function badRequestOutcome(record: Record<string, unknown>, status: string | null): OpenOutcome {
  const detail = text(record.detail) ?? "HTTP 400";
  if (status === "launch-selection-invalid") return { path: "launch-selection-invalid", detail };
  return { path: "open-refused", status: status ?? "bad-request", detail };
}

function conflictOutcome(record: Record<string, unknown>, status: string | null): OpenOutcome {
  if (status === "leaf-taken") {
    return {
      path: "leaf-taken",
      leafKey: text(record.leafKey),
      ownerSession: text(record.session),
    };
  }
  if (status === "launch-selection-conflict" && typeof record.session === "string") {
    return {
      path: "launch-selection-conflict",
      session: record.session,
      detail: text(record.detail),
      liveModel: text(record.resolvedModel),
      liveEffort: text(record.resolvedEffort),
      controlState: text(record.controlState) as HarnessControlState | null,
    };
  }
  return {
    path: "outcome-unknown",
    detail: `unrecognized open response (HTTP 409${status ? `, status ${status}` : ""})`,
  };
}

function unknownOutcome(httpStatus: number | null, status: string | null): OpenOutcome {
  return {
    path: "outcome-unknown",
    detail: `unrecognized open response (HTTP ${httpStatus ?? "?"}${status ? `, status ${status}` : ""})`,
  };
}

/** httpStatus null = the fetch itself threw (network loss). Pure — exhaustively table-tested. */
export function classifyOpenResponse(httpStatus: number | null, body: unknown): OpenOutcome {
  if (httpStatus === null) {
    return { path: "outcome-unknown", detail: "network failure — the open POST did not answer" };
  }
  const record =
    typeof body === "object" && body !== null ? (body as Record<string, unknown>) : {};
  const status = text(record.status);
  if (httpStatus === 200 && typeof record.session === "string") {
    return openedOutcome(record);
  }
  if (httpStatus === 400) return badRequestOutcome(record, status);
  if (httpStatus === 409) return conflictOutcome(record, status);
  return unknownOutcome(httpStatus, status);
}

export interface OpenHostedSessionRequest {
  harness: string;
  selection: LaunchSelectionState;
  label?: string;
  leafKey?: string;
  lifecycleId?: string;
}

function hostedSessionBody(request: OpenHostedSessionRequest): Record<string, unknown> {
  return {
    ...launchSelectionBody(request.selection),
    ...(request.label ? { label: request.label } : {}),
    ...(request.leafKey ? { leafKey: request.leafKey } : {}),
    ...(request.lifecycleId ? { lifecycleId: request.lifecycleId } : {}),
  };
}

function openedOutcomeFromSession(session: OpenedTerminalSession): OpenOutcome {
  return {
    path: "opened",
    session: session.id,
    label: session.label,
    kind: session.kind,
    harness: session.harness ?? null,
    leafKey: session.leafKey ?? null,
    controlState: session.controlState ?? null,
    resolvedModel: session.resolvedModel ?? null,
    resolvedEffort: session.resolvedEffort ?? null,
  };
}

/**
 * POST the open with the complete pair (or neither knob) and classify every response path. The
 * session id is CALLER-minted so an unknown outcome reconciles against the catalog by id.
 */
export async function openHostedSession(
  sessionId: string,
  request: OpenHostedSessionRequest,
  base = "",
): Promise<OpenOutcome> {
  const result = await openTerminalSession(sessionId, "harness", base, request.harness, {
    ...hostedSessionBody(request),
  });
  if (result.outcome === "opened") {
    return openedOutcomeFromSession(result.session);
  }
  if (
    (result.failure === "http" || result.failure === "harness") &&
    result.httpStatus !== null
  ) {
    return classifyOpenResponse(result.httpStatus, result.responseBody);
  }
  return { path: "outcome-unknown", detail: result.detail };
}

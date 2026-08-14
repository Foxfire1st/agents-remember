import type {
  ReconciliationResultWire,
  SubmissionReceiptWire,
} from "../../types/harnessCapabilities";
import type { TerminalCatalogRow } from "../../types/terminalCatalog";
import { catalogRow } from "./catalogRows";

// Contract-backed FEUI-L5 fixture pack: all five receipt values, every reconciliation terminal,
// exact multiline/non-ASCII text, controlled/raw gates, and sequence ingredients used by the
// duplicate/endgame/multi-queue/pop-back tests.

export const L5_REQUEST_ID = "8a551c24-e71a-42ef-9ea7-159a65dbf221";
export const L5_OTHER_REQUEST_ID = "f7373709-d421-456d-bd81-234c7aecad25";
export const L5_EXACT_TEXT = "  ## Résumé\n\n第二行 · 😀  ";
export const L5_BRIDGE_EPOCH = "bridge-epoch-l5";

export const L5_DUPLICATE_CONVERGENCE_FIXTURE = {
  requestId: L5_REQUEST_ID,
  originalText: L5_EXACT_TEXT,
  editedText: "edited payload that must not occupy the original requestId",
} as const;

export const L5_MULTI_QUEUE_FIXTURE = [
  {
    requestId: "queued-first",
    text: "first queued",
    preview: "first queued",
    queuedAt: 1,
    expectedBridgeEpoch: L5_BRIDGE_EPOCH,
    state: "queued" as const,
  },
  {
    requestId: "queued-second",
    text: "second\nqueued",
    preview: "second ↵ queued",
    queuedAt: 2,
    expectedBridgeEpoch: L5_BRIDGE_EPOCH,
    state: "queued" as const,
  },
] as const;

export const L5_POP_BACK_SUPERSESSION_FIXTURE = {
  requestId: "queued-pop-back",
  text: "queued\nmessage",
  preview: "queued ↵ message",
  queuedAt: 3,
  expectedBridgeEpoch: L5_BRIDGE_EPOCH,
  state: "queued" as const,
} as const;

export const L5_ALREADY_DELIVERED_RACE_FIXTURE = {
  requestId: "observed-delivered-before-pop-back",
  text: "already sent",
  submittedRevision: 1,
  acceptedAt: 4,
} as const;

export const L5_READY_SESSION: TerminalCatalogRow = catalogRow({
  id: "l5-ready",
  label: "worker-l5-ready",
  controlState: "ready",
  turnState: "turn-ended",
});

export const L5_STARTING_SESSION: TerminalCatalogRow = catalogRow({
  id: "l5-starting",
  label: "worker-l5-starting",
  controlState: "starting",
});

export const L5_RAW_SESSION: TerminalCatalogRow = catalogRow({
  id: "l5-raw",
  label: "raw-terminal-l5",
  kind: "terminal",
  harness: undefined,
  controlState: undefined,
});

export const RECEIPT_ACCEPTANCES = [
  "immediate",
  "queued",
  "rejected",
  "unknown",
  "unsupported",
] as const;

export function submitReceipt(
  acceptance: (typeof RECEIPT_ACCEPTANCES)[number],
  overrides: Partial<SubmissionReceiptWire> = {},
): SubmissionReceiptWire {
  return {
    requestId: L5_REQUEST_ID,
    acceptance,
    submittedAt: "2026-07-17T10:00:00Z",
    vendorCorrelationId: acceptance === "immediate" ? "vendor-l5-1" : null,
    acceptedAt: acceptance === "immediate" ? "2026-07-17T10:00:01Z" : null,
    detail:
      acceptance === "rejected"
        ? "queue full: 8/8"
        : acceptance === "unsupported"
          ? "native control is unavailable for this session"
          : acceptance === "unknown"
            ? "delivery outcome is unknown"
            : null,
    bridgeEpoch: L5_BRIDGE_EPOCH,
    ...overrides,
  };
}

export const RECONCILIATION_STATES = [
  "accepted",
  "rejected",
  "unresolved",
  "unsupported",
] as const;

export function reconciliationResult(
  state: (typeof RECONCILIATION_STATES)[number],
  overrides: Partial<ReconciliationResultWire> = {},
): ReconciliationResultWire {
  return {
    requestId: L5_REQUEST_ID,
    state,
    reconciledAt: "2026-07-17T10:00:02Z",
    vendorCorrelationId: state === "accepted" ? "vendor-l5-1" : null,
    detail:
      state === "rejected"
        ? "ledger full"
        : state === "unsupported"
          ? "adapter cannot reconcile submissions"
          : state === "unresolved"
            ? "not observed yet"
            : null,
    bridgeEpoch: L5_BRIDGE_EPOCH,
    submissionState:
      state === "accepted"
        ? "delivered"
        : state === "rejected"
          ? "rejected"
          : state === "unsupported"
            ? "unsupported"
            : "unknown",
    ...overrides,
  };
}

/** The exact unresolved sequence that fills the 1s → 2s → 5s bounded window to 118 seconds. */
export const L5_CAPPED_ENDGAME_RECONCILIATION_FIXTURE = Array.from(
  { length: 25 },
  () => reconciliationResult("unresolved"),
);

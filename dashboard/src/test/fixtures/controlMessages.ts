import type {
  ReconciliationResultWire,
  ReconciliationState,
  SubmissionReceiptWire,
} from "../../types/harnessCapabilities";

// Submission-receipt and reconciliation fixtures (260715-FEUI-L3 R3): the reliable-submit halves
// of the contract pack, mirrored from serving/harness_control_models.py `public_receipt_json` /
// `public_reconciliation_json`. L5 (composer/submit) consumes these; founded here because this
// is the first API-consuming leaf.

type ReceiptAcceptance = SubmissionReceiptWire["acceptance"];
const BRIDGE_EPOCH = "bridge-epoch-fixture";

export const SUBMISSION_RECEIPTS: Record<ReceiptAcceptance, SubmissionReceiptWire> = {
  immediate: {
    requestId: "req-immediate-1",
    acceptance: "immediate",
    submittedAt: "2026-07-16T10:00:00+00:00",
    vendorCorrelationId: "vendor-corr-1",
    acceptedAt: "2026-07-16T10:00:00+00:00",
    detail: null,
    bridgeEpoch: BRIDGE_EPOCH,
  },
  queued: {
    requestId: "req-queued-1",
    acceptance: "queued",
    submittedAt: "2026-07-16T10:00:01+00:00",
    vendorCorrelationId: null,
    acceptedAt: null,
    detail: "an active turn is running; the prompt is retained",
    bridgeEpoch: BRIDGE_EPOCH,
  },
  rejected: {
    requestId: "req-rejected-1",
    acceptance: "rejected",
    submittedAt: "2026-07-16T10:00:02+00:00",
    vendorCorrelationId: null,
    acceptedAt: null,
    detail: "adapter refused the prompt",
    bridgeEpoch: BRIDGE_EPOCH,
  },
  unknown: {
    requestId: "req-unknown-1",
    acceptance: "unknown",
    submittedAt: "2026-07-16T10:00:03+00:00",
    vendorCorrelationId: null,
    acceptedAt: null,
    detail: "response lost — reconcile by requestId, never resend",
    bridgeEpoch: BRIDGE_EPOCH,
  },
  unsupported: {
    requestId: "req-unsupported-1",
    acceptance: "unsupported",
    submittedAt: "2026-07-16T10:00:04+00:00",
    vendorCorrelationId: null,
    acceptedAt: null,
    detail: "session has no native protocol control endpoint",
    bridgeEpoch: BRIDGE_EPOCH,
  },
};

export const RECONCILIATIONS: Record<ReconciliationState, ReconciliationResultWire> = {
  accepted: {
    requestId: "req-unknown-1",
    state: "accepted",
    reconciledAt: "2026-07-16T10:00:10+00:00",
    vendorCorrelationId: "vendor-corr-2",
    detail: null,
    bridgeEpoch: BRIDGE_EPOCH,
    submissionState: "delivered",
  },
  rejected: {
    requestId: "req-unknown-1",
    state: "rejected",
    reconciledAt: "2026-07-16T10:00:11+00:00",
    vendorCorrelationId: null,
    detail: "the adapter recorded a refusal for this request id",
    bridgeEpoch: BRIDGE_EPOCH,
    submissionState: "rejected",
  },
  unresolved: {
    requestId: "req-unknown-1",
    state: "unresolved",
    reconciledAt: "2026-07-16T10:00:12+00:00",
    vendorCorrelationId: null,
    detail: "no receipt observed yet — keep the draft, do not resend",
    bridgeEpoch: BRIDGE_EPOCH,
    submissionState: "unknown",
  },
  unsupported: {
    requestId: "req-unknown-1",
    state: "unsupported",
    reconciledAt: "2026-07-16T10:00:13+00:00",
    vendorCorrelationId: null,
    detail: "adapter does not support reconciliation",
    bridgeEpoch: BRIDGE_EPOCH,
    submissionState: "unsupported",
  },
};

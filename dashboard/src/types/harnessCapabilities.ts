// TypeScript mirror of the pre-session capability contract (260715-FEUI-L3 R1/R3):
// mcp/src/agents_remember/serving/harness_capability_catalog.py `CapabilityCatalogResult.to_json()`
// (the daemon envelope) nesting harness_capabilities.py `capability_snapshot_json()` (the
// normalized L1 snapshot), served by `GET /api/harnesses/{h}/capabilities`
// (serving/harness_control_api.py). camelCase matches the wire form; the Python serializers are
// the source of truth — keep this dataclass-backed surface in lockstep by hand.
//
// DYNAMIC-ONLY invariant: these are SHAPES. No model key, effort key, or menu from any install
// may ever be copied here as a value — catalogs are live data fetched per install/auth.

export const CAPABILITY_SCHEMA = "ar-harness-capabilities/v1";

export type CapabilityCacheStatus = "hit" | "miss" | "refreshed";

/** One vendor token accepted by one specific model (`effort_option_json`). */
export interface EffortOptionWire {
  key: string;
  displayName: string;
  description: string | null;
  launchSettable: boolean;
  sessionSettable: boolean;
}

/** One dynamically advertised model with its model-gated effort menu (`model_capability_json`). */
export interface ModelCapabilityWire {
  key: string;
  displayName: string;
  resolvedModel: string | null;
  description: string | null;
  supportsEffort: boolean;
  /** Advertised NATIVE order — never reordered client-side (decoy-anchor discipline, R4). */
  effortOptions: EffortOptionWire[];
  defaultEffort: string | null;
  isDefault: boolean;
  hidden: boolean;
  selectable: boolean;
  /** Pi rows carry the provider; the `key` stays provider-qualified (`provider/id`) verbatim. */
  provider: string | null;
}

export interface ConfigSelectOptionWire {
  value: string;
  name: string;
  description: string | null;
}

/** ACP Sense 1 select config shape (`config_option_json`) — a shape, not an ACP transport. */
export interface SessionConfigOptionWire {
  type: "select";
  id: string;
  category: "model" | "thought_level";
  name: string;
  description: string | null;
  currentValue: string;
  options: ConfigSelectOptionWire[];
}

/** Full dynamic catalog plus the current model/effort when known (`capability_snapshot_json`). */
export interface CapabilitySnapshotWire {
  models: ModelCapabilityWire[];
  selectedModelKey: string | null;
  /** null on a fresh Claude session — stream-json emits no launch-effort echo (L5 evidence). */
  selectedEffort: string | null;
  configOptions: SessionConfigOptionWire[];
}

/** The daemon envelope for `GET /api/harnesses/{h}/capabilities` (`CapabilityCatalogResult`). */
export interface CapabilityEnvelope {
  schema: typeof CAPABILITY_SCHEMA;
  harness: string;
  /** hit = served from cache; miss/refreshed = the SAME short-lived native discovery ran (R2). */
  cacheStatus: CapabilityCacheStatus;
  installFingerprint: string;
  capabilities: CapabilitySnapshotWire;
}

/** Error body of the capability route: 404/409 lookup refusals, 503 control outage. */
export interface CapabilityRouteErrorBody {
  status: "capability-unavailable" | "control-unavailable";
  detail: string;
}

// ── Live setter / submit / reconcile evidence (fixture-pack shapes, consumed by L4/L5) ─────────

/** `SetResult.acceptance` — harness_capabilities.py SET_ACCEPTANCE_VALUES. */
export type SetAcceptance = "echo-verified" | "immediate" | "queued" | "unknown" | "unsupported";

/** Honest model/effort mutation evidence (`set_result_json`) — never a generic success boolean. */
export interface SetResultWire {
  ok: boolean;
  acceptance: SetAcceptance;
  requestedValue: string;
  /** Present only when the server PROVED the value took effect — never inferred client-side. */
  effectiveValue: string | null;
  detail: string | null;
}

/** `public_receipt_json` — normalized submission evidence (acceptance: AcceptanceState). */
export interface SubmissionReceiptWire {
  requestId: string;
  acceptance: "immediate" | "queued" | "rejected" | "unknown" | "unsupported";
  submittedAt: string;
  vendorCorrelationId: string | null;
  acceptedAt: string | null;
  detail: string | null;
  bridgeEpoch: string;
}

export type ReconciliationState = "accepted" | "rejected" | "unresolved" | "unsupported";
export type SubmissionLifecycleState =
  | "queued"
  | "dispatching"
  | "delivered"
  | "withdrawn"
  | "unknown"
  | "rejected"
  | "unsupported";

/** `public_reconciliation_json` — resolve an ambiguous submit by requestId, never a resend. */
export interface ReconciliationResultWire {
  requestId: string;
  state: ReconciliationState;
  reconciledAt: string;
  vendorCorrelationId: string | null;
  detail: string | null;
  bridgeEpoch: string;
  submissionState: SubmissionLifecycleState | null;
}

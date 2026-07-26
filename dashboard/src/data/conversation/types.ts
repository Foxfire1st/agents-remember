// Browser mirror of the landed SC1 normalized conversation wire grammar
// (mcp/src/agents_remember/serving/conversation/models.py). Every field name here is the exact
// camelCase wire name emitted by the server's WireModel (alias_generator=to_camel). Active + control
// responses drop null keys (exclude_none); optional fields are therefore `?` and may be absent OR
// null — decoders must treat both identically. These are consumed types only: the browser never
// authors durable conversation history (design §2 invariant 8, §11.3). Nothing here is a schema
// validator — the server is the sole authority; the reducer defends against protocol faults it can
// actually observe (revision regressions, cursor gaps), not against re-validating trusted shapes.

export type HarnessId = "codex" | "claude" | "pi";

export interface NativeConversationRef {
  harnessId: HarnessId;
  vendorConversationId: string;
  projectScope: string;
  identityDigest: string;
}

export interface ActiveConversationRef extends NativeConversationRef {
  arSessionId: string;
  bridgeEpoch: string;
}

// Opaque server-issued tokens. They are bare strings on the wire; the brands are compile-time only
// so a page cursor can never be handed to an event route and vice-versa (design §6.1/§6.8).
export type ActivePageCursor = string & { readonly __brand: "ActivePageCursor" };
export type ActiveEventCursor = string & { readonly __brand: "ActiveEventCursor" };

export type ConversationLane =
  | "operator"
  | "harness"
  | "agent-bus"
  | "unknown-input"
  | "interaction"
  | "control"
  | "system";

export type ConversationSource =
  | "cockpit-composer"
  | "terminal-controlled"
  | "durable-inbox"
  | "harness-live"
  | "harness-replay"
  | "interaction-response"
  | "control-authority"
  | "native-history";

export type ConversationRole = "user" | "assistant" | "system" | "tool";

export type ProvenanceStrength = "exact" | "correlated" | "native-only" | "unknown";

export interface ProvenanceEvidence {
  strength: ProvenanceStrength;
  origin: string;
  producer?: "operator" | "agent-bus" | "controlled-terminal" | "harness" | "system";
  observedAt?: string;
  evidenceRef?: string;
  reason?: string;
}

export type AccessibleLabelProvenance = "supplied-description" | "filename-mime-fallback";

export type ConversationContentBlock =
  | { blockId: string; type: "markdown"; markdown: string }
  | { blockId: string; type: "text"; text: string }
  | { blockId: string; type: "thinking"; markdown: string }
  | { blockId: string; type: "code"; text: string; language?: string }
  | { blockId: string; type: "tool-input"; summary: string; data?: unknown }
  | { blockId: string; type: "tool-output"; text?: string; data?: unknown }
  | {
      blockId: string;
      type: "diff";
      path?: string;
      oldText?: string;
      newText?: string;
      unified?: string;
    }
  | {
      blockId: string;
      type: "image-ref";
      assetId: string;
      alt: string;
      altProvenance: AccessibleLabelProvenance;
      mimeType: string;
    }
  | {
      blockId: string;
      type: "file-ref" | "resource-ref";
      name: string;
      uri?: string;
      mimeType?: string;
    }
  | {
      blockId: string;
      type: "choices";
      interactionId: string;
      options: readonly { optionId: string; label: string; description?: string }[];
    }
  | {
      blockId: string;
      type: "unknown-vendor";
      vendorType: string;
      safeSummary: string;
      evidenceRef: string;
    };

export type ConversationItemKind =
  | "message"
  | "thinking"
  | "plan"
  | "tool-call"
  | "tool-result"
  | "interaction"
  | "turn-result"
  | "notice"
  | "error"
  | "telemetry"
  | "unknown-vendor";

export type ConversationItemPhase =
  | "pending"
  | "streaming"
  | "waiting"
  | "completed"
  | "failed"
  | "interrupted"
  | "unknown";

export interface ConversationCorrelation {
  requestId?: string;
  vendorCorrelationId?: string;
  interactionId?: string;
  toolCallId?: string;
}

// ── Harness sub-agent identity ──────────────────────────────────────────────────────────────────
// Additive: absent on an item means the parent conversation. Identity is evidence-bound — the
// server populates nickname/role/agentPath only when collab/join evidence proved them; an
// unresolved identity renders as `agent <short-id>`, never a fabricated name.
export type ConversationAgentStatus =
  | "registered"
  | "running"
  | "completed"
  | "interrupted"
  | "failed"
  | "unknown";

export interface ConversationAgentRef {
  agentId: string;
  agentPath?: string | null;
  nickname?: string | null;
  role?: string | null;
  joinKey?: string | null;
  parentAgentId?: string | null;
  status: ConversationAgentStatus;
}

export interface ConversationItem {
  itemId: string;
  revision: number;
  globalOrdinal: number;
  turnId?: string;
  parentItemId?: string;
  lane: ConversationLane;
  source: ConversationSource;
  provenance: ProvenanceEvidence;
  role: ConversationRole;
  kind: ConversationItemKind;
  phase: ConversationItemPhase;
  blocks: ConversationContentBlock[];
  correlation?: ConversationCorrelation;
  agent?: ConversationAgentRef | null;
  createdAt?: string;
  updatedAt?: string;
  evidenceRef?: string;
}

// ── Canonical status (design §6.5) ──────────────────────────────────────────────────────────────
export type ConversationProcessState =
  | "starting"
  | "connected"
  | "disconnected"
  | "exited"
  | "failed";

export type ConversationTurnState =
  | "ready"
  | "working"
  | "waiting"
  | "needs-input"
  | "settling"
  | "retrying"
  | "compacting"
  | "interrupted"
  | "failed";

export interface ConversationStatus {
  identity: ActiveConversationRef;
  revision: number;
  observedAt: string;
  freshness: {
    state: "fresh" | "stale" | "unknown";
    lastEvidenceAt: string | null;
    ageMs: number | null;
    staleAfterMs: number;
    observationBound: string;
  };
  process: {
    state: ConversationProcessState;
    generation: string;
    terminalOutcome?: "clean-exit" | "failed-exit" | "signal" | "unknown";
    detail?: string;
  };
  turn: {
    state: ConversationTurnState;
    turnId: string | null;
    stateSince: string | null;
    waiting?: { reason: string; interactionId?: string; operationRef?: string };
    terminalOutcome?: {
      state: "completed" | "interrupted" | "failed" | "unknown";
      stopReason?: string;
      operationRef?: string;
    };
  };
  evidence: ProvenanceEvidence & {
    adapterRevision?: number;
    nativeEventCursor?: ActiveEventCursor;
  };
}

// ── Capabilities (design §8) ────────────────────────────────────────────────────────────────────
export type CapabilityState = "supported" | "partial" | "unavailable" | "unverified";

export interface FeatureCapability {
  state: CapabilityState;
  reason: string;
  evidenceTier: "runtime-fixture" | "native-declaration" | "adapter" | "none";
  evidence?: {
    runtimeVersion: string;
    helperVersion?: string;
    fixtureId?: string;
    observedAt: string;
  };
}

export interface AttachmentCapability extends FeatureCapability {
  allowedMimeTypes: readonly string[];
  maxBytes: number;
  maxCount: number;
  description: "required" | "filename-type-fallback";
}

export interface ConversationCapabilities {
  live: {
    text: FeatureCapability;
    thinking: FeatureCapability;
    tools: FeatureCapability;
    diffs: FeatureCapability;
    interactions: FeatureCapability;
    completeness: FeatureCapability;
  };
  history: {
    list: FeatureCapability;
    read: FeatureCapability;
    resume: FeatureCapability;
    completeness: FeatureCapability;
    toolCompleteness: FeatureCapability;
  };
  controls: {
    interrupt: FeatureCapability;
    steer: FeatureCapability;
    followUp: FeatureCapability;
    attachments: Record<"image" | "file" | "resource", AttachmentCapability>;
    policyRead: FeatureCapability;
  };
  telemetry: {
    context: FeatureCapability;
    usage: FeatureCapability;
    cost: FeatureCapability;
    rateLimit: FeatureCapability;
    compaction: FeatureCapability;
  };
}

// ── Page + event transport (design §6.7, §9.1, §9.2) ────────────────────────────────────────────
export interface ConversationPage {
  identity: ActiveConversationRef;
  items: readonly ConversationItem[];
  page: {
    olderCursor: ActivePageCursor | null;
    hasOlder: boolean;
    totalItems?: number;
  };
  eventCursor: ActiveEventCursor;
  hydrationId: string;
  status: ConversationStatus;
  capabilities: ConversationCapabilities;
}

export type ConversationMutation =
  | { op: "append-item"; item: ConversationItem }
  | {
      op: "append-block-delta";
      itemId: string;
      blockId: string;
      expectedRevision: number;
      nextRevision: number;
      delta: string;
    }
  | { op: "upsert-item"; item: ConversationItem }
  | {
      op: "replace-page";
      items: readonly ConversationItem[];
      totalItems?: number;
      eventCursor: ActiveEventCursor;
      reason: "initial" | "reset" | "native-rehydrate";
    }
  | { op: "status"; status: ConversationStatus }
  | {
      op: "gap";
      requestedAfter: ActiveEventCursor;
      reason: "retention-overflow" | "generation-changed" | "projector-restart" | "ordering-fault";
      requiresRepage: true;
      closeAfterEvent: true;
    };

export interface ConversationEventEnvelope {
  identity: ActiveConversationRef;
  cursor: ActiveEventCursor;
  previousCursor: ActiveEventCursor | null;
  sequence: number;
  eventId: string;
  emittedAt: string;
  delivery: "live" | "resume-replay" | "native-rehydrate";
  mutation: ConversationMutation;
}

// ── Telemetry (design §9.9) — absent metrics are omitted, never zero (A2). ─────────────────────
export interface MetricEvidence<T> {
  value: T;
  unit: string;
  origin: string;
  scope: { kind: "account" | "project" | "session" | "turn" | "conversation"; safeId?: string };
  observedAt: string;
  freshness: "fresh" | "stale" | "unknown";
  precision: "exact" | "estimated" | "rounded" | "unknown";
  runtimeVersion: string;
  helperVersion?: string;
  fixtureId?: string;
}

export interface ConversationTelemetry {
  revision: number;
  identity: ActiveConversationRef;
  context?: MetricEvidence<{ used: number; limit: number; percent: number }>;
  usage?: MetricEvidence<{ inputTokens?: number; outputTokens?: number; cachedTokens?: number }>;
  cost?: MetricEvidence<{ amount: number; currency: string }>;
  rateLimits?: readonly MetricEvidence<{
    windowName: string;
    remaining?: number;
    limit?: number;
    resetsAt?: string;
  }>[];
  compaction?: MetricEvidence<{ phase: "idle" | "compacting" | "completed"; detail?: string }>;
}

// ── Interrupt operation (design §9.5) — requestId-stable; ack ≠ settlement. ─────────────────────
export interface InterruptOperation {
  requestId: string;
  requestFingerprint: string;
  revision: number;
  bridgeEpoch: string;
  turnId: string;
  acknowledgement: "requested" | "accepted" | "unknown" | "rejected";
  settlement: "pending" | "interrupted" | "already-settled" | "failed";
  nativeCorrelationId?: string;
  requestedAt: string;
  settledAt?: string;
  detail?: string;
}

// A typed control-route error surfaced as evidence (never guessed into success). The server maps
// every typed refusal to `{status, detail, ...extra}` (control/api.py:_error); a capability refusal
// carries its exact reason in `detail`.
export interface ConversationRouteError {
  status: string;
  detail: string;
  httpStatus: number;
}

export type StreamPhase =
  | "idle"
  | "connecting"
  | "live"
  | "reconnecting"
  | "gap"
  | "identity-changed"
  | "projection-failed"
  | "failed";

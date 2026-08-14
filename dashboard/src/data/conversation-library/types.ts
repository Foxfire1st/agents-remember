// Browser mirror of the landed native-library wire grammar (library/api.py, models.py). Library
// responses keep null keys (NOT exclude_none), so `nextCursor`/`olderCursor`/`safeNativeIdSuffix`/
// `lastActivityAt` arrive as explicit `null` — treated identically to absent. These are read-only
// dormant-history types; a library row is NEVER a live AR session and can never mark one active
// (design §4.4, §11.2).

import type {
  ConversationItem,
  FeatureCapability,
  HarnessId,
  NativeConversationRef,
} from "../conversation/types";

export type LibraryListCursor = string & { readonly __brand: "LibraryListCursor" };
export type LibraryReadCursor = string & { readonly __brand: "LibraryReadCursor" };
export type LibraryConversationKey = string & { readonly __brand: "LibraryConversationKey" };

export interface HistoryCapabilities {
  list: FeatureCapability;
  read: FeatureCapability;
  resume: FeatureCapability;
  completeness: FeatureCapability;
  toolCompleteness: FeatureCapability;
}

export interface ConversationLibraryRow {
  conversationKey: LibraryConversationKey;
  identityDigest: string;
  title: string;
  safeNativeIdSuffix?: string | null;
  lastActivityAt?: string | null;
  capabilities: HistoryCapabilities;
  /** Harness sub-agent conversations grouped under this row. Each child opens
      through the exact same read/open path — its `conversationKey` is minted server-side. */
  agents?: readonly ConversationLibraryAgentRow[];
}

/** One harness sub-agent conversation grouped under its parent library row. */
export interface ConversationLibraryAgentRow {
  conversationKey: LibraryConversationKey;
  identityDigest: string;
  title: string;
  agentPath?: string | null;
  nickname?: string | null;
  role?: string | null;
  model?: string | null;
  joinKey?: string | null;
  safeNativeIdSuffix?: string | null;
  lastActivityAt?: string | null;
}

export interface ConversationLibraryPage {
  scope: { harnessId: HarnessId; canonicalProjectScope: string; queryDigest: string };
  rows: readonly ConversationLibraryRow[];
  nextCursor: LibraryListCursor | null;
  /** Capability honesty: why sub-agent conversations are (partially)
      unavailable on this page, when they are — the exact native reason, never silently absent. */
  agentsNote?: string | null;
}

export interface HistoricalConversationPage {
  ref: NativeConversationRef;
  items: readonly ConversationItem[];
  olderCursor: LibraryReadCursor | null;
  hasOlder: boolean;
  totalItems?: number;
  historicalCapabilities: HistoryCapabilities;
}

// Open operation (§9.4). The browser holds a caller-stable requestId across timeout/reconcile and
// focuses the new session ONLY on `phase==="opened" && outcome==="opened"` with catalog proof.
export type OpenPhase =
  | "requested"
  | "launching"
  | "catalog-wait"
  | "opened"
  | "retiring"
  | "failed"
  | "unknown";

export type OpenOutcome =
  | "pending"
  | "opened"
  | "unsupported"
  | "stale-identity"
  | "launch-failed"
  | "identity-mismatch"
  | "timeout-unknown"
  | "request-conflict";

export interface OpenConversationOperation {
  requestId: string;
  requestFingerprint: string;
  revision: number;
  phase: OpenPhase;
  outcome: OpenOutcome;
  arSessionId?: string;
  bridgeEpoch?: string;
  identity?: {
    harnessId: HarnessId;
    vendorConversationId: string;
    projectScope: string;
    identityDigest: string;
    arSessionId: string;
    bridgeEpoch: string;
  };
  catalogGeneration?: number;
  rollback: "not-needed" | "retired" | "retire-pending" | "retire-failed";
  detail?: string;
}

export interface LibraryRouteError {
  status: string;
  detail: string;
  httpStatus: number;
  capabilityState?: string;
}

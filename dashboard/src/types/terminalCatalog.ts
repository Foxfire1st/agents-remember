// TypeScript mirror of the FULL terminal-catalog row the cockpit consumes (260715-FEUI-L2 R4):
// mcp/src/agents_remember/serving/terminal_catalog.py `TerminalCatalogEntry.to_json()`, served
// verbatim by `GET /api/terminal/sessions` (serving/app.py `_catalog_payload`). camelCase to match
// the wire form; written-only-when-set fields are optional (`?:`). The Python entry is the source
// of truth — keep in lockstep by hand (same posture as types/projection.ts).

export type TerminalOpenKind = "terminal" | "harness";
export type TerminalSessionStatus = "running" | "exited" | "landed" | "terminated";
export type HarnessControlState = "starting" | "ready" | "disconnected" | "failed" | "unsupported";
export type HarnessActivityState = "idle" | "running" | "blocked" | "settling" | "unknown";
export type HarnessAcceptanceState = "immediate" | "queued" | "rejected" | "unknown" | "unsupported";
// Live turn-state, classified from pane observation on the liveness-sweep cadence (10 s
// rate-limited) — absent means unclassified, never a fabricated state.
export type SeatTurnState = "working" | "turn-ended" | "awaiting-input" | "stale";
export type TerminalLivenessEvidence = "tmux-command-failed" | "pane-gone";

// `controlRaw` is the retained verbatim adapter state. Two keys the cockpit reads are named here;
// everything else stays opaque (the backend retains vendor payloads the UI must not re-shape).
export interface ControlRawDiagnostics extends Record<string, unknown> {
  bridgeError?: unknown;
  paneDiagnostic?: unknown;
}

export interface TerminalCatalogRow {
  id: string;
  label: string;
  kind: TerminalOpenKind;
  cwd: string;
  tmuxName: string;
  /** Always serialized by the server; optional here because no cockpit surface consumes it. */
  command?: string[];
  createdAt: string;
  lastAttachedAt: string;
  status: TerminalSessionStatus;
  harness?: string;
  lifecycleId?: string;
  terminatedAt?: string;
  /** The durable leaf-identity key (qualified leaf id `repo/master/leaf-id`) this chat claims. */
  leafKey?: string;
  /** The role occupying the leaf binding (always serialized — the server migrates legacy rows). */
  seatRole?: string;
  /** Manager-declared leaf linkage for an unbound replacement seat. */
  replacementForLeaf?: string;
  // Spawned-by provenance: the dispatching session/lifecycle, the AR_SPAWN_ROLE this seat was
  // spawned AS, and the escape-hatch role knobs recorded verbatim at spawn.
  spawnedBySession?: string;
  spawnedByLifecycle?: string;
  spawnRole?: string;
  launchArgs?: string[];
  promptKeywords?: string[];
  sessionCommands?: string[];
  /** The RESOLVED dispatch level (leaf|master|portfolio) + whether it was explicit or defaulted. */
  spawnLevel?: string;
  spawnLevelSource?: string;
  // Settings-resolved model/effort pinned on the harness argv at launch — REQUESTED provenance,
  // never proof of the effective pair (evidence tiers live in the cockpit store, L4).
  resolvedModel?: string;
  resolvedEffort?: string;
  sessionLogEntryId?: string;
  sessionLogPath?: string;
  // Protocol-backed control metadata — absent on legacy/plain-terminal rows.
  controlState?: HarnessControlState;
  controlEndpoint?: string;
  controlProtocol?: string;
  controlActivity?: HarnessActivityState;
  controlAcceptance?: HarnessAcceptanceState;
  controlVendorSessionId?: string;
  controlPendingInteraction?: Record<string, unknown>;
  // Multiplexed sub-agent pendings: additive; the singular slot
  // above stays the parent-thread entry.
  controlPendingInteractions?: Record<string, unknown>[];
  controlLastEventSequence?: number;
  controlRaw?: ControlRawDiagnostics;
  // Liveness probe evidence (hysteresis persisted server-side).
  livenessFailures?: number;
  livenessFirstFailedAt?: string;
  livenessLastFailedAt?: string;
  livenessEvidence?: TerminalLivenessEvidence;
  exitEvidence?: TerminalLivenessEvidence;
  // Retirement provenance (a terminal mark layered on `terminated`).
  retiredAt?: string;
  retiredBySession?: string;
  retiredReason?: string;
  retiredEdge?: string;
  // Landed/archive provenance (successful completion, inspectable and non-active).
  landedAt?: string;
  landedReason?: string;
  landedEdge?: string;
  /** The ORIGINAL label, frozen on first rename — absent when never renamed. */
  spawnedLabel?: string;
  turnState?: SeatTurnState;
  turnStateChangedAt?: string;
}

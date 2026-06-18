// TypeScript mirror of the served projection contract
// (mcp/src/agents_remember/observer/projection.py). camelCase to match the wire form;
// the server dumps with `exclude_none=True`, so `T | None` fields are omitted when null —
// modelled here as optional (`?:`). `projection.py` is the source of truth (D7: codegen
// from pydantic is deferred; keep these in lockstep by hand for now).

export type State = "running" | "paused" | "blocked" | "completed" | "abandoned";

export type Phase =
  | "request"
  | "trust-checkpoint"
  | "reframe-research"
  | "decide"
  | "build"
  | "close";

export interface ActionAvailability {
  action: string;
  enabled: boolean;
  disabledReason?: string;
  nextSafeAction?: string;
}

export interface TokenSample {
  ts: string;
  cumulative: number;
}

export interface GateNode {
  id: string;
  kind: string;
  state: string;
  decidedBy?: string;
  decidedVia?: string;
  decisions: string[];
  packet: Record<string, unknown>;
  ts: string;
}

export interface LifecycleProjection {
  id: string;
  state: State;
  phase: Phase;
  fleeting: boolean;
  enclosure?: string;
  repoId?: string;
  scope?: string;
  tokens: number;
  startedAt: string;
  lastEventTs: string;
  staleSeconds?: number;
  inferred: boolean;
  ask?: Record<string, unknown>;
  gate?: GateNode;
  actions: ActionAvailability[];
  tokenSeries: TokenSample[];
}

export interface EnclosureNode {
  enclosure: string;
  taskId: string;
  taskName: string;
  repoName: string;
  lifecycleId: string;
  worktreeGroup: string;
  humanReviewStatus: string;
  closeoutStatus: string;
  integrationStatus: string;
  cleanup: string;
  actions: ActionAvailability[];
}

export interface ProviderNode {
  id: string;
  state: string;
  ok?: boolean;
  watcherUp: boolean;
  indexingState: string;
  snapshotStaleSeconds?: number;
  scope: string; // "workspace" | "worktree"
  role?: string; // "code" (CGC) | "memory" (GrepAI)
  repoId?: string;
  worktreeGroup?: string; // join key to the enclosure (group name); absent for workspace
}

export interface Metrics {
  lifecycleCount: number;
  runningCount: number;
  blockedCount: number;
  pausedCount: number;
  totalTokens: number;
  stalenessHistogram: Record<string, number>;
}

export interface DriftSnapshotNode {
  repository: string;
  branch: string;
  counts: Record<string, number>;
  actionableCount: number;
  snapshotStaleSeconds?: number;
}

export interface SidecarStaleNode {
  onboardingFile: string;
  repository: string;
  lastVerifiedDate: string;
  ageSeconds?: number;
}

export interface SetupSummaryNode {
  action: string;
  ok?: boolean;
  ready?: boolean;
  state?: string;
  generatedAt?: string;
  snapshotStaleSeconds?: number;
  resultCounts: Record<string, number>;
}

export interface SetupProgressNode {
  group: string;
  state: string;
  currentPhase?: string;
  heartbeatAgeSeconds?: number;
  completedCount: number;
  failedPhases: string[];
}

export interface RouteCoverageNode {
  repository?: string;
  route: string;
  sourceFilesInScope: number;
  fileSidecars: number;
  childRoutes: number;
}

export interface ToolReportNode {
  tool: string;
  path: string;
  label: string;
  ageSeconds?: number;
}

export interface LedgerNode {
  repository: string;
  closeoutCount: number;
  lastVerifiedCodeCommit: string;
  baseCodeCommit: string;
}

export interface TaskSubStepNode {
  id: string;
  title: string;
  status: string;
}

export interface TaskStepNode {
  id: string;
  title: string;
  status: string;
  substeps: TaskSubStepNode[];
}

export interface TaskDecisionNode {
  at: string;
  decision: string;
  rationale: string;
}

export interface TaskCodeExampleNode {
  id: string;
  title: string;
  distinctChange: string;
  why: string;
  language: string;
  snippet: string;
}

export interface TaskDocNode {
  lifecycleId: string;
  repository: string;
  title: string;
  status: string;
  kind: string;
  stepsDone: number;
  stepsTotal: number;
  currentStep?: string;
  docPath: string;
  ageSeconds?: number;
  steps: TaskStepNode[];
  objective: string;
  requirements: string[];
  design?: string;
  codeExamples: TaskCodeExampleNode[];
  decisions: TaskDecisionNode[];
  openQuestions: string[];
  references: string[];
}

export interface AttentionItem {
  id: string;
  kind: string; // blocked-gate | provider-down | actionable-drift | failed-setup | stale-session | dormant-fleeting | …
  severity: "alarm" | "warn" | "info";
  lane: "repo" | "worktree" | "lifecycle";
  title: string;
  detail?: string;
  waitSeconds?: number; // server-computed age (never render time)
  lifecycleId?: string; // cross-refs into the structural tree → queue↔tree coupling
  enclosure?: string;
  repoId?: string;
  providerId?: string;
}

// --- engine room process map (slice 5e) --------------------------------------

// The honesty axis: observed = checkout exists on disk; derived = recorded contract
// field whose checkout is absent; planned = expected-but-not-yet; missing = unobservable;
// not-applicable = a lane that does not exist (memory on a disabled contract).
export type ProcessFactState =
  | "observed"
  | "derived"
  | "planned"
  | "missing"
  | "not-applicable";

export type ProcessHealth =
  | "nominal"
  | "running"
  | "blocked"
  | "failed"
  | "stale"
  | "skipped"
  | "unknown"
  | "complete";

export interface CommitRefNode {
  branch?: string;
  commit?: string;
  path?: string;
  exists?: boolean;
  dirty?: boolean;
  behindSource?: number; // commits behind the local source tip (fetch-free); 0/absent when current
  factState: ProcessFactState;
}

export interface ProviderBootNode {
  id: string;
  role: string; // "code" (CGC) | "memory" (GrepAI)
  runtimeState: string; // nominal | indexing | down | configured | unknown
  factState: ProcessFactState;
}

export interface EngineProcessEdge {
  id: string;
  fromNode: string;
  toNode: string;
  kind: string; // worktree-add | ledger-map | cgc-seed | grepai-clone | sync | …
  state: string; // nominal | running | blocked | failed | stale | skipped | complete | planned | unknown
  label: string;
  detail?: string;
}

export interface EngineProcessNode {
  id: string; // the contract path — the stable enclosure id (== EnclosureNode.enclosure)
  enclosure: string;
  worktreeGroup: string;
  taskId: string;
  taskName: string;
  repoName: string;
  lifecycleId?: string;
  phase: string; // worktree-started | provider-setup | sync-needed | commit-approval-pending | … | completed | unknown
  health: ProcessHealth;
  codeSource: CommitRefNode;
  codeWorktree: CommitRefNode;
  memoryMode: string; // "external" | "internal" | "disabled"
  memorySource?: CommitRefNode;
  memoryWorktree?: CommitRefNode;
  ledgerPath?: string;
  humanReviewStatus: string;
  closeoutStatus: string;
  integrationStatus: string;
  cleanup: string;
  setupState?: string; // running | stale | failed | failed-unchecked | ok | complete | prepared
  currentPhase?: string;
  completedPhases: string[];
  failedPhases: string[];
  heartbeatAgeSeconds?: number;
  seedFallback: boolean;
  retryArgs?: Record<string, unknown>;
  providers: ProviderBootNode[];
  edges: EngineProcessEdge[];
  actions: ActionAvailability[];
  nextAction?: string; // the lifecycle-guidance next operation (display/copy only until slice 06)
  summary: string;
  missingFacts: string[];
  sourceFiles: string[];
}

export interface Analytics {
  driftSnapshots: DriftSnapshotNode[];
  stalestSidecars: SidecarStaleNode[];
  setupSummaries: SetupSummaryNode[];
  setupProgress: SetupProgressNode[];
  routeCoverage: RouteCoverageNode[];
  toolReports: ToolReportNode[];
  ledgers: LedgerNode[];
  taskDocuments: TaskDocNode[];
  attentionQueue: AttentionItem[]; // derived surface — composed by the reducer (slice 05)
  engineProcesses: EngineProcessNode[]; // derived surface — the Engine Room process map (slice 5e)
}

export interface WorkspaceProjection {
  version: number;
  generatedAt: string;
  lifecycles: LifecycleProjection[];
  enclosures: EnclosureNode[];
  providers: ProviderNode[];
  metrics: Metrics;
  analytics: Analytics;
}

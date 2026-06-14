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

export interface Analytics {
  driftSnapshots: DriftSnapshotNode[];
  stalestSidecars: SidecarStaleNode[];
  setupSummaries: SetupSummaryNode[];
  setupProgress: SetupProgressNode[];
  routeCoverage: RouteCoverageNode[];
  toolReports: ToolReportNode[];
  ledgers: LedgerNode[];
  taskDocuments: TaskDocNode[];
  attentionQueue: AttentionItem[]; // the one derived surface — composed by the reducer (slice 05)
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

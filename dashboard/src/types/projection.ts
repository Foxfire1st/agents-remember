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
  enclosureId: string;
  leafId: string;
  taskRoot: string;
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
  repoId?: string; // covered repo for workspace providers, owning repo for worktree providers
  worktreeGroup?: string; // join key to the enclosure; takes precedence over repoId
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
  closeoutCount: number; // the FULL row total (the popover "+N more" footer derives from it)
  lastVerifiedCodeCommit: string;
  baseCodeCommit: string;
  rows: LedgerRefNode[]; // newest window for the official coupler popover (5h)
}

// One memory.md ledger row — a code→memory commit mapping (5h coupler popover). Full SHAs; the popover
// shortens them for display and highlights this enclosure's row.
export interface LedgerRefNode {
  codeCommit: string;
  memoryCommit: string;
  // best-effort per-side commit message + committer ISO date (5h Tier 2); omitted when the commit
  // isn't in the local repo or the probe failed — the row falls back to the hash alone (never faked)
  codeSubject?: string;
  codeDate?: string;
  memorySubject?: string;
  memoryDate?: string;
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

// Series index rows are master-only; sections also carry non-master freeform task-doc prose.
export interface TaskSubTaskRefNode {
  number: string;
  name: string;
  file: string; // drill-in match key: its stem resolves to the slice doc's slug
  status: string;
  scope: string;
  createdAt?: string;
  linkedLifecycleId?: string; // set when `file` points at another master → a "→" cross-series jump
}

export interface TaskSectionNode {
  kind: string; // "freeform" | "subTasks" | "sharedDecisions"
  heading: string;
  body: string;
}

export interface TaskDocNode {
  id: string;
  lifecycleId?: string;
  repository: string;
  title: string;
  status: string;
  kind: string;
  stepsDone: number;
  stepsTotal: number;
  currentStep?: string;
  docPath: string;
  createdAt?: string;
  ageSeconds?: number;
  steps: TaskStepNode[];
  objective: string;
  requirements: string[];
  design?: string;
  codeExamples: TaskCodeExampleNode[];
  decisions: TaskDecisionNode[];
  openQuestions: string[];
  references: string[];
  subTasks: TaskSubTaskRefNode[]; // master-only; empty for light/subTask
  sections: TaskSectionNode[]; // master render plan or non-master freeform sections
  masterLifecycleId?: string; // parent master's lifecycle (cross-series) → "↑ parent series" breadcrumb
}

export interface SeriesNode {
  seriesId: string;
  repository: string;
  title: string;
  status: string;
  createdAt?: string;
  objective: string;
  subTasks: TaskSubTaskRefNode[];
  doneCount: number;
  totalCount: number;
  seriesTokenTotal: number;
  sections: TaskSectionNode[];
  decisions: TaskDecisionNode[];
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
  gateId?: string;
  enclosure?: string;
  repoId?: string;
  providerId?: string;
}

export interface AgentPickupNode {
  id: string;
  entryId: string;
  lifecycleId?: string;
  agentId?: string;
  gateId?: string;
  state: "waiting-for-agent" | "check-chat" | string;
  ageSeconds?: number;
  ttlSeconds: number;
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
  state: string; // nominal | running | blocked | failed | stale | skipped | complete | planned | refused | unknown
  label: string;
  detail?: string;
  // 05o — refused-conduit flash polarity (T9B/T9C/T14C): amber = a reroute/fallback (CGC seed → reindex),
  // red = a fault/conflict (GrepAI seed fault, integration conflict). Carried only on a `refused`-state edge
  // (the explicit reroute). A `failed`/`stale` seed/integration edge derives its polarity in the renderer.
  refusedPolarity?: "amber" | "red";
}

// One remote/PR participant in the successful-landing arc (slice 5h). `factState` is the honesty
// axis (like CommitRefNode): observed = a live git/gh probe confirmed it; planned = expected but not
// yet; missing = the probe could not run (e.g. gh absent). The cockpit never animates a planned PR
// as a live one.
export interface LandingRefNode {
  kind: string; // origin-main | origin-feat | origin-mem-main | pr
  label: string; // "origin/main" | "PR #128"
  state: string; // behind | tip | open | merged | pushed | planned | unknown
  factState: ProcessFactState;
  detail?: string;
}

export interface EngineProcessNode {
  id: string; // the contract path — the stable enclosure id (== EnclosureNode.enclosure)
  enclosure: string;
  worktreeGroup: string;
  taskId: string;
  leafId: string;
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
  // The memory.md ledger window for the WORKTREE coupler popover (5h): newest rows mapping this
  // worktree's code↔memory commits + the total count (for the "+N more in memory.md" footer).
  ledgerRows: LedgerRefNode[];
  ledgerRowCount: number;
  humanReviewStatus: string;
  closeoutStatus: string;
  integrationStatus: string;
  integrationStrategy?: string; // ff-only | replay; absent until the integration decision is recorded (5h)
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
  landing?: LandingRefNode[]; // the successful-landing arc (slice 5h); absent in pre-5h/persisted projections, empty until closeout/integration
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
  agentPickups?: AgentPickupNode[];
  ledgers: LedgerNode[];
  taskDocuments: TaskDocNode[];
  series: SeriesNode[];
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

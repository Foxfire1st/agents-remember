// TypeScript mirror of WorkspaceProjection — GENERATED FILE; DO NOT EDIT.
// Canonical core model: WorkspaceProjection.model_json_schema().
// Schema artifact: dashboard/src/types/projection.schema.json.
// Served-only tail: ServedWorkspaceProjection.model_json_schema().
// Generator: scripts/sync-projection-types.py.
// Regenerate: PYTHONPATH=mcp/src "$(git rev-parse --git-common-dir)/../.venv/bin/python" scripts/sync-projection-types.py
// Drift check: PYTHONPATH=mcp/src "$(git rev-parse --git-common-dir)/../.venv/bin/python" scripts/sync-projection-types.py --check

export const LIVE_STATES = ["running", "paused", "blocked", "awaiting-developer"] as const;

export const TERMINAL_STATES = ["completed", "abandoned"] as const;

export const LIFECYCLE_STATES = [...LIVE_STATES, ...TERMINAL_STATES] as const;

export type State = (typeof LIFECYCLE_STATES)[number];

export type TerminalState = (typeof TERMINAL_STATES)[number];

export type ActiveState = (typeof LIVE_STATES)[number];

export const ACTIVE_STATES: readonly ActiveState[] = LIVE_STATES;

type FiledOnce<S extends never> = S;

export type StatesAreFiledOnce = FiledOnce<ActiveState & TerminalState>;

export const PHASES = ["request", "trust-checkpoint", "reframe-research", "decide", "build", "close"] as const;

export type Phase = (typeof PHASES)[number];

export const ATTENTION_SEVERITIES = ["alarm", "warn", "info"] as const;

export type AttentionSeverity = (typeof ATTENTION_SEVERITIES)[number];

export const ATTENTION_LANES = ["repo", "worktree", "lifecycle"] as const;

export type AttentionLane = (typeof ATTENTION_LANES)[number];

export const PROCESS_FACT_STATES = ["observed", "derived", "planned", "missing", "stale", "not-applicable"] as const;

export type ProcessFactState = (typeof PROCESS_FACT_STATES)[number];

export const PROCESS_HEALTHS = ["nominal", "running", "blocked", "failed", "stale", "skipped", "unknown", "complete"] as const;

export type ProcessHealth = (typeof PROCESS_HEALTHS)[number];

export interface ActionAvailability {
  action: string;
  disabledReason?: string;
  enabled: boolean;
  nextSafeAction?: string;
}

export interface AgentNotifierHeartbeat {
  ageSeconds: number | null;
  lastSweepDurationSeconds: number | null;
  lastTickAt: string | null;
  pendingInboxCount: number;
  redeliverableInboxCount: number;
  stale: boolean;
  staleCutoffSeconds: number;
}

export interface AgentPickupNode {
  ageSeconds?: number;
  agentId?: string;
  artifactPath?: string;
  attemptCount: number;
  deliveredToSession?: string;
  deliveryState: string;
  entryId: string;
  escalatedAt?: string;
  gateId?: string;
  id: string;
  lastAttemptAt?: string;
  lifecycleId?: string;
  messageKind: string;
  nextAttemptAt?: string;
  ownerAgentId?: string;
  ownerLifecycleId?: string;
  ownerRole?: string;
  recipientRole?: string;
  senderAgentId?: string;
  senderRole?: string;
  state: string;
  ttlSeconds: number;
}

export interface Analytics {
  agentPickups: AgentPickupNode[];
  attentionQueue: AttentionItem[];
  driftSnapshots: DriftSnapshotNode[];
  engineProcesses: EngineProcessNode[];
  expectationRows: ExpectationRowNode[];
  ledgers: LedgerNode[];
  routeCoverage: RouteCoverageNode[];
  series: SeriesNode[];
  setupProgress: SetupProgressNode[];
  setupSummaries: SetupSummaryNode[];
  stalestSidecars: SidecarStaleNode[];
  taskDocuments: TaskDocNode[];
  toolReports: ToolReportNode[];
}

export interface AttentionItem {
  detail?: string;
  enclosure?: string;
  gateId?: string;
  id: string;
  kind: string;
  lane: AttentionLane;
  lifecycleId?: string;
  providerId?: string;
  repoId?: string;
  severity: AttentionSeverity;
  signalTs?: string;
  title: string;
  waitSeconds?: number;
}

export interface CommitRefNode {
  behindSource?: number;
  branch?: string;
  commit?: string;
  dirty?: boolean;
  exists?: boolean;
  factState: ProcessFactState;
  path?: string;
}

export interface DriftSnapshotNode {
  actionableCount: number;
  branch: string;
  checkedAt?: string;
  counts: Record<string, number>;
  memoryRoot?: string;
  reportPath?: string;
  repository: string;
  snapshotStaleSeconds?: number;
  sourceRoot?: string;
}

export interface EnclosureNode {
  actions: ActionAvailability[];
  cleanup: string;
  closeoutStatus: string;
  codeWorktreeExists: boolean;
  enclosure: string;
  enclosureId: string;
  humanReviewStatus: string;
  integrationStatus: string;
  leafId: string;
  lifecycleId: string;
  memoryWorktreeExists: boolean;
  repoName: string;
  taskId: string;
  taskName: string;
  taskRoot: string;
  worktreeGroup: string;
}

export interface EngineProcessEdge {
  detail?: string;
  fromNode: string;
  id: string;
  kind: string;
  label: string;
  state: string;
  toNode: string;
}

export interface EngineProcessNode {
  actions: ActionAvailability[];
  carryoverDoneAt?: string;
  cleanup: string;
  closeoutStatus: string;
  codeSource: CommitRefNode;
  codeWorktree: CommitRefNode;
  completedPhases: string[];
  currentPhase?: string;
  edges: EngineProcessEdge[];
  enclosure: string;
  failedPhases: string[];
  health: ProcessHealth;
  heartbeatAgeSeconds?: number;
  humanReviewStatus: string;
  id: string;
  integrationStatus: string;
  integrationStrategy?: string;
  landing: LandingRefNode[];
  leafId: string;
  ledgerPath?: string;
  ledgerRowCount: number;
  ledgerRows: LedgerRefNode[];
  lifecycleId?: string;
  memoryMode: string;
  memorySource?: CommitRefNode;
  memoryWorktree?: CommitRefNode;
  missingFacts: string[];
  nextAction?: string;
  phase: string;
  providers: ProviderBootNode[];
  repoName: string;
  retryArgs?: Record<string, unknown>;
  seedFallback: boolean;
  setupState?: string;
  sourceFiles: string[];
  summary: string;
  taskId: string;
  taskName: string;
  worktreeGroup: string;
}

export interface ExpectationRowNode {
  dueAt: string;
  id: string;
  kind: string;
  leafKey?: string;
  note?: string;
  overdue: boolean;
  sourceId: string;
  state: string;
  subjectAgentId?: string;
  subjectLifecycleId?: string;
}

export interface GateNode {
  decidedBy?: string;
  decidedVia?: string;
  decisions: string[];
  evidenceRefs: Record<string, unknown>[];
  id: string;
  kind: string;
  packet: Record<string, unknown>;
  state: string;
  ts: string;
}

export interface LandingRefNode {
  at?: string;
  detail?: string;
  factState: ProcessFactState;
  kind: string;
  label: string;
  lastAttemptAt?: string;
  observedAt?: string;
  staleSeconds?: number;
  state: string;
}

export interface LedgerNode {
  baseCodeCommit: string;
  closeoutCount: number;
  lastVerifiedCodeCommit: string;
  repository: string;
  rows: LedgerRefNode[];
}

export interface LedgerRefNode {
  codeCommit: string;
  codeDate?: string;
  codeSubject?: string;
  memoryCommit: string;
  memoryDate?: string;
  memorySubject?: string;
}

export interface LifecycleProjection {
  actions: ActionAvailability[];
  ask?: Record<string, unknown>;
  enclosure?: string;
  fleeting: boolean;
  gate?: GateNode;
  id: string;
  inferred: boolean;
  lastEventTs: string;
  phase: Phase;
  repoId?: string;
  scope?: string;
  staleSeconds?: number;
  startedAt: string;
  state: State;
  stateEnteredAt: string;
  tokenSeries: TokenSample[];
  tokens: number;
}

type Camel<S extends string> = S extends `${infer Head}-${infer Tail}`
  ? `${Head}${Capitalize<Camel<Tail>>}`
  : S;

export type StateCountField<S extends ActiveState> = `${Camel<S>}Count`;

export type LifecycleStateCounts = { [S in ActiveState as StateCountField<S>]: number };

export function stateCountField<S extends ActiveState>(state: S): StateCountField<S> {
  const [head, ...rest] = state.split("-");
  const camel = head + rest.map((word) => word.charAt(0).toUpperCase() + word.slice(1)).join("");
  return `${camel}Count` as StateCountField<S>;
}

function lifecycleStateCounts(
  lifecycles: readonly Pick<LifecycleProjection, "state">[],
): LifecycleStateCounts {
  return Object.fromEntries(
    ACTIVE_STATES.map((state) => [
      stateCountField(state),
      lifecycles.filter((entry) => entry.state === state).length,
    ]),
  ) as LifecycleStateCounts;
}

export interface Metrics extends LifecycleStateCounts {
  lifecycleCount: number;
  stalenessHistogram: Record<string, number>;
  totalTokens: number;
}

export function metricsFor(lifecycles: readonly LifecycleProjection[]): Metrics {
  return {
    lifecycleCount: lifecycles.length,
    totalTokens: lifecycles.reduce((sum, entry) => sum + entry.tokens, 0),
    stalenessHistogram: {},
    ...lifecycleStateCounts(lifecycles),
  };
}

export interface ProviderBootNode {
  factState: ProcessFactState;
  id: string;
  role: string;
  runtimeState: string;
}

export interface ProviderNode {
  id: string;
  indexingState: string;
  ok?: boolean;
  repoId?: string;
  role?: string;
  scope: string;
  snapshotStaleSeconds?: number;
  state: string;
  watcherUp: boolean;
  worktreeGroup?: string;
}

export interface RouteCoverageNode {
  childRoutes: number;
  fileSidecars: number;
  repository?: string;
  route: string;
  sourceFilesInScope: number;
}

export interface SeriesNode {
  ageSeconds?: number;
  createdAt: string;
  decisions: TaskDecisionNode[];
  docPath: string;
  doneCount: number;
  objective: string;
  repository: string;
  sections: SeriesSectionNode[];
  seriesId: string;
  seriesTokenTotal: number;
  status: string;
  subTasks: SeriesSubTaskNode[];
  title: string;
  totalCount: number;
}

export interface SeriesSectionNode {
  body: string;
  heading: string;
  kind: string;
}

export interface SeriesSubTaskNode {
  createdAt?: string;
  file: string;
  name: string;
  number: string;
  scope: string;
  status: string;
}

export interface ServingBuild {
  bootedAt: string;
  commit?: string;
  dashboardBuild?: string;
  dirty?: boolean;
  version: string;
}

export interface SetupProgressNode {
  completedCount: number;
  currentPhase?: string;
  failedPhases: string[];
  group: string;
  heartbeatAgeSeconds?: number;
  state: string;
}

export interface SetupSummaryNode {
  action: string;
  generatedAt?: string;
  ok?: boolean;
  ready?: boolean;
  resultCounts: Record<string, number>;
  snapshotStaleSeconds?: number;
  state?: string;
}

export interface SidecarStaleNode {
  ageSeconds?: number;
  lastVerifiedDate: string;
  onboardingFile: string;
  repository: string;
}

export interface TaskCodeExampleNode {
  distinctChange: string;
  id: string;
  language: string;
  snippet: string;
  title: string;
  why: string;
}

export interface TaskDecisionNode {
  at: string;
  decision: string;
  rationale: string;
}

export interface TaskDocNode {
  ageSeconds?: number;
  bodyRevision: string;
  codeExamples: TaskCodeExampleNode[];
  createdAt: string;
  currentStep?: string;
  decisions: TaskDecisionNode[];
  design?: string;
  docPath: string;
  id: string;
  kind: string;
  lifecycleId?: string;
  masterLifecycleId?: string;
  objective: string;
  openQuestions: string[];
  orchestrates: string[];
  references: string[];
  repository: string;
  requirements: string[];
  sections: TaskSectionNode[];
  status: string;
  steps: TaskStepNode[];
  stepsDone: number;
  stepsTotal: number;
  subTasks: TaskSubTaskRefNode[];
  title: string;
}

export interface TaskSectionNode {
  body: string;
  heading: string;
  kind: string;
}

export interface TaskStepDispositionNode {
  kind: "intentionalSkip";
  lifecycleId?: string;
  reason: string;
  recordedAt: string;
  recordedVia: "task_doc.skip_step";
}

export interface TaskStepNode {
  disposition?: TaskStepDispositionNode;
  id: string;
  status: string;
  substeps: TaskSubStepNode[];
  title: string;
}

export interface TaskSubStepNode {
  disposition?: TaskStepDispositionNode;
  id: string;
  status: string;
  title: string;
}

export interface TaskSubTaskRefNode {
  file: string;
  linkedLifecycleId?: string;
  name: string;
  number: string;
  scope: string;
  status: string;
}

export interface TokenSample {
  cumulative: number;
  ts: string;
}

export interface ToolReportNode {
  ageSeconds?: number;
  label: string;
  path: string;
  tool: string;
}

export type SubTaskRow = TaskSubTaskRefNode | SeriesSubTaskNode;

export interface WorkspaceProjection {
  activeWorktreeGroups: string[];
  analytics: Analytics;
  enclosures: EnclosureNode[];
  generatedAt: string;
  lifecycles: LifecycleProjection[];
  metrics: Metrics;
  providers: ProviderNode[];
  version: number;
  agentNotifierHeartbeat?: AgentNotifierHeartbeat;
  servingBuild?: ServingBuild;
  supervisorHeartbeat?: AgentNotifierHeartbeat;
}

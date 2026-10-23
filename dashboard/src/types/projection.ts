// TypeScript mirror of WorkspaceProjection — GENERATED FILE; DO NOT EDIT.
// Canonical core model: WorkspaceProjection.model_json_schema().
// Schema artifact: dashboard/src/types/projection.schema.json.
// Served-only tail: ServedWorkspaceProjection.model_json_schema().
// Generator: scripts/sync-projection-types.py.
// Regenerate: PYTHONPATH=mcp/src python scripts/sync-projection-types.py
// Drift check: PYTHONPATH=mcp/src python scripts/sync-projection-types.py --check

export const ATTENTION_SEVERITIES = ["alarm", "warn", "info"] as const;
export type AttentionSeverity = (typeof ATTENTION_SEVERITIES)[number];

export const ATTENTION_LANES = ["repo", "worktree", "agent", "task"] as const;
export type AttentionLane = (typeof ATTENTION_LANES)[number];

export const ATTENTION_ITEM_CLASSES = ["action", "notice", "operational"] as const;
export type AttentionItemClass = (typeof ATTENTION_ITEM_CLASSES)[number];

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

export interface Analytics {
  attentionQueue: AttentionItem[];
  driftSnapshots: DriftSnapshotNode[];
  engineProcesses: EngineProcessNode[];
  ledgers: LedgerNode[];
  routeCoverage: RouteCoverageNode[];
  setupProgress: SetupProgressNode[];
  setupSummaries: SetupSummaryNode[];
  toolReports: ToolReportNode[];
}

export interface AttentionItem {
  actionableFingerprint?: string;
  agentId?: string;
  agentRole?: string;
  detail?: string;
  dismissible: boolean;
  enclosure?: string;
  heldMessageId?: string;
  id: string;
  itemClass: AttentionItemClass;
  kind: string;
  lane: AttentionLane;
  occurrenceId?: string;
  promptId?: string;
  providerId?: string;
  repoId?: string;
  severity: AttentionSeverity;
  taskKey?: string;
  taskReference?: TaskDocumentRef;
  title: string;
  waitSeconds?: number;
}

export interface CheckoutBinding {
  configuredRoot?: string;
  coordinationRoot: string;
  remedy: string;
  state: string;
  worktree: string;
}

export interface CloseoutCandidateNode {
  classification: string;
  generationId: string;
  order: number;
  owningMaster: TaskDocumentRef;
  priority: string;
  /** JSON Schema refinements: {"maxItems":256} */
  reasons: string[];
  taskDocumentRef: TaskDocumentRef;
}

export interface CloseoutProjectionProblemNode {
  address: string;
  errorType: string;
  kind: string;
  repairAction: string;
  state: string;
}

export interface CloseoutQueueNode {
  members: CloseoutCandidateNode[];
  revision: number;
  serviceCondition: string;
  sourceClassification?: string;
  sourceFingerprint?: string;
  /** JSON Schema refinements: {"maxItems":256} */
  sourceProblems: CloseoutProjectionProblemNode[];
  sprintRef: TaskDocumentRef;
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

export interface DirectGrantNode {
  grantedAt: string;
  grantedBy: string;
}

export interface DiscardUnstartedProofNode {
  childJson: Record<string, unknown>;
  childMarkdown: Record<string, unknown>;
  commitState: string;
  doorState: string;
  enclosureState: string;
  fingerprint: string;
  locatorState: string;
  operationState: string;
  reviewState: string;
  seatState: string;
  taskDocumentRef: TaskDocumentRef;
  taskState: string;
  version: string;
}

export interface DiscardedSubTaskNode {
  discardedAt: string;
  disposition: string;
  file: string;
  name: string;
  number: string;
  proof: DiscardUnstartedProofNode;
  reason: string;
  scope: string;
}

export interface DriftSnapshotNode {
  actionableCount: number;
  actionableFingerprint?: string;
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
  lifecycleOperation?: LifecycleOperationProjection;
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
  sourceLineage?: SourceLineageProjection;
  summary: string;
  taskId: string;
  taskName: string;
  worktreeGroup: string;
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

export interface LifecycleApprovalObservation {
  state: "claimed" | "unclaimed";
}

export interface LifecycleOperationProjection {
  approval?: LifecycleApprovalObservation;
  cancellable: boolean;
  componentBindings?: LifecycleProjectionComponentBindings;
  currentCommand: string;
  elapsedSeconds: number;
  failure?: string;
  finishedAt?: string;
  generation?: number;
  guidance?: string;
  heartbeatAt?: string;
  identity?: LifecycleProjectionIdentity;
  kind: "closeout" | "integrate" | "direct-landing";
  /** JSON Schema refinements: {"maxItems":32} */
  legalControls: Record<string, unknown>[];
  /** JSON Schema refinements: {"minimum":1} */
  meaningfulRevision?: number;
  phase: "queued" | "preflight" | "memory-preflight" | "quality" | "approval-claim" | "recovering-after-claim" | "recovering-private-preparation" | "code-commit" | "memory-refresh" | "memory-commit" | "integration-replay" | "integration-quality" | "source-merge" | "contract-finalization" | "door-publication" | "termination-required" | "direct-preflight" | "direct-memory-commit" | "direct-terminal-publication" | "completed" | "failed" | "cancelled";
  /** JSON Schema refinements: {"maxItems":8} */
  projectionEffects: TaskDocProjectionEffect[];
  recommendedAction?: LifecycleRecommendedAction;
  reportPath: string;
  result?: Record<string, unknown>;
  schemaVersion: "lifecycle-operation-projection/v1";
  startedAt?: string;
  stateMatrixVersion: "lifecycle-operation-state-matrix/v1";
  status: "queued" | "running" | "input-required" | "termination-required" | "completed" | "failed" | "cancelled" | "unreadable" | "incoherent";
  taskIntent?: TaskIntentIdentity;
  worker?: LifecycleWorkerObservation;
}

export interface LifecycleProjectionComponentBindings {
  /** JSON Schema refinements: {"pattern":"^[0-9a-f]{64}$"} */
  approval?: string;
  /** JSON Schema refinements: {"maxItems":32} */
  legalControls: string[];
  /** JSON Schema refinements: {"pattern":"^[0-9a-f]{64}$"} */
  recommendedAction?: string;
  /** JSON Schema refinements: {"pattern":"^[0-9a-f]{64}$"} */
  result?: string;
  /** JSON Schema refinements: {"pattern":"^[0-9a-f]{64}$"} */
  worker?: string;
}

export interface LifecycleProjectionIdentity {
  /** JSON Schema refinements: {"pattern":"^[0-9a-f]{64}$"} */
  candidateTupleDigest: string;
  /** JSON Schema refinements: {"maxLength":4096,"minLength":1} */
  contractPath: string;
  /** JSON Schema refinements: {"minimum":1} */
  generation: number;
  /** JSON Schema refinements: {"pattern":"^[0-9a-f]{64}$"} */
  identityDigest: string;
  operationKind: "closeout" | "integrate" | "direct-landing";
  /** JSON Schema refinements: {"pattern":"^[0-9a-f]{64}$"} */
  planIdentityDigest: string;
  /** JSON Schema refinements: {"minimum":1} */
  recordRevision: number;
}

export interface LifecycleRecommendedAction {
  /** JSON Schema refinements: {"maxLength":128,"minLength":1} */
  action: string;
  arguments?: Record<string, unknown>;
  mutating: boolean;
  /** JSON Schema refinements: {"maxLength":2048,"minLength":1} */
  summary: string;
  /** JSON Schema refinements: {"maxLength":256} */
  tool?: string;
}

export interface LifecycleWorkerObservation {
  /** JSON Schema refinements: {"maxLength":1024} */
  detail: string;
  identityRetained: boolean;
  observedAt?: string;
  state: "live" | "termination-requested" | "termination-required" | "exited";
  /** JSON Schema refinements: {"pattern":"^[0-9a-f]{64}$"} */
  workerIdentitySha256?: string;
}

export interface LimitedSeat {
  account: string;
  agentId: string;
  /** JSON Schema refinements: {"format":"date-time"} */
  resetsAt: string;
  role: string;
  source: string;
  window: string;
}

export interface Metrics {
  ignoredHostAgents: number;
}

export interface ProjectionInvalidationResult {
  diagnostic?: ProjectionSourceProblem;
  outcome: "persisted-empty" | "already-empty" | "recovered-malformed" | "would-recover-malformed" | "would-persist-empty" | "failed";
  /** JSON Schema refinements: {"minimum":0} */
  revision?: number;
}

export interface ProjectionRebuildResult {
  /** JSON Schema refinements: {"minimum":0} */
  memberCount: number;
  outcome: "published" | "already-current" | "source-changed" | "source-unreadable" | "would-publish" | "not-attempted";
  /** JSON Schema refinements: {"minimum":0} */
  revision?: number;
  sourceClassification?: "active" | "terminal";
  /** JSON Schema refinements: {"pattern":"^[0-9a-f]{64}$"} */
  sourceFingerprint?: string;
  /** JSON Schema refinements: {"maxItems":256} */
  sourceProblems: ProjectionSourceProblem[];
}

export interface ProjectionSourceProblem {
  /** JSON Schema refinements: {"maxLength":8192,"minLength":1} */
  address: string;
  /** JSON Schema refinements: {"maxLength":256,"minLength":1} */
  errorType: string;
  kind: "task" | "door" | "series" | "projection";
  /** JSON Schema refinements: {"maxLength":8192,"minLength":1} */
  repairAction: string;
  state: "missing" | "unreadable" | "invalid";
}

export interface ProviderBootNode {
  factState: ProcessFactState;
  id: string;
  role: string;
  runtimeState: string;
}

export interface ProviderLimit {
  seats: LimitedSeat[];
  /** JSON Schema refinements: {"format":"date-time"} */
  until: string;
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

export interface ServingBuild {
  bootedAt: string;
  commit?: string;
  dashboardBuild?: string;
  dirty?: boolean;
  packageRoot?: string;
  pythonExecutable?: string;
  sourceDigest?: string;
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

export interface SourceLineageEdge {
  ahead?: number;
  behind?: number;
  contractPath: string;
  descendantBranch: string;
  detail?: string;
  relation: "super-to-master" | "master-to-leaf" | "super-to-leaf";
  side: "code" | "memory";
  sourceBranch: string;
  state: "current" | "behind" | "diverged" | "unavailable";
  syncContractPath: string;
}

export interface SourceLineageProjection {
  edges: SourceLineageEdge[];
  recoveries: SourceLineageRecovery[];
  state: "current" | "blocked" | "unavailable";
  summary: string;
}

export interface SourceLineageRecovery {
  args: Record<string, unknown>;
  contractPath: string;
  tool: "worktree_sync";
}

export interface TaskCodeExampleNode {
  distinctChange: string;
  id: string;
  language: string;
  snippet: string;
  title: string;
  why: string;
}

export interface TaskCounts {
  aborted: number;
  completed: number;
  paused: number;
  planned: number;
  started: number;
  unknown: number;
  works: number;
}

export interface TaskDecisionNode {
  at: string;
  decision: string;
  product_record: boolean;
  rationale: string;
}

export interface TaskDependencyNode {
  gone: boolean;
  id: string;
  reference?: TaskDocumentRef;
  title: string;
}

export interface TaskDocProjectionEffect {
  invalidation: ProjectionInvalidationResult;
  /** JSON Schema refinements: {"maxLength":8192} */
  nextAction?: string;
  /** JSON Schema refinements: {"minimum":0} */
  priorRevision?: number;
  /** JSON Schema refinements: {"pattern":"^[0-9a-f]{64}$"} */
  priorSourceFingerprint?: string;
  queueExisted: boolean;
  rebuild: ProjectionRebuildResult;
  /** JSON Schema refinements: {"minimum":0} */
  rebuiltRevision?: number;
  sprintTaskDocumentRef: TaskDocumentRef;
}

export interface TaskDocumentRef {
  path: string;
  repository: string;
}

export interface TaskEntry {
  activity?: "works" | "paused" | "unknown";
  children: string[];
  counts: TaskCounts;
  disagreements?: TaskListDisagreement[];
  eventAt?: string;
  id: string;
  key: string;
  parent?: string;
  providerLimit?: ProviderLimit;
  reference?: TaskDocumentRef;
  scope?: string;
  stage: "planned" | "started" | "aborted" | "completed";
  stepsDone: number;
  stepsTotal: number;
  summary?: TaskSummary;
  title: string;
  worktrees: EnclosureNode[];
}

export interface TaskExecutionEdgeNode {
  judgmentId?: string;
  predecessor: TaskExecutionEndpointNode;
  reason: string;
  successor: TaskExecutionEndpointNode;
}

export interface TaskExecutionEndpointNode {
  leafId?: string;
  ref: TaskDocumentRef;
}

export interface TaskExecutionGraphNode {
  edges: TaskExecutionEdgeNode[];
  nodes: TaskExecutionNode[];
}

export interface TaskExecutionGraphView {
  nodes: TaskExecutionNodeView[];
}

export interface TaskExecutionNode {
  kind: string;
  leafIds: string[];
  ref: TaskDocumentRef;
}

export interface TaskExecutionNodeView {
  cycle?: boolean;
  executionNature?: string;
  frontierState: "landed" | "ready" | "waiting" | "in-flight" | "abandoned";
  kind: "lump" | "segment";
  leafIds: string[];
  leafTitles: string[];
  masterRef: TaskDocumentRef;
  masterTitle: string;
  nodeId: string;
  predecessors: TaskExecutionPredecessorNode[];
  waveIndex: number;
}

export interface TaskExecutionPredecessorNode {
  judgmentId?: string;
  predecessorRef: TaskDocumentRef;
  predecessorTitle: string;
  reason: string;
}

export interface TaskHeaderNoteNode {
  label: string;
  value: string;
}

export interface TaskIntentIdentity {
  /** JSON Schema refinements: {"pattern":"^[0-9a-f]{64}$"} */
  digest: string;
  schema: "task-intent/v1";
}

export interface TaskListDisagreement {
  kind: "names-another-master" | "not-listed" | "listed-twice" | "unresolvable-master";
  masterLiteral?: string;
  parent?: string;
  record: TaskDocumentRef;
  recordMaster?: TaskDocumentRef;
  row?: string;
}

export interface TaskReviewFindingNode {
  description: string;
  descriptionTruncated: boolean;
  findingId: string;
  open: boolean;
}

export interface TaskReviewStateNode {
  additionalRounds: number;
  developerApproval?: string;
  findings: TaskReviewFindingNode[];
  openFindings: number;
  pending: boolean;
  round: number;
  totalFindings: number;
}

export interface TaskSeatNode {
  identity?: string;
  label: string;
  role: string;
  state: string;
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
  note?: string;
  noteTruncated: boolean;
  outcome?: string;
  outcomeTruncated: boolean;
  status: string;
  substeps: TaskSubStepNode[];
  title: string;
}

export interface TaskSubStepNode {
  disposition?: TaskStepDispositionNode;
  id: string;
  note?: string;
  status: string;
  title: string;
}

export interface TaskSummary {
  acceptance?: string;
  ageSeconds?: number;
  bodyRevision: string;
  codeExamples: TaskCodeExampleNode[];
  completedAt?: string;
  createdAt: string;
  currentStep?: string;
  decisions: TaskDecisionNode[];
  design?: string;
  directGrant?: DirectGrantNode;
  discardedCount?: number;
  /** JSON Schema refinements: {"maxItems":256} */
  discardedSubTasks?: DiscardedSubTaskNode[];
  executionGraph?: TaskExecutionGraphNode;
  executionGraphView?: TaskExecutionGraphView;
  executionNature?: "organizational" | "atomic";
  executionWaves: TaskExecutionNode[][];
  form: "sprint" | "master" | "task";
  headerNotes: TaskHeaderNoteNode[];
  headerNotesTotal?: number;
  integrationBranch?: string;
  kind: string;
  neededByTasks?: TaskDependencyNode[];
  nonGoals?: string;
  objective: string;
  openQuestions: string[];
  orchestrates: string[];
  references: string[];
  requirements: string[];
  reviewState?: TaskReviewStateNode;
  seats: TaskSeatNode[];
  sections: TaskSectionNode[];
  statusNote?: string;
  statusNoteTruncated: boolean;
  steps: TaskStepNode[];
  type: string;
  waitsFor?: string[];
  waitsForTasks?: TaskDependencyNode[];
}

export interface ToolReportNode {
  ageSeconds?: number;
  label: string;
  path: string;
  tool: string;
}

export interface WatcherHealth {
  downSince?: number;
  running: boolean;
}

export interface WorkspaceProjection {
  activeWorktreeGroups: string[];
  analytics: Analytics;
  closeoutQueues?: CloseoutQueueNode[];
  enclosures: EnclosureNode[];
  generatedAt: string;
  metrics: Metrics;
  providers: ProviderNode[];
  taskCounts: TaskCounts;
  tasks: TaskEntry[];
  version: number;
  checkoutBinding?: CheckoutBinding;
  servingBuild?: ServingBuild;
  watcherHealth?: WatcherHealth;
}

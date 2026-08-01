// TypeScript mirror of the served projection contract
// (mcp/src/agents_remember/observer/projection.py). camelCase to match the wire form;
// the server dumps with `exclude_none=True`, so `T | None` fields are omitted when null —
// modelled here as optional (`?:`). `projection.py` is the source of truth (codegen
// from pydantic is deferred; keep these in lockstep by hand for now).
//
// A `?:` therefore normally means "the server omits this when null". A handful of fields
// marked `LATE MIRROR` below are always on the wire (a non-None default server-side) and are
// still declared optional here, because making them required would force every hand-written
// `LifecycleProjection` / `Analytics` / `GateNode` literal across the test suite to be edited in
// the same change. They are optional as a client-side tolerance, not as a wire fact. Codegen
// from the pydantic models is what removes the distinction (and the hand-editing) for good.

// Mirrors `observer/lifecycle_state`'s state vocabulary (six states) — and mirrors the way that
// module DECLARES it, as a PARTITION. Every state is either LIVE (work is in flight) or TERMINAL
// (the lifecycle is over); the two halves are written out below and the whole is COMPOSED from
// them. Server-side that composition is `State = Literal[LiveState, TerminalState]`, which PEP 586
// flattens into exactly the six; here it is the same composition by tuple spread.
//
// Composing is the load-bearing part, not a tidier spelling. This file used to declare
// `LIFECYCLE_STATES` as one list of six and `TERMINAL_STATES` as a second, independent list of two
// beside it. Two lists naming one vocabulary can disagree, and nothing here would have noticed:
// `Exclude<State, TerminalState>` silently ignores a member of the terminal list that is absent
// from the whole, so the terminal half could have named a state the vocabulary had never heard of
// and every gate would have stayed green. Assembled from the halves there is no second list left
// to disagree — filing a state on a half is what puts it in the vocabulary at all.
//
// Both halves are exported on purpose, even though nothing outside this file imports either.
// Server-side the same pair is public (`LIVE_STATES` / `TERMINAL_STATES`) and `lifecycle_state.py`
// refuses at import any state filed on neither side, because that filing is what decides whether a
// state gets a metrics bucket and whether the lifecycle is over. Publishing one half and hiding
// the other is what invites the next consumer to hand-roll the missing half beside it — the same
// move that produced the bucket list this file replaced. Half a partition is not a smaller API
// surface, it is a bigger one.

// The LIVE half (mirrors `lifecycle_state.LiveState` / `LIVE_STATES`). `paused` is system-owned:
// there is no pause signal, the projection infers it from a stale heartbeat or a recorded
// switch-away. `awaiting-developer` is the NOTIFY-AND-CONTINUE turn-end state — the model has
// handed the turn back and stopped, still live and auto-resumed by the next AR tool call. It is
// neither healthy nor a fault, so every surface that maps state → colour must give it the "your
// move" treatment rather than falling through to running/ok.
export const LIVE_STATES = ["running", "paused", "blocked", "awaiting-developer"] as const;

// The TERMINAL half (mirrors `lifecycle_state.TerminalState` / `TERMINAL_STATES`): the states that
// are the END of a lifecycle rather than a stage of a live one. Server-side this half IS the
// `lifecycle_end` outcome vocabulary — a lifecycle reaches a terminal state exactly one way, by
// being ended, and `lifecycle.ended`'s `outcome` names which one.
export const TERMINAL_STATES = ["completed", "abandoned"] as const;

// The whole vocabulary: the live half then the terminal half, which is exactly the order
// `lifecycle_state.STATES` comes out in — PEP 586 flattens `Literal[LiveState, TerminalState]` in
// declaration order, so composing here the way the server composes there keeps the two sides
// enumerating identically for free rather than by anyone remembering to. Nothing indexes this
// tuple; consumers iterate or spread it (`topology/model.test.ts`, `grammar/Dot.test.tsx`,
// `test/contract.test.ts`), which is the point: still a runtime tuple with the type DERIVED from
// it, so a test (or any other consumer) that needs to enumerate the states reads this one list
// instead of hand-copying a fifth. It is now assembled rather than typed out, which is what
// removes the list that could disagree.
export const LIFECYCLE_STATES = [...LIVE_STATES, ...TERMINAL_STATES] as const;

export type State = (typeof LIFECYCLE_STATES)[number];

export type TerminalState = (typeof TERMINAL_STATES)[number];

// The states a workspace rollup buckets (mirrors `projection.py::ACTIVE_STATES`): the live half
// ITSELF, not the vocabulary minus the terminal pair. `projection.py` made the same choice for the
// reason its comment gives — a set difference re-derives the answer from a second list that could
// itself be wrong, and removing that re-derivation is this declaration's whole business. A state
// joins the buckets by being filed live; a terminal one never can.
export type ActiveState = (typeof LIVE_STATES)[number];

export const ACTIVE_STATES: readonly ActiveState[] = LIVE_STATES;

// What composition alone cannot rule out, refused at COMPILE time — the mirror's answer to
// `lifecycle_state.check_state_partition`, which refuses at import. Two of that function's three
// refusals are unrepresentable here rather than checked, and asserting them would be a check that
// cannot fail: a state cannot be on `State` and filed on neither half (there is nothing else for
// it to be on), and it cannot be filed yet absent from `State` (filing is what puts it there).
//
// What survives composition is DOUBLE-filing — one state on both halves — and that is a real
// defect, not an untidiness: it puts a terminal state into `ActiveState`, gives it a metrics bucket
// the server never sends, and lists it twice in `LIFECYCLE_STATES`. Disjoint string-literal unions
// intersect to `never`, so the constraint below holds exactly while the halves are disjoint and
// fails `tsc -b` naming the offender otherwise:
//   error TS2344: Type '"completed"' does not satisfy the constraint 'never'.
// Exported because it has to be: `noUnusedLocals` rejects a type alias nothing reads, and an
// assertion is precisely a declaration nothing reads.
type FiledOnce<S extends never> = S;

export type StatesAreFiledOnce = FiledOnce<ActiveState & TerminalState>;

// Mirrors `observer/lifecycle_state.Phase`. Declared as a runtime tuple with the type derived
// from it for the same reason `LIFECYCLE_STATES` is: the contract test held its OWN copy of the
// six phase names and validated the served payload against that copy, so a seventh phase on the
// server would have been checked against a list that had never heard of it. There is one list.
export const PHASES = [
  "request",
  "trust-checkpoint",
  "reframe-research",
  "decide",
  "build",
  "close",
] as const;

export type Phase = (typeof PHASES)[number];

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
  /** LATE MIRROR — `projection.py::GateNode.evidenceRefs`, a list default, always on the wire. */
  evidenceRefs?: Record<string, unknown>[];
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
  // LATE MIRROR — when the lifecycle last ENTERED its current state. Unlike `lastEventTs` it is
  // immune to heartbeats, which is what makes it the stable acknowledgement anchor the server's
  // attention queue uses to re-surface a NEW awaiting-developer / blocked occurrence. A `str`
  // with a `""` default server-side, so it is always on the wire.
  stateEnteredAt?: string;
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
  // Worktree-existence truth: stat'ed server-side at snapshot time (never inferred from
  // cleanup state). The tasks surface renders a leaf ONLY while a worktree physically exists —
  // cleanup=reopened means contract-reset-awaiting-restart, not live work.
  codeWorktreeExists: boolean;
  memoryWorktreeExists: boolean;
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

// `awaiting-developer` → `awaitingDeveloperCount`: the package's camelCase wire convention
// applied to a hyphenated state name, expressed as a TYPE so the bucket field NAMES are
// derived from the vocabulary and not hand-copied. Mirrors `projection.py::state_count_field`.
//
// THE RULE, in both copies: each segment after the first has its first character upper-cased
// and THE TAIL LEFT ALONE. Python used to spell it `str.capitalize()`, which lower-cases the
// tail as well — so `awaiting-DEVELOPER` bucketed into `awaitingDeveloperCount` server-side and
// `awaitingDEVELOPERCount` here, one rule with two answers. It was settled in favour of this
// spelling (`projection.py::state_count_field` now does `word[:1].upper() + word[1:]`) because
// `Capitalize<>` cannot lower-case a tail at the type level, and because lower-casing one
// quietly merges two states that differ only in the case of their tail into a single bucket.
type Camel<S extends string> = S extends `${infer Head}-${infer Tail}`
  ? `${Head}${Capitalize<Camel<Tail>>}`
  : S;

export type StateCountField<S extends ActiveState> = `${Camel<S>}Count`;

// One `number` per live state, keyed by that state's bucket field. This is the whole point of
// the derivation: filing a state on `LIVE_STATES` adds a REQUIRED field here, so every object
// that claims to be a `Metrics` stops compiling until it counts the new state. Filing one on
// `TERMINAL_STATES` adds no field, which is the filing doing its job rather than an omission —
// history is not work in flight. The hand-written bucket list this replaces is what let
// `awaiting-developer` be counted nowhere.
export type LifecycleStateCounts = { [S in ActiveState as StateCountField<S>]: number };

// `charAt(0)`, not `word[0]`: it is the exact runtime twin of `Camel<>` above and of the
// server's `word[:1].upper() + word[1:]` — including on an empty segment (`a--b`), where
// indexing would splice the string "undefined" into a field name and the other two copies of
// the rule produce nothing.
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

// The whole rollup a client reads: the all-states totals plus one bucket per live state.
// `lifecycleCount` counts every lifecycle and `totalTokens` sums every one — including the
// terminal ones the buckets deliberately leave out.
export interface Metrics extends LifecycleStateCounts {
  lifecycleCount: number;
  totalTokens: number;
  stalenessHistogram: Record<string, number>;
}

// The client-side mirror of the reducer's rollup (`reducer.py::_metrics`), so a fixture or a
// test states a workspace's lifecycles and gets the metrics the server would have sent —
// instead of re-listing the buckets beside them. Those hand-kept copies (one in the dev
// fixtures, one per test seed) were where the gap kept reappearing.
export function metricsFor(lifecycles: readonly LifecycleProjection[]): Metrics {
  return {
    lifecycleCount: lifecycles.length,
    totalTokens: lifecycles.reduce((sum, entry) => sum + entry.tokens, 0),
    stalenessHistogram: {},
    ...lifecycleStateCounts(lifecycles),
  };
}

export interface DriftSnapshotNode {
  repository: string;
  branch: string;
  counts: Record<string, number>;
  actionableCount: number;
  checkedAt?: string;
  sourceRoot?: string;
  memoryRoot?: string;
  reportPath?: string;
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
  rows: LedgerRefNode[]; // newest window for the official coupler popover
}

// One memory.md ledger row — a code→memory commit mapping for the coupler popover. Full SHAs; the popover
// shortens them for display and highlights this enclosure's row.
export interface LedgerRefNode {
  codeCommit: string;
  memoryCommit: string;
  // best-effort per-side commit message + committer ISO date; omitted when the commit
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
//
// NB: this and `SeriesSubTaskNode` below are TWO distinct server models
// (`projection.py::TaskSubTaskRefNode` / `::SeriesSubTaskNode`, both `extra="forbid"`) that were
// once collapsed into this one interface. The collapse invented a `createdAt` here that the
// server never sends, and lent `linkedLifecycleId` to the series rows, which never carry it.
// They share five fields and differ in exactly one each — keep them separate.
export interface TaskSubTaskRefNode {
  number: string;
  name: string;
  file: string; // drill-in match key: its stem resolves to the slice doc's slug
  status: string;
  scope: string;
  linkedLifecycleId?: string; // set when `file` points at another master → a "→" cross-series jump
}

// One subtask checkbox of a SERIES master (`projection.py::SeriesSubTaskNode`). `createdAt` is
// resolved server-side from the referenced leaf document, and `snapshots.py::_series_subtask_nodes`
// has already ordered the rows by it. No `linkedLifecycleId`: a series row never cross-links.
export interface SeriesSubTaskNode {
  number: string;
  name: string;
  file: string;
  status: string;
  scope: string;
  createdAt?: string;
}

// Either side of the sub-task index. `SubTaskIndex` renders both, so the shared fields are all it
// may rely on without narrowing.
export type SubTaskRow = TaskSubTaskRefNode | SeriesSubTaskNode;

export interface TaskSectionNode {
  kind: string; // "freeform" | "subTasks" | "sharedDecisions"
  heading: string;
  body: string;
}

// One ordered render section of a SERIES master (`projection.py::SeriesSectionNode`) — a distinct
// `extra="forbid"` model from `TaskSectionNode`, and declared distinctly here for the same reason
// `SeriesSubTaskNode` is: the rule this mirror lives by is one interface per Python model, and the
// last time two models shared one interface it invented a `createdAt` the server never sends and
// lent `linkedLifecycleId` to rows that never carry it.
//
// Be honest about what this buys. The two models declare the same three fields, so TypeScript's
// structural typing makes them interchangeable — `SeriesSectionNode[]` still assigns to
// `TaskSectionNode[]` (`DetailPanel.tsx::seriesAsMasterDoc` does exactly that), no assertion over
// any payload can separate them, and `contract.test.ts`'s structural walk never will. What it buys
// is a NAME and a SLOT: when the server adds a field to one of them, the divergence has somewhere
// to land as a one-line edit instead of arriving as a refactor at the worst moment. Only per-model
// codegen makes the distinction load-bearing.
export interface SeriesSectionNode {
  kind: string;
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
  bodyRevision?: string;
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
  // The orchestration-command relation: non-empty only on a master doc that IS an orchestration
  // task — the master task names it commands. Optional so projections persisted before this field
  // was added still parse.
  orchestrates?: string[];
}

export interface SeriesNode {
  seriesId: string;
  repository: string;
  title: string;
  status: string;
  createdAt?: string;
  objective: string;
  subTasks: SeriesSubTaskNode[];
  doneCount: number;
  totalCount: number;
  seriesTokenTotal: number;
  sections: SeriesSectionNode[]; // the series' own section model — see `SeriesSectionNode`
  decisions: TaskDecisionNode[];
  docPath: string;
  ageSeconds?: number;
}

// The attention queue's two closed vocabularies. Python declares both as bare `str`
// (`projection.py::AttentionItem`), so the mirror is NARROWER than the server by construction and
// nothing type-level can notice: `resolveJsonModule` widens the fixture's literals to `string`, so
// a served `severity: "critical"` assigns to `"alarm" | "warn" | "info"` without complaint. The
// only check that can bite is a runtime one against a list — which is why these are declared as
// tuples with the type DERIVED, the same shape as `LIFECYCLE_STATES` / `PHASES`, so
// `contract.test.ts` can measure the payload against the vocabulary instead of a hand-copy.
export const ATTENTION_SEVERITIES = ["alarm", "warn", "info"] as const;

export type AttentionSeverity = (typeof ATTENTION_SEVERITIES)[number];

export const ATTENTION_LANES = ["repo", "worktree", "lifecycle"] as const;

export type AttentionLane = (typeof ATTENTION_LANES)[number];

export interface AttentionItem {
  id: string;
  kind: string; // blocked-gate | provider-down | actionable-drift | failed-setup | stale-session | dormant-fleeting | …
  severity: AttentionSeverity;
  lane: AttentionLane;
  title: string;
  detail?: string;
  waitSeconds?: number; // server-computed age (never render time)
  lifecycleId?: string; // cross-refs into the structural tree → queue↔tree coupling
  gateId?: string;
  enclosure?: string;
  repoId?: string;
  providerId?: string;
  signalTs?: string; // triggering-signal time — current-occurrence acknowledgement anchor
}

export interface AgentPickupNode {
  id: string;
  entryId: string;
  lifecycleId?: string;
  agentId?: string;
  senderAgentId?: string;
  senderRole?: string;
  recipientRole?: string;
  /** Routed owner facts were added after the original pickup projection; optional for old rows. */
  ownerRole?: string;
  ownerAgentId?: string;
  ownerLifecycleId?: string;
  gateId?: string;
  messageKind: string;
  artifactPath?: string;
  deliveryState: "queued" | "no-hosted-session" | "delivered" | "unconfirmed" | string;
  deliveredToSession?: string;
  /** Redelivery/escalation facts are omitted by persisted projections that predate those fields. */
  attemptCount?: number;
  lastAttemptAt?: string;
  nextAttemptAt?: string;
  escalatedAt?: string;
  state: "waiting-for-agent" | "check-chat" | string;
  ageSeconds?: number;
  ttlSeconds: number;
}

// --- engine room process map -------------------------------------------------

// The honesty axis: observed = checkout exists on disk; derived = recorded contract
// field whose checkout is absent; planned = expected-but-not-yet; missing = unobservable;
// not-applicable = a lane that does not exist (memory on a disabled contract).
//
// Tuple-first for the same reason as `ATTENTION_SEVERITIES`: `projection.py` types every
// `factState` and `health` field as a bare `str`, so this union is the mirror claiming a narrower
// contract than the server holds. Only a runtime membership check can catch the day that claim
// stops being true, and a runtime check needs a runtime list.
export const PROCESS_FACT_STATES = [
  "observed",
  "derived",
  "planned",
  "missing",
  "stale",
  "not-applicable",
] as const;

export type ProcessFactState = (typeof PROCESS_FACT_STATES)[number];

export const PROCESS_HEALTHS = [
  "nominal",
  "running",
  "blocked",
  "failed",
  "stale",
  "skipped",
  "unknown",
  "complete",
] as const;

export type ProcessHealth = (typeof PROCESS_HEALTHS)[number];

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
  runtimeState: string; // nominal | indexing | down | configured | missing | unknown
  factState: ProcessFactState;
}

export interface EngineProcessEdge {
  id: string;
  fromNode: string;
  toNode: string;
  kind: string; // worktree-add | ledger-map | cgc-seed | grepai-clone | sync | …
  // Mirrors the reducer's vocabulary exactly. There is no `refused` state: the renderer's
  // flash polarity is DERIVED (failed → red fault, stale → amber reroute), never carried.
  state: string; // nominal | running | blocked | failed | stale | skipped | complete | planned | unknown
  label: string;
  detail?: string;
}

// One remote/PR participant in the successful-landing arc. `factState` is the honesty
// axis (like CommitRefNode): observed = a live git/gh probe confirmed it; planned = expected but not
// yet; missing = the probe could not run (e.g. gh absent). The cockpit never animates a planned PR
// as a live one.
export interface LandingRefNode {
  kind: string; // origin-main | origin-feat | origin-mem-main | pr
  label: string; // "origin/main" | "PR #128"
  state: string; // behind | tip | open | merged | pushed | planned | unknown
  factState: ProcessFactState;
  detail?: string;
  at?: string; // the ref's own timestamp (merge/push time), distinct from the probe's `observedAt`
  observedAt?: string;
  lastAttemptAt?: string;
  staleSeconds?: number;
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
  // The memory.md ledger window for the WORKTREE coupler popover: newest rows mapping this
  // worktree's code↔memory commits + the total count (for the "+N more in memory.md" footer).
  ledgerRows: LedgerRefNode[];
  ledgerRowCount: number;
  humanReviewStatus: string;
  closeoutStatus: string;
  integrationStatus: string;
  integrationStrategy?: string; // ff-only | replay; absent until the integration decision is recorded
  cleanup: string;
  carryoverDoneAt?: string; // when memory carryover landed; absent until it has
  setupState?: string; // running | stale | failed | failed-unchecked | ok | complete | prepared
  currentPhase?: string;
  completedPhases: string[];
  failedPhases: string[];
  heartbeatAgeSeconds?: number;
  seedFallback: boolean;
  retryArgs?: Record<string, unknown>;
  providers: ProviderBootNode[];
  edges: EngineProcessEdge[];
  landing?: LandingRefNode[]; // the successful-landing arc; absent in persisted projections, empty until closeout/integration
  actions: ActionAvailability[];
  nextAction?: string; // the lifecycle-guidance next operation (display/copy only for now)
  summary: string;
  missingFacts: string[];
  sourceFiles: string[];
}

// One outstanding expectation the supervisor is holding open: a thing that was asked for, who
// it was asked of, and when it stops being merely pending and becomes late (`dueAt`/`overdue`).
// Mirrors `projection.py::ExpectationRowNode`.
export interface ExpectationRowNode {
  id: string;
  kind: string;
  state: string;
  sourceId: string;
  subjectAgentId?: string;
  subjectLifecycleId?: string;
  leafKey?: string;
  dueAt: string;
  overdue: boolean;
  note?: string;
}

export interface Analytics {
  driftSnapshots: DriftSnapshotNode[];
  stalestSidecars: SidecarStaleNode[];
  setupSummaries: SetupSummaryNode[];
  setupProgress: SetupProgressNode[];
  routeCoverage: RouteCoverageNode[];
  toolReports: ToolReportNode[];
  agentPickups?: AgentPickupNode[];
  /** LATE MIRROR — `projection.py::Analytics.expectationRows`, a list default, always on the wire. */
  expectationRows?: ExpectationRowNode[];
  ledgers: LedgerNode[];
  taskDocuments: TaskDocNode[];
  series: SeriesNode[];
  attentionQueue: AttentionItem[]; // derived surface — composed by the reducer
  engineProcesses: EngineProcessNode[]; // derived surface — the Engine Room process map
}

// The boot-time serving stamp (serving/build_info.py): which build/process is
// answering. Injected app-side onto /api/state and the SSE snapshot (NOT reducer truth, so it
// is optional here and absent from persisted latest-state.json). `commit` is best-effort —
// omitted when the server runs off-checkout (an installed wheel).
export interface ServingBuild {
  version: string;
  bootedAt: string;
  commit?: string;
  /** Fingerprint of the shipped dashboard build inputs; compared with the running JS bundle. */
  dashboardBuild?: string;
  /** Present (true) when the serving checkout had uncommitted code at boot — rendered as `·dirty`. */
  dirty?: boolean;
}

// The supervisor sweep's self-liveness tick (serving/supervisor_heartbeat.py):
// "the watcher must be code AND watched". Injected app-side onto /api/state and the SSE
// snapshot at RESPONSE time (deliberately volatile — never gates the projection's ETag change
// revision), so `lastTickAt`/`ageSeconds` are as-of-request, not as-of-last-projection-change.
// `lastTickAt: null` means the supervisor has never ticked in this workspace (dashboard/supervisor
// autostart is opt-in) — that is NOT the same as `stale: true`, and the header renders nothing for
// it rather than a false alarm.
export interface SupervisorHeartbeat {
  lastTickAt: string | null;
  ageSeconds: number | null;
  staleCutoffSeconds: number;
  stale: boolean;
  pendingInboxCount: number;
  redeliverableInboxCount: number;
  lastSweepDurationSeconds: number | null;
}

export interface WorkspaceProjection {
  version: number;
  generatedAt: string;
  lifecycles: LifecycleProjection[];
  enclosures: EnclosureNode[];
  providers: ProviderNode[];
  // Worktree-group basenames whose enclosure lifecycle is still active — the bounded set the
  // Topology constellation filters on (the shared lifecycles/enclosures arrays keep all-time
  // history for other views). Join key = worktree ProviderNode.worktreeGroup / basename of
  // EnclosureNode.worktreeGroup. Source of truth: projection.py activeWorktreeGroups.
  activeWorktreeGroups: string[];
  metrics: Metrics;
  analytics: Analytics;
  servingBuild?: ServingBuild; // app-injected on the wire only — see ServingBuild
  supervisorHeartbeat?: SupervisorHeartbeat; // app-injected on the wire only — see SupervisorHeartbeat
}

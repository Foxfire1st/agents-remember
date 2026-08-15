// The one place a dashboard test may build a node of the served projection.
//
// R6 in one sentence: a test whose fixture is authored by the consumer cannot detect producer
// drift. This leaf proved it twice — a test asserted `refusedPolarity === "amber"` against a
// fixture that set the field itself, on a model that is `extra="forbid"` server-side; and three
// tests built a master `TaskSubTaskRefNode` carrying `createdAt`, which its server model omits. Both
// fixtures were written with `as SomeWireType`, and an assertion skips excess-property checking, so
// both compiled.
//
// The rule these builders exist to enforce: EVERY wire node a test uses is type-checked against
// `types/projection.ts`, the mirror — which `test/contract.test.ts` then measures in three
// directions. A fixture that type-checks here is a shape THE MIRROR can produce; how much of a
// claim that is about the server is spelled out under BE PRECISE ABOUT WHAT PINS WHAT below.
//
// HOW THE DEFAULTS STAY HONEST — and exactly how far that goes. Each base below is assembled from
// `fixtures/snapshot.json` and annotated with the mirror type, so it is pinned from both sides at
// once: a required field the server adds fails to compile until it is filled, and it can only be
// filled from a served row. Only the REQUIRED fields are carried. Optionals (`gate`, `ask`,
// `staleSeconds`, `seedFallback`, …) are left off on purpose: a default gate nobody asked for would
// silently change what an attention-queue test is measuring.
//
// BE PRECISE ABOUT WHAT PINS WHAT. `snapshot.json` is NOT generated; it remains a hand-maintained
// sampled payload. The producer-to-TypeScript link is generated and checked, while this file and
// `contract.test.ts` hold dashboard fixtures against that generated contract. So the chain is:
//
//   this fixture  --type-checked against-->  generated types/projection.ts
//                  (`Overrides<O, Node>`,        ↑ generated + stale-checked from
//                   and every annotated      observer/projection.py's Pydantic schema
//                   base below)                  ↕ sampled by `snapshot.json`
//
// What a green build here therefore claims is: the generated wire contract can produce this shape,
// and the sampled payload exercises it. Read `contract.test.ts` for exactly how far its three
// fixture directions reach; schema generation separately catches omitted nullable fields and
// vocabulary members no sample happens to carry.
//
// HOW PARTIAL FIXTURES STAY ERGONOMIC. Every builder takes an `Overrides<O, Node>` (see
// `fixtures/overrides.ts`), so a test that cares about three fields of a fifty-field node names
// three fields. The override is still checked, twice over: a field the mirror does not declare is
// an excess property on a fresh object literal and fails `tsc -b` at the call site, which is
// exactly where the two proven defects would have died — and a REQUIRED field written as an
// explicit `undefined` fails there too, which plain `Partial<Node>` allows whenever
// `exactOptionalPropertyTypes` is off. (`wireFixtureGuard.ts` covers the residual case TypeScript
// will not — an override that has been through a variable and so lost its literal freshness.)

import { metricsFor } from "../../types/projection";
import type {
  ActionAvailability,
  AgentPickupNode,
  Analytics,
  AttentionItem,
  EnclosureNode,
  EngineProcessNode,
  GateNode,
  LifecycleProjection,
  ProviderNode,
  AgentNotifierHeartbeat,
  TaskDocNode,
  WorkspaceProjection,
} from "../../types/projection";
import type { ObserverEvent } from "../../types/event";
import { asServedProjection } from "../servedProjection";
import type { NoOverrides, Overrides } from "./overrides";
import snapshot from "../../fixtures/snapshot.json";

/** The sampled payload, read as the projection the server would have sent. */
export const SERVED: WorkspaceProjection = asServedProjection(snapshot);

/**
 * A row the snapshot is expected to carry. Throws rather than spreading `undefined`, so a snapshot
 * that stops sampling a node fails loudly here instead of producing a base that is quietly missing
 * every field.
 */
function demandServed<Node>(row: Node | undefined, what: string): Node {
  if (row === undefined) throw new Error(`snapshot.json no longer carries ${what}`);
  return row;
}

const SERVED_LIFECYCLE = demandServed(SERVED.lifecycles[0], "lifecycles[0]");
const SERVED_ENCLOSURE = demandServed(SERVED.enclosures[0], "enclosures[0]");
const SERVED_PROVIDER = demandServed(SERVED.providers[0], "providers[0]");
const SERVED_TASK_DOC = demandServed(SERVED.analytics.taskDocuments[0], "analytics.taskDocuments[0]");
const SERVED_ENGINE_PROCESS = demandServed(
  SERVED.analytics.engineProcesses[0],
  "analytics.engineProcesses[0]",
);
const SERVED_PICKUP = demandServed(SERVED.analytics.agentPickups[0], "analytics.agentPickups[0]");
const SERVED_ATTENTION = demandServed(SERVED.analytics.attentionQueue[0], "analytics.attentionQueue[0]");
const SERVED_GATE = demandServed(
  SERVED.lifecycles.find((entry) => entry.gate !== undefined)?.gate,
  "a lifecycle carrying a gate",
);

// ── the bases: required fields only, every value taken from the served payload ──

const BASE_LIFECYCLE: LifecycleProjection = {
  id: SERVED_LIFECYCLE.id,
  state: SERVED_LIFECYCLE.state,
  phase: SERVED_LIFECYCLE.phase,
  fleeting: SERVED_LIFECYCLE.fleeting,
  tokens: SERVED_LIFECYCLE.tokens,
  startedAt: SERVED_LIFECYCLE.startedAt,
  lastEventTs: SERVED_LIFECYCLE.lastEventTs,
  stateEnteredAt: SERVED_LIFECYCLE.stateEnteredAt,
  inferred: SERVED_LIFECYCLE.inferred,
  actions: [],
  tokenSeries: [],
};

const BASE_GATE: GateNode = {
  id: SERVED_GATE.id,
  kind: SERVED_GATE.kind,
  state: SERVED_GATE.state,
  evidenceRefs: [],
  decisions: SERVED_GATE.decisions,
  packet: {},
  ts: SERVED_GATE.ts,
};

const BASE_ENCLOSURE: EnclosureNode = {
  enclosure: SERVED_ENCLOSURE.enclosure,
  enclosureId: SERVED_ENCLOSURE.enclosureId,
  leafId: SERVED_ENCLOSURE.leafId,
  taskRoot: SERVED_ENCLOSURE.taskRoot,
  taskId: SERVED_ENCLOSURE.taskId,
  taskName: SERVED_ENCLOSURE.taskName,
  repoName: SERVED_ENCLOSURE.repoName,
  lifecycleId: SERVED_ENCLOSURE.lifecycleId,
  worktreeGroup: SERVED_ENCLOSURE.worktreeGroup,
  humanReviewStatus: SERVED_ENCLOSURE.humanReviewStatus,
  closeoutStatus: SERVED_ENCLOSURE.closeoutStatus,
  integrationStatus: SERVED_ENCLOSURE.integrationStatus,
  cleanup: SERVED_ENCLOSURE.cleanup,
  codeWorktreeExists: SERVED_ENCLOSURE.codeWorktreeExists,
  memoryWorktreeExists: SERVED_ENCLOSURE.memoryWorktreeExists,
  actions: [],
};

const BASE_PROVIDER: ProviderNode = {
  id: SERVED_PROVIDER.id,
  state: SERVED_PROVIDER.state,
  watcherUp: SERVED_PROVIDER.watcherUp,
  indexingState: SERVED_PROVIDER.indexingState,
  scope: SERVED_PROVIDER.scope,
};

const BASE_TASK_DOC: TaskDocNode = {
  id: SERVED_TASK_DOC.id,
  repository: SERVED_TASK_DOC.repository,
  title: SERVED_TASK_DOC.title,
  status: SERVED_TASK_DOC.status,
  kind: SERVED_TASK_DOC.kind,
  stepsDone: SERVED_TASK_DOC.stepsDone,
  stepsTotal: SERVED_TASK_DOC.stepsTotal,
  docPath: SERVED_TASK_DOC.docPath,
  bodyRevision: SERVED_TASK_DOC.bodyRevision,
  createdAt: SERVED_TASK_DOC.createdAt,
  steps: [],
  objective: SERVED_TASK_DOC.objective,
  requirements: [],
  codeExamples: [],
  decisions: [],
  openQuestions: [],
  references: [],
  subTasks: [],
  sections: [],
  orchestrates: [],
  executionWaves: [],
};

const BASE_ENGINE_PROCESS: EngineProcessNode = {
  id: SERVED_ENGINE_PROCESS.id,
  enclosure: SERVED_ENGINE_PROCESS.enclosure,
  worktreeGroup: SERVED_ENGINE_PROCESS.worktreeGroup,
  taskId: SERVED_ENGINE_PROCESS.taskId,
  leafId: SERVED_ENGINE_PROCESS.leafId,
  taskName: SERVED_ENGINE_PROCESS.taskName,
  repoName: SERVED_ENGINE_PROCESS.repoName,
  phase: SERVED_ENGINE_PROCESS.phase,
  health: SERVED_ENGINE_PROCESS.health,
  codeSource: SERVED_ENGINE_PROCESS.codeSource,
  codeWorktree: SERVED_ENGINE_PROCESS.codeWorktree,
  memoryMode: SERVED_ENGINE_PROCESS.memoryMode,
  ledgerRows: [],
  ledgerRowCount: 0,
  humanReviewStatus: SERVED_ENGINE_PROCESS.humanReviewStatus,
  closeoutStatus: SERVED_ENGINE_PROCESS.closeoutStatus,
  integrationStatus: SERVED_ENGINE_PROCESS.integrationStatus,
  cleanup: SERVED_ENGINE_PROCESS.cleanup,
  completedPhases: [],
  failedPhases: [],
  seedFallback: SERVED_ENGINE_PROCESS.seedFallback,
  providers: [],
  edges: [],
  landing: [],
  actions: [],
  summary: SERVED_ENGINE_PROCESS.summary,
  missingFacts: [],
  sourceFiles: [],
};

const BASE_PICKUP: AgentPickupNode = {
  id: SERVED_PICKUP.id,
  entryId: SERVED_PICKUP.entryId,
  messageKind: SERVED_PICKUP.messageKind,
  deliveryState: SERVED_PICKUP.deliveryState,
  state: SERVED_PICKUP.state,
  attemptCount: SERVED_PICKUP.attemptCount,
  ttlSeconds: SERVED_PICKUP.ttlSeconds,
};

const BASE_ATTENTION: AttentionItem = {
  id: SERVED_ATTENTION.id,
  kind: SERVED_ATTENTION.kind,
  severity: SERVED_ATTENTION.severity,
  lane: SERVED_ATTENTION.lane,
  title: SERVED_ATTENTION.title,
};

/**
 * An analytics bucket with every list empty. The reducer always sends every key (they are list
 * defaults server-side), so "empty" is a shape the server produces — unlike an object that omits
 * them, which is what a `{} as Analytics` fixture claimed.
 */
export const EMPTY_ANALYTICS: Analytics = {
  driftSnapshots: [],
  stalestSidecars: [],
  setupSummaries: [],
  setupProgress: [],
  routeCoverage: [],
  toolReports: [],
  agentPickups: [],
  expectationRows: [],
  ledgers: [],
  taskDocuments: [],
  series: [],
  attentionQueue: [],
  engineProcesses: [],
};

// ── the builders ─────────────────────────────────────────────────────────────

export function lifecycle<O extends Partial<LifecycleProjection> = NoOverrides>(
  overrides?: Overrides<O, LifecycleProjection>,
): LifecycleProjection {
  const over: Partial<LifecycleProjection> = overrides ?? {};
  return { ...BASE_LIFECYCLE, ...over };
}

export function gate<O extends Partial<GateNode> = NoOverrides>(
  overrides?: Overrides<O, GateNode>,
): GateNode {
  const over: Partial<GateNode> = overrides ?? {};
  return { ...BASE_GATE, ...over };
}

/** A lifecycle holding an open gate — the shape every gate/interaction test needs. */
export function lifecycleWithGate<
  O extends Partial<LifecycleProjection> = NoOverrides,
  G extends Partial<GateNode> = NoOverrides,
>(
  overrides?: Overrides<O, LifecycleProjection>,
  gateOverrides?: Overrides<G, GateNode>,
): LifecycleProjection {
  const over: Partial<LifecycleProjection> = overrides ?? {};
  const gateOver: Partial<GateNode> = gateOverrides ?? {};
  return { ...BASE_LIFECYCLE, gate: gate(gateOver), ...over };
}

export function enclosure<O extends Partial<EnclosureNode> = NoOverrides>(
  overrides?: Overrides<O, EnclosureNode>,
): EnclosureNode {
  const over: Partial<EnclosureNode> = overrides ?? {};
  return { ...BASE_ENCLOSURE, ...over };
}

export function provider<O extends Partial<ProviderNode> = NoOverrides>(
  overrides?: Overrides<O, ProviderNode>,
): ProviderNode {
  const over: Partial<ProviderNode> = overrides ?? {};
  return { ...BASE_PROVIDER, ...over };
}

export function taskDoc<O extends Partial<TaskDocNode> = NoOverrides>(
  overrides?: Overrides<O, TaskDocNode>,
): TaskDocNode {
  const over: Partial<TaskDocNode> = overrides ?? {};
  return { ...BASE_TASK_DOC, ...over };
}

export function engineProcess<O extends Partial<EngineProcessNode> = NoOverrides>(
  overrides?: Overrides<O, EngineProcessNode>,
): EngineProcessNode {
  const over: Partial<EngineProcessNode> = overrides ?? {};
  return { ...BASE_ENGINE_PROCESS, ...over };
}

export function agentPickup<O extends Partial<AgentPickupNode> = NoOverrides>(
  overrides?: Overrides<O, AgentPickupNode>,
): AgentPickupNode {
  const over: Partial<AgentPickupNode> = overrides ?? {};
  return { ...BASE_PICKUP, ...over };
}

export function attentionItem<O extends Partial<AttentionItem> = NoOverrides>(
  overrides?: Overrides<O, AttentionItem>,
): AttentionItem {
  const over: Partial<AttentionItem> = overrides ?? {};
  return { ...BASE_ATTENTION, ...over };
}

export function action<
  O extends Partial<ActionAvailability> & Pick<ActionAvailability, "action">,
>(overrides: Overrides<O, ActionAvailability>): ActionAvailability {
  const over: Partial<ActionAvailability> & Pick<ActionAvailability, "action"> = overrides;
  return { enabled: true, ...over };
}

export function analytics<O extends Partial<Analytics> = NoOverrides>(
  overrides?: Overrides<O, Analytics>,
): Analytics {
  const over: Partial<Analytics> = overrides ?? {};
  return { ...EMPTY_ANALYTICS, ...over };
}

/**
 * A whole projection. `metrics` is DERIVED from the lifecycles by the mirror's own rollup rather
 * than restated beside them — the hand-kept bucket lists are where the `awaiting-developer` gap
 * kept reappearing, on both sides of the wire.
 */
export function projection<O extends Partial<WorkspaceProjection> = NoOverrides>(
  overrides?: Overrides<O, WorkspaceProjection>,
): WorkspaceProjection {
  const over: Partial<WorkspaceProjection> = overrides ?? {};
  const { lifecycles = [], analytics: analyticsOver, metrics, ...rest } = over;
  return {
    version: SERVED.version,
    generatedAt: SERVED.generatedAt,
    enclosures: [],
    providers: [],
    activeWorktreeGroups: [],
    ...rest,
    lifecycles,
    metrics: metrics ?? metricsFor(lifecycles),
    analytics: analytics(analyticsOver),
  };
}

/**
 * The app-injected agent-notifier tick. It is NOT reducer truth and so is absent from the snapshot
 * (`contract.test.ts::KnownUnsampled` names it); the base is a typed literal, which is still
 * checked against the mirror in both directions.
 */
export function agentNotifierHeartbeat<O extends Partial<AgentNotifierHeartbeat> = NoOverrides>(
  overrides?: Overrides<O, AgentNotifierHeartbeat>,
): AgentNotifierHeartbeat {
  const over: Partial<AgentNotifierHeartbeat> = overrides ?? {};
  return {
    lastTickAt: "2026-07-08T00:00:00+00:00",
    ageSeconds: 1,
    staleCutoffSeconds: 30,
    stale: false,
    pendingInboxCount: 0,
    redeliverableInboxCount: 0,
    lastSweepDurationSeconds: 0.2,
    ...over,
  };
}

/**
 * An observer-event envelope (`types/event.ts` ← `observer/events.py`). The event channel is a
 * separate contract from the projection, so the base cannot come from `snapshot.json`; it is a
 * typed literal, which the mirror still checks in both directions.
 */
export function observerEvent<O extends Partial<ObserverEvent> & Pick<ObserverEvent, "kind">>(
  overrides: Overrides<O, ObserverEvent>,
): ObserverEvent {
  const over: Partial<ObserverEvent> & Pick<ObserverEvent, "kind"> = overrides;
  return {
    schema: "ar-observer-event/v1",
    id: `evt-${over.kind}`,
    ts: SERVED.generatedAt,
    trust: "observed",
    actor: "system",
    ...over,
  };
}

/**
 * A byte-fresh copy of a projection — a new object graph, which is what the store's change-gate
 * tests need in order to prove that a re-sent snapshot costs no write and no re-render.
 *
 * `structuredClone` rather than `JSON.parse(JSON.stringify(…))` on purpose. The round-trip answers
 * `any`, and `any` assigns to anything: routing it through `asServedProjection` LOOKS like a check
 * but is vacuous, because a parameter type cannot narrow an argument that is already `any`. The
 * clone keeps the source's type honestly, so there is nothing to re-check and nothing to assert.
 */
export function reparsed(source: WorkspaceProjection): WorkspaceProjection {
  return structuredClone(source);
}

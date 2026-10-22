import { describe, expect, it } from 'vitest';

import {
  ATTENTION_LANES,
  ATTENTION_SEVERITIES,
  PROCESS_FACT_STATES,
  PROCESS_HEALTHS,
} from '../types/projection';
import type { EngineProcessEdge, WorkspaceProjection } from '../types/projection';
import { asServedProjection } from './servedProjection';
import snapshot from '../fixtures/snapshot.json';

// The fixture-coverage guard. `types/projection.ts` is generated from the Pydantic schema, and the
// Python codegen tests plus `scripts/sync-projection-types.py --check` enforce that producer-to-
// mirror link. `snapshot.json` remains a hand-authored sampled payload: this file measures the
// generated contract against that independent sample so required fields and runtime vocabulary
// coverage cannot disappear from dashboard fixtures unnoticed.
//
// It has to bite in BOTH directions, and it does so at three seams:
//
//   mirror ⊇ served — the server grows a field (or a bucket, or a vocabulary member) and the
//     hand-kept mirror does not. THIS IS THE DIRECTION THAT HAPPENS. It was unguarded: the
//     fixture was consumed as `snapshot as unknown as WorkspaceProjection`, a double cast that
//     turns off assignability, excess-property checking and everything else, so a served field
//     the mirror had never heard of typechecked and passed. It is how the mirror's `State` union
//     stayed five members long while the server declared six, and how `Metrics` bucketed three
//     states out of six. `mirrorMustDeclare` below is that direction, and it names the path.
//
//   served ⊇ mirror — the mirror declares something the server never sends, so a renderer
//     branch ships permanently dead. Guarded by passing the fixture through `asServedProjection`
//     (every required mirror field must be present in the payload) plus the `@ts-expect-error`
//     pins at the bottom for the two fields that actually did this.
//
//   fixture ⊇ mirror — THE ORACLE ITSELF. Both directions above are measured against a hand-kept
//     payload, so a field the fixture does not exercise is a field neither direction can see. A
//     reviewer proved the cost: deleting two sampled fields and emptying two arrays of nodes
//     produced zero new `tsc` errors and a green suite. An empty array is worse than a missing
//     field — it is blindness in BOTH directions at once, because `AsJsonModule` accepts `never[]`
//     as assignable to anything and `ServedOnlyPaths<never, …>` is `never`, so a *required* field
//     added to the mirror's element type was not reported either. `fixtureMustSample` below
//     closes that: it walks the mirror against the
//     fixture's OWN type (`typeof snapshot` — the JSON module's exact shape, empty arrays and
//     all) and names every declared path the payload does not reach.
//
// All three are TYPE-level: free at runtime, enforced by `npm run typecheck` (`tsc -b`). The
// runtime assertions below cover what types cannot — the string VOCABULARIES, which a JSON module
// import widens to `string` (see `AsJsonModule`) and which therefore no type can check here. The
// fixture must reach every registered path and every sampled value must be legal; exhaustive
// producer vocabulary belongs to generated schema/codegen rather than to one representative payload.
//
// WHAT SCHEMA CODEGEN CLOSES. The generated contract reaches two forms of producer drift that a
// sample cannot, even a sample this file polices:
//   (1) a server field that is `T | None` and currently null is *omitted* by `exclude_none=True`,
//       so no sampled payload can reveal it — only the schema can;
//   (2) a vocabulary member the server declares and no sample happens to carry. The generated
//       schema and stale-output check own that exhaustive proof; requiring this representative
//       payload to instantiate every member would duplicate the authority and amplify fixtures;
//   (3) the difference between two field-identical models. The mirror now declares
//       `SeriesSectionNode` separately from `TaskSectionNode`, matching the server's two
//       `extra="forbid"` models — but they declare the same three fields, so TypeScript's
//       structural typing keeps them interchangeable and no walk over any payload can tell them
//       apart. It is the `TaskSubTaskRefNode` / `SeriesSubTaskNode` collapse this file pins at the
//       bottom, standing one model over; generation still emits both named model declarations.

const served = asServedProjection(snapshot);

// ── the shared vocabulary of a type-level path walk ──────────────────────────
// A leaf: recursion stops here. `undefined` is in the set because an optional mirror field is
// `T | undefined`, and `null` because the wire form of a nullable one is null.
type JsonScalar = string | number | boolean | null | undefined;

type Keys<T> = Extract<keyof T, string>;

// True for an object the mirror types with a string index signature. Such a node absorbs any key
// the server puts in it, so no walk can descend into it — see `INDEX_SIGNATURE_SITES`.
type Absorbs<T> = string extends keyof T ? true : false;

// ── direction `mirror ⊇ served` ──────────────────────────────────────────────
// Every dotted path the SERVED payload carries that the MIRROR does not declare. Recurses
// through objects and arrays; an index signature on the mirror side absorbs any key, which is
// correct — the server really can put anything there. A mirror field the payload types
// differently is not this check's business; `asServedProjection` above is.
type ServedOnlyPaths<Served, Mirror, Path extends string> = Served extends JsonScalar
  ? never
  : Served extends readonly (infer ServedItem)[]
    ? NonNullable<Mirror> extends readonly (infer MirrorItem)[]
      ? ServedOnlyPaths<ServedItem, MirrorItem, `${Path}[]`>
      : never
    : Absorbs<NonNullable<Mirror>> extends true
      ? never
      : NonNullable<Mirror> extends object
        ? | UndeclaredPaths<Served, NonNullable<Mirror>, Path>
          | SharedFieldPaths<Served, NonNullable<Mirror>, Path>
        : never;

type UndeclaredKeys<Served, Mirror> = Exclude<Keys<Served>, keyof Mirror>;

type UndeclaredPaths<Served, Mirror, Path extends string> = {
  [K in UndeclaredKeys<Served, Mirror>]: `${Path}.${K}`;
}[UndeclaredKeys<Served, Mirror>];

type SharedKeys<Served, Mirror> = Extract<Keys<Served>, keyof Mirror>;

type SharedFieldPaths<Served, Mirror, Path extends string> = {
  [K in SharedKeys<Served, Mirror>]: ServedOnlyPaths<
    Served[K],
    K extends keyof Mirror ? Mirror[K] : never,
    `${Path}.${K}`
  >;
}[SharedKeys<Served, Mirror>];

/**
 * Fails `tsc -b` when the mirror does not declare something the server sends, naming the path:
 * `Type '"projection.metrics.awaitingReviewCount"' does not satisfy the constraint 'never'.`
 * With several fields missing at once TypeScript prints the deferred type rather than the list;
 * declaring the ones you know about narrows it back down to names.
 */
function mirrorMustDeclare<ServedOnly extends never>(...undeclaredPaths: ServedOnly[]): void {
  expect(undeclaredPaths).toEqual([]);
}

// ── direction `fixture ⊇ mirror`: the oracle is guarded too ──────────────────
// Every dotted path the MIRROR declares that the FIXTURE does not reach. This is the dual of
// `ServedOnlyPaths` and it reads the fixture's own JSON-module type, so it sees exactly what the
// file contains: an absent key, an absent optional node, and — the case that made the fixture
// silently useless — an EMPTY ARRAY, whose element type is `never` and whose contents therefore
// satisfy every check ever written about them.
//
// A path is reported once, at the highest level that is missing: deleting an enclosure's
// `lifecycleOperation` reports `projection.enclosures[].lifecycleOperation`, not its children;
// emptying `toolReports` reports `projection.analytics.toolReports[]`. Arrays are keyed by TYPE,
// not by element, so one sidecar carrying `ageSeconds` samples it for all of them — the
// fixture does not have to be uniform, it has to be COMPLETE between them.
// The scalar test comes FIRST and the empty-array test second, in that order on purpose: an empty
// array of strings samples nothing and hides nothing (there is no structure inside a string to
// walk), while an empty array of OBJECTS hides a whole model. Reversing them would demand a
// `failedPhases` entry — noise — and say nothing more.
type MirrorOnlyPaths<Mirror, Served, Path extends string> = [NonNullable<Mirror>] extends [
  JsonScalar,
]
  ? never
  : [Served] extends [never]
    ? Path
    : [NonNullable<Mirror>] extends [readonly (infer MirrorItem)[]]
      ? [Served] extends [readonly (infer ServedItem)[]]
        ? MirrorOnlyPaths<MirrorItem, ServedItem, `${Path}[]`>
        : Path
      : Absorbs<NonNullable<Mirror>> extends true
        ? never
        : [NonNullable<Mirror>] extends [object]
          ? MirrorFieldPaths<NonNullable<Mirror>, Served, Path>
          : never;

// The keys any member of the sampled union carries, and the values only those members contribute.
// Distributive on purpose: the sample is a union of two element shapes and a field present in
// either one is sampled.
type SampledKeys<Served> = Served extends unknown ? Keys<Served> : never;
type SampledValue<Served, K extends string> = Served extends Record<K, infer Value> ? Value : never;

type MirrorFieldPaths<Mirror, Served, Path extends string> = {
  [K in Keys<Mirror>]-?: K extends SampledKeys<Served>
    ? MirrorOnlyPaths<Mirror[K], SampledValue<Served, K>, `${Path}.${K}`>
    : `${Path}.${K}`;
}[Keys<Mirror>];

type UnsampledMirrorPaths = MirrorOnlyPaths<WorkspaceProjection, typeof snapshot, 'projection'>;

// THE RESIDUE, NAMED. Every mirror path this fixture does not exercise, and why. It is meant to
// stay this short; an entry added here is a field no assertion in this file can see, so it is a
// hole being accepted rather than a formality. Both directions are checked below, so an entry
// that becomes sampled — or that names a path the mirror no longer declares — fails too.
//
// It is injected by the serving app at RESPONSE time (`serving/build_info.py`), not by the
// reducer, and is absent from the persisted `latest-state.json` this fixture is shaped like.
type KnownUnsampled =
  | 'projection.servingBuild'
  // MIK-R190 rule 13: present only while a provider limit holds a task. The recorded fixture was
  // captured with no limited task, so no assertion in this file can see it yet.
  | 'projection.tasks[].providerLimit';

/** Fails `tsc -b` naming every mirror path the fixture stopped sampling. */
function fixtureMustSample<Unsampled extends never>(...unsampledPaths: Unsampled[]): void {
  expect(unsampledPaths).toEqual([]);
}

/** Fails `tsc -b` naming an allowlist entry the fixture now samples (or the mirror dropped). */
function allowlistMustStayEarned<Stale extends never>(...spentEntries: Stale[]): void {
  expect(spentEntries).toEqual([]);
}

// ── the walls of the walk, named rather than described ───────────────────────
// Every path where the mirror types a node with a string index signature. Such a node absorbs any
// key, so `ServedOnlyPaths` cannot report anything inside it and `MirrorOnlyPaths` cannot require
// anything inside it — whatever the server puts there is unwalkable by construction. This used to
// be a prose list of four in a comment; it was actually seven, and one of the three it missed
// was added by the same change that wrote the comment. `Record<AbsorbingPaths<…>, string>` is
// exhaustive and closed, so the list cannot be short again and cannot list a site that has gone.
type AbsorbingPaths<Mirror, Path extends string> = [NonNullable<Mirror>] extends [JsonScalar]
  ? never
  : [NonNullable<Mirror>] extends [readonly (infer Item)[]]
    ? AbsorbingPaths<Item, `${Path}[]`>
    : Absorbs<NonNullable<Mirror>> extends true
      ? Path
      : [NonNullable<Mirror>] extends [object]
        ? {
            [K in Keys<NonNullable<Mirror>>]-?: AbsorbingPaths<
              NonNullable<Mirror>[K],
              `${Path}.${K}`
            >;
          }[Keys<NonNullable<Mirror>>]
        : never;

const INDEX_SIGNATURE_SITES: Record<AbsorbingPaths<WorkspaceProjection, 'projection'>, string> = {
  'projection.analytics.driftSnapshots[].counts': 'drift categories come from the drift report',
  'projection.analytics.setupSummaries[].resultCounts': 'result buckets come from the setup action',
  'projection.analytics.engineProcesses[].retryArgs': "the retry call's own kwargs",
  'projection.analytics.engineProcesses[].sourceLineage.recoveries[].args':
    "the contract-addressed worktree_sync call's own kwargs",
  'projection.tasks[].summary.discardedSubTasks[].proof.childJson':
    'the task projection preserves the opaque JSON source proof',
  'projection.tasks[].summary.discardedSubTasks[].proof.childMarkdown':
    'the task projection preserves the opaque Markdown source proof',
  'projection.enclosures[].lifecycleOperation.result':
    'the lifecycle operation result is the underlying closeout or integration payload',
  'projection.enclosures[].lifecycleOperation.legalControls[]':
    'each advertised operation control is an opaque tool invocation payload',
  'projection.enclosures[].lifecycleOperation.recommendedAction.arguments':
    "the recommended action's task-addressed invocation arguments",
  'projection.tasks[].worktrees[].lifecycleOperation.result':
    'the lifecycle operation result is the underlying closeout or integration payload',
  'projection.tasks[].worktrees[].lifecycleOperation.legalControls[]':
    'each advertised operation control is an opaque tool invocation payload',
  'projection.tasks[].worktrees[].lifecycleOperation.recommendedAction.arguments':
    "the recommended action's task-addressed invocation arguments",
};

// ── the string vocabularies ──────────────────────────────────────────────────
// A closed string union in the mirror is a claim that the server sends nothing else. Every one of
// them is a bare `str` in `projection.py`, so the claim is NARROWER than the server by
// construction and nothing type-level can notice: the JSON import widens the payload's literals to
// `string`, so a served `severity: "critical"` assigns to `"alarm" | "warn" | "info"` in silence.
// A runtime membership check is the only thing that bites — and it used to cover two of the six
// vocabularies (`state`, `phase`), hand-written, with the other four unguarded: `severity`, `lane`,
// `EngineProcessNode.health`, and `factState` (which alone reaches six of the eleven paths below —
// the commit refs, the boot providers and the landing arc).
//
// So the registry is derived instead of listed. `ClosedUnionPaths` finds every path in the mirror
// whose type is a literal union, and `Record<…>` over it is exhaustive AND closed: a new closed
// union anywhere in the mirror fails `tsc -b` until it is bound to a vocabulary here, and a
// vocabulary bound to a path that has opened up to `string` fails too.
type ClosedStringUnion<T> = [NonNullable<T>] extends [string]
  ? string extends NonNullable<T>
    ? false
    : true
  : false;

type ClosedUnionPaths<Mirror, Path extends string> =
  ClosedStringUnion<Mirror> extends true
    ? Path
    : [NonNullable<Mirror>] extends [JsonScalar]
      ? never
      : [NonNullable<Mirror>] extends [readonly (infer Item)[]]
        ? ClosedUnionPaths<Item, `${Path}[]`>
        : Absorbs<NonNullable<Mirror>> extends true
          ? never
          : [NonNullable<Mirror>] extends [object]
            ? {
                [K in Keys<NonNullable<Mirror>>]-?: ClosedUnionPaths<
                  NonNullable<Mirror>[K],
                  `${Path}.${K}`
                >;
              }[Keys<NonNullable<Mirror>>]
            : never;

const VOCABULARIES: Record<
  ClosedUnionPaths<WorkspaceProjection, 'projection'>,
  readonly string[]
> = {
  'projection.tasks[].stage': ['planned', 'started', 'aborted', 'completed'],
  'projection.tasks[].activity': ['works', 'paused', 'unknown'],
  'projection.tasks[].disagreements[].kind': [
    'names-another-master',
    'not-listed',
    'listed-twice',
    'unresolvable-master',
  ],
  'projection.analytics.attentionQueue[].severity': ATTENTION_SEVERITIES,
  'projection.analytics.attentionQueue[].lane': ATTENTION_LANES,
  'projection.analytics.engineProcesses[].health': PROCESS_HEALTHS,
  'projection.analytics.engineProcesses[].codeSource.factState': PROCESS_FACT_STATES,
  'projection.analytics.engineProcesses[].codeWorktree.factState': PROCESS_FACT_STATES,
  'projection.analytics.engineProcesses[].memorySource.factState': PROCESS_FACT_STATES,
  'projection.analytics.engineProcesses[].memoryWorktree.factState': PROCESS_FACT_STATES,
  'projection.analytics.engineProcesses[].providers[].factState': PROCESS_FACT_STATES,
  'projection.analytics.engineProcesses[].landing[].factState': PROCESS_FACT_STATES,
  'projection.analytics.engineProcesses[].sourceLineage.state': [
    'current',
    'blocked',
    'unavailable',
  ],
  'projection.analytics.engineProcesses[].sourceLineage.edges[].state': [
    'current',
    'behind',
    'diverged',
    'unavailable',
  ],
  'projection.analytics.engineProcesses[].sourceLineage.edges[].side': ['code', 'memory'],
  'projection.analytics.engineProcesses[].sourceLineage.edges[].relation': [
    'super-to-master',
    'master-to-leaf',
    'super-to-leaf',
  ],
  'projection.analytics.engineProcesses[].sourceLineage.recoveries[].tool': ['worktree_sync'],
  'projection.tasks[].summary.steps[].disposition.kind': ['intentionalSkip'],
  'projection.tasks[].summary.steps[].disposition.recordedVia': ['task_doc.skip_step'],
  'projection.tasks[].summary.steps[].substeps[].disposition.kind': ['intentionalSkip'],
  'projection.tasks[].summary.steps[].substeps[].disposition.recordedVia': ['task_doc.skip_step'],
  'projection.tasks[].summary.executionNature': ['organizational', 'atomic'],
  'projection.tasks[].summary.form': ['sprint', 'master', 'task'],
  'projection.tasks[].summary.executionGraphView.nodes[].kind': ['lump', 'segment'],
  'projection.tasks[].summary.executionGraphView.nodes[].frontierState': [
    'landed',
    'ready',
    'waiting',
    'in-flight',
  ],
  'projection.enclosures[].lifecycleOperation.kind': ['closeout', 'integrate', 'direct-landing'],
  'projection.enclosures[].lifecycleOperation.schemaVersion': ['lifecycle-operation-projection/v1'],
  'projection.enclosures[].lifecycleOperation.stateMatrixVersion': [
    'lifecycle-operation-state-matrix/v1',
  ],
  'projection.enclosures[].lifecycleOperation.status': [
    'queued',
    'running',
    'input-required',
    'termination-required',
    'completed',
    'failed',
    'cancelled',
    'unreadable',
    'incoherent',
  ],
  'projection.enclosures[].lifecycleOperation.identity.operationKind': [
    'closeout',
    'integrate',
    'direct-landing',
  ],
  'projection.enclosures[].lifecycleOperation.worker.state': [
    'live',
    'termination-requested',
    'termination-required',
    'exited',
  ],
  'projection.enclosures[].lifecycleOperation.approval.state': ['claimed', 'unclaimed'],
  'projection.enclosures[].lifecycleOperation.phase': [
    'queued',
    'preflight',
    'memory-preflight',
    'quality',
    'approval-claim',
    'recovering-after-claim',
    'code-commit',
    'memory-refresh',
    'memory-commit',
    'integration-replay',
    'integration-quality',
    'source-merge',
    'contract-finalization',
    'door-publication',
    'termination-required',
    'direct-preflight',
    'direct-memory-commit',
    'direct-terminal-publication',
    'completed',
    'failed',
    'cancelled',
  ],
  'projection.enclosures[].lifecycleOperation.taskIntent.schema': ['task-intent/v1'],
  'projection.enclosures[].lifecycleOperation.projectionEffects[].invalidation.outcome': [
    'persisted-empty',
    'already-empty',
    'recovered-malformed',
    'would-recover-malformed',
    'would-persist-empty',
    'failed',
  ],
  'projection.enclosures[].lifecycleOperation.projectionEffects[].invalidation.diagnostic.kind': [
    'task',
    'door',
    'series',
    'projection',
  ],
  'projection.enclosures[].lifecycleOperation.projectionEffects[].invalidation.diagnostic.state': [
    'missing',
    'unreadable',
    'invalid',
  ],
  'projection.enclosures[].lifecycleOperation.projectionEffects[].rebuild.outcome': [
    'published',
    'already-current',
    'source-changed',
    'source-unreadable',
    'would-publish',
    'not-attempted',
  ],
  'projection.enclosures[].lifecycleOperation.projectionEffects[].rebuild.sourceClassification': [
    'active',
    'terminal',
  ],
  'projection.enclosures[].lifecycleOperation.projectionEffects[].rebuild.sourceProblems[].kind': [
    'task',
    'door',
    'series',
    'projection',
  ],
  'projection.enclosures[].lifecycleOperation.projectionEffects[].rebuild.sourceProblems[].state': [
    'missing',
    'unreadable',
    'invalid',
  ],
  'projection.tasks[].worktrees[].lifecycleOperation.kind': [
    'closeout',
    'integrate',
    'direct-landing',
  ],
  'projection.tasks[].worktrees[].lifecycleOperation.schemaVersion': [
    'lifecycle-operation-projection/v1',
  ],
  'projection.tasks[].worktrees[].lifecycleOperation.stateMatrixVersion': [
    'lifecycle-operation-state-matrix/v1',
  ],
  'projection.tasks[].worktrees[].lifecycleOperation.status': [
    'queued',
    'running',
    'input-required',
    'termination-required',
    'completed',
    'failed',
    'cancelled',
    'unreadable',
    'incoherent',
  ],
  'projection.tasks[].worktrees[].lifecycleOperation.identity.operationKind': [
    'closeout',
    'integrate',
    'direct-landing',
  ],
  'projection.tasks[].worktrees[].lifecycleOperation.worker.state': [
    'live',
    'termination-requested',
    'termination-required',
    'exited',
  ],
  'projection.tasks[].worktrees[].lifecycleOperation.approval.state': ['claimed', 'unclaimed'],
  'projection.tasks[].worktrees[].lifecycleOperation.phase': [
    'queued',
    'preflight',
    'memory-preflight',
    'quality',
    'approval-claim',
    'recovering-after-claim',
    'code-commit',
    'memory-refresh',
    'memory-commit',
    'integration-replay',
    'integration-quality',
    'source-merge',
    'contract-finalization',
    'door-publication',
    'termination-required',
    'direct-preflight',
    'direct-memory-commit',
    'direct-terminal-publication',
    'completed',
    'failed',
    'cancelled',
  ],
  'projection.tasks[].worktrees[].lifecycleOperation.taskIntent.schema': ['task-intent/v1'],
  'projection.tasks[].worktrees[].lifecycleOperation.projectionEffects[].invalidation.outcome': [
    'persisted-empty',
    'already-empty',
    'recovered-malformed',
    'would-recover-malformed',
    'would-persist-empty',
    'failed',
  ],
  'projection.tasks[].worktrees[].lifecycleOperation.projectionEffects[].invalidation.diagnostic.kind':
    ['task', 'door', 'series', 'projection'],
  'projection.tasks[].worktrees[].lifecycleOperation.projectionEffects[].invalidation.diagnostic.state':
    ['missing', 'unreadable', 'invalid'],
  'projection.tasks[].worktrees[].lifecycleOperation.projectionEffects[].rebuild.outcome': [
    'published',
    'already-current',
    'source-changed',
    'source-unreadable',
    'would-publish',
    'not-attempted',
  ],
  'projection.tasks[].worktrees[].lifecycleOperation.projectionEffects[].rebuild.sourceClassification':
    ['active', 'terminal'],
  'projection.tasks[].worktrees[].lifecycleOperation.projectionEffects[].rebuild.sourceProblems[].kind':
    ['task', 'door', 'series', 'projection'],
  'projection.tasks[].worktrees[].lifecycleOperation.projectionEffects[].rebuild.sourceProblems[].state':
    ['missing', 'unreadable', 'invalid'],
};

// Reads every value the payload carries at one of the registry's dotted paths. `[]` fans out over
// an array, so one path yields one value per element — `…engineProcesses[].health` returns the
// health of every process, not the first.
function valuesAt(root: unknown, path: string): unknown[] {
  let frontier: unknown[] = [root];
  for (const [, key] of path.matchAll(/\.([A-Za-z0-9_]+)|(\[\])/g)) {
    const next: unknown[] = [];
    for (const node of frontier) {
      if (node == null) continue;
      if (key === undefined) {
        if (Array.isArray(node)) next.push(...node);
      } else if (typeof node === 'object' && key in node) {
        next.push((node as Record<string, unknown>)[key]);
      }
    }
    frontier = next;
  }
  return frontier;
}

describe('the mirror declares everything the server sends', () => {
  it('has no served field the mirror is missing', () => {
    mirrorMustDeclare<ServedOnlyPaths<typeof snapshot, WorkspaceProjection, 'projection'>>();
  });

  it('carries exactly the metrics fields the mirror models, and no others', () => {
    expect(Object.keys(served.metrics).sort()).toEqual(['ignoredHostAgents']);
  });
});

describe('the fixture samples everything the mirror declares', () => {
  it('reaches every declared path except the named residue', () => {
    // Fails `tsc -b` naming the path, e.g.
    // `Type '"projection.tasks[].summary.ageSeconds"' does not satisfy the constraint 'never'.`
    fixtureMustSample<Exclude<UnsampledMirrorPaths, KnownUnsampled>>();
  });

  it('keeps the residue list spent — no entry the fixture now samples, none the mirror dropped', () => {
    allowlistMustStayEarned<Exclude<KnownUnsampled, UnsampledMirrorPaths>>();
  });

  it('reaches every node the mirror leaves unwalkable, and says why each one is opaque', () => {
    // The registry's KEYS are exhaustive at the type level. What types cannot say is that the
    // fixture actually carries these nodes: `MirrorOnlyPaths` deliberately stops at an index
    // signature, so an absorbing node the payload omits is the one kind of unsampled path it
    // cannot report. This is that check, and it is why the reason strings are required — a wall
    // in the walk that nobody can see needs a name and a sentence, not a silent gap.
    for (const [path, reason] of Object.entries(INDEX_SIGNATURE_SITES)) {
      expect(valuesAt(snapshot, path).length, `no served value at ${path}`).toBeGreaterThan(0);
      expect(reason.length, `no reason given for ${path}`).toBeGreaterThan(0);
    }
  });
});

describe('every closed vocabulary in the mirror is checked against the payload', () => {
  it("carries only values the mirror's vocabulary declares, at every registered path", () => {
    for (const [path, vocabulary] of Object.entries(VOCABULARIES)) {
      const sampled = valuesAt(snapshot, path);
      // A path the payload never reaches makes its vocabulary check vacuous. `fixtureMustSample`
      // fails on that too, but at the type level and under a different name — say it here as well
      // so a vacuous check reads as a failure of THIS assertion rather than as a pass.
      expect(sampled.length, `no served value at ${path}`).toBeGreaterThan(0);
      for (const value of sampled) expect(vocabulary, `at ${path}`).toContain(value);
    }
  });
});

describe('projection contract fixture', () => {
  it('has the top-level projection shape', () => {
    expect(typeof served.version).toBe('number');
    expect(typeof served.generatedAt).toBe('string');
    expect(Array.isArray(served.tasks)).toBe(true);
    expect(served.taskCounts).toBeTypeOf('object');
    expect(Array.isArray(served.enclosures)).toBe(true);
    expect(Array.isArray(served.providers)).toBe(true);
    expect(Array.isArray(served.activeWorktreeGroups)).toBe(true);
    expect(served.metrics).toBeTypeOf('object');
    expect(served.analytics).toBeTypeOf('object');
  });

  it('carries identity and status once per task entry, with reader content separate', () => {
    expect(served.tasks.length).toBeGreaterThan(0);
    for (const entry of served.tasks) {
      expect(entry.key).toBeTypeOf('string');
      if (entry.reference) {
        expect(entry.reference.repository).toBeTypeOf('string');
        expect(entry.reference.path).toMatch(/\.json$/);
        expect(entry.key).toBe(`${entry.reference.repository}/${entry.reference.path}`);
      } else {
        expect(entry.summary).toBeUndefined();
      }
      expect(entry.id).toBeTypeOf('string');
      expect(entry.title).toBeTypeOf('string');
      if (entry.summary) {
        for (const duplicate of [
          'id',
          'title',
          'repository',
          'docPath',
          'status',
          'subTasks',
          'stepsDone',
          'stepsTotal',
        ]) {
          expect(duplicate in entry.summary).toBe(false);
        }
      }
    }
  });
});

// Fields the mirror invented — ones no Python model declares, on models that are `extra="forbid"`
// server-side, so the server could never send them. Each one grew a renderer branch that shipped
// permanently dead. These are TYPE-level assertions: they are free at runtime but fail `tsc -b`
// (the typecheck gate) the moment a field comes back, because an UNUSED `@ts-expect-error` is
// itself a compile error.
describe('mirror does not invent fields the server cannot send', () => {
  it('keeps engine-process edges free of a carried refusal polarity', () => {
    // `refusedPolarity` existed on no Python model. Its renderer branch was gated on the edge
    // state `refused`, which the reducer never produces either (`_materialize_edge_state`,
    // `_seed_edge_state`, and the inline literals emit only complete/planned/blocked/skipped/
    // unknown/failed/running/stale). Flash polarity is derived from the state instead.
    const edge: EngineProcessEdge = {
      id: 'cgc-seed',
      fromNode: 'code-worktree',
      toNode: 'cgc-engine',
      kind: 'cgc-seed',
      state: 'stale',
      label: 'CGC seed',
    };
    // @ts-expect-error polarity is derived from `state`, never carried on the edge
    void edge.refusedPolarity;
    expect(edge.state).toBe('stale');
  });
});

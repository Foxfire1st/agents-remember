import type {
  AgentPickupNode,
  GateNode,
  LifecycleProjection,
  TaskDocNode,
} from '../types/projection';
import { sessionHasPendingInteraction, sessionSeatRole, type OpenSession } from './sessions';
import { seatVisualState } from './stateGrammar';
import { reviewerParentMatches } from './reviewerContext';
import { isOrchestrationDoc, masterCommandNames, pathDir } from './taskHierarchy';
import { qualifiedLeafKey, taskDocumentRefForDoc } from './taskIdentity';
import type { TaskDocumentRef } from '../types/terminalCatalog';

// The default chat hierarchy is a projection of canonical task-document altitude + role. Spawn
// ancestry is provenance only and is available through buildSpawnTree below. No runtime id, label,
// or synthetic "logical agent id" participates in hierarchy or routing identity.

/** Three-letter role codes (RULED): ARC/ORC/MGR/WKR/REV/CUR; other known roles keep the pattern. */
const ROLE_CODES: Record<string, string> = {
  architect: 'ARC',
  orchestrator: 'ORC',
  manager: 'MGR',
  worker: 'WKR',
  reviewer: 'REV',
  curator: 'CUR',
  strategist: 'STR',
  designer: 'DSG',
  'system-specialist': 'SYS',
};

/** The rail chip code for a seat's spawn role — absent when the seat has no spawn role (R6). */
export function roleCode(
  session: Pick<OpenSession, 'kind' | 'seatRole' | 'spawnRole'>,
): string | undefined {
  const role = session.spawnRole ?? session.seatRole;
  if (!role || role === 'chat' || role === 'terminal') return undefined;
  return ROLE_CODES[role] ?? role.slice(0, 3).toUpperCase();
}

const SPRINT_RANK: Record<string, number> = {
  architect: 0,
  orchestrator: 1,
  strategist: 2,
  designer: 3,
  'system-specialist': 4,
  reviewer: 5,
};
const SPRINT_ROLES = new Set(Object.keys(SPRINT_RANK));
const LEAF_ROLES = new Set(['worker', 'reviewer', 'curator']);

// In-cluster base order (RULED): worker → reviewer → curator; specialists behind, unknown last.
const CLUSTER_RANK: Record<string, number> = {
  worker: 0,
  reviewer: 1,
  curator: 2,
};

const isWorking = (session: OpenSession): boolean => seatVisualState(session).key === 'working';

/**
 * In-cluster comparator (RULED): the ACTIVE seat (turnState working) sorts to the top; ties keep
 * the base order. Pure over turnState, so the order changes ONLY when a state changes — no jumpy
 * reflows between polls that report the same states.
 */
export function compareClusterSeats(left: OpenSession, right: OpenSession): number {
  const activeDelta = Number(isWorking(right)) - Number(isWorking(left));
  if (activeDelta !== 0) return activeDelta;
  const rankDelta =
    (CLUSTER_RANK[sessionSeatRole(left)] ?? 4) - (CLUSTER_RANK[sessionSeatRole(right)] ?? 4);
  return rankDelta !== 0 ? rankDelta : left.id.localeCompare(right.id);
}

function compareSprintSeats(left: OpenSession, right: OpenSession): number {
  const rankDelta =
    (SPRINT_RANK[sessionSeatRole(left)] ?? 5) - (SPRINT_RANK[sessionSeatRole(right)] ?? 5);
  return rankDelta !== 0 ? rankDelta : left.id.localeCompare(right.id);
}

function documentKey(ref: TaskDocumentRef): string {
  return `${ref.repository}:${ref.path}`;
}

const isLive = (session: OpenSession): boolean => (session.status ?? 'running') === 'running';

export interface RailLeafCluster {
  /** Canonical task-document key (cluster identity). */
  key: string;
  label: string;
  taskDocumentRef: TaskDocumentRef;
  /** Legacy gate-projection join only; never seat identity. */
  gateLeafKey?: string;
  seats: OpenSession[];
}

export interface RailMasterSection {
  /** Canonical master task-document key. */
  key: string;
  label: string;
  taskDocumentRef: TaskDocumentRef;
  manager?: OpenSession;
  reviewer?: OpenSession;
  clusters: RailLeafCluster[];
  completed: OpenSession[];
}

export interface RailSprintSection {
  key: string;
  label: string;
  taskDocumentRef: TaskDocumentRef;
  seats: OpenSession[];
  masters: RailMasterSection[];
}

export interface RailModel {
  sprints: RailSprintSection[];
  /** Masters not named by a sprint document remain structurally valid top-level masters. */
  masters: RailMasterSection[];
  /** Live rows without a resolvable and altitude-valid document+role seat. */
  unattached: OpenSession[];
  completedUnattached: OpenSession[];
  completedTotal: number;
}

interface MasterAccumulator {
  doc: TaskDocNode;
  ref: TaskDocumentRef;
  manager?: OpenSession;
  reviewer?: OpenSession;
  leaf: Map<string, LeafAccumulator>;
  completed: OpenSession[];
}

interface LeafAccumulator {
  doc: TaskDocNode;
  ref: TaskDocumentRef;
  seats: OpenSession[];
}

interface SprintAccumulator {
  doc: TaskDocNode;
  ref: TaskDocumentRef;
  seats: OpenSession[];
  masters: Set<string>;
}

function pickupAging(pickup: AgentPickupNode): boolean {
  return pickup.ageSeconds !== undefined && pickup.ttlSeconds > 0
    ? pickup.ageSeconds >= pickup.ttlSeconds * 0.8
    : false;
}

function pickupTargetSession(
  pickup: AgentPickupNode,
  sessions: readonly OpenSession[],
): OpenSession | undefined {
  return (
    (pickup.deliveredToSession &&
      sessions.find((session) => session.id === pickup.deliveredToSession)) ||
    (pickup.lifecycleId &&
      sessions.find((session) => session.lifecycleId === pickup.lifecycleId)) ||
    undefined
  );
}

function masterForLeaf(
  doc: TaskDocNode,
  mastersByPath: ReadonlyMap<string, MasterAccumulator>,
): MasterAccumulator | undefined {
  const ref = taskDocumentRefForDoc(doc);
  return ref ? mastersByPath.get(`${pathDir(ref.path)}/task.json`) : undefined;
}

function sprintForMaster(
  master: TaskDocNode,
  sprints: readonly SprintAccumulator[],
): SprintAccumulator | undefined {
  const names = new Set(masterCommandNames(master));
  return sprints.find(
    (sprint) =>
      sprint.doc.repository === master.repository &&
      sprint.doc.docPath !== master.docPath &&
      sprint.doc.orchestrates.some((name) => names.has(name)),
  );
}

function materializeMaster(acc: MasterAccumulator): RailMasterSection {
  return {
    key: documentKey(acc.ref),
    label: acc.doc.title || acc.doc.id,
    taskDocumentRef: acc.ref,
    ...(acc.manager ? { manager: acc.manager } : {}),
    ...(acc.reviewer ? { reviewer: acc.reviewer } : {}),
    clusters: [...acc.leaf.values()]
      .sort((left, right) => left.doc.docPath.localeCompare(right.doc.docPath))
      .map((leaf) => ({
        key: documentKey(leaf.ref),
        label: leaf.doc.title || leaf.doc.id,
        taskDocumentRef: leaf.ref,
        ...(qualifiedLeafKey(leaf.doc) ? { gateLeafKey: qualifiedLeafKey(leaf.doc) } : {}),
        seats: [...leaf.seats].sort(compareClusterSeats),
      })),
    completed: [...acc.completed].sort((left, right) => left.id.localeCompare(right.id)),
  };
}

function currentSessions(sessions: readonly OpenSession[]): OpenSession[] {
  return sessions.filter((session) => session.status !== 'terminated');
}

interface RailAccumulators {
  docsByKey: Map<string, TaskDocNode>;
  mastersByPath: Map<string, MasterAccumulator>;
  sprints: SprintAccumulator[];
}

function initializeRailAccumulators(taskDocuments: readonly TaskDocNode[]): RailAccumulators {
  const docsByKey = new Map<string, TaskDocNode>();
  const mastersByPath = new Map<string, MasterAccumulator>();
  const sprints: SprintAccumulator[] = [];
  for (const doc of taskDocuments) {
    const ref = taskDocumentRefForDoc(doc);
    if (!ref) continue;
    docsByKey.set(documentKey(ref), doc);
    if (doc.kind !== 'master') continue;
    mastersByPath.set(ref.path, { doc, ref, leaf: new Map(), completed: [] });
    if (isOrchestrationDoc(doc)) sprints.push({ doc, ref, seats: [], masters: new Set() });
  }
  return { docsByKey, mastersByPath, sprints };
}

function appendDetached(
  session: OpenSession,
  unattached: OpenSession[],
  completedUnattached: OpenSession[],
): void {
  (session.status === 'landed' ? completedUnattached : unattached).push(session);
}

function attachSprintSeat(
  session: OpenSession,
  doc: TaskDocNode,
  ref: TaskDocumentRef,
  role: string,
  sprints: SprintAccumulator[],
  unattached: OpenSession[],
  completedUnattached: OpenSession[],
): boolean {
  if (!isOrchestrationDoc(doc) || !SPRINT_ROLES.has(role)) return false;
  if (role === 'reviewer' && !reviewerParentMatches(session, 'sprint', ref)) {
    appendDetached(session, unattached, completedUnattached);
    return true;
  }
  const sprint = sprints.find((item) => documentKey(item.ref) === documentKey(ref));
  if (!sprint) appendDetached(session, unattached, completedUnattached);
  else if (session.status === 'landed') completedUnattached.push(session);
  else sprint.seats.push(session);
  return true;
}

function attachMasterSeat(
  session: OpenSession,
  doc: TaskDocNode,
  ref: TaskDocumentRef,
  role: string,
  mastersByPath: Map<string, MasterAccumulator>,
  unattached: OpenSession[],
  completedUnattached: OpenSession[],
): boolean {
  if (
    doc.kind !== 'master' ||
    isOrchestrationDoc(doc) ||
    (role !== 'manager' && role !== 'reviewer')
  )
    return false;
  const master = mastersByPath.get(ref.path);
  if (!master) appendDetached(session, unattached, completedUnattached);
  else placeMasterSeat(master, session, ref, role, unattached, completedUnattached);
  return true;
}

function placeMasterSeat(
  master: MasterAccumulator,
  session: OpenSession,
  ref: TaskDocumentRef,
  role: string,
  unattached: OpenSession[],
  completedUnattached: OpenSession[],
): void {
  if (role === 'reviewer' && !reviewerParentMatches(session, 'master', ref))
    appendDetached(session, unattached, completedUnattached);
  else if (session.status === 'landed') master.completed.push(session);
  else if (role === 'manager' && !master.manager) master.manager = session;
  else if (role === 'reviewer' && !master.reviewer) master.reviewer = session;
  else unattached.push(session);
}

function attachLeafSeat(
  session: OpenSession,
  doc: TaskDocNode,
  ref: TaskDocumentRef,
  role: string,
  mastersByPath: Map<string, MasterAccumulator>,
  unattached: OpenSession[],
  completedUnattached: OpenSession[],
): boolean {
  if (doc.kind === 'master' || !LEAF_ROLES.has(role)) return false;
  const master = masterForLeaf(doc, mastersByPath);
  if (!master) appendDetached(session, unattached, completedUnattached);
  else if (role === 'reviewer' && !reviewerParentMatches(session, 'leaf', ref, master.ref))
    appendDetached(session, unattached, completedUnattached);
  else if (session.status === 'landed') master.completed.push(session);
  else {
    const key = documentKey(ref);
    const leaf = master.leaf.get(key) ?? { doc, ref, seats: [] };
    leaf.seats.push(session);
    master.leaf.set(key, leaf);
  }
  return true;
}

function attachSession(
  session: OpenSession,
  accumulators: RailAccumulators,
  unattached: OpenSession[],
  completedUnattached: OpenSession[],
): void {
  const ref = session.taskDocumentRef;
  const doc = ref ? accumulators.docsByKey.get(documentKey(ref)) : undefined;
  if (!ref || !doc) return appendDetached(session, unattached, completedUnattached);
  const role = sessionSeatRole(session);
  if (
    attachSprintSeat(session, doc, ref, role, accumulators.sprints, unattached, completedUnattached)
  )
    return;
  if (
    attachMasterSeat(
      session,
      doc,
      ref,
      role,
      accumulators.mastersByPath,
      unattached,
      completedUnattached,
    )
  )
    return;
  if (
    attachLeafSeat(
      session,
      doc,
      ref,
      role,
      accumulators.mastersByPath,
      unattached,
      completedUnattached,
    )
  )
    return;
  appendDetached(session, unattached, completedUnattached);
}

function materializeRailSections(
  mastersByPath: Map<string, MasterAccumulator>,
  sprints: SprintAccumulator[],
): Pick<RailModel, 'sprints' | 'masters'> {
  const visibleMasters = [...mastersByPath.values()].filter(
    (master) =>
      master.manager || master.reviewer || master.leaf.size > 0 || master.completed.length > 0,
  );
  const masterSprint = new Map<string, SprintAccumulator>();
  for (const master of visibleMasters) {
    const sprint = sprintForMaster(master.doc, sprints);
    if (!sprint) continue;
    sprint.masters.add(documentKey(master.ref));
    masterSprint.set(documentKey(master.ref), sprint);
  }
  const sprintSections = sprints
    .filter((sprint) => sprint.seats.length > 0 || sprint.masters.size > 0)
    .sort((left, right) => left.doc.docPath.localeCompare(right.doc.docPath))
    .map((sprint) => ({
      key: documentKey(sprint.ref),
      label: sprint.doc.title || sprint.doc.id,
      taskDocumentRef: sprint.ref,
      seats: [...sprint.seats].sort(compareSprintSeats),
      masters: visibleMasters
        .filter((master) => masterSprint.get(documentKey(master.ref)) === sprint)
        .sort((left, right) => left.doc.docPath.localeCompare(right.doc.docPath))
        .map(materializeMaster),
    }));
  const masters = visibleMasters
    .filter((master) => !masterSprint.has(documentKey(master.ref)))
    .sort((left, right) => left.doc.docPath.localeCompare(right.doc.docPath))
    .map(materializeMaster);
  return { sprints: sprintSections, masters };
}

export function buildRailModel(
  sessions: OpenSession[],
  taskDocuments: readonly TaskDocNode[] = [],
): RailModel {
  const unattached: OpenSession[] = [];
  const completedUnattached: OpenSession[] = [];
  const accumulators = initializeRailAccumulators(taskDocuments);
  for (const session of currentSessions(sessions))
    attachSession(session, accumulators, unattached, completedUnattached);

  unattached.sort(
    (left, right) =>
      Number(isLive(right)) - Number(isLive(left)) || left.id.localeCompare(right.id),
  );
  const { sprints, masters } = materializeRailSections(
    accumulators.mastersByPath,
    accumulators.sprints,
  );

  const completedTotal =
    [...masters, ...sprints.flatMap((sprint) => sprint.masters)].reduce(
      (sum, master) => sum + master.completed.length,
      0,
    ) + completedUnattached.length;

  return { sprints, masters, unattached, completedUnattached, completedTotal };
}

/** Task hierarchy flattened for alt+↑/↓ session cycling; spawn ancestry never changes this order. */
export function railCycleOrder(model: RailModel): string[] {
  const ids: string[] = [];
  const appendMaster = (master: RailMasterSection) => {
    if (master.manager) ids.push(master.manager.id);
    if (master.reviewer) ids.push(master.reviewer.id);
    for (const cluster of master.clusters) for (const seat of cluster.seats) ids.push(seat.id);
  };
  for (const sprint of model.sprints) {
    for (const seat of sprint.seats) ids.push(seat.id);
    for (const master of sprint.masters) appendMaster(master);
  }
  for (const master of model.masters) appendMaster(master);
  for (const session of model.unattached) ids.push(session.id);
  return ids;
}

// ── The orchestration tree (R5's palette toggle) ────────────────────────────────────────────
// The spawn-edge view exists for PROVENANCE INSPECTION only — it is exactly the who-spawned-whom
// forest the ruled hierarchy replaced as the default.

export interface SpawnTreeRow {
  session: OpenSession;
  depth: number;
}

export function buildSpawnTree(sessions: OpenSession[]): SpawnTreeRow[] {
  const visible = sessions.filter((session) => session.status !== 'terminated');
  const byParent = new Map<string, OpenSession[]>();
  const ids = new Set(visible.map((session) => session.id));
  const roots: OpenSession[] = [];
  for (const session of visible) {
    if (session.spawnedBySession && ids.has(session.spawnedBySession)) {
      const list = byParent.get(session.spawnedBySession);
      if (list) list.push(session);
      else byParent.set(session.spawnedBySession, [session]);
    } else {
      roots.push(session);
    }
  }
  const byId = (l: OpenSession, r: OpenSession) => l.id.localeCompare(r.id);
  const rows: SpawnTreeRow[] = [];
  const emit = (session: OpenSession, depth: number) => {
    rows.push({ session, depth });
    for (const child of (byParent.get(session.id) ?? []).sort(byId)) emit(child, depth + 1);
  };
  for (const root of roots.sort(byId)) emit(root, 0);
  return rows;
}

// ── Row anatomy invariants (R6) ─────────────────────────────────────────────────────────────
// dot | role | title | status | End. Truncation: dot + role + title ALWAYS survive; the status
// chip is the only elidable segment and its truth stays reachable via the row tooltip.

export const ROW_SEGMENTS = ['dot', 'role', 'title', 'status', 'end'] as const;
export const ROW_ELIDABLE_SEGMENTS: readonly (typeof ROW_SEGMENTS)[number][] = ['status'];

/** The full untruncated row truth — the tooltip the elided status chip falls back to. */
export function railRowTooltip(session: OpenSession, taskLabel?: string): string {
  const visual = seatVisualState(session);
  const parts = [session.label];
  const role = session.spawnRole ?? session.seatRole;
  if (role) parts.push(`role: ${role}`);
  parts.push(`state: ${visual.word}`);
  if (taskLabel) parts.push(`task: ${taskLabel}`);
  if (session.landedReason) parts.push(`landed: ${session.landedReason}`);
  if (session.retiredReason) parts.push(`retired: ${session.retiredReason}`);
  return parts.join(' · ');
}

// ── Fleet attention (R12) ───────────────────────────────────────────────────────────────────

export interface AttentionRollup {
  needsInput: string[];
  failed: string[];
  /** Sessions with unacknowledged set outcomes (cockpit-store join). */
  unacked: string[];
  /** Sessions targeted by critical bus items: age ≥ ttl·0.8 or escalated (F11). */
  criticalBus: string[];
  working: string[];
}

export function attentionRollup(
  sessions: OpenSession[],
  joins: { unackedSessionIds?: readonly string[]; criticalBusSessionIds?: readonly string[] } = {},
): AttentionRollup {
  const live = sessions.filter(isLive);
  const byKey = (key: string) =>
    live.filter((session) => seatVisualState(session).key === key).map((session) => session.id);
  const liveIds = new Set(live.map((session) => session.id));
  return {
    needsInput: byKey('awaiting-input'),
    failed: byKey('failed'),
    unacked: (joins.unackedSessionIds ?? []).filter((id) => liveIds.has(id)),
    criticalBus: (joins.criticalBusSessionIds ?? []).filter((id) => liveIds.has(id)),
    working: byKey('working'),
  };
}

/** True ⇒ the attention strip renders NOTHING (empty-state suppression — working alone is not attention). */
export function attentionZeroState(rollup: AttentionRollup): boolean {
  return (
    rollup.needsInput.length === 0 &&
    rollup.failed.length === 0 &&
    rollup.unacked.length === 0 &&
    rollup.criticalBus.length === 0
  );
}

/**
 * Jump-to-attention priority (R12): awaiting-input → failed → unacknowledged set outcomes →
 * critical bus items → OLDEST working. Within a class the longest-waiting seat wins.
 */
export function jumpToAttentionTarget(
  rollup: AttentionRollup,
  sessions: OpenSession[],
): string | null {
  const byId = new Map(sessions.map((session) => [session.id, session]));
  const oldestFirst = (ids: string[]): string | undefined =>
    [...ids].sort((left, right) => {
      const l = byId.get(left)?.turnStateChangedAt ?? byId.get(left)?.createdAt ?? '';
      const r = byId.get(right)?.turnStateChangedAt ?? byId.get(right)?.createdAt ?? '';
      return l.localeCompare(r) || left.localeCompare(right);
    })[0];
  return (
    oldestFirst(rollup.needsInput) ??
    oldestFirst(rollup.failed) ??
    oldestFirst(rollup.unacked) ??
    oldestFirst(rollup.criticalBus) ??
    oldestFirst(rollup.working) ??
    null
  );
}

/** Per-master attention rollup badge for group headers (dominant class + count). */
export function masterAttentionBadge(
  master: RailMasterSection,
  rollup: AttentionRollup,
): { glyph: string; count: number; kind: 'needsInput' | 'failed' } | null {
  const memberIds = new Set<string>([
    ...(master.manager ? [master.manager.id] : []),
    ...(master.reviewer ? [master.reviewer.id] : []),
    ...master.clusters.flatMap((cluster) => cluster.seats.map((seat) => seat.id)),
  ]);
  const needsInput = rollup.needsInput.filter((id) => memberIds.has(id)).length;
  if (needsInput > 0) return { glyph: '❗', count: needsInput, kind: 'needsInput' };
  const failed = rollup.failed.filter((id) => memberIds.has(id)).length;
  if (failed > 0) return { glyph: '✖', count: failed, kind: 'failed' };
  return null;
}

// ── Smart-default focus (R9) ────────────────────────────────────────────────────────────────

/**
 * View-entry focus: awaiting-input → failed → most recently active running → null (the stage
 * then renders the launcher hint — never an empty landing without explanation).
 */
export function smartDefaultFocus(sessions: OpenSession[]): string | null {
  const live = sessions.filter(isLive);
  const stamp = (session: OpenSession) => session.turnStateChangedAt ?? session.createdAt ?? '';
  const oldest = (list: OpenSession[]) =>
    [...list].sort((l, r) => stamp(l).localeCompare(stamp(r)) || l.id.localeCompare(r.id))[0];
  const waiting = live.filter((session) => seatVisualState(session).key === 'awaiting-input');
  if (waiting.length > 0) return oldest(waiting).id;
  const failed = live.filter((session) => seatVisualState(session).key === 'failed');
  if (failed.length > 0) return oldest(failed).id;
  if (live.length > 0) {
    const newest = [...live].sort(
      (l, r) => stamp(r).localeCompare(stamp(l)) || l.id.localeCompare(r.id),
    )[0];
    return newest.id;
  }
  return null;
}

// ── Projection joins (R13 gates, R8 brief column, F11 critical bus) ─────────────────────────

/** Gate state joined by leafKey (R13): a leaf whose lifecycle holds an UNDECIDED gate. */
export function heldGatesByLeafKey(
  taskDocuments: readonly TaskDocNode[],
  lifecycles: Record<string, LifecycleProjection>,
): Map<string, GateNode> {
  const held = new Map<string, GateNode>();
  for (const doc of taskDocuments) {
    if (!doc.lifecycleId) continue;
    const gate = lifecycles[doc.lifecycleId]?.gate;
    // An open gate (no decision recorded) is the held/decision-pending state.
    if (!gate || gate.state !== 'open') continue;
    const leafKey = qualifiedLeafKey(doc);
    if (leafKey) held.set(leafKey, gate);
  }
  return held;
}

/** Two-state brief column (R8): sessions with a dispatch brief still awaiting acknowledgment.
 *  Deliberately TWO-state (brief pending / none): consumed history is not projected — the
 *  tri-state is gated on upstream ask UA-3 and must never be faked. */
export function briefPendingSessionIds(
  pickups: readonly AgentPickupNode[],
  sessions: readonly OpenSession[],
): Set<string> {
  const pending = new Set<string>();
  for (const pickup of pickups) {
    if (pickup.messageKind !== 'dispatch-brief') continue;
    if (pickup.state !== 'waiting-for-agent' && pickup.state !== 'check-chat') continue;
    const target = pickupTargetSession(pickup, sessions);
    if (target) pending.add(target.id);
  }
  return pending;
}

/** Critical bus items (F11): pickups at ≥ 80% of their ttl or already escalated to check-chat. */
export function criticalBusSessionIds(
  pickups: readonly AgentPickupNode[],
  sessions: readonly OpenSession[],
): Set<string> {
  const critical = new Set<string>();
  for (const pickup of pickups) {
    const escalated = pickup.state === 'check-chat';
    const aging = pickupAging(pickup);
    if (!escalated && !aging) continue;
    const target = pickupTargetSession(pickup, sessions);
    if (target) critical.add(target.id);
  }
  return critical;
}

// ── Question triage (R16) ───────────────────────────────────────────────────────────────────

/** The pending question's prompt preview (tooltip + palette triage), clamped for chip rendering. */
export function interactionPromptPreview(
  pendingInteraction: Record<string, unknown> | undefined,
  maxLength = 120,
): string | undefined {
  if (!pendingInteraction) return undefined;
  const candidate = ['prompt', 'question', 'message', 'title']
    .map((key) => pendingInteraction[key])
    .find((value): value is string => typeof value === 'string' && value.length > 0);
  if (!candidate) return undefined;
  return candidate.length > maxLength ? `${candidate.slice(0, maxLength - 1)}…` : candidate;
}

/** All seats with a pending question — parent OR multiplexed sub-agent — newest first (the palette's triage list). */
export function waitingSeats(sessions: OpenSession[]): OpenSession[] {
  return sessions
    .filter((session) => isLive(session) && sessionHasPendingInteraction(session))
    .sort(
      (l, r) =>
        (r.turnStateChangedAt ?? r.createdAt ?? '').localeCompare(
          l.turnStateChangedAt ?? l.createdAt ?? '',
        ) || l.id.localeCompare(r.id),
    );
}

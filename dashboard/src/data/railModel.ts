import type { AgentPickupNode, GateNode, LifecycleProjection, TaskDocNode } from "../types/projection";
import { sessionHasPendingInteraction, sessionSeatRole, type OpenSession } from "./sessions";
import { seatVisualState } from "./stateGrammar";
import { pathDir } from "./taskHierarchy";
import { qualifiedLeafKey } from "./taskIdentity";

// The session-rail model (260715-FEUI-L2 S4) — pure derivations, RULED 2026-07-16 (spec §1.6b):
// role-driven hierarchy, never spawn-edge nesting. Architect / orchestrator / manager render as a
// FLAT command spine in that order; only leaf agents indent, clustered per leaf under their
// manager, base order worker → reviewer → curator with the ACTIVE seat sorted to the top.
// Completed seats fold into a per-master collapsed folder; bulk end lives at master AND sprint
// level with honest preview counts. Consumes the 260713_chat-rail-role-hierarchy analysis (flat
// spine; the landed spawn-edge forest in SessionList.orderedVisibleMembers is the diagnosed
// defect) — that master's formal roll-in stays pending the developer's call.

/** Three-letter role codes (RULED): ARC/ORC/MGR/WKR/REV/CUR; other known roles keep the pattern. */
const ROLE_CODES: Record<string, string> = {
  architect: "ARC",
  orchestrator: "ORC",
  manager: "MGR",
  worker: "WKR",
  reviewer: "REV",
  curator: "CUR",
  strategist: "STR",
  designer: "DSG",
  "system-specialist": "SYS",
};

/** The rail chip code for a seat's spawn role — absent when the seat has no spawn role (R6). */
export function roleCode(session: Pick<OpenSession, "kind" | "seatRole" | "spawnRole">): string | undefined {
  const role = session.spawnRole ?? session.seatRole;
  if (!role || role === "chat" || role === "terminal") return undefined;
  return ROLE_CODES[role] ?? role.slice(0, 3).toUpperCase();
}

// Spine order (RULED): architect → orchestrator → (strategist/designer) → unresolvable managers.
const SPINE_RANK: Record<string, number> = {
  architect: 0,
  orchestrator: 1,
  strategist: 2,
  designer: 2,
  manager: 3,
};
const SPINE_ROLES = new Set(Object.keys(SPINE_RANK));

// In-cluster base order (RULED): worker → reviewer → curator; specialists behind, unknown last.
const CLUSTER_RANK: Record<string, number> = {
  worker: 0,
  reviewer: 1,
  curator: 2,
  "system-specialist": 3,
};

const isWorking = (session: OpenSession): boolean =>
  seatVisualState(session).key === "working";

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

function compareSpine(left: OpenSession, right: OpenSession): number {
  const rankDelta =
    (SPINE_RANK[sessionSeatRole(left)] ?? 4) - (SPINE_RANK[sessionSeatRole(right)] ?? 4);
  return rankDelta !== 0 ? rankDelta : left.id.localeCompare(right.id);
}

function masterKeyOf(session: OpenSession): string | undefined {
  const parts = session.leafKey?.split("/").filter(Boolean);
  return parts && parts.length >= 3 ? `${parts[0]}/${parts[1]}` : undefined;
}

const isLive = (session: OpenSession): boolean => (session.status ?? "running") === "running";

export interface RailLeafCluster {
  /** The qualified leaf key (cluster identity). */
  key: string;
  /** The `└ leaf-id` caption label. */
  label: string;
  seats: OpenSession[];
}

export interface RailMasterSection {
  /** `repo/master`. */
  key: string;
  label: string;
  /** Managers render flat (never indented) at the section top. */
  managers: OpenSession[];
  clusters: RailLeafCluster[];
  /** Landed seats of this master — the collapsed completed folder (R17). */
  completed: OpenSession[];
}

export interface RailModel {
  /** Architect / orchestrator (/ strategist / designer / master-less managers) — flat, in order. */
  spine: OpenSession[];
  masters: RailMasterSection[];
  /** Live seats with no leaf claim and no command role. */
  unattached: OpenSession[];
  /** Landed seats with no resolvable master (bulk-ended only at sprint level). */
  completedUnattached: OpenSession[];
  /** Sprint-level bulk-end target: every landed seat in the rail. */
  completedTotal: number;
}

export interface RailModelLabels {
  masterLabel?: (masterKey: string) => string | undefined;
  leafLabel?: (leafKey: string) => string | undefined;
}

/** `repo/master` → master-doc title (the sessionGroups.masterDocsBySprint derivation). */
export function masterLabels(taskDocuments: readonly TaskDocNode[]): Map<string, string> {
  const labels = new Map<string, string>();
  for (const doc of taskDocuments) {
    if (doc.kind !== "master" || !doc.repository || !doc.title) continue;
    const folder = pathDir(doc.docPath).split("/").filter(Boolean).pop() ?? "";
    const key = folder ? `${doc.repository}/${folder}` : undefined;
    if (key && !labels.has(key)) labels.set(key, doc.title);
  }
  return labels;
}

interface MasterAccumulator {
  managers: OpenSession[];
  leaf: Map<string, OpenSession[]>;
  completed: OpenSession[];
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

function bucketSession(
  session: OpenSession,
  spine: OpenSession[],
  unattached: OpenSession[],
  completedUnattached: OpenSession[],
  sectionFor: (masterKey: string) => MasterAccumulator,
): void {
  if (session.status === "terminated") return; // tombstones never render
  const role = sessionSeatRole(session);
  const masterKey = masterKeyOf(session);
  if (session.status === "landed") {
    // Completed folder per master (RULED, restores the regressed behavior) — command seats too:
    // a landed seat is done regardless of rank.
    if (masterKey) sectionFor(masterKey).completed.push(session);
    else completedUnattached.push(session);
    return;
  }
  if (role === "manager") {
    if (masterKey) sectionFor(masterKey).managers.push(session);
    else spine.push(session); // a master-less manager still outranks the leaf level
    return;
  }
  if (SPINE_ROLES.has(role)) {
    spine.push(session);
    return;
  }
  if (masterKey && session.leafKey) {
    const section = sectionFor(masterKey);
    const seats = section.leaf.get(session.leafKey);
    if (seats) seats.push(session);
    else section.leaf.set(session.leafKey, [session]);
  } else {
    unattached.push(session);
  }
}

export function buildRailModel(sessions: OpenSession[], labels: RailModelLabels = {}): RailModel {
  const spine: OpenSession[] = [];
  const unattached: OpenSession[] = [];
  const completedUnattached: OpenSession[] = [];
  const byMaster = new Map<string, MasterAccumulator>();

  const sectionFor = (masterKey: string): MasterAccumulator => {
    const existing = byMaster.get(masterKey);
    if (existing) return existing;
    const created: MasterAccumulator = { managers: [], leaf: new Map(), completed: [] };
    byMaster.set(masterKey, created);
    return created;
  };

  for (const session of sessions) {
    bucketSession(session, spine, unattached, completedUnattached, sectionFor);
  }

  spine.sort(compareSpine);
  unattached.sort((left, right) =>
    Number(isLive(right)) - Number(isLive(left)) || left.id.localeCompare(right.id),
  );

  const masters: RailMasterSection[] = [...byMaster.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, section]) => ({
      key,
      label: labels.masterLabel?.(key) ?? key.split("/").pop() ?? key,
      managers: [...section.managers].sort((l, r) => l.id.localeCompare(r.id)),
      clusters: [...section.leaf.entries()]
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([leafKey, seats]) => ({
          key: leafKey,
          label: labels.leafLabel?.(leafKey) ?? leafKey.split("/").pop() ?? leafKey,
          seats: [...seats].sort(compareClusterSeats),
        })),
      completed: [...section.completed].sort((l, r) => l.id.localeCompare(r.id)),
    }));

  const completedTotal =
    masters.reduce((sum, master) => sum + master.completed.length, 0) + completedUnattached.length;

  return { spine, masters, unattached, completedUnattached, completedTotal };
}

/** Rail order flattened for alt+↑/↓ session cycling (spine → masters → unattached; live rows only). */
export function railCycleOrder(model: RailModel): string[] {
  const ids: string[] = model.spine.map((session) => session.id);
  for (const master of model.masters) {
    for (const manager of master.managers) ids.push(manager.id);
    for (const cluster of master.clusters) for (const seat of cluster.seats) ids.push(seat.id);
  }
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
  const visible = sessions.filter((session) => session.status !== "terminated");
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

export const ROW_SEGMENTS = ["dot", "role", "title", "status", "end"] as const;
export const ROW_ELIDABLE_SEGMENTS: readonly (typeof ROW_SEGMENTS)[number][] = ["status"];

/** The full untruncated row truth — the tooltip the elided status chip falls back to. */
export function railRowTooltip(session: OpenSession, leafLabel?: string): string {
  const visual = seatVisualState(session);
  const parts = [session.label];
  const role = session.spawnRole ?? session.seatRole;
  if (role) parts.push(`role: ${role}`);
  parts.push(`state: ${visual.word}`);
  if (leafLabel) parts.push(`leaf: ${leafLabel}`);
  if (session.landedReason) parts.push(`landed: ${session.landedReason}`);
  if (session.retiredReason) parts.push(`retired: ${session.retiredReason}`);
  return parts.join(" · ");
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
    needsInput: byKey("awaiting-input"),
    failed: byKey("failed"),
    unacked: (joins.unackedSessionIds ?? []).filter((id) => liveIds.has(id)),
    criticalBus: (joins.criticalBusSessionIds ?? []).filter((id) => liveIds.has(id)),
    working: byKey("working"),
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
      const l = byId.get(left)?.turnStateChangedAt ?? byId.get(left)?.createdAt ?? "";
      const r = byId.get(right)?.turnStateChangedAt ?? byId.get(right)?.createdAt ?? "";
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
): { glyph: string; count: number; kind: "needsInput" | "failed" } | null {
  const memberIds = new Set<string>([
    ...master.managers.map((session) => session.id),
    ...master.clusters.flatMap((cluster) => cluster.seats.map((seat) => seat.id)),
  ]);
  const needsInput = rollup.needsInput.filter((id) => memberIds.has(id)).length;
  if (needsInput > 0) return { glyph: "❗", count: needsInput, kind: "needsInput" };
  const failed = rollup.failed.filter((id) => memberIds.has(id)).length;
  if (failed > 0) return { glyph: "✖", count: failed, kind: "failed" };
  return null;
}

// ── Smart-default focus (R9) ────────────────────────────────────────────────────────────────

/**
 * View-entry focus: awaiting-input → failed → most recently active running → null (the stage
 * then renders the launcher hint — never an empty landing without explanation).
 */
export function smartDefaultFocus(sessions: OpenSession[]): string | null {
  const live = sessions.filter(isLive);
  const stamp = (session: OpenSession) => session.turnStateChangedAt ?? session.createdAt ?? "";
  const oldest = (list: OpenSession[]) =>
    [...list].sort((l, r) => stamp(l).localeCompare(stamp(r)) || l.id.localeCompare(r.id))[0];
  const waiting = live.filter((session) => seatVisualState(session).key === "awaiting-input");
  if (waiting.length > 0) return oldest(waiting).id;
  const failed = live.filter((session) => seatVisualState(session).key === "failed");
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
    if (!gate || gate.state !== "open") continue;
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
    if (pickup.messageKind !== "dispatch-brief") continue;
    if (pickup.state !== "waiting-for-agent" && pickup.state !== "check-chat") continue;
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
    const escalated = pickup.state === "check-chat";
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
  const candidate = ["prompt", "question", "message", "title"]
    .map((key) => pendingInteraction[key])
    .find((value): value is string => typeof value === "string" && value.length > 0);
  if (!candidate) return undefined;
  return candidate.length > maxLength ? `${candidate.slice(0, maxLength - 1)}…` : candidate;
}

/** All seats with a pending question — parent OR multiplexed sub-agent — newest first (the palette's triage list). */
export function waitingSeats(sessions: OpenSession[]): OpenSession[] {
  return sessions
    .filter((session) => isLive(session) && sessionHasPendingInteraction(session))
    .sort((l, r) =>
      (r.turnStateChangedAt ?? r.createdAt ?? "").localeCompare(
        l.turnStateChangedAt ?? l.createdAt ?? "",
      ) || l.id.localeCompare(r.id),
    );
}

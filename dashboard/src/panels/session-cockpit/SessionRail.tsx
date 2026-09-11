import { useMemo, useState, type Dispatch, type SetStateAction } from 'react';

import {
  briefPendingSessionIds,
  buildSpawnTree,
  heldGatesByLeafKey,
  jumpToAttentionTarget,
  type AttentionRollup,
  type RailModel,
} from '../../data/railModel';
import { usePtyHarvest } from '../../data/ptyHarvest';
import { endLandedDetailed, endSessionDetailed } from '../../data/sessionLifecycle';
import { useSessionCockpit } from '../../data/sessionCockpitStore';
import { useSessions, type OpenSession } from '../../data/sessions';
import { useDashboard } from '../../data/store';
import type { AgentPickupNode, TaskDocNode } from '../../types/projection';
import { RailBody, type BulkTarget, type RailRowProps } from './sessionRailParts';

// The session rail: the document+role hierarchy — sprint roles, manager and reviewer master seats,
// and worker/reviewer/curator seats below each leaf — plus per-master completed folders, fleet
// attention, gate badges,
// the two-state brief column, the poll-health banner, and the bus-summary footer.
// Unresolved rows remain visibly unattached; the orchestration-tree (spawn-edge) view stays
// available only as a provenance inspection toggle. Render parts live in
// sessionRailParts.tsx and the shared styles in sessionRailStyles.ts.

export type { BulkTarget } from './sessionRailParts';

const EMPTY_DOCS: TaskDocNode[] = [];
const EMPTY_PICKUPS: AgentPickupNode[] = [];

/** Terminate one seat (the detailed flow keeps the stop residual — data/sessionLifecycle). */
export async function endSession(session: OpenSession): Promise<void> {
  await endSessionDetailed(session);
}

/** Bulk-end landed seats (master/sprint bulk affordances + their palette mirrors). The detailed
 *  flow records the route's honest outcome (closed + skipped with reasons) for the rail note. */
export async function endLanded(
  sessions: readonly Pick<OpenSession, 'id' | 'label'>[],
): Promise<void> {
  await endLandedDetailed(sessions);
}

export interface SessionRailProps {
  onFocusSession: (id: string) => void;
  focusedSessionId: string | null;
  /** Precomputed joins the view shares with the palette commands (one derivation, two surfaces). */
  model: RailModel;
  rollup: AttentionRollup;
}

/** Browser-render virtualization starts only beyond the measured 50-row fleet envelope. */
export const RAIL_VIRTUALIZE_THRESHOLD = 50;

function useRailData() {
  const sessions = useSessions((state) => state.sessions);
  const treeView = useSessionCockpit((state) => state.orchestrationTreeView);
  const setTreeView = useSessionCockpit((state) => state.setOrchestrationTreeView);
  // A healthy catalog beat updates only lastBeatAt every 2.5 s. This rail renders no beat-age
  // value, so subscribe only to the two facts it actually shows; otherwise every successful poll
  // reconstructs the full role hierarchy for an invisible timestamp.
  const pollHealthy = useSessionCockpit((state) => state.pollHealth.healthy);
  const pollMissedBeats = useSessionCockpit((state) => state.pollHealth.missedBeats);
  // Unacknowledged set outcomes drive a per-row attention marker.
  const perSessionCockpit = useSessionCockpit((state) => state.perSession);
  const taskDocuments = useDashboard((state) => state.analytics?.taskDocuments ?? EMPTY_DOCS);
  const lifecycles = useDashboard((state) => state.lifecycles);
  const pickups = useDashboard((state) =>
    state.analytics ? state.analytics.agentPickups : EMPTY_PICKUPS,
  );
  const harvestBySession = usePtyHarvest((state) => state.bySession);
  return {
    sessions,
    treeView,
    setTreeView,
    pollHealthy,
    pollMissedBeats,
    perSessionCockpit,
    taskDocuments,
    lifecycles,
    pickups,
    harvestBySession,
  };
}

function rolesRowCount(model: RailModel, openDoneFolders: Record<string, boolean>): number {
  const masterRows = (masters: RailModel['masters']): number =>
    masters.reduce(
      (sum, master) =>
        sum +
        Number(master.manager !== undefined) +
        Number(master.reviewer !== undefined) +
        master.clusters.reduce((clusterSum, cluster) => clusterSum + cluster.seats.length, 0) +
        ((openDoneFolders[master.key] ?? false) ? master.completed.length : 0),
      0,
    );
  return (
    model.sprints.reduce(
      (sum, sprint) => sum + sprint.seats.length + masterRows(sprint.masters),
      0,
    ) +
    masterRows(model.masters) +
    model.unattached.length +
    model.completedUnattached.length
  );
}

function useRailEndFailure() {
  // Single-row End acts IMMEDIATELY — no armed inline confirm. The earlier armed confirm added a
  // step for a single seat and its overlay glitched; only bulk end keeps a confirm, because it
  // names multiple sessions at once. A FAILED terminate POST renders verbatim with a retry —
  // never silent (distinct from informational stop residuals).
  const [endFailure, setEndFailure] = useState<{
    sessionId: string;
    error: string;
  } | null>(null);
  const executeEnd = async (session: OpenSession) => {
    const outcome = await endSessionDetailed(session);
    if (!outcome.ok) {
      setEndFailure({
        sessionId: session.id,
        error: outcome.error ?? 'terminate POST failed',
      });
    } else {
      setEndFailure((current) => (current?.sessionId === session.id ? null : current));
    }
  };
  return {
    endFailure,
    executeEnd,
    dismissEndFailure: () => setEndFailure(null),
  };
}

function focusAttention(
  setHighlightKind: Dispatch<SetStateAction<keyof AttentionRollup | null>>,
  onFocusSession: (id: string) => void,
  kind: keyof AttentionRollup,
  first: string | null,
): void {
  setHighlightKind(kind);
  if (first) onFocusSession(first);
}

function attentionClass(partial: Partial<AttentionRollup>, sessions: OpenSession[]): string | null {
  return jumpToAttentionTarget(
    { needsInput: [], failed: [], unacked: [], criticalBus: [], working: [], ...partial },
    sessions,
  );
}

function bulkSessions(
  target: BulkTarget,
  allLanded: OpenSession[],
  landedByMaster: Map<string, OpenSession[]>,
): OpenSession[] {
  return target.scope === 'sprint' ? allLanded : (landedByMaster.get(target.key) ?? []);
}

export function SessionRail({ onFocusSession, focusedSessionId, model, rollup }: SessionRailProps) {
  const data = useRailData();
  const [openDoneFolders, setOpenDoneFolders] = useState<Record<string, boolean>>({});
  const [armedBulk, setArmedBulk] = useState<BulkTarget | null>(null);
  const railEnd = useRailEndFailure();
  // The clicked attention CLASS, not a snapshot of ids: the highlighted set derives from the
  // LIVE rollup each render, so a ring expires the moment the seat's state resolves (a stale
  // snapshot kept suggesting attention after it was gone).
  const [highlightKind, setHighlightKind] = useState<keyof AttentionRollup | null>(null);
  const highlight = highlightKind ? new Set(rollup[highlightKind]) : null;

  const heldGates = useMemo(
    () => heldGatesByLeafKey(data.taskDocuments, data.lifecycles),
    [data.taskDocuments, data.lifecycles],
  );
  const briefPending = useMemo(
    () => briefPendingSessionIds(data.pickups, data.sessions),
    [data.pickups, data.sessions],
  );

  const allMasters = [...model.sprints.flatMap((sprint) => sprint.masters), ...model.masters];
  const landedByMaster = new Map(allMasters.map((master) => [master.key, master.completed]));
  const allLanded = [
    ...allMasters.flatMap((master) => master.completed),
    ...model.completedUnattached,
  ];
  const treeRows = useMemo(() => buildSpawnTree(data.sessions), [data.sessions]);
  // The threshold is a rendered-surface contract. Count the row shells the active view actually
  // emits, not a live-seat subtotal: completed-unattached is always visible, master-completed is
  // visible only while its folder is expanded, and tree mode has its own flattened population.
  const renderedRowCount = data.treeView ? treeRows.length : rolesRowCount(model, openDoneFolders);
  const virtualized = renderedRowCount > RAIL_VIRTUALIZE_THRESHOLD;

  const executeBulk = (target: BulkTarget) => {
    setArmedBulk(null);
    void endLanded(bulkSessions(target, allLanded, landedByMaster));
  };

  const rowProps: Omit<RailRowProps, 'session' | 'dormant'> = {
    focusedSessionId,
    heldGates,
    briefPending,
    perSessionCockpit: data.perSessionCockpit,
    harvestBySession: data.harvestBySession,
    endFailure: railEnd.endFailure,
    virtualized,
    highlight,
    onFocusSession,
    onEnd: railEnd.executeEnd,
    onDismissEndFailure: railEnd.dismissEndFailure,
  };

  return (
    <RailBody
      pollHealthy={data.pollHealthy}
      pollMissedBeats={data.pollMissedBeats}
      model={model}
      treeView={data.treeView}
      treeRows={treeRows}
      virtualized={virtualized}
      renderedRowCount={renderedRowCount}
      armedBulk={armedBulk}
      allLanded={allLanded}
      openDoneFolders={openDoneFolders}
      rollup={rollup}
      rowProps={rowProps}
      onFocusSet={(kind, first) => focusAttention(setHighlightKind, onFocusSession, kind, first)}
      jumpClass={(partial) => attentionClass(partial, data.sessions)}
      onToggleTree={() => data.setTreeView(!data.treeView)}
      onArmSprint={() => setArmedBulk({ scope: 'sprint' })}
      onConfirmBulk={executeBulk}
      onCancelBulk={() => setArmedBulk(null)}
      onToggleDone={(key) =>
        setOpenDoneFolders((current) => ({
          ...current,
          [key]: !(current[key] ?? false),
        }))
      }
      onArmBulk={setArmedBulk}
    />
  );
}

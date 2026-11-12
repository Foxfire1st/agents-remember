import type { EngineProcessNode } from '../../types/projection';
import type { EngineRoomModel } from './engineRoomTypes';

const PHASE_ORDER: Record<string, number> = {
  'provider-setup': 0,
  'worktree-started': 1,
  'sync-needed': 2,
  'closeout-pending': 3,
  'commit-approval-pending': 4,
  'integration-blocked': 5,
  'integration-pending': 6,
  'carryover-pending': 7,
  'cleanup-pending': 8,
  completed: 9,
  abandoned: 10,
};

/**
 * Pure: the resolved collections -> the Engine Room render model (slice 5e).
 *
 * The server already composes the enclosure-centered process nodes (`analytics.engineProcesses`)
 * with their observed/derived/planned/missing fact-state honesty, so this client seam does no
 * inference or session join. Takes a flat array (like `buildTopology`) so it stays React-free and
 * unit-testable. No clock, no git, no safety inference.
 */
export function buildEngineRoomModel(engineProcesses: EngineProcessNode[]): EngineRoomModel {
  const processes = engineProcesses
    .map((node) => {
      return {
        enclosureKey: node.worktreeGroup,
        node,
      };
    })
    .sort((a, b) => {
      const phase = (PHASE_ORDER[a.node.phase] ?? 99) - (PHASE_ORDER[b.node.phase] ?? 99);
      if (phase !== 0) return phase;
      return (a.node.leafId || a.node.taskName).localeCompare(b.node.leafId || b.node.taskName);
    });

  return {
    processes,
  };
}
